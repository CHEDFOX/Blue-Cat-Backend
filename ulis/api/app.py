"""FastAPI application.

Ingestion is deliberately *not* exposed over HTTP: it runs from the CLI / workers with
their own credentials, which keeps write access to the corpus off the public surface.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ulis import __version__
from ulis.api.deps import AppState, Principal, get_session, require_api_key, state
from ulis.api.research import install_research
from ulis.api.security import install_security
from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.db import models as m
from ulis.retrieval.coverage import coverage_for
from ulis.retrieval.lookup import NotFound, instrument_detail, judgment_detail, provision_at
from ulis.retrieval.search import SearchError, SearchRequest, search
from ulis.settings import Settings, load_settings
from ulis.storage.object_store import LocalObjectStore, ObjectStore
from ulis.verification.integrity import verify_source_document
from ulis.verification.quotes import check_quote

SessionDep = Annotated[Session, Depends(get_session)]
StateDep = Annotated[AppState, Depends(state)]
PrincipalDep = Annotated[Principal, Depends(require_api_key)]


class QuoteRequest(BaseModel):
    quote: str = Field(min_length=1, max_length=5000)
    provision_version_id: uuid.UUID | None = None
    judgment_paragraph_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_target(self) -> QuoteRequest:
        if (self.provision_version_id is None) == (self.judgment_paragraph_id is None):
            raise ValueError("give exactly one of provision_version_id or judgment_paragraph_id")
        return self


def create_app(
    settings: Settings | None = None, catalog: Catalog | None = None, store: ObjectStore | None = None
) -> FastAPI:
    settings = settings or load_settings()
    if settings.environment != "development" and not settings.api_keys:
        raise RuntimeError("ULIS_API_KEYS must be set outside development")
    catalog = catalog or Catalog.load(settings.config_dir)
    app = FastAPI(
        title="ULIS API",
        version=__version__,
        description="Jurisdiction-aware legal research API. Research aid only - not legal advice.",
    )
    app.state.ulis = AppState(
        settings=settings,
        catalog=catalog,
        session_factory=make_session_factory(make_engine(settings.database_url)),
        store=store or LocalObjectStore(settings.object_store_dir),
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SearchError)
    async def bad_search(_: Request, exc: SearchError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/jurisdictions")
    def jurisdictions(st: StateDep, _: PrincipalDep) -> dict[str, Any]:
        reg = st.catalog.jurisdictions
        return {
            "tree": reg.tree(),
            "jurisdictions": [
                {
                    "id": p.id,
                    "name": p.name,
                    "kind": p.kind,
                    "parent": p.parent,
                    "fictional": p.fictional,
                    "profile_version": p.profile_version,
                    "verification": p.verification.status,
                    "official_languages": p.official_languages,
                    "corpus_status": p.coverage.corpus_status,
                    "legal_families": p.legal_system.families,
                    "courts": [{"id": c.id, "name": c.name} for c in p.courts],
                }
                for p in reg.profiles.values()
            ],
        }

    @app.get("/v1/jurisdictions/{jurisdiction_id}")
    def jurisdiction(jurisdiction_id: str, st: StateDep, session: SessionDep, _: PrincipalDep) -> dict[str, Any]:
        reg = st.catalog.jurisdictions
        if jurisdiction_id not in reg:
            raise HTTPException(404, f"unknown jurisdiction {jurisdiction_id}")
        profile = reg.get(jurisdiction_id)
        return {
            "profile": profile.model_dump(mode="json"),
            "profile_sha256": profile.sha256(),
            "chain": reg.chain(jurisdiction_id),
            "children": reg.children(jurisdiction_id),
            "coverage": coverage_for(session, st.catalog, [jurisdiction_id])[0],
        }

    @app.get("/v1/sources")
    def sources(st: StateDep, _: PrincipalDep) -> list[dict[str, Any]]:
        return [s.model_dump(mode="json") for s in st.catalog.sources]

    @app.get("/v1/coverage")
    def coverage(st: StateDep, session: SessionDep, _: PrincipalDep) -> list[dict[str, Any]]:
        return coverage_for(session, st.catalog)

    @app.get("/v1/search")
    def search_endpoint(
        st: StateDep,
        session: SessionDep,
        principal: PrincipalDep,
        q: Annotated[str, Query(min_length=1, max_length=500)],
        jurisdiction: str | None = None,
        as_of: date | None = None,
        language: Annotated[list[str] | None, Query()] = None,
        include_foreign: bool = False,
        include_proposed: bool = False,
        include_historical: bool = False,
        include_prospective: bool = False,
        include_case_law: bool = True,
        forum_court: str | None = None,
        query_language: str | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        req = SearchRequest(
            q=q,
            jurisdiction=jurisdiction or None,
            as_of=as_of,
            languages=language or [],
            include_foreign=include_foreign,
            include_proposed=include_proposed,
            include_historical=include_historical,
            include_prospective=include_prospective,
            include_case_law=include_case_law,
            forum_court=forum_court or None,
            query_language=query_language or None,
            limit=limit,
        )
        result = search(
            session, st.catalog, req, today=date.today(), candidate_budget=st.settings.retrieval_candidate_budget
        )
        # Queries can reveal client matters: audit a digest, never the text.
        session.add(
            m.AuditEvent(
                actor=principal.id,
                action="search",
                subject=jurisdiction,
                details={
                    "q_sha256": hashlib.sha256(q.encode()).hexdigest(),
                    "as_of": result["query"]["as_of"],
                    "results": len(result["results"]),
                },
            )
        )
        return result

    @app.get("/v1/provisions")
    def provision(
        st: StateDep,
        session: SessionDep,
        _: PrincipalDep,
        uri: str,
        as_of: date | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        return provision_at(session, st.catalog, uri, as_of or date.today(), language)

    @app.get("/v1/instruments")
    def instrument(st: StateDep, session: SessionDep, _: PrincipalDep, uri: str) -> dict[str, Any]:
        return instrument_detail(session, st.catalog, uri)

    @app.get("/v1/judgments")
    def judgment(st: StateDep, session: SessionDep, _: PrincipalDep, uri: str) -> dict[str, Any]:
        return judgment_detail(session, st.catalog, uri)

    @app.post("/v1/verify/quote")
    def verify_quote(body: QuoteRequest, session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
        if body.provision_version_id is not None:
            pv = session.get(m.ProvisionVersion, body.provision_version_id)
            if pv is None:
                raise HTTPException(404, "unknown provision version")
            target_text, subject_kind, subject_id, sha = pv.text, "provision_version", pv.id, pv.text_sha256
        else:
            para = session.get(m.JudgmentParagraph, body.judgment_paragraph_id)
            if para is None:
                raise HTTPException(404, "unknown judgment paragraph")
            target_text, subject_kind, subject_id, sha = para.text, "judgment_paragraph", para.id, para.text_sha256
        res = check_quote(body.quote, target_text)
        session.add(
            m.VerificationRecord(
                subject_kind=subject_kind,
                subject_id=str(subject_id),
                check="quote_exactness",
                result="pass"
                if res.result == "exact"
                else ("warning" if res.result == "match_after_normalisation" else "fail"),
                details={"normalisations": res.normalisations},
                checker=principal.id,
            )
        )
        return {
            "result": res.result,
            "offset": res.offset,
            "normalisations": res.normalisations,
            "subject_kind": subject_kind,
            "subject_id": str(subject_id),
            "text_sha256": sha,
        }

    @app.post("/v1/verify/source-documents/{document_id}")
    def verify_document(
        document_id: uuid.UUID, st: StateDep, session: SessionDep, principal: PrincipalDep
    ) -> dict[str, Any]:
        try:
            return verify_source_document(session, st.catalog, st.store, document_id, checker=principal.id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/v1/source-documents")
    def source_documents(session: SessionDep, _: PrincipalDep) -> list[dict[str, Any]]:
        return [
            {
                "id": str(d.id),
                "source_id": d.source_id,
                "external_id": d.external_id,
                "sha256": d.sha256,
                "media_type": d.media_type,
                "byte_size": d.byte_size,
                "retrieved_at": d.retrieved_at.isoformat(),
            }
            for d in session.scalars(select(m.SourceDocument).order_by(m.SourceDocument.external_id))
        ]

    install_security(app, settings)  # rate limits, body limits, CORS, access log, /ready, production checks
    install_research(app)
    return app
