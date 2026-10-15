"""Sub-national law of Canada and Australia: Queensland, Tasmania, Ontario and Manitoba (parsers, connector,
profiles, scoping) and the sources that stay planned or rejected (licence gate)."""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor, resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import subnational_http
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers.manitoba_laws import ManitobaParseError, parse_manitoba_laws
from ulis.sources.parsers.ontario_elaws import OntarioParseError, parse_ontario_elaws
from ulis.sources.parsers.qld_legislation import QldParseError, parse_qld_legislation
from ulis.sources.parsers.tas_legislation import TasParseError, parse_tas_legislation
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "subnational"
TODAY = date(2026, 10, 14)


def raw(path: str) -> bytes:
    return gzip.open(FIX / path).read()


def doc(parse: Any, path: str) -> ParsedDocument:
    docs = parse(raw(path))
    assert len(docs) == 1
    return docs[0]  # type: ignore[no-any-return]


def cite(d: ParsedDocument, eid: str) -> str:
    p = {x.eid: x for x in d.provisions}[eid]
    module = get_module(d.ext_module or "")
    return module.provision_citation(
        ProvisionCitationInput(d.title, "act", d.jurisdiction_id, p.top_kind, p.num_path, "en", ext=d.ext, eid=p.eid)
    )


def by_eid(d: ParsedDocument) -> dict[str, Any]:
    return {p.eid: p for p in d.provisions}


# --------------------------------------------------------------------------- Queensland


def test_queensland_small_act() -> None:
    d = doc(parse_qld_legislation, "qld/act-2013-022.xml.gz")
    p = by_eid(d)
    assert (d.jurisdiction_id, d.work_uri, d.language) == ("AU-QLD", "/au/qld/act/2013-022", "en")
    assert d.title == "Succession to the Crown Act 2013" and d.number == "22 of 2013"
    assert d.work_date == date(2013, 5, 14)
    # The reprint states when it applies from; individual provisions are not dated.
    assert d.version_date == date(2014, 5, 15) and d.validity_basis == "revision_snapshot"
    assert d.currency_date is None and d.authoritative is False
    assert {x.valid_from for x in d.provisions} == {date(2014, 5, 15)}
    assert p["sec.1"].text == "This Act may be cited as the Succession to the Crown Act 2013."
    assert p["sec.1"].heading == "Short title"
    assert p["sec.6"].text.startswith("In determining the succession to the Crown, the gender of a person born after")
    assert p["sec.7-ssec.1"].parent_eid == "sec.7" and p["sec.7-ssec.1"].num_path == ["7", "(1)"]
    assert p["preamble"].text.startswith("Parliament’s reasons for enacting this Act are—")
    assert cite(d, "sec.7-ssec.1") == "Succession to the Crown Act 2013 (Qld) s 7(1)"
    assert cite(d, "preamble") == "Succession to the Crown Act 2013 (Qld) Preamble"
    # Part and Division headings are context, not provisions.
    assert p["sec.7"].context_heading == "Part 3 Marriage and succession to the Crown"
    assert p["sec.17"].context_heading.endswith("Division 1 Amendment of Act of Settlement")  # type: ignore[union-attr]


def test_queensland_code_in_a_schedule_and_repealed_provision() -> None:
    d = doc(parse_qld_legislation, "qld/act-1899-009-excerpt.xml.gz")
    p = by_eid(d)
    assert d.version_date == date(2026, 6, 1) and d.work_date == date(1899, 11, 28)
    assert d.ext["code_title"] == "Criminal Code" and d.ext["state"] == "Qld"
    assert [o["by_title"] for o in d.outstanding_changes] == ["2024 Act No. 5", "2024 Act No. 47"]
    # A repealed section keeps its number, no text; the history note is a note.
    assert p["sec.9"].status == "repealed" and p["sec.9"].text == "" and p["sec.9"].note.startswith("s 9 om 1908")
    s302 = p["sch.1-sec.302"]
    assert s302.top_kind == "code_section" and s302.heading == "Definition of murder"
    assert p["sch.1-sec.302-ssec.1-para1.a"].text == (
        "if the offender intends to cause the death of the person killed or that of some other person or if the "
        "offender intends to do to the person killed or to some other person some grievous bodily harm;"
    )
    assert "amd 1971 No. 41" in s302.note and "amd 1971" not in s302.text
    assert cite(d, "sch.1-sec.302") == "Criminal Code (Qld) s 302"
    assert cite(d, "sch.1-sec.302-ssec.1-para1.a") == "Criminal Code (Qld) s 302(1)(a)"
    assert cite(d, "sec.1") == "Criminal Code Act 1899 (Qld) s 1"
    assert cite(d, "sec.9") == "Criminal Code Act 1899 (Qld) s 9"
    assert s302.context_heading.startswith("The Criminal Code — Part 5")  # type: ignore[union-attr]


