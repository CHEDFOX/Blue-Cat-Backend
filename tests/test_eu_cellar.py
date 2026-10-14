"""EU law from Cellar: connector, parser, citations and EU search behaviour (real EU texts).

Fixtures are unmodified Cellar responses (see tests/fixtures/eu_cellar/README.md), replayed by
the connector in offline mode.
"""

from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.jurisdictions.modules.eu import designation, pinpoint
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.eu_cellar import CellarConnector, query_metadata
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.eu_cellar import EuParseError, parse_eu_cellar, resolve_uri
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "eu_cellar"
LANGS = ("en", "fr", "de")


def _cache_copy(dest: Path) -> Path:
    """Copy the fixture cache and decompress it into the connector's cache layout."""
    shutil.copytree(FIX / "cache", dest)
    for gz in dest.rglob("*.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


@pytest.fixture(scope="module")
def bundles(tmp_path_factory: pytest.TempPathFactory) -> dict[str, RawDocument]:
    cache = _cache_copy(tmp_path_factory.mktemp("eu") / "cache")
    connector = CellarConnector(FIX / "manifest.yaml", "eu-eur-lex", cache, offline=True)
    docs = {raw.external_id: raw for raw in connector.fetch()}
    assert connector.http is not None and connector.http.requests_made == 0
    return docs


def _doc(bundles: dict[str, RawDocument], celex: str, lang: str) -> ParsedDocument:
    (doc,) = parse_eu_cellar(bundles[f"cellar:{celex}:{lang}"].content)
    return doc


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return {p.eid: p for p in doc.provisions}[eid]


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    return get_module("eu").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title,
            doc.instrument_type,
            "EU",
            p.top_kind,
            p.num_path,
            doc.language,
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- connector


def test_connector_replays_cache_offline_and_bundles_every_response(bundles: dict[str, RawDocument]) -> None:
    assert sorted(bundles) == sorted(
        f"cellar:{c}:{lang}" for c in ("12016M/TXT", "12016P/TXT", "32008R0593", "32011L0083") for lang in LANGS
    )
    raw = bundles["cellar:32008R0593:fr"]
    assert raw.media_type == "application/vnd.ulis.cellar-bundle+json"
    bundle = json.loads(raw.content)
    roles = [p["role"] for p in bundle["parts"]]
    assert roles[:3] == ["sparql:metadata", "sparql:changes", "sparql:items:fmx4"]
    assert roles[3:] == ["item:fmx4", "item:fmx4"]  # the act and the Official Journal descriptor
    assert all(p["url"].startswith("https://publications.europa.eu/") for p in bundle["parts"])
    assert raw.uri is not None and raw.uri.startswith("https://publications.europa.eu/resource/cellar/")


def test_connector_refuses_bad_manifests_and_uncached_requests(tmp_path: Path) -> None:
    manifest = yaml.safe_load((FIX / "manifest.yaml").read_text())

    def run(**changes: Any) -> None:
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump({**manifest, **changes}))
        list(CellarConnector(path, "eu-eur-lex", tmp_path / "cache", offline=True).fetch())

    with pytest.raises(ManifestError, match="CELEX"):
        run(works=[{"celex": '32016R0679" } ; DROP', "format": "fmx4"}])
    with pytest.raises(ManifestError, match="format"):
        run(works=[{"celex": "32016R0679", "format": "pdf"}])
    with pytest.raises(ManifestError, match="user_agent"):
        run(user_agent="python-urllib")
    with pytest.raises(ManifestError, match="languages"):
        run(languages=["en", "xx"])
    with pytest.raises(FetchError, match="offline and not cached"):
        run(works=[{"celex": "32016R0679", "format": "fmx4"}])


def test_sparql_queries_only_embed_validated_celex() -> None:
    q = query_metadata("32016R0679")
    assert '"32016R0679"^^xsd:string' in q and "ann:type_of_date" in q


# --------------------------------------------------------------------------- parser


