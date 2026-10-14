"""Authority classification: how much legal weight a passage carries *for a given question*.

Classification depends on the target jurisdiction, the date, the forum court (if any) and
the jurisdiction profiles' explicit rules.  It never treats foreign material as binding,
never treats a bill as law, and never treats civil-law case law as formally binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ulis.jurisdictions.registry import JurisdictionRegistry

CATEGORIES: dict[str, tuple[str, float]] = {
    "binding_constitution": ("Binding: constitutional provision in force", 1.35),
    "binding_legislation": ("Binding: legislation in force", 1.25),
    "binding_regulation": ("Binding: regulation in force", 1.2),
    "binding_precedent": ("Binding precedent for the forum court", 1.2),
    "precedent_vertical": ("Precedent: binds courts below the deciding court", 1.1),
    "regional_law": ("Law of a regional organisation of which the State is a member", 1.0),
    "regional_case_law": ("Court of a regional organisation of which the State is a member", 1.05),
    "treaty_obligation": ("Treaty in force for the State", 1.0),
    "regional_directive": ("Directive: binds the State as to the result; applies through national law", 0.95),
    "applicability_unverified": ("In force at the source date; applicability on the requested date unverified", 0.9),
    "persuasive_precedent": ("Persuasive only", 0.85),
    "historical_legislation": ("Historical version: not the version in force on the date", 0.6),
    "prospective_legislation": ("Enacted but not in force", 0.5),
    "foreign": ("Foreign authority: not binding", 0.5),
    "treaty_not_in_force_for_state": ("Treaty not in force for the State on the date", 0.4),
    "proposed": ("Proposed: not law", 0.4),
    "non_binding_resolution": ("Resolution: not law", 0.35),
    "overruled_precedent": ("Overruled: not good law on the point decided", 0.3),
}


@dataclass(frozen=True)
class Authority:
    category: str
    label: str
    rationale: str
    weight: float


def _make(category: str, rationale: str) -> Authority:
    label, weight = CATEGORIES[category]
    return Authority(category, label, rationale, weight)


@dataclass(frozen=True)
class TreatyStatus:
    party: str
    signed_on: date | None
    consented_on: date | None
    entry_into_force_on: date | None
    withdrawn_on: date | None

    def in_force_on(self, when: date) -> bool:
        return (
            self.entry_into_force_on is not None
            and self.entry_into_force_on <= when
            and (self.withdrawn_on is None or when < self.withdrawn_on)
        )


def classify_provision(
    reg: JurisdictionRegistry,
    *,
    target: str | None,
    passage_jurisdiction: str,
    doc_type: str,
    status: str,
    applies_on_date: bool | None,
    as_of: date,
    treaty_parties: list[TreatyStatus] | None = None,
    implementing_titles: list[str] | None = None,
) -> Authority:
    home = target or passage_jurisdiction
    if status == "proposed":
        if doc_type == "resolution":
            return _make(
                "non_binding_resolution",
                "A simple or concurrent resolution expresses the position of one or both chambers; it is not law.",
            )
        return _make("proposed", "A bill or draft has no legal effect unless and until it is enacted.")
    if doc_type == "treaty":
        state = reg.sovereign(home)
        party = next((p for p in treaty_parties or [] if p.party == state), None)
        if state is None or party is None:
            return _make("foreign", f"{state or home} is not recorded as a party to this treaty.")
        if not party.in_force_on(as_of):
            return _make(
                "treaty_not_in_force_for_state",
                f"Not in force for {state} on {as_of.isoformat()} "
                f"(entry into force for {state}: {party.entry_into_force_on or 'not recorded'}).",
            )
        reception = reg.get(state).legal_system.treaty_reception
        if reception == "dualist":
            impl = ", ".join(implementing_titles or []) or "none indexed"
            note = (
                f"{state} is dualist: the treaty binds the State internationally but has domestic effect "
                f"only through implementing legislation (indexed implementing instruments: {impl})."
            )
        elif reception == "monist":
            note = f"{state} is monist: a ratified treaty forms part of domestic law (per profile)."
        else:
            note = f"Domestic effect in {state} depends on its treaty-reception rules ({reception})."
        return _make("treaty_obligation", f"In force for {state} since {party.entry_into_force_on}. {note}")
    regional = passage_jurisdiction not in reg.chain(home) and passage_jurisdiction in reg.organisations_on(home, as_of)
    if passage_jurisdiction not in reg.chain(home) and not regional:
        return _make(
            "foreign", f"Legislation of {passage_jurisdiction} does not apply in {home} on {as_of.isoformat()}."
        )
    if status == "prospective":
        return _make("prospective_legislation", "Enacted, but no commencement has been recorded.")
    if applies_on_date is None:
        return _make(
            "applicability_unverified",
            "The source attests this text only as of its edition date; when it "
            f"took effect, and whether it applied on {as_of.isoformat()}, is not "
            "recorded.",
        )
    if not applies_on_date:
        return _make("historical_legislation", f"This version did not apply on {as_of.isoformat()}.")
    if regional:
        state = reg.sovereign(home)
        org = reg.get(passage_jurisdiction).name
        if doc_type == "directive":
            return _make(
                "regional_directive",
                f"Directive of {org}: binds {state} as to the result to be achieved and normally applies through "
                "national transposing law, which is not linked here; direct effect is limited (to verify).",
            )
        return _make(
            "regional_law",
            f"{doc_type.capitalize()} of {org}, of which {state} is a member on {as_of.isoformat()} (per profile). "
            f"Whether and how it applies in {state} (direct applicability, scope, primacy) depends on the instrument "
            "and the organisation's rules; check its own scope provisions (to verify).",
        )
    if doc_type == "regulation":
        return _make("binding_regulation", f"Regulation of {passage_jurisdiction} in force on {as_of.isoformat()}.")
    if doc_type == "constitution":
        return _make("binding_constitution", f"Constitution of {passage_jurisdiction}; highest norm in the hierarchy.")
    return _make(
        "binding_legislation",
        f"{doc_type.capitalize()} of {passage_jurisdiction} in force on {as_of.isoformat()} and extending to {home}.",
    )


def classify_judgment(
    reg: JurisdictionRegistry,
    *,
    target: str | None,
    judgment_jurisdiction: str,
    court_id: str,
    forum_court: str | None,
    overruled_by: str | None,
    as_of: date | None = None,
) -> Authority:
    home = target or judgment_jurisdiction
    court_profile = reg.get(reg.court_jurisdiction(court_id))
    court_name = reg.courts()[court_id].name
    if judgment_jurisdiction in reg.organisations_on(home, as_of or date.today()):
        org = reg.get(judgment_jurisdiction).name
        return _make(
            "regional_case_law",
            f"Decision of {court_name} ({org}); {reg.sovereign(home)} is a member of {org}. Its weight on questions "
            f"of {org} law follows the organisation's rules (see the {judgment_jurisdiction} profile; to verify).",
        )
    if reg.sovereign(judgment_jurisdiction) != reg.sovereign(home):
        return _make(
            "foreign",
            f"Decision of {court_name} ({judgment_jurisdiction}); courts of {home} are not "
            "bound by it. Any persuasive value depends on the forum's own practice.",
        )
    if overruled_by:
        return _make("overruled_precedent", f"Overruled by {overruled_by}.")
    precedent = court_profile.precedent
    if precedent.doctrine != "stare_decisis" or not precedent.vertical_binding:
        return _make(
            "persuasive_precedent",
            f"{court_profile.id} follows {precedent.doctrine.replace('_', ' ')}: "
            f"decisions are not formally binding. {precedent.notes or ''}".strip(),
        )
    if forum_court:
        if court_id in reg.courts_above(forum_court):
            return _make(
                "binding_precedent",
                f"{court_name} is above {reg.courts()[forum_court].name} in the appellate hierarchy.",
            )
        if court_id == forum_court and reg.courts()[court_id].binds_self:
            return _make("binding_precedent", f"{court_name} is bound by its own previous decisions.")
        return _make(
            "persuasive_precedent", f"{court_name} is not above the forum court {reg.courts()[forum_court].name}."
        )
    below = [
        c for c in reg.courts_below(court_id) if reg.court_jurisdiction(c) in reg.chain(home) + reg.descendants(home)
    ]
    if below:
        names = ", ".join(reg.courts()[c].name for c in below)
        return _make(
            "precedent_vertical",
            f"Binds courts whose appeals lie to {court_name}: {names}. Give a forum court for a definite answer.",
        )
    return _make("persuasive_precedent", f"No court in {home} is below {court_name}; persuasive only.")