def test_queensland_dictionary_definitions() -> None:
    d = doc(parse_qld_legislation, "qld/act-2019-005.xml.gz")
    p = by_eid(d)
    assert p["sec.13-ssec.1"].text.startswith("A human right may be subject under law only to reasonable limits")
    definition = p["sch.1-def.act_"]
    assert definition.kind == "definition" and definition.text == "act includes a failure to act or a proposal to act."
    assert cite(d, "sch.1-def.act_") == "Human Rights Act 2019 (Qld) sch 1 (definition of ‘act’)"
    assert cite(d, "sec.13") == "Human Rights Act 2019 (Qld) s 13"


def test_queensland_parser_rejects_unsafe_or_foreign_xml() -> None:
    with pytest.raises(QldParseError):
        parse_qld_legislation(b"<sl title='x'/>")
    bomb = (
        b'<!DOCTYPE act [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><act title="x" year.assent="1" no="1">&b;</act>'
    )
    with pytest.raises(QldParseError):
        parse_qld_legislation(bomb)
    with pytest.raises(QldParseError):
        parse_qld_legislation(b'<act title="X Act 2000" year.assent="2000" no="1"><body/></act>')  # no first.valid.date


# --------------------------------------------------------------------------- Tasmania


def test_tasmanian_code_section_and_dates() -> None:
    d = doc(parse_tas_legislation, "tas/act-1924-069-excerpt.xml.gz")
    p = by_eid(d)
    assert (d.jurisdiction_id, d.work_uri) == ("AU-TAS", "/au/tas/act/1924-069")
    # END.VALID.DATE 9999-11-02 is the open-end sentinel: not an end date.
    assert d.version_date == date(2025, 12, 23) and d.work_date == date(1924, 4, 4)
    assert all(x.valid_to is None for x in d.provisions)
    s158 = p["JS1-GS158"]
    assert s158.heading == "Murder" and s158.top_kind == "code_section"
    assert s158.text.startswith("Any person who commits murder is guilty of a crime, and is liable to imprisonment")
    assert s158.text.endswith("as the Court determines.\nMurder.")  # the charge wording is kept as published
    assert "Amended by No. 77 of 1968, s. 4" in s158.note and "Amended" not in s158.text
    assert cite(d, "JS1-GS158") == "Criminal Code (Tas) s 158"
    assert cite(d, "GS2-Gs1") == "Criminal Code Act 1924 (Tas) s 2(1)"
    assert p["JS1-GS160"].status == "repealed" and p["JS1-GS160"].text == ""
    assert p["JS1-GS158"].context_heading.startswith("Part V Crimes Against the Person — Chapter XVII")  # type: ignore[union-attr]


def test_tasmanian_act_with_dates_in_attributes_and_numeric_references() -> None:
    d = doc(parse_tas_legislation, "tas/act-2000-019.xml.gz")
    p = by_eid(d)
    assert d.version_date == date(2026, 10, 9)
    # <DATE DAY="1" MONTH="JULY" YEAR="2000"/> is shown as the site shows it; &#150; is an en dash.
    assert p["GS2"].text == "This Act commences on 1 July 2000."
    assert p["GS3"].text.startswith("(1) In this Act, unless the contrary intention appears –\n")
    assert cite(d, "GS3-Gs1-Nd36578450972222") == (
        "First Home Owner Grant Act 2000 (Tas) s 3(1) (definition of ‘Australian citizen’)"
    )
    assert p["GS18D-Gs1"].status == "repealed" and p["GS18D-Gs1"].note.startswith("Subsection (1) omitted by")


def test_tasmanian_parser_rejects_foreign_xml() -> None:
    with pytest.raises(TasParseError):
        parse_tas_legislation(b"<SR/>")


# --------------------------------------------------------------------------- Ontario


def test_ontario_statute_structure_citation_and_history_split() -> None:
    d = doc(parse_ontario_elaws, "ontario/90t21.html.gz")
    p = by_eid(d)
    assert (d.jurisdiction_id, d.work_uri, d.title) == ("CA-ON", "/ca/on/act/90t21", "Trespass to Property Act")
    assert d.version_date == date(2025, 6, 5) and d.currency_date is None
    assert d.ext["statute_citation"] == "RSO 1990, c T.21" and d.validity_basis == "source_periods"
    occupier = p["section-1-1-def-occupier"]
    assert occupier.text.endswith("even if there is more than one occupier of the same premises; (“occupant”)")
    assert cite(d, "section-1-1") == "Trespass to Property Act, RSO 1990, c T.21, s 1(1)"
    assert cite(d, "section-1-1-def-occupier-a") == "Trespass to Property Act, RSO 1990, c T.21, s 1(1) “occupier”(a)"
    # The legislative history after a paragraph is a note, never law text.
    texts = " ".join(x.text for x in d.provisions)
    assert "R.S.O. 1990, c. T.21" not in texts
    assert any((x.note or "").startswith("R.S.O. 1990, c. T.21") for x in d.provisions)