def test_regulation_dates_text_and_metadata(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, "32008R0593", "en")
    assert (doc.jurisdiction_id, doc.instrument_type, doc.work_uri) == ("EU", "regulation", "/eu/reg/2008/593")
    assert doc.expression_uri == "/eu/reg/2008/593/eng@2008-07-04"  # OJ L 177, 4.7.2008
    assert (doc.text_origin, doc.authoritative, doc.validity_basis) == ("official_conversion", True, "source_periods")
    assert doc.work_date == date(2008, 6, 17) and doc.currency_date is None
    assert doc.ext == {
        "celex": "32008R0593",
        "eli": "http://data.europa.eu/eli/reg/2008/593/oj",
        "resource_type": "REG",
        "act_type": "regulation",
        "year": 2008,
        "number": "593",
        "domain": "EC",
    }
    events = {(e.event_type, e.date) for e in doc.events}
    # Entry into force (20 days after publication) differs from the date of application.
    assert {
        ("entry_into_force", date(2008, 7, 24)),
        ("application", date(2009, 12, 17)),
        ("partial_application", date(2009, 6, 17)),
    } <= events
    p = _p(doc, "art_4__para_1__point_a")
    assert p.text == (
        "a contract for the sale of goods shall be governed by the law of the country where the seller has "
        "his habitual residence;"
    )
    assert p.valid_from == date(2009, 12, 17) and p.valid_to is None and p.extent == ["EU"]
    assert "partial application 2009-06-17" in (p.note or "")
    assert _p(doc, "art_29").text.endswith(
        "It shall apply from 17 December 2009 except for Article 26 which shall apply from 17 June 2009."
    )
    assert not any(p.eid.startswith("rct") for p in doc.provisions)  # recitals are not indexed
    # Corrigenda are reported for the language versions they concern, never applied.
    assert doc.outstanding_changes and all(
        c["affects_eid"] is None and "Corrigendum" in c["by_title"] for c in doc.outstanding_changes
    )


def test_language_versions_share_identifiers(bundles: dict[str, RawDocument]) -> None:
    for celex in ("12016M/TXT", "12016P/TXT", "32008R0593", "32011L0083"):
        eids = [[p.eid for p in _doc(bundles, celex, lang).provisions] for lang in LANGS]
        assert eids[0] == eids[1] == eids[2], celex
        uris = {_doc(bundles, celex, lang).work_uri for lang in LANGS}
        assert len(uris) == 1


def test_citations_in_english_french_and_german(bundles: dict[str, RawDocument]) -> None:
    rome = {lang: _doc(bundles, "32008R0593", lang) for lang in LANGS}
    assert _cite(rome["en"], "art_4__para_1__point_a") == "Regulation (EC) No 593/2008, art 4(1)(a)"
    assert _cite(rome["fr"], "art_4__para_1__point_a") == "Règlement (CE) n° 593/2008, art. 4, par. 1, point a)"
    assert _cite(rome["de"], "art_4__para_1__point_a") == "Verordnung (EG) Nr. 593/2008, Art. 4 Abs. 1 Buchst. a"
    crd = {lang: _doc(bundles, "32011L0083", lang) for lang in LANGS}
    assert _cite(crd["en"], "art_2__point_1") == "Directive 2011/83/EU, art 2(1)"
    assert _cite(crd["fr"], "art_2__point_1") == "Directive 2011/83/UE, art. 2, point 1)"
    assert _cite(crd["de"], "art_2__point_1") == "Richtlinie 2011/83/EU, Art. 2 Nr. 1"
    teu = {lang: _doc(bundles, "12016M/TXT", lang) for lang in LANGS}
    assert [_cite(teu[lang], "art_6__para_1") for lang in LANGS] == [
        "TEU, art 6(1)",
        "TUE, art. 6, par. 1",
        "EUV, Art. 6 Abs. 1",
    ]
    charter = {lang: _doc(bundles, "12016P/TXT", lang) for lang in LANGS}
    assert [_cite(charter[lang], "art_8__para_1") for lang in LANGS] == [
        "Charter of Fundamental Rights of the European Union, art 8(1)",
        "Charte des droits fondamentaux de l'Union européenne, art. 8, par. 1",
        "Charta der Grundrechte der Europäischen Union, Art. 8 Abs. 1",
    ]
    # The designation is the one printed in each language's title.
    for lang in LANGS:
        assert (rome[lang].short_title or "").replace("n°", "no") in rome[lang].title.replace("n°", "no")
        assert (crd[lang].short_title or "") in crd[lang].title


