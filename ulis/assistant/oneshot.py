"""Run the research pipeline once, synchronously, without the HTTP job layer (CLI and evaluation)."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant.config import AssistantConfig
from ulis.assistant.jobs import default_llm_factory
from ulis.assistant.llm import LLMClient
from ulis.assistant.pipeline import Reporter, ResearchInput, ResearchPipeline
from ulis.assistant.schemas import ResearchResult
from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.settings import Settings


class NotConfigured(RuntimeError):
    pass


def run_once(
    settings: Settings,
    catalog: Catalog,
    config: AssistantConfig,
    request: ResearchInput,
    *,
    llm: LLMClient | None = None,
    report: Reporter | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> ResearchResult:
    if llm is None:
        factory = default_llm_factory(config)
        if factory is None:
            raise NotConfigured("ANTHROPIC_API_KEY is not set")
        llm = factory(request.depth)
    pipeline = ResearchPipeline(
        llm=llm,
        session_factory=session_factory or make_session_factory(make_engine(settings.database_url)),
        catalog=catalog,
        config=config,
        report=report,
        candidate_budget=settings.retrieval_candidate_budget,
    )
    return pipeline.run(request)