def test_ontario_dates_repeal_outstanding_changes_and_schedule() -> None:
    d = doc(parse_ontario_elaws, "ontario/90c43.html.gz")
    p = by_eid(d)
    assert d.version_date == date(2025, 12, 11)
    # A section starts at the latest in-force date listed for it; one without a dated amendment has no start.
    assert p["section-11"].valid_from == date(1999, 4, 19)
    assert p["section-2"].valid_from is None
    assert p["section-11"].heading == "Superior Court of Justice"
    assert p["section-11"].context_heading == "PART II COURT OF ontario — Superior Court of Justice"
    assert p["section-9-2"].status == "repealed" and p["section-9-2"].text == ""
    assert p["section-9-2"].note.startswith("Repealed") and p["section-9-2"].valid_from == date(2015, 1, 1)
    assert [(o["affects_eid"], o["by_title"]) for o in d.outstanding_changes][0] == (
        "section-17",
        "2021, c. 34, Sched. 4, s. 3 (2)",
    )
    assert p["schedule"].kind == "schedule" and p["schedule"].text.startswith("APPENDIX A OF FRAMEWORK AGREEMENT")
    assert cite(d, "schedule") == "Courts of Justice Act, RSO 1990, c C.43, Sch"
    assert len([x for x in d.provisions if x.kind == "section"]) == 199


def test_ontario_older_page_template_and_annual_statute_citation() -> None:
    d = doc(parse_ontario_elaws, "ontario/90n01.html.gz")
    assert d.title == "Negligence Act" and d.version_date == date(2004, 1, 1)
    assert cite(d, "section-1") == "Negligence Act, RSO 1990, c N.1, s 1"
    assert all(x.valid_from is None for x in d.provisions)
    # An annual statute's chapter line gives its own citation form.
    html = raw("ontario/90t21.html.gz").replace(b"R.S.O. 1990, Chapter T.21", b"S.O. 2002, CHAPTER 24 Schedule B")
    assert parse_ontario_elaws(html)[0].ext["statute_citation"] == "SO 2002, c 24, Sch B"


def test_ontario_parser_rejects_other_pages() -> None:
    with pytest.raises(OntarioParseError):
        parse_ontario_elaws(b"<html><body><p class='headnote'>x</p></body></html>")
    ended = raw("ontario/90t21.html.gz").replace(b"2025 to the <a", b"2025 to December 1, 2025 and the <a", 1)
    with pytest.raises(OntarioParseError):
        parse_ontario_elaws(ended)


# --------------------------------------------------------------------------- Manitoba


def test_manitoba_act() -> None:
    d = doc(parse_manitoba_laws, "manitoba/h175.html.gz")
    p = by_eid(d)
    assert (d.jurisdiction_id, d.work_uri, d.title) == ("CA-MB", "/ca/mb/act/h175", "The Human Rights Code")
    assert d.version_date == date(2025, 6, 3) and d.currency_date == date(2026, 10, 12)
    assert d.work_date == date(1987, 7, 17) and d.validity_basis == "revision_snapshot"
    assert p["section-8-1.1"].text == (
        "The Lieutenant Governor in Council must designate one member of the adjudication panel as the chief adjudicator."
    )
    assert p["section-8-1.1"].heading == "Chief adjudicator"
    assert p["section-1-def-court"].text == '"court" means the Court of King\'s Bench; (« tribunal »)'
    assert p["section-21"].status == "repealed" and p["section-21"].note == "[Repealed] S.M. 2012, c. 38, s. 7."
    assert p["preamble"].text.startswith("WHEREAS Manitobans recognize the individual worth and dignity")
    assert "Assented" not in p["preamble"].text
    assert cite(d, "section-8-1.1") == "The Human Rights Code, CCSM c H175, s 8(1.1)"
    assert cite(d, "section-1-def-court") == "The Human Rights Code, CCSM c H175, s 1 “court”"
    assert p["section-8-1"].context_heading == "PART I COMMISSION AND ADJUDICATION PANEL"
    # French is in the same file but is not read: no provision carries the French marginal note.
    assert not any("Tribunal d'arbitrage" in (x.heading or "") for x in d.provisions)


def test_manitoba_parser_rejects_other_pages() -> None:
    with pytest.raises(ManitobaParseError):
        parse_manitoba_laws(b"<html><body><p>File not found</p></body></html>")


# --------------------------------------------------------------------------- uri resolution