def test_citation_rules() -> None:
    assert designation("regulation", 2016, "679", "EU", "en") == "Regulation (EU) 2016/679"
    assert designation("regulation", 2016, "679", "EU", "fr") == "Règlement (UE) 2016/679"
    assert designation("regulation", 2016, "679", "EU", "de") == "Verordnung (EU) 2016/679"
    assert designation("regulation", 2012, "1215", "EU", "de") == "Verordnung (EU) Nr. 1215/2012"
    assert designation("directive", 1993, "13", "EEC", "en") == "Directive 93/13/EEC"
    assert designation("directive", 1993, "13", "EEC", "fr") == "Directive 93/13/CEE"
    assert designation("directive", 2019, "2161", "EU", "de") == "Richtlinie (EU) 2019/2161"
    assert designation("regulation", 2016, "679", "EU", "it") is None  # falls back to the title
    eid = "art_6__para_1__point_a__subpoint_ii"
    assert [pinpoint(eid, lang) for lang in LANGS] == [
        "art 6(1)(a)(ii)",
        "art. 6, par. 1, point a) ii)",
        "Art. 6 Abs. 1 Buchst. a Ziff. ii",
    ]
    eid = "art_6__para_1__subpara_2__point_b"
    assert [pinpoint(eid, lang) for lang in LANGS] == [
        "art 6(1), subpara 2, point (b)",
        "art. 6, par. 1, al. 2, point b)",
        "Art. 6 Abs. 1 UAbs. 2 Buchst. b",
    ]
    assert [pinpoint("anx_III", lang) for lang in LANGS] == ["Annex III", "annexe III", "Anhang III"]
    module = get_module("eu")
    tfeu = ProvisionCitationInput(
        "TFEU", "founding_treaty", "EU", "article", [], "en", ext={"treaty": "TFEU"}, eid="art_101__para_1"
    )
    assert module.provision_citation(tfeu) == "TFEU, art 101(1)"


def test_amendments_end_the_original_wording(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, "32011L0083", "en")
    assert doc.instrument_type == "directive" and doc.work_uri == "/eu/dir/2011/83"
    unchanged = _p(doc, "art_6__para_1__point_a")
    assert (unchanged.valid_from, unchanged.valid_to) == (date(2011, 12, 12), None)
    assert "Article 288 TFEU" in (unchanged.note or "")  # a directive is not directly applicable
    # Replaced by Directive (EU) 2023/2673 with effect from 18 December 2023 (Publications Office metadata).
    replaced = _p(doc, "art_6__para_1__point_h")
    assert replaced.text.startswith("where a right of withdrawal exists, the conditions, time limit and procedures")
    assert replaced.valid_to == date(2023, 12, 18) and replaced.record_version
    assert "32023L2673" in (replaced.note or "")
    # Paragraph 1 contains replaced and inserted points: its composite text ended at the first change.
    assert _p(doc, "art_6__para_1").valid_to == date(2020, 1, 7)
    linked = [c for c in doc.outstanding_changes if c["affects_eid"] == "art_6__para_1__point_h"]
    assert linked and linked[0]["by_uri"] == "http://publications.europa.eu/resource/celex/32023L2673"
    assert linked[0]["by_title"].startswith("Directive (EU) 2023/2673")
    # Corrigenda concern other language versions only (NL, CS, FI, PL, EL, ...).
    assert not any("Corrigendum" in c["by_title"] for c in doc.outstanding_changes)
    # Article 32 inserts a quoted article into Directive 93/13/EEC: the quoted text is kept whole.
    assert "‘Article 8a" in _p(doc, "art_32").text
    assert ("transposition_deadline", date(2013, 12, 13)) in {(e.event_type, e.date) for e in doc.events}


def test_consolidated_treaty_from_xhtml(bundles: dict[str, RawDocument]) -> None:
    en, fr, de = (_doc(bundles, "12016M/TXT", lang) for lang in LANGS)
    assert (en.instrument_type, en.work_uri, en.short_title) == ("founding_treaty", "/eu/treaty/teu_2016", "TEU")
    assert (en.text_origin, en.authoritative, en.validity_basis) == ("official_revised", False, "attested_at_edition")
    assert en.currency_date == date(2016, 6, 7) and en.work_date == date(1992, 2, 7)
    assert sum(p.kind == "article" for p in en.provisions) == 55  # protocols and declarations excluded
    p = _p(en, "art_50__para_1")
    assert p.text == (
        "Any Member State may decide to withdraw from the Union in accordance with its own constitutional requirements."
    )
    assert p.valid_from is None and p.context_heading == "TITLE VI FINAL PROVISIONS"
    art1 = _p(en, "art_1")
    assert (art1.note or "").startswith("(ex Article 1 TEU)\nThese references are merely indicative.")
    assert "(2)" not in art1.text  # footnote calls are not text
    assert _p(fr, "art_1").text.startswith(
        "Par le présent traité, les HAUTES PARTIES CONTRACTANTES"
    )  # "Article premier"
    # German paragraphs are numbered "(1)": the label is not part of the paragraph's text.
    assert (
        _p(de, "art_3__para_1").text
        == "Ziel der Union ist es, den Frieden, ihre Werte und das Wohlergehen ihrer Völker zu fördern."
    )
    assert (
        _p(en, "art_3__para_1").text
        == "The Union's aim is to promote peace, its values and the well-being of its peoples."
    )


