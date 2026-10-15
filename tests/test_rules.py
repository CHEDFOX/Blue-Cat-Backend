"""Temporal validity, citation modules, authority classification and quote checks (pure logic)."""

from __future__ import annotations

from datetime import date

import pytest

from ulis.catalog import Catalog
from ulis.jurisdictions.modules import JudgmentCitationInput, ProvisionCitationInput, get_module
from ulis.retrieval.authority import TreatyStatus, classify_judgment, classify_provision
from ulis.temporal.validity import Span, derive_valid_to, resolve_at
from ulis.verification.quotes import check_quote

D = date


def _versions() -> list[Span]:
    derived = derive_valid_to(
        [
            Span("v2015", "in_force", D(2015, 7, 1), None),
            Span("v2022", "in_force", D(2022, 1, 1), None),
            Span("rep", "repealed", D(2030, 1, 1), None),
            Span("pro", "prospective", None, None),
        ]
    )
    return [
        Span("v2015", "in_force", D(2015, 7, 1), derived["v2015"][0]),
        Span("v2022", "in_force", D(2022, 1, 1), derived["v2022"][0]),
        Span("rep", "repealed", D(2030, 1, 1), derived["rep"][0]),
        Span("pro", "prospective", None, None),
    ]


def test_derived_end_dates_are_flagged() -> None:
    derived = derive_valid_to(
        [
            Span("a", "in_force", D(2015, 7, 1), None),
            Span("b", "in_force", D(2022, 1, 1), None),
            Span("c", "in_force", D(1999, 1, 1), D(2001, 1, 1), valid_to_explicit=True),
        ]
    )
    assert derived["a"] == (D(2022, 1, 1), "derived")
    assert derived["b"] == (None, "open")
    assert derived["c"] == (D(2001, 1, 1), "explicit")


@pytest.mark.parametrize(
    ("as_of", "status", "version"),
    [
        (D(2015, 6, 30), "not_yet_in_force", None),
        (D(2015, 7, 1), "in_force", "v2015"),
        (D(2021, 12, 31), "in_force", "v2015"),
        (D(2022, 1, 1), "in_force", "v2022"),  # half-open interval: the new version applies on its start date
        (D(2030, 1, 1), "repealed", "rep"),
    ],
)
def test_point_in_time_resolution(as_of: date, status: str, version: str | None) -> None:
    result = resolve_at(_versions(), as_of)
    assert result.status == status
    assert (result.version.id if result.version else None) == version


def test_prospective_only() -> None:
    assert resolve_at([Span("p", "prospective", None, None)], D(2024, 1, 1)).status == "prospective_only"


def test_citation_modules(catalog: Catalog) -> None:
    xa, xb, default = get_module("xa"), get_module("xb"), get_module("default")
    act = ProvisionCitationInput("Personal Data Act 2015", "act", "XA", "section", ["12", "(1)"], "en")
    assert xa.provision_citation(act) == "Personal Data Act 2015 (XA) s 12(1)"
    const = ProvisionCitationInput("Constitution of Aurelia", "constitution", "XA", "article", ["14", "(2)"], "en")
    assert xa.provision_citation(const) == "Constitution of Aurelia art 14(2)"
    ext = catalog.jurisdictions.get("XB").extensions
    fr = ProvisionCitationInput("Code civil", "code", "XB", "article", ["28", "2"], "fr", profile_extensions=ext)
    de = ProvisionCitationInput("Zivilgesetzbuch", "code", "XB", "article", ["28", "2"], "de", profile_extensions=ext)
    assert xb.provision_citation(fr) == "art. 28 al. 2 CC-XB"
    assert xb.provision_citation(de) == "Art. 28 Abs. 2 ZGB-XB"
    treaty = ProvisionCitationInput("Northern Union Convention 2010", "treaty", "XR", "article", ["5", "1."], "en")
    assert default.provision_citation(treaty) == "Northern Union Convention 2010, art 5(1)"
    j = JudgmentCitationInput(
        "Minerva Holdings Ltd v Information Commissioner",
        "[2023] XASC 7",
        D(2023, 6, 14),
        None,
        "XA-SC",
        "Supreme Court of Aurelia",
        "4",
        "en",
        "XA",
    )
    assert xa.judgment_citation(j) == "Minerva Holdings Ltd v Information Commissioner [2023] XASC 7 [4]"
    c = JudgmentCitationInput(
        "Société Helvar c. Dubois", None, D(2019, 3, 12), "2018-114", "XB-CC", "Cour de cassation", "3", "fr", "XB"
    )
    assert xb.judgment_citation(c) == "Cass. XB, 12 mars 2019, n° 2018-114, § 3"