def test_resolve_uri() -> None:
    assert resolve_uri("https://www.legislation.qld.gov.au/view/html/inforce/current/act-1899-009") == (
        "/au/qld/act/1899-009",
        None,
    )
    assert resolve_uri("https://www.legislation.qld.gov.au/view/whole/html/inforce/2024-02-01/act-1954-003") == (
        "/au/qld/act/1954-003",
        None,
    )
    assert resolve_uri("https://www.legislation.tas.gov.au/view/html/inforce/current/act-1924-069")[0] == (  # type: ignore[index]
        "/au/tas/act/1924-069"
    )
    assert resolve_uri("http://www.ontario.ca/laws/statute/S25024#sched7s1") == ("/ca/on/act/s25024", None)
    assert resolve_uri("https://www.ontario.ca/laws/statute/90c43") == ("/ca/on/act/90c43", None)
    assert resolve_uri("https://web2.gov.mb.ca/laws/statutes/ccsm/h175.php?lang=en") == ("/ca/mb/act/h175", None)
    assert resolve_uri("https://example.org/x") == ("https://example.org/x", None)


# --------------------------------------------------------------------------- profiles and scoping


def test_profiles_scope_provincial_law_to_the_province(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    assert reg.chain("CA-ON") == ["CA-ON", "CA", "INT"] and reg.chain("AU-QLD") == ["AU-QLD", "AU", "INT"]
    assert reg.sovereign("CA-MB") == "CA" and reg.sovereign("AU-TAS") == "AU"
    # A federal Act applies in every province; a provincial Act only in its own province.
    assert reg.extent_covers(["CA"], "CA-ON") and reg.extent_covers(["AU"], "AU-QLD")
    assert reg.extent_covers(["CA-ON"], "CA-ON")
    assert not reg.extent_covers(["CA-ON"], "CA") and not reg.extent_covers(["CA-ON"], "CA-MB")
    assert not reg.extent_covers(["AU-QLD"], "AU") and not reg.extent_covers(["AU-QLD"], "AU-TAS")
    assert set(reg.children("CA")) == {f"CA-{c}" for c in "ON BC AB QC MB NS SK NB NL PE YT NT NU".split()}
    assert set(reg.children("AU")) == {f"AU-{c}" for c in "NSW VIC QLD WA SA TAS ACT NT".split()}
    for jid, status in (
        ("CA-ON", "pilot"),
        ("CA-MB", "pilot"),
        ("AU-QLD", "pilot"),
        ("AU-TAS", "pilot"),
        ("CA-BC", "none"),
        ("CA-AB", "none"),
        ("CA-QC", "none"),
        ("AU-NSW", "none"),
        ("AU-ACT", "none"),
    ):
        profile = reg.get(jid)
        assert profile.coverage.corpus_status == status and profile.verification.status == "draft_unverified"
        assert profile.kind == "constituent_unit" and not profile.fictional
    assert "civil_law" in reg.get("CA-QC").legal_system.families
    assert reg.get("CA-QC").official_languages == ["fr", "en"]


# --------------------------------------------------------------------------- sources: active, planned, rejected

ACTIVE = {
    "au-qld-legislation": ("qld_legislation", "qld_quills_xml"),
    "au-tas-legislation": ("tas_legislation", "tas_act_xml"),
    "ca-on-e-laws": ("ontario_e_laws", "ontario_elaws_html"),
    "ca-mb-manitoba-laws": ("manitoba_laws", "manitoba_laws_html"),
}
BLOCKED = {
    "ca-bc-bclaws": "planned",
    "ca-ab-kings-printer": "planned",
    "ca-qc-legisquebec": "planned",
    "ca-ns-legislature": "planned",
    "au-nsw-legislation": "planned",
    "au-act-legislation": "planned",
    "au-sa-legislation": "planned",
    "au-wa-legislation": "planned",
    "au-vic-legislation": "rejected",
}


@pytest.mark.parametrize("source_id", sorted(ACTIVE))
def test_active_source_is_approved_registered_and_attributed(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    licence_gate(source)
    assert (source.connector, source.parser) == ACTIVE[source_id]
    assert source.connector in CONNECTORS and source.parser in PARSERS
    assert source.licence.attribution_required and source.licence.attribution_text
    assert source.authority == "official_non_authentic"
    assert "https://" in (source.licence.reviewed_by or "") and source.licence.reviewed_on == date(2026, 10, 14)


@pytest.mark.parametrize("source_id", sorted(BLOCKED))
def test_blocked_source_is_refused_by_the_licence_gate(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == BLOCKED[source_id]
    assert source.connector is None and source.parser is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    assert source.licence.attribution_text is None
    assert source.licence.review_status in ("pending_review", "rejected")
    # What was found and read is recorded with its URLs.
    assert "http" in (source.licence.reviewed_by or "") + (source.rate_limits or "")
    assert source.jurisdictions[0] in catalog.jurisdictions


def test_the_blocking_findings_are_recorded(catalog: Catalog) -> None:
    get = catalog.sources.get
    assert "Disallow: /" in get("ca-ab-kings-printer").rate_limits  # type: ignore[operator]
    assert "Disallow:" in get("ca-bc-bclaws").rate_limits and "Googlebot" in get("ca-bc-bclaws").rate_limits  # type: ignore[operator]
    for sid in ("ca-qc-legisquebec", "au-nsw-legislation", "au-sa-legislation"):
        assert "403" in get(sid).rate_limits  # type: ignore[operator]
    assert "All rights reserved" in (get("au-act-legislation").licence.reviewed_by or "")
    assert "personal use only" in (get("au-vic-legislation").licence.reviewed_by or "")
    assert get("au-vic-legislation").licence.automated_processing == "not_allowed"


# --------------------------------------------------------------------------- connectors (offline, with a fake HTTP layer)


class FakeHttp:
    pages: dict[str, bytes] = {}
    requested: list[str] = []

    def __init__(self, cache_dir: Path, user_agent: str, *, allowed_hosts: set[str], crawl_delay: float, **kw: Any):
        self.allowed_hosts, self.crawl_delay = allowed_hosts, crawl_delay

    def cache_path(self, url: str) -> Path:
        return Path("/nonexistent") / url.rsplit("/", 1)[-1]

    def get(self, url: str) -> tuple[bytes, dict[str, Any]] | None:
        FakeHttp.requested.append(url)
        if url.endswith("/robots.txt") and url not in FakeHttp.pages:
            return None
        data = FakeHttp.pages.get(url)
        if data == b"REFUSE":
            raise FetchError(f"{url}: HTTP 403 (not retried: a refusal is respected)")
        if data is None:
            return None
        return data, {
            "url": url,
            "final_url": url,
            "status": 200,
            "content_type": "text/html",
            "retrieved_at": datetime(2026, 10, 14, tzinfo=UTC).isoformat(),
        }


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> type[FakeHttp]:
    FakeHttp.pages, FakeHttp.requested = {}, []
    monkeypatch.setattr(subnational_http, "PoliteHttp", FakeHttp)
    return FakeHttp


def write_manifest(tmp_path: Path, source_id: str, host: str, ids: list[str]) -> Path:
    path = tmp_path / "m.yaml"
    docs = "\n".join(f"  - {{id: {i}, title: t}}" for i in ids)
    path.write_text(
        f"schema_version: 1\nsource_id: {source_id}\nhost: {host}\n"
        "user_agent: ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)\n"
        f"crawl_delay_seconds: 1\ndocuments:\n{docs}\n"
    )
    return path


def test_connector_yields_documents_and_reports_missing_ones(fake_http: type[FakeHttp], tmp_path: Path) -> None:
    host = "www.legislation.qld.gov.au"
    fake_http.pages[f"https://{host}/robots.txt"] = (
        b"User-agent: *\nDisallow: /admin/\nDisallow: /view/pdf/\nCrawl-delay: 9\n"
    )
    fake_http.pages[f"https://{host}/view/xml/inforce/current/act-2013-022"] = raw("qld/act-2013-022.xml.gz")
    path = write_manifest(tmp_path, "au-qld-legislation", host, ["act-2013-022", "act-1999-999"])
    connector = CONNECTORS["qld_legislation"](path, "au-qld-legislation", tmp_path)
    docs = list(connector.fetch())  # type: ignore[attr-defined]
    assert [d.external_id for d in docs] == ["qld-legislation:act-2013-022"]
    assert docs[0].content == raw("qld/act-2013-022.xml.gz") and docs[0].media_type == "application/xml"
    assert docs[0].uri == f"https://{host}/view/xml/inforce/current/act-2013-022"
    assert connector.fetch_errors == [  # type: ignore[attr-defined]
        f"qld-legislation:act-1999-999: https://{host}/view/xml/inforce/current/act-1999-999: not found (404)"
    ]
    assert connector.http.crawl_delay == 9  # type: ignore[attr-defined]  # robots.txt Crawl-delay is honoured


def test_connector_refuses_what_robots_txt_disallows(fake_http: type[FakeHttp], tmp_path: Path) -> None:
    host = "www.legislation.qld.gov.au"
    fake_http.pages[f"https://{host}/robots.txt"] = b"User-agent: *\nDisallow: /\n"
    path = write_manifest(tmp_path, "au-qld-legislation", host, ["act-2013-022"])
    with pytest.raises(FetchError, match="disallowed by robots.txt"):
        list(CONNECTORS["qld_legislation"](path, "au-qld-legislation", tmp_path).fetch())  # type: ignore[attr-defined]
    assert fake_http.requested == [f"https://{host}/robots.txt"]  # nothing else was requested


def test_connector_stops_on_a_refusal_and_on_repeated_challenge_pages(
    fake_http: type[FakeHttp], tmp_path: Path
) -> None:
    host = "www.ontario.ca"
    path = write_manifest(tmp_path, "ca-on-e-laws", host, ["90c43", "90h19", "90s22", "90b16"])
    fake_http.pages[f"https://{host}/robots.txt"] = b"REFUSE"
    with pytest.raises(FetchError, match="403"):
        list(CONNECTORS["ontario_e_laws"](path, "ca-on-e-laws", tmp_path).fetch())  # type: ignore[attr-defined]
    del fake_http.pages[f"https://{host}/robots.txt"]
    for i in ("90c43", "90h19", "90s22", "90b16"):
        fake_http.pages[f"https://{host}/laws/statute/{i}"] = b"<html><script>__uzdbm_1</script></html>"
    connector = CONNECTORS["ontario_e_laws"](path, "ca-on-e-laws", tmp_path)
    with pytest.raises(FetchError, match="challenging"):
        list(connector.fetch())  # type: ignore[attr-defined]
    assert len(connector.fetch_errors) == 3  # type: ignore[attr-defined]
    assert f"https://{host}/laws/statute/90b16" not in fake_http.requested  # stopped after the third


def test_connector_treats_an_html_error_page_for_robots_as_no_rules(fake_http: type[FakeHttp], tmp_path: Path) -> None:
    host = "web2.gov.mb.ca"
    fake_http.pages[f"https://{host}/robots.txt"] = b"<!DOCTYPE html><html><title>Resource Not Found</title>"
    fake_http.pages[f"https://{host}/laws/statutes/ccsm/h175.php?lang=en"] = raw("manitoba/h175.html.gz")
    path = write_manifest(tmp_path, "ca-mb-manitoba-laws", host, ["h175"])
    docs = list(CONNECTORS["manitoba_laws"](path, "ca-mb-manitoba-laws", tmp_path).fetch())  # type: ignore[attr-defined]
    assert len(docs) == 1 and docs[0].media_type == "text/html"


def test_robots_helpers() -> None:
    robots = "User-agent: Googlebot\nAllow: /\n\nUser-agent: *\nCrawl-delay: 10\nDisallow: /view/pdf/\n"
    assert subnational_http.robots_crawl_delay(robots) == 10
    assert subnational_http.robots_crawl_delay("User-agent: Bingbot\nCrawl-delay: 3\n") is None
    assert subnational_http.looks_like_robots(robots.encode()) and not subnational_http.looks_like_robots(b"<html>")


def test_manifest_validation(tmp_path: Path) -> None:
    from ulis.sources.connectors.local_manifest import ManifestError

    connector_cls = CONNECTORS["qld_legislation"]
    for bad_host, ids in (
        ("example.org", ["act-1899-009"]),
        ("www.legislation.qld.gov.au", ["../etc"]),
        ("www.legislation.qld.gov.au", ["act-1899-009", "act-1899-009"]),
    ):
        path = write_manifest(tmp_path, "au-qld-legislation", bad_host, ids)
        with pytest.raises(ManifestError):
            connector_cls(path, "au-qld-legislation", tmp_path).manifest()  # type: ignore[attr-defined]
    path = write_manifest(tmp_path, "other", "www.legislation.qld.gov.au", ["act-1899-009"])
    with pytest.raises(ManifestError):
        connector_cls(path, "au-qld-legislation", tmp_path).manifest()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("source_id", "expected", "minimum"),
    [
        ("au-qld-legislation", 534, 534),
        ("au-tas-legislation", 44, 40),
        ("ca-on-e-laws", 37, 30),
        ("ca-mb-manitoba-laws", 490, 490),
    ],
)
def test_committed_manifests_load(catalog: Catalog, source_id: str, expected: int, minimum: int) -> None:
    source = catalog.sources.get(source_id)
    connector = CONNECTORS[source.connector or ""](REPO_ROOT / source.access.endpoint, source_id, Path("/nonexistent"))  # type: ignore[arg-type]
    manifest = connector.manifest()  # type: ignore[attr-defined]
    assert len(manifest["documents"]) == expected >= minimum
    assert manifest["host"] in connector.HOSTS  # type: ignore[attr-defined]
    assert re.search(
        r"ULIS-research-bot/0\.1 \(\+https://github\.com/CHEDFOX/Blue-Cat-Backend\)", manifest["user_agent"]
    )
    if source_id == "au-qld-legislation":
        ids = {d["id"] for d in manifest["documents"]}
        assert {"act-1899-009", "act-1954-003", "act-2019-005", "act-2013-022"} <= ids


# --------------------------------------------------------------------------- PostgreSQL: ingest, search, point in time

SOURCE_DOCS: dict[str, list[tuple[str, str, str]]] = {
    "au-qld-legislation": [
        ("qld-legislation:act-1899-009", "qld/act-1899-009-excerpt.xml.gz", "application/xml"),
        ("qld-legislation:act-2013-022", "qld/act-2013-022.xml.gz", "application/xml"),
        ("qld-legislation:act-2019-005", "qld/act-2019-005.xml.gz", "application/xml"),
    ],
    "au-tas-legislation": [
        ("tas-legislation:act-1924-069", "tas/act-1924-069-excerpt.xml.gz", "application/xml"),
        ("tas-legislation:act-2000-019", "tas/act-2000-019.xml.gz", "application/xml"),
    ],
    "ca-on-e-laws": [
        ("ontario-e-laws:90c43", "ontario/90c43.html.gz", "text/html"),
        ("ontario-e-laws:90t21", "ontario/90t21.html.gz", "text/html"),
    ],
    "ca-mb-manitoba-laws": [("manitoba-laws:h175", "manitoba/h175.html.gz", "text/html")],
}


class _Fixtures:
    def __init__(self, source_id: str):
        self.source_id = source_id

    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 14, tzinfo=UTC)
        for external_id, path, media_type in SOURCE_DOCS[self.source_id]:
            yield RawDocument(external_id, f"file://{path}", media_type, raw(path), now, {})


@pytest.fixture(scope="module")
def subnational_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> dict[str, IngestionReport]:
    reports: dict[str, IngestionReport] = {}
    with session_factory() as session:
        for source_id in SOURCE_DOCS:
            reports[source_id] = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Fixtures(source_id)
            )
        # The federal Canadian Act, to show that federal law applies inside a province.
        fed = REPO_ROOT / "tests" / "fixtures" / "justice_canada" / "eng" / "acts" / "A-1.xml"

        class _Federal:
            def fetch(self) -> Iterator[RawDocument]:
                yield RawDocument(
                    "justice-canada:eng:A-1",
                    "file://A-1.xml",
                    "application/xml",
                    fed.read_bytes(),
                    datetime(2026, 10, 14, tzinfo=UTC),
                    {},
                )

        Ingestor(session, catalog, store).ingest_source("ca-justice-laws-xml", actor="pytest", connector=_Federal())
        session.commit()
    return reports


