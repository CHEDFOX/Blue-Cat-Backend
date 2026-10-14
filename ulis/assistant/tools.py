"""Tools the model may call: thin, capped wrappers over the retrieval, lookup and coverage code.

Every provision or judgment paragraph a tool returns is registered (``AuthorityRegistry``) under a
stable id; those ids are the only thing the model is allowed to cite.  Tool results are DATA: the
system prompt tells the model never to follow instructions found in them.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant.registry import AuthorityRecord, AuthorityRegistry
from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.retrieval.coverage import coverage_for
from ulis.retrieval.details import load_provision
from ulis.retrieval.lookup import NotFound, provision_at
from ulis.retrieval.search import SearchError, SearchRequest, search
from ulis.verification.limitations import jurisdiction_limitations

SEARCH_RESULT_LIMIT = 8
SEARCH_TEXT_CHARS = 1500
PROVISION_TEXT_CHARS = 6000
MAX_CHILDREN = 20
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")

SEARCH_LAW = "search_law"
GET_PROVISION = "get_provision"
LIST_JURISDICTIONS = "list_jurisdictions"
COVERAGE = "coverage"


def tool_specs(catalog: Catalog) -> list[dict[str, Any]]:
    """Strict tool definitions. Deterministic for a given catalog, so they stay prompt-cacheable."""
    ids = sorted(catalog.jurisdictions.profiles)
    return [
        {
            "name": SEARCH_LAW,
            "description": (
                "Search the indexed law of ONE jurisdiction as it stood on a given date. Returns at most "
                f"{SEARCH_RESULT_LIMIT} provisions or judgment paragraphs, each with an authority_id, an exact "
                "citation, its uri, the stored text (long texts are clipped: use get_provision for the full "
                "text) and legal-status limitations. Only text that appears in the results can be relied on. "
                "Queries may be written in any language; set query_language when you know it. Use short "
                "keyword queries naming the legal concept, not whole sentences. An empty result says nothing "
                "about the law of the jurisdiction: it only means nothing matching is indexed."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords (1 to 500 characters)."},
                    "jurisdiction": {"type": "string", "enum": ids, "description": "Profile id, e.g. DE or GB-ENG."},
                    "as_of": {"type": "string", "description": "Date (YYYY-MM-DD) at which the law must apply."},
                    "languages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: only return texts in these languages (BCP 47, e.g. de, fr).",
                    },
                    "include_case_law": {"type": "boolean", "description": "Optional (default true)."},
                    "query_language": {
                        "type": "string",
                        "description": "Optional: the language the query is written in (BCP 47).",
                    },
                },
                "required": ["query", "jurisdiction", "as_of"],
                "additionalProperties": False,
            },
        },
        {
            "name": GET_PROVISION,
            "description": (
                "Read one provision by its uri (taken from search_law or from the children of another "
                "provision) as it stood on a date: full text, citation, validity period, other versions and "
                "limitations. Use it to read a provision in full or to check a neighbouring provision."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "Provision uri, exactly as returned by a tool."},
                    "as_of": {"type": "string", "description": "Date (YYYY-MM-DD)."},
                    "language": {"type": "string", "description": "Optional language of the text (BCP 47)."},
                },
                "required": ["uri", "as_of"],
                "additionalProperties": False,
            },
        },
        {
            "name": LIST_JURISDICTIONS,
            "description": (
                "List every jurisdiction profile with its id, name, official languages and corpus_status "
                "(none, fictional_sample, pilot, partial, broad). Use it to pick a valid jurisdiction id."
            ),
            "strict": True,
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": COVERAGE,
            "description": (
                "What is actually indexed for one jurisdiction: corpus status, counts of instruments, "
                "judgments and passages, indexed languages, active sources and known gaps. Check it before "
                "concluding that the law does not address something."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"jurisdiction": {"type": "string", "enum": ids}},
                "required": ["jurisdiction"],
                "additionalProperties": False,
            },
        },
    ]


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    authority_ids: list[str] = field(default_factory=list)


def _error(message: str) -> ToolResult:
    return ToolResult(json.dumps({"error": message}, ensure_ascii=False), is_error=True)


def _ok(payload: dict[str, Any], authority_ids: list[str] | None = None) -> ToolResult:
    return ToolResult(json.dumps(payload, ensure_ascii=False, default=str), authority_ids=authority_ids or [])


def _limitations_for_model(limitations: list[dict[str, str]]) -> dict[str, Any]:
    """Warnings and critical limitations in full; informational ones as bare codes (keeps tool results small)."""
    return {
        "limitations": [x for x in limitations if x.get("severity") != "info"],
        "info_codes": sorted({x["code"] for x in limitations if x.get("severity") == "info"}),
    }


def _web_url(value: str | None) -> str | None:
    """Source documents may carry internal locators (``manifest:...``); only real web links are exposed."""
    return value if value and value.startswith(("http://", "https://")) else None


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit].rstrip(), True)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


class ToolBox:
    """Executes tool calls for one job. Not shared between jobs."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        catalog: Catalog,
        registry: AuthorityRegistry,
        *,
        today: date,
        candidate_budget: int = 200,
        max_searches: int = 12,
    ) -> None:
        self._factory = session_factory
        self._catalog = catalog
        self.registry = registry
        self._today = today
        self._candidate_budget = candidate_budget
        self.max_searches = max_searches
        self.max_calls = 4 * max_searches + 12
        self.searches_used = 0
        self.calls = 0
        self._issue_searches_left = max_searches
        # Deduplicated search-level limitations and the coverage rows the model looked at.
        self.limitations: dict[tuple[str, str], dict[str, str]] = {}
        self.coverage_seen: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ budget

    def begin_issue(self, allowance: int) -> None:
        self._issue_searches_left = max(0, min(allowance, self.max_searches - self.searches_used))

    @property
    def searches_left(self) -> int:
        return min(self._issue_searches_left, self.max_searches - self.searches_used)

    # ------------------------------------------------------------------ dispatch

    def execute(self, name: str, args: Any) -> ToolResult:
        self.calls += 1
        if self.calls > self.max_calls:
            return _error("The tool-call budget for this job is used up. Stop calling tools and finish.")
        if not isinstance(args, dict):
            return _error("Tool input must be a JSON object.")
        try:
            if name == SEARCH_LAW:
                return self._search_law(args)
            if name == GET_PROVISION:
                return self._get_provision(args)
            if name == LIST_JURISDICTIONS:
                return self._list_jurisdictions()
            if name == COVERAGE:
                return self._coverage(args)
        except NotFound as exc:
            return _error(str(exc))
        return _error(
            f"Unknown tool {name!r}. Available tools: {SEARCH_LAW}, {GET_PROVISION}, {LIST_JURISDICTIONS}, {COVERAGE}."
        )

    @contextmanager
    def _read(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    def _check_jurisdiction(self, value: Any) -> str | ToolResult:
        if not isinstance(value, str) or value not in self._catalog.jurisdictions:
            return _error(f"Unknown jurisdiction {value!r}; call {LIST_JURISDICTIONS} for valid ids.")
        return value

    # ------------------------------------------------------------------ search_law

    def _search_law(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            return _error("query must be a non-empty string of at most 500 characters.")
        jurisdiction = self._check_jurisdiction(args.get("jurisdiction"))
        if isinstance(jurisdiction, ToolResult):
            return jurisdiction
        as_of = _parse_date(args.get("as_of"))
        if as_of is None:
            return _error("as_of must be a date in YYYY-MM-DD format.")
        languages = args.get("languages") or []
        if not isinstance(languages, list) or not all(isinstance(x, str) and _LANG_RE.match(x) for x in languages):
            return _error("languages must be a list of BCP 47 codes such as 'de' or 'fr'.")
        query_language = args.get("query_language")
        if query_language is not None and not (isinstance(query_language, str) and _LANG_RE.match(query_language)):
            return _error("query_language must be a BCP 47 code such as 'de'.")
        include_case_law = args.get("include_case_law", True)
        if not isinstance(include_case_law, bool):
            return _error("include_case_law must be a boolean.")
        if self.searches_left <= 0:
            return _error("The search budget for this issue is used up. Stop searching and report what you found.")

        self.searches_used += 1
        request = SearchRequest(
            q=query.strip(),
            jurisdiction=jurisdiction,
            as_of=as_of,
            languages=languages,
            include_case_law=include_case_law,
            query_language=query_language or None,
            limit=SEARCH_RESULT_LIMIT,
        )
        self._issue_searches_left -= 1
        with self._read() as session:
            try:
                result = search(
                    session, self._catalog, request, today=self._today, candidate_budget=self._candidate_budget
                )
            except SearchError as exc:
                return _error(str(exc))
            judgment_ids = self._judgment_paragraph_ids(session, result["results"])
        ids: list[str] = []
        shown: list[dict[str, Any]] = []
        for hit in result["results"]:
            record = self.registry.register(self._record_from_hit(hit, judgment_ids))
            text, truncated = _clip(hit["text"], SEARCH_TEXT_CHARS)
            ids.append(record.id)
            shown.append(
                {
                    "authority_id": record.id,
                    "citation": record.citation,
                    "uri": record.uri,
                    "kind": record.kind,
                    "jurisdiction": record.jurisdiction,
                    "valid_from": record.valid_from,
                    "valid_to": record.valid_to,
                    "authority": record.authority_label,
                    "text": text,
                    "text_truncated": truncated,
                    **_limitations_for_model(record.limitations),
                }
            )
        for lim in result["limitations"]:
            if lim["code"] != "NOT_LEGAL_ADVICE":
                self.limitations[(lim["code"], lim["message"])] = lim
        payload: dict[str, Any] = {
            "jurisdiction": jurisdiction,
            "as_of": as_of.isoformat(),
            "results": shown,
            "searches_left_for_this_issue": self.searches_left,
            "search_notes": [
                f"{lim['code']}: {lim['message']}" for lim in result["limitations"] if lim["code"] != "NOT_LEGAL_ADVICE"
            ],
        }
        if not shown:
            payload["note"] = (
                "No indexed authority matched. This does not mean the law is silent: check coverage and try "
                "different keywords before concluding anything."
            )
        return _ok(payload, ids)

    @staticmethod
    def _judgment_paragraph_ids(session: Session, hits: list[dict[str, Any]]) -> dict[str, str]:
        passage_ids = [uuid.UUID(h["passage_id"]) for h in hits if h["kind"] == "judgment_paragraph"]
        if not passage_ids:
            return {}
        rows = session.execute(
            select(m.Passage.id, m.Passage.judgment_paragraph_id).where(m.Passage.id.in_(passage_ids))
        ).all()
        return {str(pid): str(para) for pid, para in rows if para is not None}

    @staticmethod
    def _record_from_hit(hit: dict[str, Any], judgment_ids: dict[str, str]) -> AuthorityRecord:
        src = hit["source"]
        version = hit["version"]
        is_provision = hit["kind"] == "provision"
        return AuthorityRecord(
            kind="provision" if is_provision else "judgment_paragraph",
            target_id=version["id"] if is_provision else judgment_ids[hit["passage_id"]],
            uri=hit["unit"]["uri"],
            citation=hit["citation"],
            jurisdiction=hit["jurisdiction"]["id"],
            text=hit["text"],
            text_sha256=hit["text_sha256"],
            valid_from=version.get("valid_from"),
            valid_to=version.get("valid_to"),
            authority_label=hit["authority"]["label"],
            limitations=[dict(x) for x in hit["limitations"]],
            source={
                "name": src["source_name"],
                "url": _web_url(src.get("document_uri")),
                "attribution": src.get("attribution"),
            },
            language=version.get("language"),
        )

    # ------------------------------------------------------------------ get_provision

    def _get_provision(self, args: dict[str, Any]) -> ToolResult:
        uri = args.get("uri")
        if not isinstance(uri, str) or not uri.strip() or len(uri) > 600:
            return _error("uri must be a provision uri exactly as returned by a tool.")
        as_of = _parse_date(args.get("as_of"))
        if as_of is None:
            return _error("as_of must be a date in YYYY-MM-DD format.")
        language = args.get("language")
        if language is not None and not (isinstance(language, str) and _LANG_RE.match(language)):
            return _error("language must be a BCP 47 code such as 'de'.")
        with self._read() as session:
            try:
                data = provision_at(session, self._catalog, uri.strip(), as_of, language or None)
            except NotFound as exc:
                return _error(f"{exc}. Use uris returned by {SEARCH_LAW} or listed as children of a provision.")
            version = data["version"]
            source: dict[str, Any] = {}
            if version is not None:
                src = load_provision(session, self._catalog, uuid.UUID(version["id"])).source
                source = {"name": src.source_name, "url": _web_url(src.document_uri), "attribution": src.attribution}
        base: dict[str, Any] = {
            "uri": data["provision"]["uri"],
            "citation": data["provision"]["citation"],
            "instrument": data["instrument"]["title"],
            "jurisdiction": data["instrument"]["jurisdiction"],
            "as_of": data["as_of"],
            "status_on_date": data["status_on_date"],
            "language": data["language"],
            "available_languages": data["available_languages"],
            "other_versions": [
                {
                    "valid_from": t["valid_from"],
                    "valid_to": t["valid_to"],
                    "status": t["status"],
                    "selected": t["selected"],
                }
                for t in data["timeline"]
            ],
            "children": data["children"][:MAX_CHILDREN],
            **_limitations_for_model(data["limitations"]),
        }
        if version is None:
            base["note"] = "No version of this provision applies on that date, so there is no text to cite."
            return _ok(base)
        record = self.registry.register(
            AuthorityRecord(
                kind="provision",
                target_id=version["id"],
                uri=data["provision"]["uri"],
                citation=data["provision"]["citation"],
                jurisdiction=data["instrument"]["jurisdiction"],
                text=version["text"],
                text_sha256=version["text_sha256"],
                valid_from=version["valid_from"],
                valid_to=version["valid_to"],
                authority_label=version["authority"]["label"],
                limitations=[dict(x) for x in data["limitations"]],
                source=source,
                language=data["language"],
            )
        )
        text, truncated = _clip(version["text"], PROVISION_TEXT_CHARS)
        base.update(
            authority_id=record.id,
            heading=version["heading"],
            text=text,
            text_truncated=truncated,
            valid_from=version["valid_from"],
            valid_to=version["valid_to"],
            authority=version["authority"]["label"],
        )
        return _ok(base, [record.id])

    # ------------------------------------------------------------------ list_jurisdictions / coverage

    def _list_jurisdictions(self) -> ToolResult:
        rows = [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "parent": p.parent,
                "official_languages": p.official_languages,
                "corpus_status": p.coverage.corpus_status,
                "fictional": p.fictional,
            }
            for p in self._catalog.jurisdictions.profiles.values()
        ]
        return _ok({"jurisdictions": sorted(rows, key=lambda r: str(r["id"]))})

    def _coverage(self, args: dict[str, Any]) -> ToolResult:
        jurisdiction = self._check_jurisdiction(args.get("jurisdiction"))
        if isinstance(jurisdiction, ToolResult):
            return jurisdiction
        with self._read() as session:
            row = coverage_for(session, self._catalog, [jurisdiction])[0]
        reg = self._catalog.jurisdictions
        self.coverage_seen[jurisdiction] = row
        payload = {
            **row,
            "sub_jurisdictions": [
                {"id": c, "corpus_status": reg.get(c).coverage.corpus_status} for c in reg.children(jurisdiction)
            ],
            "limitations": [lim.as_dict() for lim in jurisdiction_limitations(self._catalog, jurisdiction)],
        }
        return _ok(payload)
