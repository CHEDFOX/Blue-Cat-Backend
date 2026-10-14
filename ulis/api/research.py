"""``POST /v1/research`` and ``GET /v1/research/{id}``: the LLM research assistant as a job API.

The question and the case text are validated, handed to a background task and never stored or
logged; only a SHA-256 of the request is audited (see ``ulis.assistant.jobs``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ulis.api.deps import AppState, Principal, require_api_key, state
from ulis.assistant.config import (
    MAX_BODY_BYTES,
    MAX_CASE_CHARS,
    MAX_QUESTION_CHARS,
    AssistantConfig,
    load_assistant_config,
)
from ulis.assistant.jobs import LLMFactory, RateLimited, ResearchService
from ulis.assistant.pipeline import ResearchInput
from ulis.assistant.safety import clean_text

router = APIRouter()
PrincipalDep = Annotated[Principal, Depends(require_api_key)]
StateDep = Annotated[AppState, Depends(state)]


class ResearchRequest(BaseModel):
    """Body of ``POST /v1/research``. Length limits are enforced as 413 before validation."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, description=f"1..{MAX_QUESTION_CHARS} characters")
    case_text: str | None = Field(default=None, description=f"Pasted case file, at most {MAX_CASE_CHARS} characters")
    jurisdiction: str | None = Field(default=None, description="Profile id such as DE, GB-ENG or CA")
    as_of: date | None = None
    depth: Literal["standard", "deep"] = "standard"

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not clean_text(value).strip():
            raise ValueError("question must not be blank")
        return value


def _problem(status: int, detail: Any, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status, detail, headers=headers)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _problem(413, "request body is too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _problem(413, "request body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _validation_detail(exc: ValidationError) -> list[dict[str, Any]]:
    # pydantic echoes the offending input by default; here that would be the user's question.
    return [{"loc": ["body", *map(str, e["loc"])], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]


def _external_rate_limit(request: Request, depth: str) -> None:
    """Charge the shared research limiter (``ulis.api.security``): one unit is charged by the middleware for
    every ``POST /v1/research*``; a deep run (stronger model, more searches) costs three."""
    from ulis.api.security import check_research_limits

    check_research_limits(request, cost=3 if depth == "deep" else 1)


def _parse_request(raw: bytes, st: AppState) -> ResearchInput:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise _problem(400, "request body is not valid JSON") from None
    if not isinstance(data, dict):
        raise _problem(422, [{"loc": ["body"], "msg": "body must be a JSON object", "type": "type_error"}])
    question, case_text = data.get("question"), data.get("case_text")
    if isinstance(question, str) and len(question) > MAX_QUESTION_CHARS:
        raise _problem(413, f"question is limited to {MAX_QUESTION_CHARS} characters")
    if isinstance(case_text, str) and len(case_text) > MAX_CASE_CHARS:
        raise _problem(413, f"case_text is limited to {MAX_CASE_CHARS} characters")
    try:
        body = ResearchRequest.model_validate(data)
    except ValidationError as exc:
        raise _problem(422, _validation_detail(exc)) from None
    jurisdiction = body.jurisdiction.strip().upper() if body.jurisdiction and body.jurisdiction.strip() else None
    if jurisdiction is not None and jurisdiction not in st.catalog.jurisdictions:
        raise _problem(
            422,
            [
                {
                    "loc": ["body", "jurisdiction"],
                    "msg": f"unknown jurisdiction {jurisdiction[:40]!r}",
                    "type": "value_error",
                }
            ],
        )
    return ResearchInput(
        question=clean_text(body.question).strip(),
        case_text=clean_text(body.case_text).strip() if body.case_text and body.case_text.strip() else None,
        jurisdiction=jurisdiction,
        as_of=body.as_of,
        depth=body.depth,
    )


@router.post(
    "/v1/research",
    status_code=202,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ResearchRequest.model_json_schema()}},
        }
    },
    responses={
        400: {"description": "Body is not valid JSON"},
        413: {"description": "Question, case text or body too large"},
        422: {"description": "Invalid field"},
        429: {"description": "Rate or concurrency limit reached"},
        503: {"description": "No language-model API key is configured"},
    },
)
async def create_research(request: Request, st: StateDep, principal: PrincipalDep) -> JSONResponse:
    service: ResearchService = request.app.state.research
    req = _parse_request(await _read_body(request), st)
    if not service.llm_available:
        raise _problem(
            503,
            "The research assistant is not available: no language-model API key (ANTHROPIC_API_KEY) is configured "
            "on this server. The rest of the API is unaffected.",
        )
    try:
        await run_in_threadpool(_external_rate_limit, request, req.depth)
        job_id = await run_in_threadpool(service.create, principal.id, req)
    except RateLimited as exc:
        raise _problem(429, exc.message, {"Retry-After": str(exc.retry_after)}) from None
    service.submit(job_id, principal.id, req)
    return JSONResponse(
        status_code=202,
        content={"id": str(job_id), "status": "queued"},
        headers={"Location": f"/v1/research/{job_id}"},
    )


@router.get("/v1/research/{job_id}", responses={404: {"description": "Unknown or expired id"}})
def get_research(job_id: str, request: Request, principal: PrincipalDep) -> dict[str, Any]:
    service: ResearchService = request.app.state.research
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        raise _problem(404, "unknown or expired research job") from None
    job = service.get(parsed, principal.id)
    if job is None:
        raise _problem(404, "unknown or expired research job")
    return job


def install_research(
    app: FastAPI, *, config: AssistantConfig | None = None, llm_factory: LLMFactory | None = None
) -> ResearchService:
    """Mount the research routes on ``app`` (which must already carry ``app.state.ulis``)."""
    st: AppState = app.state.ulis
    service = ResearchService(
        st.session_factory,
        st.catalog,
        config or load_assistant_config(),
        llm_factory=llm_factory,
        candidate_budget=st.settings.retrieval_candidate_budget,
    )
    app.state.research = service
    app.include_router(router)

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner: FastAPI) -> AsyncIterator[Any]:
        service.start()
        try:
            async with previous(inner) as maybe_state:
                yield maybe_state
        finally:
            service.stop()

    app.router.lifespan_context = lifespan
    return service
