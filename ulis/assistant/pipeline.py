"""The research pipeline: understanding -> searching (tool loop per issue) -> drafting -> verifying.

The case text is shown to the model exactly once, in the understanding call, inside unguessable
delimiters; later stages work from the extracted facts only.  Everything the model produces is
treated as untrusted until ``ulis.assistant.verify`` has checked it against the database.
"""

from __future__ import annotations

import json
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant import prompts
from ulis.assistant.config import AssistantConfig
from ulis.assistant.llm import LLMClient, LLMRequest, LLMResponse, Usage
from ulis.assistant.registry import AuthorityRecord, AuthorityRegistry
from ulis.assistant.safety import clean_text, wrap_untrusted
from ulis.assistant.schemas import (
    DraftOut,
    IssueOut,
    JurisdictionOut,
    LimitationOut,
    ModelOutputError,
    ResearchResult,
    SearchNotes,
    UnderstandingOut,
    UnderstandingResult,
    UsageOut,
    draft_schema,
    parse_model_json,
    understanding_schema,
)
from ulis.assistant.tools import ToolBox, tool_specs
from ulis.assistant.verify import IssueRef, verify_guidance
from ulis.catalog import Catalog
from ulis.retrieval.coverage import coverage_for

DISCLAIMER = (
    "This is a research aid for lawyers, not legal advice. The guidance was drafted by an AI model working only "
    "from the indexed corpus and then checked mechanically against the stored texts; it can still be incomplete "
    "or wrong. A qualified lawyer must verify every authority against the official source before relying on it."
)

UNDERSTANDING_TOKENS = 8000
SEARCH_TURN_TOKENS = 8000
DRAFT_TOKENS = 20000
MIN_CALL_TOKENS = 1500
MAX_ISSUES = {"standard": 6, "deep": 8}
MAX_JURISDICTIONS = 4
MAX_AUTHORITIES_PER_ISSUE = 12
MAX_AUTHORITIES_TOTAL = 40
DRAFT_TEXT_CHARS = 3000
EFFORT = {"understanding": "medium", "searching": "medium", "drafting": "high"}

Reporter = Callable[[str, str], None]