def run(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(**kw), today=TODAY)


def citations(res: dict[str, Any]) -> list[str]:
    return [h["citation"] for h in res["results"]]


@pytest.mark.db
def test_ingestion_reports_are_clean(subnational_ingested: dict[str, IngestionReport]) -> None:
    for source_id, report in subnational_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_seen == len(SOURCE_DOCS[source_id])
    assert subnational_ingested["au-qld-legislation"].instruments_created in (3, 0)


@pytest.mark.db
def test_queensland_search_exact_citation_and_limitations(
    session: Session, catalog: Catalog, subnational_ingested: dict[str, IngestionReport]
) -> None:
    res = run(
        session,
        catalog,
        q="unlawfully kills another intends to cause the death",
        jurisdiction="AU-QLD",
        as_of=date(2026, 7, 1),
        query_language="en",
    )
    hit = next(h for h in res["results"] if h["citation"] == "Criminal Code (Qld) s 302(1)")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"PROFILE_UNVERIFIED", "OFFICIAL_CONSOLIDATION_REPRODUCED"} <= codes
    para = run(
        session,
        catalog,
        q="offender intends to cause the death of the person killed",
        jurisdiction="AU-QLD",
        as_of=date(2026, 7, 1),
        query_language="en",
    )
    assert "Criminal Code (Qld) s 302(1)(a)" in citations(para)
    assert hit["source"]["attribution"].startswith("Based on content from the Queensland Legislation website")
    assert hit["jurisdiction"]["id"] == "AU-QLD"
    # Queensland law does not leak into a Commonwealth, Tasmanian or other State query.
    for other in ("AU", "AU-TAS", "AU-NSW"):
        res = run(
            session,
            catalog,
            q="unlawfully kills another intends to cause the death",
            jurisdiction=other,
            as_of=date(2026, 7, 1),
            query_language="en",
        )
        assert not any("(Qld)" in c for c in citations(res)), other