def test_charter(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, "12016P/TXT", "en")
    assert (doc.instrument_type, doc.work_uri) == ("charter", "/eu/treaty/char_2016")
    assert doc.ext["treaty"] == "CHARTER"
    art8 = _p(doc, "art_8")
    assert art8.heading == "Protection of personal data" and art8.context_heading == "TITLE II FREEDOMS"
    p = _p(doc, "art_8__para_1")
    assert p.text == "Everyone has the right to the protection of personal data concerning him or her."
    assert p.valid_from == date(2009, 12, 1)  # entry into force recorded in the metadata


def test_bundle_integrity_is_checked(bundles: dict[str, RawDocument]) -> None:
    bundle = json.loads(bundles["cellar:12016P/TXT:en"].content)
    item = next(p for p in bundle["parts"] if p["role"] == "item:fmx4" and "Human dignity" in p["content"])
    item["content"] = item["content"].replace("Human dignity is inviolable", "Human dignity is negotiable")
    with pytest.raises(EuParseError, match="SHA-256"):
        parse_eu_cellar(json.dumps(bundle).encode())
    with pytest.raises(EuParseError, match="bundle"):
        parse_eu_cellar(b'{"format": "something-else"}')


def test_resolve_uri() -> None:
    assert resolve_uri("http://data.europa.eu/eli/reg/2016/679/oj") == ("/eu/reg/2016/679", None)
    assert resolve_uri("http://data.europa.eu/eli/treaty/teu_2016/oj") == ("/eu/treaty/teu_2016", None)
    assert pipeline_resolve_uri("http://data.europa.eu/eli/dir/2011/83/oj") == ("/eu/dir/2011/83", None)
    assert resolve_uri("https://example.org/eli/reg/2016/679/oj") is None


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def __init__(self, docs: dict[str, RawDocument]):
        self.docs = docs

    def fetch(self) -> Iterator[RawDocument]:
        yield from self.docs.values()


@pytest.fixture(scope="module")
def eu_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    bundles: dict[str, RawDocument],
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "eu-eur-lex", actor="pytest", connector=_Fixtures(bundles)
        )
        session.commit()
    return report


def _hit(res: dict[str, Any], eid: str) -> dict[str, Any]:
    """The hit for one provision; asserts it is among the top three."""
    ranked = [h["unit"]["eid"] for h in res["results"]]
    assert eid in ranked[:3], ranked
    return res["results"][ranked.index(eid)]


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="EU", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingestion_report(eu_ingested: IngestionReport) -> None:
    assert eu_ingested.errors == [] and eu_ingested.conflicts == []
    assert (eu_ingested.documents_new, eu_ingested.instruments_created, eu_ingested.instrument_versions_created) == (
        12,
        4,
        12,
    )