class PipelineError(Exception):
    """A job-level failure with a stable ``code`` and a user-safe ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Refused(PipelineError):
    """The model declined to work on the request (safety refusal)."""

    def __init__(self) -> None:
        super().__init__("refused", "The language model declined to process this request.")


@dataclass
class ResearchInput:
    question: str
    case_text: str | None
    jurisdiction: str | None
    as_of: date | None
    depth: Literal["standard", "deep"] = "standard"


@dataclass
class _Issue:
    id: str
    title: str
    area_of_law: str
    question: str


class ResearchPipeline:
    def __init__(
        self,
        *,
        llm: LLMClient,
        session_factory: sessionmaker[Session],
        catalog: Catalog,
        config: AssistantConfig,
        report: Reporter | None = None,
        today: date | None = None,
        candidate_budget: int = 200,
    ) -> None:
        self.llm = llm
        self._factory = session_factory
        self.catalog = catalog
        self.config = config
        self._report: Reporter = report or (lambda stage, message: None)
        self.today = today or date.today()
        self._candidate_budget = candidate_budget
        self.usage = Usage()
        self._output_budget = 0
        self._budget_hit = False

    # ------------------------------------------------------------------ model calls

    def _effort(self, stage: str) -> str | None:
        if self.config.effort == "off":
            return None
        return EFFORT[stage] if self.config.effort == "auto" else self.config.effort

    def _tokens_left(self, stage: str) -> int:
        left = self._output_budget - self.usage.output_tokens
        if stage != "drafting":
            left -= min(DRAFT_TOKENS, self._output_budget // 3)
        return left

    def _call(
        self,
        stage: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        cap: int,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        cache: bool = False,
    ) -> LLMResponse:
        max_tokens = min(cap, self._tokens_left(stage))
        if max_tokens < MIN_CALL_TOKENS:
            self._budget_hit = True
            raise PipelineError("output_budget_exhausted", "The output-token budget for this job is used up.")
        response = self.llm.complete(
            LLMRequest(
                stage=stage,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=tools or [],
                json_schema=schema,
                effort=self._effort(stage),
                cache=cache,
            )
        )
        self.usage.add(response.usage)
        if response.stop_reason == "refusal":
            raise Refused
        return response

    def _ask_json(
        self,
        stage: str,
        system: str,
        user: str,
        model: type[Any],
        schema: dict[str, Any],
        *,
        cap: int,
    ) -> Any:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        response = self._call(stage, system, messages, cap=cap, schema=schema)
        try:
            return parse_model_json(response.text, model)
        except ModelOutputError as first:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": prompts.JSON_REPAIR.format(error=first)})
            response = self._call(stage, system, messages, cap=cap, schema=schema)
            try:
                return parse_model_json(response.text, model)
            except ModelOutputError as second:
                raise PipelineError(
                    "model_output_invalid", f"The model did not return usable {stage} output ({second})."
                ) from None

    # ------------------------------------------------------------------ run

    def run(self, req: ResearchInput) -> ResearchResult:
        self._output_budget = self.config.output_budget(req.depth)
        registry = AuthorityRegistry()
        toolbox = ToolBox(
            self._factory,
            self.catalog,
            registry,
            today=self.today,
            candidate_budget=self._candidate_budget,
            max_searches=self.config.search_cap(req.depth),
        )

        understanding, issues, effective_as_of, jurisdiction_ids, coverage_rows = self._understand(req)
        gaps: dict[str, list[str]] = {}
        notes: dict[str, SearchNotes | None] = {}
        seen: dict[str, list[str]] = {}
        searchable = [j.id for j in understanding.jurisdictions if j.covered]
        if issues and not searchable:
            for issue in issues:
                gaps[issue.id] = [
                    "No jurisdiction with indexed material was identified, so nothing was searched."
                    if not jurisdiction_ids
                    else "None of the identified jurisdictions has indexed material, so nothing was searched."
                ]
        elif issues:
            self._search_all(req, understanding, issues, searchable, effective_as_of, toolbox, notes, seen, gaps)

        draft = self._draft(understanding, issues, effective_as_of, registry, notes, seen)

        self._report("verifying", "Re-reading the cited authorities from the database and checking quotations")
        with self._factory() as session:
            try:
                verified = verify_guidance(
                    session, draft, [IssueRef(i.id, i.title) for i in issues], registry, gaps=gaps
                )
            finally:
                session.rollback()
        if not issues:
            verified.guidance.summary = (
                "No researchable legal issue could be identified from the question. Please restate it with the "
                "facts and the legal question you need researched."
            )
        n_ok = sum(1 for i in verified.guidance.issues if i.reasoning)
        self._report(
            "verifying",
            f"Verified {len(verified.authorities)} authorities; {verified.removed_claims} unsupported statement(s) "
            f"removed; {n_ok} of {len(issues)} issue(s) supported",
        )

        limitations = self._limitations(toolbox, verified.removed_claims, effective_as_of is None)
        result = ResearchResult(
            understanding=understanding,
            guidance=verified.guidance,
            authorities=verified.authorities,
            coverage_notes=self._coverage_notes(understanding, coverage_rows, toolbox),
            removed_claims=verified.removed_claims,
            limitations=limitations,
            disclaimer=DISCLAIMER,
            usage=UsageOut(
                searches=toolbox.searches_used,
                model=self.llm.model,
                input_tokens=self.usage.input_tokens,
                output_tokens=self.usage.output_tokens,
                cached_input_tokens=self.usage.cached_input_tokens,
            ),
            verification=verified.verification,
        )
        self._check_invariants(result)
        return result

    # ------------------------------------------------------------------ (a) understanding

    def _understand(
        self, req: ResearchInput
    ) -> tuple[UnderstandingResult, list[_Issue], date | None, list[str], list[dict[str, Any]]]:
        self._report("understanding", "Reading the question and the case file")
        profiles = self.catalog.jurisdictions.profiles
        # Fictional test jurisdictions are offered to the model only when the request itself names one.
        offer_fictional = bool(req.jurisdiction and profiles[req.jurisdiction].fictional)
        offered = {p.id: p for p in profiles.values() if offer_fictional or not p.fictional}
        token = secrets.token_hex(8)
        parts = [
            f"Today's date: {self.today.isoformat()}",
            f"Jurisdiction named by the user: {req.jurisdiction or 'none'}",
            f"Date named by the user: {req.as_of.isoformat() if req.as_of else 'none'}",
            "Jurisdiction profile ids you may use: " + "; ".join(f"{p.id} = {p.name}" for p in offered.values()),
            "",
            wrap_untrusted("question", req.question, token),
        ]
        if req.case_text:
            parts += ["", wrap_untrusted("untrusted_case_file", req.case_text, token)]
        parts += ["", "Task: understand the matter as described in the system prompt and return the JSON object."]
        out: UnderstandingOut = self._ask_json(
            "understanding",
            prompts.UNDERSTANDING_SYSTEM,
            "\n".join(parts),
            UnderstandingOut,
            understanding_schema(sorted(offered)),
            cap=UNDERSTANDING_TOKENS,
        )

        reasons: dict[str, str] = {}
        for j in out.jurisdictions:
            if j.id in offered and j.id not in reasons:
                reasons[j.id] = clean_text(j.reason).strip() or "Inferred from the facts."
        ordered: list[str] = []
        if req.jurisdiction:
            ordered.append(req.jurisdiction)
            reasons[req.jurisdiction] = "Specified in the request."
        ordered += [j for j in reasons if j not in ordered]
        ordered = ordered[:MAX_JURISDICTIONS]

        as_of = req.as_of
        if as_of is None and out.as_of:
            try:
                parsed = date.fromisoformat(out.as_of.strip())
                as_of = parsed if parsed <= self.today else None
            except ValueError:
                as_of = None
        with self._factory() as session:
            try:
                rows = coverage_for(session, self.catalog, ordered) if ordered else []
            finally:
                session.rollback()
        covered = {
            r["jurisdiction"]: r["corpus_status"] != "none" and (r["instruments"] + r["judgments"]) > 0 for r in rows
        }
        max_issues = MAX_ISSUES[req.depth]
        issues = [
            _Issue(
                f"I{n}",
                clean_text(i.title).strip()[:300],
                clean_text(i.area_of_law).strip()[:200],
                clean_text(i.question).strip()[:1000],
            )
            for n, i in enumerate((i for i in out.issues if i.title.strip()), start=1)
            if n <= max_issues
        ]
        understanding = UnderstandingResult(
            summary=clean_text(out.summary).strip(),
            facts=[clean_text(f).strip() for f in out.facts if f.strip()][:40],
            as_of=as_of.isoformat() if as_of else None,
            jurisdictions=[JurisdictionOut(id=j, reason=reasons[j], covered=covered.get(j, False)) for j in ordered],
            issues=[IssueOut(id=i.id, title=i.title, area_of_law=i.area_of_law, question=i.question) for i in issues],
        )
        self._report(
            "understanding",
            f"Identified {len(issues)} legal issue(s) in {len(ordered)} jurisdiction(s)",
        )
        return understanding, issues, as_of, ordered, rows

    # ------------------------------------------------------------------ (b) searching

    def _search_all(
        self,
        req: ResearchInput,
        understanding: UnderstandingResult,
        issues: list[_Issue],
        searchable: list[str],
        as_of: date | None,
        toolbox: ToolBox,
        notes: dict[str, SearchNotes | None],
        seen: dict[str, list[str]],
        gaps: dict[str, list[str]],
    ) -> None:
        tools = tool_specs(self.catalog)
        applied = (as_of or self.today).isoformat()
        names = {j.id: self.catalog.jurisdictions.get(j.id).name for j in understanding.jurisdictions}
        for n, issue in enumerate(issues, start=1):
            left = toolbox.max_searches - toolbox.searches_used
            if left <= 0 or self._tokens_left("searching") < MIN_CALL_TOKENS:
                self._budget_hit = True
                gaps[issue.id] = ["The search or output budget was used up before this issue could be researched."]
                notes[issue.id] = None
                continue
            allowance = min(left, max(2, math.ceil(left / (len(issues) - n + 1))))
            toolbox.begin_issue(allowance)
            self._report("searching", f"Researching issue {n} of {len(issues)}: {issue.title}")
            user = "\n".join(
                [
                    "Jurisdictions to research (use these ids): " + "; ".join(f"{j} ({names[j]})" for j in searchable),
                    f"Date at which the law applies: {applied}",
                    f"Matter summary: {understanding.summary}",
                    "Facts:\n" + "\n".join(f"- {f}" for f in understanding.facts),
                    "",
                    f"Issue {issue.id}: {issue.title}",
                    f"Area of law: {issue.area_of_law}",
                    f"Legal question: {issue.question}",
                    f"Search budget for this issue: {allowance} search_law call(s).",
                    self._known_authorities(toolbox.registry),
                    "",
                    "Task: research this issue with the tools, then reply with the JSON object described in the "
                    "system prompt.",
                ]
            )
            parsed, seen[issue.id] = self._search_issue(issue, user, tools, toolbox, allowance, n, len(issues))
            notes[issue.id] = parsed
            if parsed is not None:
                gaps[issue.id] = [clean_text(g).strip() for g in parsed.gaps if g.strip()][:5]
            else:
                gaps[issue.id] = ["The research on this issue ended without usable notes; coverage may be incomplete."]
        self._report(
            "searching",
            f"Searches complete: {toolbox.searches_used} run, {len(toolbox.registry)} authorities retrieved",
        )

    @staticmethod
    def _known_authorities(registry: AuthorityRegistry) -> str:
        if not len(registry):
            return "Authorities retrieved so far for other issues: none."
        lines = [f"- {r.id}: {r.citation}" for r in registry.all()[:25]]
        return (
            "Authorities already retrieved for other issues (you may cite these ids without searching):\n"
            + "\n".join(lines)
        )

    def _search_issue(
        self,
        issue: _Issue,
        user: str,
        tools: list[dict[str, Any]],
        toolbox: ToolBox,
        allowance: int,
        n: int,
        total: int,
    ) -> tuple[SearchNotes | None, list[str]]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        seen: list[str] = []
        final_text = ""
        for _turn in range(allowance * 2 + 4):
            try:
                response = self._call(
                    "searching", prompts.SEARCH_SYSTEM, messages, cap=SEARCH_TURN_TOKENS, tools=tools, cache=True
                )
            except PipelineError as exc:
                if exc.code == "output_budget_exhausted":
                    break
                raise
            messages.append({"role": "assistant", "content": response.content})
            if not response.tool_calls or response.stop_reason != "tool_use":
                final_text = response.text
                break
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                outcome = toolbox.execute(call.name, call.input)
                for aid in outcome.authority_ids:
                    if aid not in seen:
                        seen.append(aid)
                block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call.id, "content": outcome.content}
                if outcome.is_error:
                    block["is_error"] = True
                results.append(block)
            messages.append({"role": "user", "content": results})
            self._report(
                "searching",
                f"Issue {n} of {total}: {toolbox.searches_used} of {toolbox.max_searches} searches used, "
                f"{len(toolbox.registry)} authorities retrieved",
            )
        try:
            return parse_model_json(final_text, SearchNotes), seen
        except ModelOutputError:
            return None, seen

    # ------------------------------------------------------------------ (c) drafting

    def _draft(
        self,
        understanding: UnderstandingResult,
        issues: list[_Issue],
        as_of: date | None,
        registry: AuthorityRegistry,
        notes: dict[str, SearchNotes | None],
        seen: dict[str, list[str]],
    ) -> DraftOut | None:
        selected: list[str] = []
        for issue in issues:
            parsed = notes.get(issue.id)
            ids = (
                [i for i in parsed.relevant_authority_ids if i in registry]
                if parsed is not None
                else seen.get(issue.id, [])[:8]
            )
            for aid in dict.fromkeys(ids[:MAX_AUTHORITIES_PER_ISSUE]):
                if aid not in selected:
                    selected.append(aid)
        selected = selected[:MAX_AUTHORITIES_TOTAL]
        if not issues or not selected:
            self._report("drafting", "No relevant authority was retrieved; skipping the drafting step")
            return None
        self._report("drafting", f"Drafting guidance from {len(selected)} retrieved authorities")
        records = [r for aid in selected if (r := registry.get(aid)) is not None]
        issue_lines = []
        for issue in issues:
            parsed = notes.get(issue.id)
            note = f"\n  Research notes (unverified): {clean_text(parsed.notes).strip()[:1500]}" if parsed else ""
            issue_lines.append(f"- {issue.id}: {issue.title} ({issue.area_of_law}). Question: {issue.question}{note}")
        user = "\n".join(
            [
                f"Date at which the law applies: {(as_of or self.today).isoformat()}",
                f"Matter summary: {understanding.summary}",
                "Facts:\n" + "\n".join(f"- {f}" for f in understanding.facts),
                "",
                "Issues (one entry each in your answer):",
                *issue_lines,
                "",
                "Authorities (the only citable sources; JSON):",
                json.dumps([self._authority_for_prompt(r) for r in records], ensure_ascii=False),
                "",
                "Task: draft the guidance as described in the system prompt and return the JSON object.",
            ]
        )
        draft: DraftOut = self._ask_json(
            "drafting", prompts.DRAFT_SYSTEM, user, DraftOut, draft_schema(), cap=DRAFT_TOKENS
        )
        return draft

    @staticmethod
    def _authority_for_prompt(r: AuthorityRecord) -> dict[str, Any]:
        clipped = len(r.text) > DRAFT_TEXT_CHARS
        return {
            "id": r.id,
            "citation": r.citation,
            "jurisdiction": r.jurisdiction,
            "kind": r.kind,
            "valid_from": r.valid_from,
            "valid_to": r.valid_to,
            "authority": r.authority_label,
            "limitations": [
                {"code": x["code"], "severity": x["severity"], "message": x["message"]}
                for x in r.limitations
                if x.get("severity") != "info"
            ],
            "text": r.text[:DRAFT_TEXT_CHARS],
            "text_clipped": clipped,
        }

    # ------------------------------------------------------------------ (d) assembling

    def _coverage_notes(
        self, understanding: UnderstandingResult, rows: list[dict[str, Any]], toolbox: ToolBox
    ) -> list[str]:
        notes: list[str] = []
        if not understanding.jurisdictions:
            notes.append(
                "No governing jurisdiction could be identified from the question; name one (for example DE or GB-ENG)."
            )
        by_id = {r["jurisdiction"]: r for r in rows}
        reasons = {j.id: j.reason for j in understanding.jurisdictions}
        for j in understanding.jurisdictions:
            row = by_id.get(j.id)
            if row is None:
                continue
            label = f"{row['name']} ({j.id})"
            status = row["corpus_status"]
            if reasons[j.id] != "Specified in the request.":
                notes.append(f"{label} was inferred by the model, not specified by the user: {reasons[j.id]}")
            if status == "none" or (row["instruments"] + row["judgments"]) == 0:
                notes.append(f"{label}: no legal materials are indexed in this database, so no guidance can be given.")
            elif status in ("pilot", "fictional_sample"):
                what = "fictional test data" if status == "fictional_sample" else "a pilot corpus"
                notes.append(
                    f"{label}: only {what} is indexed ({row['instruments']} instruments, {row['judgments']} "
                    "judgments); relevant law may be missing."
                )
            elif status == "partial":
                notes.append(f"{label}: the corpus is partial; relevant law may be missing.")
            notes.extend(f"{label}: known gap - {gap}" for gap in row["known_gaps"])
        for jid, row in toolbox.coverage_seen.items():
            if jid not in by_id and row["corpus_status"] in ("none", "pilot", "partial"):
                notes.append(f"{row['name']} ({jid}) was consulted: corpus status '{row['corpus_status']}'.")
        return list(dict.fromkeys(notes))

    def _limitations(self, toolbox: ToolBox, removed: int, date_defaulted: bool) -> list[LimitationOut]:
        out = [
            LimitationOut(code="NOT_LEGAL_ADVICE", severity="info", message=DISCLAIMER),
            LimitationOut(
                code="AI_DRAFTED",
                severity="warning",
                message="The guidance text was drafted by a language model and then checked mechanically; the "
                "check proves that cited texts and quotations exist, not that the legal reasoning is correct.",
            ),
        ]
        if date_defaulted:
            out.append(
                LimitationOut(
                    code="DATE_DEFAULTED",
                    severity="info",
                    message="No date was given or could be determined; the law as indexed for "
                    f"{self.today.isoformat()} was applied.",
                )
            )
        if removed:
            out.append(
                LimitationOut(
                    code="UNVERIFIED_CLAIMS_REMOVED",
                    severity="warning",
                    message=f"{removed} drafted statement(s) were removed because the verifier could not support "
                    "them from the indexed texts.",
                )
            )
        if toolbox.searches_used >= toolbox.max_searches or self._budget_hit:
            out.append(
                LimitationOut(
                    code="RESEARCH_BUDGET_REACHED",
                    severity="warning",
                    message="The search or output budget for this job was reached; further relevant law may exist "
                    "that was not retrieved. Use depth 'deep' or narrow the question.",
                )
            )
        for lim in toolbox.limitations.values():
            out.append(LimitationOut(**lim))
        return list({(x.code, x.message): x for x in out}.values())

    @staticmethod
    def _check_invariants(result: ResearchResult) -> None:
        """Contract invariants that must hold whatever the model did (a failure here is a bug, not bad output)."""
        known = {a.id for a in result.authorities}
        for issue in result.guidance.issues:
            for item in issue.reasoning:
                if not item.authority_ids or not set(item.authority_ids) <= known:
                    raise PipelineError("internal_error", "Guidance cites an authority that is not in the result.")
        if any(not a.verified for a in result.authorities):
            raise PipelineError("internal_error", "An unverified authority reached the result.")