def test_extension_schema_is_enforced() -> None:
    assert get_module("xa").validate_ext({"chapter": "Cap. 512"}) == {"chapter": "Cap. 512"}
    with pytest.raises(ValueError):
        get_module("xa").validate_ext({"chapter": "512"})
    with pytest.raises(ValueError):
        get_module("xa").validate_ext({"unknown_field": 1})


def test_judgment_authority(catalog: Catalog) -> None:
    reg = catalog.jurisdictions

    def cls(court: str, target: str, forum: str | None = None, overruled: str | None = None) -> str:
        jur = reg.court_jurisdiction(court)
        return classify_judgment(
            reg, target=target, judgment_jurisdiction=jur, court_id=court, forum_court=forum, overruled_by=overruled
        ).category

    assert cls("XA-SC", "XA-NP", forum="XA-NP-DC") == "binding_precedent"
    assert cls("XA-NP-HC", "XA-SP", forum="XA-SP-HC") == "persuasive_precedent"
    assert cls("XA-NP-HC", "XA-NP") == "precedent_vertical"
    assert cls("XA-NP-HC", "XA-SP") == "persuasive_precedent"
    assert cls("XA-NP-HC", "XA-NP", overruled="[2023] XASC 7") == "overruled_precedent"
    assert cls("XB-CC", "XB", forum="XB-CA") == "persuasive_precedent"  # jurisprudence constante
    assert cls("XB-CC", "XA") == "foreign"


