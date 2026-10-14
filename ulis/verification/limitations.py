"""Legal-status limitations: what a user must know before relying on a passage.

Every result carries explicit limitations computed from recorded facts (dates, currency,
coverage, profile verification, treatment) - never inferred by a model.  Absence of a
recorded fact is itself reported ("no judicial treatment *indexed*"), not read as a negative.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

from ulis.catalog import Catalog
from ulis.retrieval.authority import Authority
from ulis.retrieval.details import JudgmentData, ProvisionData

Severity = Literal["critical", "warning", "info"]
_ORDER = {"critical": 0, "warning": 1, "info": 2}
AMENDMENT_VERBS = {
    "amends": "Amended by",
    "repeals": "Repealed by",
    "inserts": "New provision inserted at this point by",
}


@dataclass(frozen=True)
class Limitation:
    code: str
    severity: Severity
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _sorted(items: list[Limitation]) -> list[Limitation]:
    return sorted(items, key=lambda x: _ORDER[x.severity])


def jurisdiction_limitations(catalog: Catalog, jurisdiction_id: str) -> list[Limitation]:
    profile = catalog.jurisdictions.get(jurisdiction_id)
    out: list[Limitation] = []
    if profile.fictional:
        out.append(
            Limitation(
                "FICTIONAL_DATA",
                "critical",
                f"{profile.name} is a fictional test jurisdiction. This text is not law anywhere.",
            )
        )
    elif profile.verification.status != "reviewed":
        out.append(
            Limitation(
                "PROFILE_UNVERIFIED",
                "warning",
                f"The {jurisdiction_id} jurisdiction profile (court hierarchy, precedent and treaty "
                "rules) has not been reviewed by a qualified lawyer.",
            )
        )
    return out


def provision_limitations(catalog: Catalog, data: ProvisionData, authority: Authority, as_of: date) -> list[Limitation]:
    v = data.version
    j = data.instrument.jurisdiction_id
    out = jurisdiction_limitations(catalog, j)

    iv = data.instrument_version
    if data.instrument.is_proposed:
        if data.instrument.instrument_type == "resolution":
            out.append(
                Limitation(
                    "RESOLUTION_NOT_LAW",
                    "critical",
                    f"This is the text of {iv.title}. A simple or concurrent resolution is not law.",
                )
            )
        else:
            out.append(
                Limitation(
                    "PROPOSED_NOT_LAW",
                    "critical",
                    f"This is the text of {iv.title}. It is not law and may change or lapse.",
                )
            )
        if v.valid_to is not None:
            out.append(
                Limitation(
                    "LATER_STAGE_EXISTS", "info", f"A later version of this measure is recorded from {v.valid_to}."
                )
            )
        if str((data.instrument.ext or {}).get("version_date_basis", "")).endswith("lower_bound"):
            out.append(
                Limitation(
                    "VERSION_DATE_APPROXIMATE",
                    "info",
                    f"The version date {v.valid_from} is the latest dated action found in the "
                    "document; the stage itself may be later.",
                )
            )
    if v.status == "repealed":
        out.append(
            Limitation(
                "REPEALED",
                "critical",
                f"Repealed with effect from {v.valid_from}." + (f" Note: {v.note}" if v.note else ""),
            )
        )
    if v.status == "prospective":
        out.append(
            Limitation(
                "NOT_IN_FORCE",
                "critical",
                "Enacted but not in force: no commencement has been recorded." + (f" Note: {v.note}" if v.note else ""),
            )
        )
    elif v.status == "in_force":
        period = f"from {v.valid_from}" if v.valid_from else "from an unrecorded date"
        if v.valid_to:
            basis = " (end date derived from the start of the next version)" if v.valid_to_basis == "derived" else ""
            period += f" until {v.valid_to}{basis}"
        else:
            period += "; no end date recorded"
        out.append(Limitation("VERSION_PERIOD", "info", f"This version applies {period}."))
        if v.valid_from_basis == "observed":
            out.append(
                Limitation(
                    "START_DATE_OBSERVED",
                    "warning",
                    f"Start date approximate: the source dates this wording from an earlier date that its own "
                    f"earlier versions contradict, so it is shown from {v.valid_from}, the first point-in-time "
                    "version in which it appears. The change took effect on or before that date.",
                )
            )
        if v.valid_from is None and iv.validity_basis == "attested_at_edition":
            out.append(
                Limitation(
                    "ATTESTED_ONLY_AT",
                    "warning",
                    f"The source attests this text as in force on {data.currency_date} "
                    f"({iv.consolidation_kind.replace('_', ' ')}). When it took effect, and whether it applied on "
                    f"{as_of}, is not recorded.",
                )
            )
        elif v.valid_from is None:
            out.append(
                Limitation(
                    "EFFECTIVE_DATE_UNKNOWN", "warning", "The source does not state when this version took effect."
                )
            )
        if iv.validity_basis == "enactment_date_presumed" and v.valid_from is not None:
            out.append(
                Limitation(
                    "EFFECTIVE_DATE_PRESUMED",
                    "warning",
                    f"Dated from enactment ({v.valid_from}) under the default rule that an Act takes effect "
                    "on enactment unless it provides otherwise. Provision-specific effective dates were not "
                    "extracted, and later amendments to this Act are not tracked.",
                )
            )
        if v.note:
            out.append(Limitation("VERSION_NOTE", "info", v.note))
        applies = v.valid_from is not None and v.valid_from <= as_of and (v.valid_to is None or as_of < v.valid_to)
        if v.valid_from is not None and not applies:
            out.append(
                Limitation(
                    "NOT_APPLICABLE_ON_DATE",
                    "warning",
                    f"This version did not apply on {as_of}; check the version timeline.",
                )
            )
        later = [t for t in data.timeline if t.valid_from and v.valid_from and t.valid_from > v.valid_from]
        if later:
            nxt = later[0]
            what = "repealed" if nxt.status == "repealed" else "replaced by a later version"
            out.append(
                Limitation(
                    "LATER_VERSION_EXISTS", "warning", f"This provision was {what} with effect from {nxt.valid_from}."
                )
            )
        if v.valid_to is None:
            if data.currency_date is None:
                out.append(
                    Limitation(
                        "CURRENCY_UNKNOWN",
                        "warning",
                        "The source does not state the date to which this text is up to date "
                        f"({data.instrument_version.consolidation_kind.replace('_', ' ')} text).",
                    )
                )
            else:
                out.append(
                    Limitation(
                        "CURRENCY",
                        "info",
                        f"The source states this text is up to date to {data.currency_date}. "
                        "Later changes are not reflected.",
                    )
                )
                if as_of > data.currency_date:
                    out.append(
                        Limitation(
                            "AS_OF_AFTER_CURRENCY",
                            "warning",
                            f"The requested date {as_of} is after the verified currency date "
                            f"{data.currency_date}; the provision may have changed since.",
                        )
                    )
    specific = [c for c in data.outstanding_changes if c.get("affects_eid")]
    general = [c for c in data.outstanding_changes if not c.get("affects_eid")]
    for change in specific[:5]:
        out.append(
            Limitation(
                "OUTSTANDING_CHANGES",
                "warning",
                f"Source reports a change not yet incorporated: {change.get('by_title') or change.get('by_uri')}"
                f" - {change.get('description')}",
            )
        )
    if len(specific) > 5:
        out.append(
            Limitation(
                "OUTSTANDING_CHANGES",
                "warning",
                f"...and {len(specific) - 5} more outstanding changes affecting this provision.",
            )
        )
    if general:
        examples = "; ".join(str(c.get("affected_label") or c.get("by_title")) for c in general[:3])
        out.append(
            Limitation(
                "OUTSTANDING_CHANGES_INSTRUMENT",
                "warning",
                f"The source lists {len(general)} change(s) to this instrument not yet incorporated and not "
                f"linked to a specific provision (e.g. {examples}); check whether they affect this text.",
            )
        )

    pending = [t for t in data.legislative_treatment if t.is_proposed]
    for t in pending:
        out.append(
            Limitation(
                "PENDING_PROPOSAL", "info", f"A bill proposes to change this provision ({t.citing_citation}); not law."
            )
        )
    for t in data.legislative_treatment:
        if not t.is_proposed and t.relation in AMENDMENT_VERBS:
            out.append(
                Limitation("AMENDING_LEGISLATION", "info", f"{AMENDMENT_VERBS[t.relation]} {t.citing_citation}.")
            )

    corpus = catalog.jurisdictions.get(j).coverage.corpus_status
    if data.judicial_treatment:
        cases = "; ".join(f"{t.citing_citation} ({t.relation}, {t.date})" for t in data.judicial_treatment)
        out.append(Limitation("JUDICIAL_TREATMENT", "info", f"Indexed judicial treatment: {cases}."))
        other_version = [
            t
            for t in data.judicial_treatment
            if t.date and not (v.valid_from and v.valid_from <= t.date and (v.valid_to is None or t.date < v.valid_to))
        ]
        if other_version:
            out.append(
                Limitation(
                    "TREATMENT_OF_OTHER_VERSION",
                    "warning",
                    "Some indexed decisions were given when a different version of this provision "
                    "applied: " + "; ".join(t.citing_citation for t in other_version) + ".",
                )
            )
    out.append(
        Limitation(
            "CITATOR_COVERAGE",
            "warning",
            f"Citator coverage for {j} is '{corpus}'. Absence of indexed treatment is not evidence "
            "that no court has considered this provision.",
        )
    )

    if data.parallel_languages and data.instrument_version.authoritative:
        langs = ", ".join(p["language"] for p in data.parallel_languages if p["authoritative"])
        if langs:
            out.append(
                Limitation(
                    "EQUALLY_AUTHENTIC_VERSIONS",
                    "info",
                    f"Equally authentic language versions exist ({langs}); interpretation may require comparing them.",
                )
            )
    if iv.text_origin == "unofficial_compilation":
        out.append(
            Limitation(
                "COMPILATION_NOT_OFFICIAL",
                "warning",
                "Statute compilation: the publisher states it does not represent an official version of any federal "
                "statute; the official text is in the Statutes at Large and the United States Code "
                "(1 U.S.C. 112, 204).",
            )
        )
    elif iv.text_origin == "official_revised":
        out.append(
            Limitation(
                "REVISED_TEXT",
                "info",
                "Revised (consolidated) text maintained by the official publisher's editors; it is not the "
                "authentic enacted text.",
            )
        )
    elif iv.text_origin == "official_conversion":
        out.append(
            Limitation(
                "NOT_AUTHENTICATED",
                "info",
                "XML converted by the official publisher; it is not the authenticated publication of this text.",
            )
        )
    if data.instrument_version.text_origin == "machine_translation":
        out.append(Limitation("MACHINE_TRANSLATION", "critical", "Machine translation: not an authentic text."))
    elif not data.instrument_version.authoritative and iv.text_origin not in (
        "unofficial_compilation",
        "official_conversion",
        "official_revised",  # REVISED_TEXT already says it is not the authentic enacted text
    ):
        out.append(Limitation("NOT_AUTHENTIC_TEXT", "warning", "This language version is not an authentic text."))

    if authority.category in ("treaty_obligation", "treaty_not_in_force_for_state", "foreign"):
        out.append(Limitation("AUTHORITY", "warning", authority.rationale))
    if data.source.source_authority not in ("official_authentic",):
        out.append(
            Limitation(
                "SOURCE_NOT_AUTHENTIC",
                "info",
                f"Source '{data.source.source_name}' is not an authentic official publication "
                f"({data.source.source_authority}); verify against the official text before relying on it.",
            )
        )
    return _sorted(out)


def judgment_limitations(catalog: Catalog, data: JudgmentData, authority: Authority, as_of: date) -> list[Limitation]:
    j = data.judgment.jurisdiction_id
    out = jurisdiction_limitations(catalog, j)
    if data.judgment.decision_date > as_of:
        out.append(
            Limitation(
                "DECIDED_AFTER_DATE",
                "warning",
                f"Decided on {data.judgment.decision_date}, after the requested date {as_of}. Whether it "
                f"states the law as it stood on {as_of} depends on {j} rules on the retrospective "
                "effect of judicial decisions; it may also concern a later version of legislation.",
            )
        )
    negative = [t for t in data.treatment if t.relation in ("overrules", "reverses", "disapproves")]
    for t in negative:
        out.append(
            Limitation(
                "NEGATIVE_TREATMENT", "critical", f"{t.relation.capitalize()} by {t.citing_citation} on {t.date}."
            )
        )
    others = [t for t in data.treatment if t not in negative]
    if others:
        out.append(
            Limitation(
                "SUBSEQUENT_TREATMENT",
                "info",
                "Cited in: " + "; ".join(f"{t.citing_citation} ({t.relation})" for t in others) + ".",
            )
        )
    corpus = catalog.jurisdictions.get(j).coverage.corpus_status
    out.append(
        Limitation(
            "CITATOR_COVERAGE",
            "warning",
            f"Citator coverage for {j} is '{corpus}'. Later treatment outside the indexed corpus "
            "(appeals, overruling, legislative reversal) is not known.",
        )
    )
    if authority.category in ("foreign", "persuasive_precedent", "precedent_vertical", "overruled_precedent"):
        out.append(Limitation("AUTHORITY", "warning", authority.rationale))
    if data.version.text_origin == "machine_translation":
        out.append(Limitation("MACHINE_TRANSLATION", "critical", "Machine translation: not an authentic text."))
    if data.source.source_authority not in ("official_authentic",):
        out.append(
            Limitation(
                "SOURCE_NOT_AUTHENTIC",
                "info",
                f"Source '{data.source.source_name}' is not an authentic official publication "
                f"({data.source.source_authority}).",
            )
        )
    return _sorted(out)