@pytest.mark.db
def test_commonwealth_query_reports_excluded_state_law(
    session: Session, catalog: Catalog, subnational_ingested: dict[str, IngestionReport]
) -> None:
    res = run(session, catalog, q="murder", jurisdiction="AU", as_of=date(2026, 7, 1), query_language="en")
    limits = {lim["code"]: lim for lim in res["limitations"]}
    assert "SUBNATIONAL_LEGISLATION_EXCLUDED" in limits
    assert "AU-QLD" in limits["SUBNATIONAL_LEGISLATION_EXCLUDED"]["message"]


@pytest.mark.db
def test_tasmanian_search(session: Session, catalog: Catalog, subnational_ingested: dict[str, IngestionReport]) -> None:
    res = run(
        session,
        catalog,
        q="commits murder is guilty of a crime imprisonment natural life",
        jurisdiction="AU-TAS",
        as_of=date(2026, 7, 1),
        query_language="en",
    )
    assert "Criminal Code (Tas) s 158" in citations(res)
    res = run(
        session,
        catalog,
        q="commits murder is guilty of a crime imprisonment natural life",
        jurisdiction="AU-QLD",
        as_of=date(2026, 7, 1),
        query_language="en",
    )
    assert not any("(Tas)" in c for c in citations(res))


@pytest.mark.db
def test_ontario_search_and_federal_law_applies_inside_the_province(
    session: Session, catalog: Catalog, subnational_ingested: dict[str, IngestionReport]
) -> None:
    kw = {"as_of": date(2026, 7, 1), "query_language": "en"}
    res = run(session, catalog, q="person who is in physical possession of premises", jurisdiction="CA-ON", **kw)
    assert "Trespass to Property Act, RSO 1990, c T.21, s 1(1) “occupier”(a)" in citations(res)
    # The federal Access to Information Act applies in Ontario (parent chain) ...
    res = run(session, catalog, q="right to be given access to any record", jurisdiction="CA-ON", **kw)
    assert "Access to Information Act, RSC 1985, c A-1, s 4(1)" in citations(res)
    # ... but the Ontario Act does not appear in a federal, Manitoba or Quebec query.
    for other in ("CA", "CA-MB", "CA-QC"):
        res = run(session, catalog, q="person who is in physical possession of premises", jurisdiction=other, **kw)
        assert not any(c.startswith("Trespass to Property Act") for c in citations(res)), other
    fed = run(session, catalog, q="premises", jurisdiction="CA", **kw)
    assert any(lim["code"] == "SUBNATIONAL_LEGISLATION_EXCLUDED" for lim in fed["limitations"])


