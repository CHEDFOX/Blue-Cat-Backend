"""Research jobs: persistence in ``research_jobs`` and a thread-pool runner.

The request text (question, case file) exists only in memory, as arguments of the running task; the
table holds a digest of the request, progress messages, and the structured result, all deleted at
``expires_at``.  Jobs are in-process: a restart marks unfinished jobs failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant.config import AssistantConfig
from ulis.assistant.llm import AnthropicLLM, LLMClient, LLMError
from ulis.assistant.pipeline import PipelineError, Refused, ResearchInput, ResearchPipeline
from ulis.catalog import Catalog
from ulis.db import models as m

log = logging.getLogger("ulis.assistant.jobs")

LLMFactory = Callable[[str], LLMClient]
ACTIVE = ("queued", "running")


class RateLimited(Exception):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def request_digest(req: ResearchInput) -> str:
    """SHA-256 of the canonical request. The only trace of the request that is ever stored or audited."""
    canonical = json.dumps(
        {
            "question": req.question,
            "case_text": req.case_text,
            "jurisdiction": req.jurisdiction,
            "as_of": req.as_of.isoformat() if req.as_of else None,
            "depth": req.depth,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_llm_factory(config: AssistantConfig) -> LLMFactory | None:
    if not config.llm_configured:
        return None
    assert config.api_key is not None
    key = config.api_key
    cache: dict[str, LLMClient] = {}

    def make(depth: str) -> LLMClient:
        model = config.model_for(depth)
        if model not in cache:
            cache[model] = AnthropicLLM(
                key,
                model,
                fallbacks=config.fallbacks,
                send_effort=config.effort != "off",
                timeout=config.request_timeout_s,
            )
        return cache[model]

    return make


def _now() -> datetime:
    return datetime.now(UTC)


class ResearchService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        catalog: Catalog,
        config: AssistantConfig,
        *,
        llm_factory: LLMFactory | None = None,
        candidate_budget: int = 200,
    ) -> None:
        self._factory = session_factory
        self._catalog = catalog
        self.config = config
        self.llm_factory = llm_factory if llm_factory is not None else default_llm_factory(config)
        self._candidate_budget = candidate_budget
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[uuid.UUID, Future[None]] = {}
        self._progress: dict[uuid.UUID, list[dict[str, str]]] = {}

    # ------------------------------------------------------------------ lifecycle

    @property
    def llm_available(self) -> bool:
        return self.llm_factory is not None

    def start(self) -> None:
        self.fail_interrupted()
        self.purge_expired()
        self._executor = ThreadPoolExecutor(max_workers=self.config.workers, thread_name_prefix="research")

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def fail_interrupted(self) -> int:
        """Jobs left queued/running by a process that died can never finish: mark them failed."""
        error = {
            "code": "interrupted",
            "message": "The server restarted while this research job was running. Please submit it again.",
        }
        with self._factory() as session:
            res = session.execute(
                update(m.ResearchJob).where(m.ResearchJob.status.in_(ACTIVE)).values(status="failed", error=error)
            )
            session.commit()
            return int(getattr(res, "rowcount", 0) or 0)

    def purge_expired(self) -> int:
        with self._factory() as session:
            res = session.execute(delete(m.ResearchJob).where(m.ResearchJob.expires_at <= func.now()))
            session.commit()
            return int(getattr(res, "rowcount", 0) or 0)

    # ------------------------------------------------------------------ create / read

    def check_limits(self, principal: str) -> None:
        with self._factory() as session:
            active = session.scalar(
                select(func.count())
                .select_from(m.ResearchJob)
                .where(m.ResearchJob.principal == principal, m.ResearchJob.status.in_(ACTIVE))
            )
            if (active or 0) >= self.config.max_active_per_principal:
                raise RateLimited("Too many research jobs are running for this key; wait for one to finish.", 30)
            since = _now() - timedelta(hours=1)
            recent = session.scalar(
                select(func.count())
                .select_from(m.ResearchJob)
                .where(m.ResearchJob.principal == principal, m.ResearchJob.created_at >= since)
            )
            if (recent or 0) >= self.config.rate_per_hour:
                raise RateLimited("Hourly research limit reached for this key.", 600)

    def create(self, principal: str, req: ResearchInput) -> uuid.UUID:
        self.purge_expired()
        self.check_limits(principal)
        job_id = uuid.uuid4()
        digest = request_digest(req)
        now = _now()
        with self._factory() as session:
            session.add(
                m.ResearchJob(
                    id=job_id,
                    status="queued",
                    principal=principal,
                    created_at=now,
                    expires_at=now + timedelta(hours=self.config.ttl_hours),
                    request_sha256=digest,
                    progress=[],
                )
            )
            session.add(
                m.AuditEvent(
                    actor=principal,
                    action="research_created",
                    subject=req.jurisdiction,
                    details={
                        "job_id": str(job_id),
                        "request_sha256": digest,
                        "depth": req.depth,
                        "as_of": req.as_of.isoformat() if req.as_of else None,
                        "question_chars": len(req.question),
                        "case_text_chars": len(req.case_text or ""),
                    },
                )
            )
            session.commit()
        return job_id

    def submit(self, job_id: uuid.UUID, principal: str, req: ResearchInput) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.config.workers, thread_name_prefix="research")
        future = self._executor.submit(self._run, job_id, principal, req)
        self._futures[job_id] = future
        future.add_done_callback(lambda _f, jid=job_id: self._futures.pop(jid, None))  # type: ignore[misc]

    def wait(self, job_id: uuid.UUID, timeout: float = 30.0) -> None:
        """Block until the job's task has finished (used by tests and the CLI)."""
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)

    def get(self, job_id: uuid.UUID, principal: str | None = None) -> dict[str, Any] | None:
        """The job as the API returns it; None if unknown, expired, or (when given) owned by someone else."""
        with self._factory() as session:
            row = session.get(m.ResearchJob, job_id)
            if row is None or (principal is not None and row.principal != principal):
                return None
            if row.expires_at <= _now():
                session.delete(row)
                session.commit()
                return None
            return {
                "id": str(row.id),
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "progress": row.progress or [],
                "result": row.result,
                "error": row.error,
            }

    # ------------------------------------------------------------------ the task

    def _update(self, job_id: uuid.UUID, **values: Any) -> None:
        with self._factory() as session:
            session.execute(update(m.ResearchJob).where(m.ResearchJob.id == job_id).values(**values))
            session.commit()

    def _report(self, job_id: uuid.UUID, stage: str, message: str) -> None:
        entries = self._progress.setdefault(job_id, [])
        entries.append({"at": _now().isoformat(), "stage": stage, "message": message})
        self._update(job_id, progress=list(entries))

    def _run(self, job_id: uuid.UUID, principal: str, req: ResearchInput) -> None:
        started = time.monotonic()
        status, error, result = "failed", None, None
        details: dict[str, Any] = {"job_id": str(job_id), "request_sha256": request_digest(req), "depth": req.depth}
        try:
            self._update(job_id, status="running")
            if self.llm_factory is None:
                raise PipelineError("llm_not_configured", "No language-model API key is configured.")
            pipeline = ResearchPipeline(
                llm=self.llm_factory(req.depth),
                session_factory=self._factory,
                catalog=self._catalog,
                config=self.config,
                report=lambda stage, message: self._report(job_id, stage, message),
                candidate_budget=self._candidate_budget,
            )
            outcome = pipeline.run(req)
            result = outcome.model_dump(mode="json")
            status = "completed"
            details.update(
                searches=outcome.usage.searches,
                authorities=len(outcome.authorities),
                removed_claims=outcome.removed_claims,
                input_tokens=outcome.usage.input_tokens,
                output_tokens=outcome.usage.output_tokens,
                model=outcome.usage.model,
            )
        except Refused as exc:
            status, error = "refused", {"code": exc.code, "message": exc.message}
        except PipelineError as exc:
            error = {"code": exc.code, "message": exc.message}
        except LLMError as exc:
            error = {"code": exc.code, "message": exc.message}
        except Exception as exc:
            # Never log exception text: it could carry request content. The type is enough to find the bug.
            log.error("research job %s failed unexpectedly: %s", job_id, type(exc).__name__)
            log.debug("traceback for research job %s", job_id, exc_info=True)
            error = {"code": "internal_error", "message": "The research job failed unexpectedly."}
        finally:
            details.update(status=status, seconds=round(time.monotonic() - started, 2))
            if error:
                details["error_code"] = error["code"]
            try:
                self._update(job_id, status=status, result=result, error=error)
                with self._factory() as session:
                    session.add(
                        m.AuditEvent(actor=principal, action="research_finished", subject=None, details=details)
                    )
                    session.commit()
            except Exception as exc:
                log.error("could not record the outcome of research job %s: %s", job_id, type(exc).__name__)
            self._progress.pop(job_id, None)