@pytest.mark.db
def test_search_regulation_in_three_languages(session: Session, catalog: Catalog, eu_ingested: IngestionReport) -> None:
    res = _search(
        session,
        catalog,
        q="choice of law made expressly or clearly demonstrated",
        as_of=date(2026, 10, 14),
        query_language="en",
    )
    hit = _hit(res, "art_3__para_1")
    assert hit["citation"] == "Regulation (EC) No 593/2008, art 3(1)"
    assert hit["authority"]["category"] == "binding_regulation"
    assert hit["version"]["valid_from"] == "2009-12-17" and hit["version"]["text_origin"] == "official_conversion"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "PROFILE_UNVERIFIED",
        "NOT_AUTHENTICATED",
        "EQUALLY_AUTHENTIC_VERSIONS",
        "SOURCE_NOT_AUTHENTIC",
        "CURRENCY_UNKNOWN",
        "VERSION_NOTE",
        "OUTSTANDING_CHANGES_INSTRUMENT",
    } <= codes
    assert {p["citation"] for p in hit["parallel_versions"]} == {
        "Règlement (CE) n° 593/2008, art. 3, par. 1",
        "Verordnung (EG) Nr. 593/2008, Art. 3 Abs. 1",
    }
    assert hit["source"]["attribution"].startswith("Source: EUR-Lex / Cellar")
    fr = _search(session, catalog, q="le choix de la loi est exprès", as_of=date(2026, 10, 14), query_language="fr")
    assert _hit(fr, "art_3__para_1")["citation"] == "Règlement (CE) n° 593/2008, art. 3, par. 1"
    de = _search(
        session, catalog, q="Rechtswahl muss ausdrücklich erfolgen", as_of=date(2026, 10, 14), query_language="de"
    )
    assert _hit(de, "art_3__para_1")["citation"] == "Verordnung (EG) Nr. 593/2008, Art. 3 Abs. 1"


@pytest.mark.db
def test_search_treaty_and_directive(session: Session, catalog: Catalog, eu_ingested: IngestionReport) -> None:
    res = _search(
        session, catalog, q="decide to withdraw from the Union", as_of=date(2026, 10, 14), query_language="en"
    )
    hit = res["results"][0]
    assert hit["citation"] == "TEU, art 50(1)"
    assert hit["authority"]["category"] == "applicability_unverified"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "REVISED_TEXT", "AS_OF_AFTER_CURRENCY"} <= codes
    res = _search(session, catalog, q="main characteristics of the goods or services", as_of=date(2026, 10, 14))
    hit = next(h for h in res["results"] if h["document"]["type"] == "directive")
    assert hit["authority"]["category"] == "binding_legislation"
    assert any("Article 288 TFEU" in lim["message"] for lim in hit["limitations"])


@pytest.mark.db
def test_point_in_time_follows_the_publications_office_dates(
    session: Session, catalog: Catalog, eu_ingested: IngestionReport
) -> None:
    uri = "/eu/reg/2008/593/~art_3__para_1"
    # In force from 24 July 2008, but Rome I applies from 17 December 2009.
    assert provision_at(session, catalog, uri, date(2009, 1, 1), language="en")["status_on_date"] == "not_yet_in_force"
    assert provision_at(session, catalog, uri, date(2010, 1, 1), language="de")["status_on_date"] == "in_force"
    uri = "/eu/dir/2011/83/~art_6__para_1__point_h"
    assert provision_at(session, catalog, uri, date(2020, 1, 1), language="en")["status_on_date"] == "in_force"
    later = provision_at(session, catalog, uri, date(2024, 1, 1), language="en")
    assert later["status_on_date"] == "no_version"  # replaced on 18 December 2023; new wording not indexed
    assert later["timeline"][0]["valid_to"] == "2023-12-18"
    # A consolidated Treaty is attested only at its publication date.
    teu = provision_at(session, catalog, "/eu/treaty/teu_2016/~art_50__para_1", date(2026, 10, 14), language="fr")
    assert teu["status_on_date"] == "in_force_date_unverified"
    assert teu["provision"]["citation"] == "TUE, art. 50, par. 1"


@pytest.mark.db
def test_eu_law_applies_in_member_states_only_while_they_are_members(
    session: Session, catalog: Catalog, eu_ingested: IngestionReport
) -> None:
    def rome_i(jurisdiction: str, when: date) -> dict[str, Any] | None:
        res = search(
            session,
            catalog,
            SearchRequest(
                q="choice of law made expressly or clearly demonstrated",
                jurisdiction=jurisdiction,
                as_of=when,
                query_language="en",
            ),
            today=date(2026, 10, 14),
        )
        return next((h for h in res["results"] if h["citation"] == "Regulation (EC) No 593/2008, art 3(1)"), None)

    in_germany = rome_i("DE", date(2020, 1, 1))
    assert in_germany is not None and in_germany["authority"]["category"] == "regional_law"
    # The United Kingdom: a member until EU law ceased to apply at the end of 2020 (per profile).
    assert rome_i("GB-ENG", date(2015, 6, 1)) is not None
    assert rome_i("GB-ENG", date(2022, 6, 1)) is None
    assert rome_i("CA", date(2020, 1, 1)) is None  # not a member