@pytest.mark.db
def test_manitoba_search_and_currency(
    session: Session, catalog: Catalog, subnational_ingested: dict[str, IngestionReport]
) -> None:
    res = run(
        session,
        catalog,
        q="designate one member of the adjudication panel as the chief adjudicator",
        jurisdiction="CA-MB",
        as_of=date(2026, 10, 12),
        query_language="en",
    )
    assert citations(res)[0] == "The Human Rights Code, CCSM c H175, s 8(1.1)"
    hit = res["results"][0]
    assert hit["source"]["attribution"].startswith("Contains information from the Government of Manitoba")
    assert not any(lim["code"] == "AS_OF_AFTER_CURRENCY" for lim in hit["limitations"])
    later = run(
        session,
        catalog,
        q="designate one member of the adjudication panel as the chief adjudicator",
        jurisdiction="CA-MB",
        as_of=date(2026, 10, 14),
        query_language="en",
    )
    assert later["results"] and any(lim["code"] == "AS_OF_AFTER_CURRENCY" for lim in later["results"][0]["limitations"])


@pytest.mark.db
def test_point_in_time_follows_the_source(
    session: Session, catalog: Catalog, subnational_ingested: dict[str, IngestionReport]
) -> None:
    # Ontario s 11 has a dated amendment (in force 1999-04-19): earlier wording is a history gap, not absence.
    on = "/ca/on/act/90c43/~section-11"
    assert provision_at(session, catalog, on, date(2026, 1, 1), language="en")["status_on_date"] == "in_force"
    assert provision_at(session, catalog, on, date(1999, 4, 19), language="en")["status_on_date"] == "in_force"
    assert (
        provision_at(session, catalog, on, date(1990, 1, 1), language="en")["status_on_date"] == "history_not_indexed"
    )
    # Queensland: the reprint applies from 2026-06-01; before that nothing is indexed (the Act dates from 1899).
    qld = "/au/qld/act/1899-009/~sch.1-sec.302"
    assert provision_at(session, catalog, qld, date(2026, 6, 1), language="en")["status_on_date"] == "in_force"
    assert (
        provision_at(session, catalog, qld, date(2020, 1, 1), language="en")["status_on_date"] == "history_not_indexed"
    )
    # A repealed provision is reported as repealed, not as missing.
    repealed = provision_at(session, catalog, "/au/qld/act/1899-009/~sec.9", date(2026, 7, 1), language="en")
    assert repealed["status_on_date"] == "repealed"
