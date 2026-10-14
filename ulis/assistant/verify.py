"""Deterministic verification of drafted guidance. No model is involved in any decision here.

* Every cited authority is re-read from the database (by version / paragraph id and uri) and its text
  and SHA-256 are compared with what the tools registered; the text shown to users is the database
  text, never anything the model produced.
* A reasoning item survives only if it cites at least one authority, every cited id is registered and
  verified, and every quotation inside it (text between quotation marks) occurs in a cited authority
  (``ulis.verification.quotes.check_quote``).  Everything else is dropped and counted.
* An issue left without supporting reasoning is concluded as "insufficient authority indexed" with low
  confidence.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ulis.assistant.registry import AuthorityRecord, AuthorityRegistry
from ulis.assistant.schemas import (
    INSUFFICIENT,
    AuthorityOut,
    Confidence,
    DIssue,
    DraftOut,
    DReasoning,
    GuidanceIssueOut,
    GuidanceResult,
    LimitationOut,
    ReasoningOut,
    RemovedItem,
    SourceOut,
    VerificationOut,
)
from ulis.db import models as m
from ulis.verification.quotes import check_quote

MIN_QUOTE_CHARS = 4
_ID_REF = re.compile(r"\bA\d{1,4}\b")
_ELLIPSIS_OR_ALTERATION = re.compile(r"\[[^\]]*\]|…|\.\s?\.\s?\.")
_QUOTE_PATTERNS = (
    re.compile(r'"([^"\n]+)"'),
    re.compile("“([^“”\n]+)”"),
    re.compile("„([^“”\n]+)[“”]"),
    re.compile("«\\s?([^«»\n]+?)\\s?»"),
    re.compile("「([^「」\n]+)」"),
    re.compile("『([^『』\n]+)』"),
    # Single curly quotes only when long enough not to be a scare quote around one word.
    re.compile("‘([^‘’\n]{12,})’"),
)
_RANK = {"low": 0, "medium": 1, "high": 2}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- quotes


def extract_quotes(text: str) -> list[str]:
    """Fragments of ``text`` that sit between quotation marks (ellipses and [alterations] split a quote)."""
    out: list[str] = []
    # Line breaks inside a quotation must not hide it from the checks.
    text = re.sub(r"\s+", " ", text)
    for pattern in _QUOTE_PATTERNS:
        for match in pattern.finditer(text):
            for fragment in _ELLIPSIS_OR_ALTERATION.split(match.group(1)):
                fragment = fragment.strip(" \t\n,;:")
                if len(fragment) >= MIN_QUOTE_CHARS:
                    out.append(fragment)
    return out


# --------------------------------------------------------------------------- re-reading


@dataclass
class Reread:
    record: AuthorityRecord
    verified: bool
    text: str = ""
    text_sha256: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    problem: str | None = None


def reread(session: Session, record: AuthorityRecord) -> Reread:
    """Load the record again from the database and compare it with what was retrieved."""
    try:
        target = uuid.UUID(record.target_id)
    except ValueError:
        return Reread(record, False, problem="malformed record id")
    valid_from: str | None = None
    valid_to: str | None = None
    if record.kind == "provision":
        pv = session.get(m.ProvisionVersion, target)
        if pv is None:
            return Reread(record, False, problem="provision version no longer exists")
        prov = session.get(m.Provision, pv.provision_id)
        uri = prov.uri if prov is not None else None
        text, stored_sha = pv.text, pv.text_sha256
        valid_from = pv.valid_from.isoformat() if pv.valid_from else None
        valid_to = pv.valid_to.isoformat() if pv.valid_to else None
    else:
        para = session.get(m.JudgmentParagraph, target)
        if para is None:
            return Reread(record, False, problem="judgment paragraph no longer exists")
        version = session.get(m.JudgmentVersion, para.judgment_version_id)
        judgment = session.get(m.Judgment, version.judgment_id) if version is not None else None
        uri = f"{judgment.uri}/~{para.eid}" if judgment is not None else None
        text, stored_sha = para.text, para.text_sha256
    problem: str | None = None
    if uri != record.uri:
        problem = "uri differs from the retrieved record"
    elif _sha(text) != stored_sha:
        problem = "stored text does not match its recorded SHA-256"
    elif stored_sha != record.text_sha256 or text != record.text:
        problem = "stored text changed after retrieval"
    return Reread(record, problem is None, text, stored_sha, valid_from, valid_to, problem)


# --------------------------------------------------------------------------- verification


@dataclass
class IssueRef:
    id: str
    title: str


@dataclass
class VerifiedGuidance:
    guidance: GuidanceResult
    authorities: list[AuthorityOut]
    removed_claims: int
    verification: VerificationOut
    cited_ids: list[str] = field(default_factory=list)


class _Verifier:
    def __init__(self, session: Session, registry: AuthorityRegistry) -> None:
        self.session = session
        self.registry = registry
        self.reads: dict[str, Reread] = {}
        self.removed: list[RemovedItem] = []
        self.quotes_checked = 0
        self.quotes_failed = 0

    def authority(self, authority_id: str) -> Reread | None:
        record = self.registry.get(authority_id)
        if record is None:
            return None
        if authority_id not in self.reads:
            self.reads[authority_id] = reread(self.session, record)
        return self.reads[authority_id]

    def remove(self, issue_id: str | None, where: str, reason: str) -> None:
        self.removed.append(RemovedItem(issue_id=issue_id, where=where, reason=reason))

    def text_problem(self, text: str, cited: Sequence[Reread], *, mentionable: set[str]) -> str | None:
        """Why ``text`` cannot stand (a quotation not found, an id unknown or not cited), else None."""
        for ref in _ID_REF.findall(text):
            if ref not in self.registry:
                return f"mentions unknown authority {ref}"
            if ref not in mentionable:
                return f"mentions {ref}, which it does not cite"
        for quote in extract_quotes(text):
            self.quotes_checked += 1
            if not any(check_quote(quote, c.text).result != "not_found" for c in cited):
                self.quotes_failed += 1
                return "quotation not found in the cited authority"
        return None

    def reasoning_item(self, issue_id: str, item: DReasoning) -> tuple[ReasoningOut, list[Reread]] | None:
        ids = list(dict.fromkeys(item.authority_ids))
        if not ids:
            self.remove(issue_id, "reasoning", "no authority cited")
            return None
        unknown = [i for i in ids if i not in self.registry]
        if unknown:
            self.remove(issue_id, "reasoning", f"cites unknown authority id {', '.join(unknown)}")
            return None
        cited = [r for i in ids if (r := self.authority(i)) is not None]
        bad = [c.record.id for c in cited if not c.verified]
        if bad:
            self.remove(issue_id, "reasoning", f"cited authority could not be verified ({', '.join(bad)})")
            return None
        problem = self.text_problem(item.text, cited, mentionable=set(ids))
        if problem:
            self.remove(issue_id, "reasoning", problem)
            return None
        return ReasoningOut(text=item.text.strip(), authority_ids=ids), cited


def _cap(confidence: Confidence, limit: Confidence) -> Confidence:
    return confidence if _RANK[confidence] <= _RANK[limit] else limit


def _critical_messages(records: Iterable[AuthorityRecord]) -> list[str]:
    return [
        f"{r.id} ({r.citation}): {lim['message']}"
        for r in records
        for lim in r.limitations
        if lim.get("severity") == "critical"
    ]


def verify_guidance(
    session: Session,
    draft: DraftOut | None,
    issues: Sequence[IssueRef],
    registry: AuthorityRegistry,
    *,
    gaps: dict[str, list[str]] | None = None,
) -> VerifiedGuidance:
    """Verify ``draft`` against the database. ``draft=None`` yields the all-insufficient guidance."""
    v = _Verifier(session, registry)
    gaps = gaps or {}
    known = {i.id for i in issues}
    drafted: dict[str, DIssue] = {}
    if draft is not None:
        for d in draft.issues:
            if d.issue_id not in known:
                for _ in range(max(1, len(d.reasoning))):
                    v.remove(None, "issue", "drafted for an issue that was not identified")
            elif d.issue_id not in drafted:
                drafted[d.issue_id] = d
    out_issues: list[GuidanceIssueOut] = []
    used: dict[str, Reread] = {}

    for ref in issues:
        d_issue = drafted.get(ref.id)
        kept: list[ReasoningOut] = []
        cited_reads: dict[str, Reread] = {}
        dropped_here = 0
        if d_issue is not None:
            for item in d_issue.reasoning:
                checked = v.reasoning_item(ref.id, item)
                if checked is None:
                    dropped_here += 1
                    continue
                kept.append(checked[0])
                for c in checked[1]:
                    cited_reads[c.record.id] = c
        if not kept or d_issue is None:
            notes = [
                "No indexed authority was found that supports a conclusion on this issue. This does not show that "
                "the law is silent: see the coverage notes and verify against official sources."
            ]
            notes += [g for g in gaps.get(ref.id, []) if v.text_problem(g, [], mentionable=set()) is None][:3]
            out_issues.append(
                GuidanceIssueOut(
                    issue_id=ref.id, conclusion=INSUFFICIENT, confidence="low", reasoning=[], uncertainties=notes
                )
            )
            continue

        cited_list = list(cited_reads.values())
        mentionable = set(cited_reads)
        conclusion = d_issue.conclusion.strip()
        confidence: Confidence = d_issue.confidence
        problem = v.text_problem(conclusion, cited_list, mentionable=mentionable) if conclusion else "empty conclusion"
        if problem:
            v.remove(ref.id, "conclusion", problem)
            conclusion = (
                "The drafted conclusion could not be verified against the cited text and was removed; rely on "
                "the verified reasoning below."
            )
            confidence = "low"
        uncertainties: list[str] = []
        for u in d_issue.uncertainties:
            p = v.text_problem(u, cited_list, mentionable=mentionable)
            if p:
                v.remove(ref.id, "uncertainty", p)
            else:
                uncertainties.append(u.strip())
        if dropped_here:
            confidence = _cap(confidence, "medium")
            uncertainties.append(
                f"{dropped_here} statement(s) drafted for this issue were removed because they could not be "
                "verified against the indexed text; the analysis may be incomplete."
            )
        critical = _critical_messages(c.record for c in cited_list)
        if critical:
            confidence = _cap(confidence, "medium")
            uncertainties.extend(f"Limitation of {msg}" for msg in critical)
        used.update(cited_reads)
        out_issues.append(
            GuidanceIssueOut(
                issue_id=ref.id,
                conclusion=conclusion,
                confidence=confidence,
                reasoning=kept,
                uncertainties=list(dict.fromkeys(uncertainties)),
            )
        )

    summary, steps = _summary_and_steps(v, draft, out_issues, used, len(issues))
    authorities = [
        AuthorityOut(
            id=r.record.id,
            citation=r.record.citation,
            uri=r.record.uri,
            jurisdiction=r.record.jurisdiction,
            kind=r.record.kind,
            text=r.text,
            text_sha256=r.text_sha256,
            valid_from=r.valid_from,
            valid_to=r.valid_to,
            authority_label=r.record.authority_label,
            limitations=[LimitationOut(**lim) for lim in r.record.limitations],
            source=SourceOut(
                name=str(r.record.source.get("name") or ""),
                url=r.record.source.get("url"),
                attribution=r.record.source.get("attribution"),
            ),
            verified=r.verified,
        )
        for r in sorted(used.values(), key=lambda x: int(x.record.id[1:]))
    ]
    verification = VerificationOut(
        authorities_checked=len(v.reads),
        authorities_verified=sum(1 for r in v.reads.values() if r.verified),
        quotes_checked=v.quotes_checked,
        quotes_failed=v.quotes_failed,
        removed=v.removed,
    )
    return VerifiedGuidance(
        guidance=GuidanceResult(summary=summary, issues=out_issues, next_steps=steps),
        authorities=authorities,
        removed_claims=len(v.removed),
        verification=verification,
        cited_ids=[a.id for a in authorities],
    )


def _summary_and_steps(
    v: _Verifier, draft: DraftOut | None, issues: list[GuidanceIssueOut], used: dict[str, Reread], n_issues: int
) -> tuple[str, list[str]]:
    """Keep the drafted summary / next steps only if they pass the same checks; otherwise state facts only."""
    supported = sum(1 for i in issues if i.reasoning)
    cited = list(used.values())
    mentionable = set(used)
    summary = ""
    if draft is not None and draft.summary.strip() and not v.removed and supported:
        if v.text_problem(draft.summary, cited, mentionable=mentionable) is None:
            summary = draft.summary.strip()
        else:
            v.remove(None, "summary", "summary contained text that could not be verified")
    if not summary:
        if supported == 0:
            summary = (
                "No indexed authority supports guidance on any of the identified issues. Missing coverage is "
                "stated in the coverage notes; the absence of results is not evidence about the law."
            )
        else:
            summary = f"Verified guidance is available for {supported} of {n_issues} identified issue(s)." + (
                " Statements that could not be verified against the indexed text were removed." if v.removed else ""
            )
    steps: list[str] = []
    if draft is not None:
        for step in draft.next_steps:
            problem = v.text_problem(step, cited, mentionable=mentionable)
            if problem:
                v.remove(None, "next_step", problem)
            else:
                steps.append(step.strip())
    steps.append("Verify every cited authority against the official source before relying on it.")
    return summary, list(dict.fromkeys(steps))