def test_provision_authority(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    parties = [
        TreatyStatus("XA", D(2010, 5, 20), D(2012, 2, 10), D(2012, 3, 1), None),
        TreatyStatus("XB", D(2010, 5, 20), D(2011, 9, 1), D(2012, 3, 1), None),
    ]

    def cls(**kw: object) -> tuple[str, str]:
        base: dict[str, object] = dict(
            target="XA",
            passage_jurisdiction="XA",
            doc_type="act",
            status="in_force",
            applies_on_date=True,
            as_of=D(2024, 1, 1),
        )
        base.update(kw)
        a = classify_provision(reg, **base)  # type: ignore[arg-type]
        return a.category, a.rationale

    assert cls()[0] == "binding_legislation"
    assert cls(doc_type="constitution")[0] == "binding_constitution"
    assert cls(status="proposed")[0] == "proposed"
    assert cls(status="prospective")[0] == "prospective_legislation"
    assert cls(applies_on_date=False)[0] == "historical_legislation"
    assert cls(passage_jurisdiction="XB")[0] == "foreign"
    category, why = cls(
        passage_jurisdiction="XR",
        doc_type="treaty",
        treaty_parties=parties,
        implementing_titles=["Personal Data Act 2015"],
    )
    assert category == "treaty_obligation" and "dualist" in why and "Personal Data Act 2015" in why
    category, why = cls(target="XB", passage_jurisdiction="XR", doc_type="treaty", treaty_parties=parties)
    assert category == "treaty_obligation" and "monist" in why
    assert (
        cls(passage_jurisdiction="XR", doc_type="treaty", treaty_parties=parties, as_of=D(2011, 1, 1))[0]
        == "treaty_not_in_force_for_state"
    )


def test_quote_checks() -> None:
    text = 'A controller shall not retain personal data for longer than "five years".'
    assert check_quote("shall not retain personal data", text).result == "exact"
    assert check_quote("shall  not\nretain", text).result == "match_after_normalisation"
    assert check_quote("than “five years”", text).normalisations == ["quotation_marks"]
    assert check_quote("seven years", text).result == "not_found"
    assert check_quote("   ", text).result == "not_found"


def test_unindexed_history_is_not_reported_as_not_yet_in_force() -> None:
    # e.g. an Act made in 1968 whose indexed point-in-time versions start at 1991-02-01
    versions = [Span("v1991", "in_force", D(1991, 2, 1), None)]
    gap = resolve_at(versions, D(1980, 1, 1), history_start=D(1991, 2, 1), work_date=D(1968, 7, 26))
    before = resolve_at(versions, D(1960, 1, 1), history_start=D(1991, 2, 1), work_date=D(1968, 7, 26))
    assert gap.status == "history_not_indexed" and gap.version is None
    assert before.status == "not_yet_in_force"
    assert resolve_at(versions, D(1980, 1, 1)).status == "not_yet_in_force"  # no history information given


def test_law_of_a_regional_organisation_applies_only_to_members_and_only_from_accession(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    base = {"passage_jurisdiction": "XR", "status": "in_force", "applies_on_date": True}
    member = classify_provision(reg, target="XA", doc_type="regulation", as_of=date(2020, 1, 1), **base)  # type: ignore[arg-type]
    assert member.category == "regional_law"
    directive = classify_provision(reg, target="XA", doc_type="directive", as_of=date(2020, 1, 1), **base)  # type: ignore[arg-type]
    assert directive.category == "regional_directive" and "transposing" in directive.rationale
    # XA joined XR on 2008-01-01 (profile); before that, and for non-members, XR law is foreign.
    assert (
        classify_provision(reg, target="XA", doc_type="regulation", as_of=date(2005, 1, 1), **base).category
        == "foreign"
    )  # type: ignore[arg-type]
    assert (
        classify_provision(reg, target="XB", doc_type="regulation", as_of=date(2020, 1, 1), **base).category
        == "foreign"
    )  # type: ignore[arg-type]
    assert reg.organisations_on("XA-NP", date(2020, 1, 1)) == ["XR"]  # provinces share the State's memberships


@pytest.mark.parametrize(
    ("state", "joined"),
    [
        ("FR", date(1958, 1, 1)),
        ("LU", date(1958, 1, 1)),
        ("PT", date(1986, 1, 1)),
        ("CZ", date(2004, 5, 1)),
        ("SK", date(2004, 5, 1)),
    ],
)
def test_eu_membership_dates_of_added_member_states(catalog: Catalog, state: str, joined: date) -> None:
    reg = catalog.jurisdictions
    assert reg.organisations_on(state, joined) == ["EU"]
    assert "EU" not in reg.organisations_on(state, date(joined.year - 1, 12, 31))


def test_scripts_without_spaces_are_searched_as_character_bigrams() -> None:
    from ulis.languages import segment_for_search

    assert segment_for_search("Article 1") is None  # other scripts are left to PostgreSQL
    assert segment_for_search("個人情報").split() == ["個人", "人情", "情報"]  # type: ignore[union-attr]
    assert segment_for_search("개인정보 보호법").split() == ["개인", "인정", "정보", "보호", "호법"]  # type: ignore[union-attr]


def test_commercial_deployments_refuse_sources_whose_commercial_use_is_unknown(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ulis.sources.registry import LicenceNotApproved, licence_gate

    unknown = [
        s
        for s in catalog.sources
        if s.status == "active" and s.licence.commercial_use not in ("allowed", "allowed_with_conditions")
    ]
    assert {s.id for s in unknown} >= {"us-wa-rcw", "us-tx-statutes", "us-or-ors", "it-normattiva"}
    monkeypatch.delenv("ULIS_COMMERCIAL_USE", raising=False)
    licence_gate(catalog.sources.get("us-wa-rcw"))  # the closed research test is not commercial
    for source in unknown:
        with pytest.raises(LicenceNotApproved, match="commercial"):
            licence_gate(source, commercial=True)
    monkeypatch.setenv("ULIS_COMMERCIAL_USE", "1")
    with pytest.raises(LicenceNotApproved, match="commercial"):
        licence_gate(catalog.sources.get("us-wa-rcw"))
    licence_gate(catalog.sources.get("de-gesetze-im-internet"))  # CC-style free reuse stays loadable


@pytest.mark.parametrize("doc_type", ["recommendation", "declaration", "arrangement", "decision_recommendation"])
def test_soft_law_instruments_are_never_labelled_binding(catalog: Catalog, doc_type: str) -> None:
    reg = catalog.jurisdictions
    for target in ("INT-OECD", "DE", None):
        got = classify_provision(
            reg,
            target=target,
            passage_jurisdiction="INT-OECD",
            doc_type=doc_type,
            status="in_force",
            applies_on_date=True,
            as_of=date(2025, 1, 1),
        )
        assert got.category == "non_binding_instrument" and not got.label.startswith("Binding")
    binding = classify_provision(
        reg,
        target="INT-OECD",
        passage_jurisdiction="INT-OECD",
        doc_type="decision",
        status="in_force",
        applies_on_date=True,
        as_of=date(2025, 1, 1),
    )
    assert binding.category == "binding_legislation"  # an OECD Decision does bind its members
