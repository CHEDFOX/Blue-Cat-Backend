"""EU law from Cellar: connector, parser, citations and EU search behaviour (real EU texts).

Fixtures are unmodified Cellar responses (see tests/fixtures/eu_cellar/README.md), replayed by
the connector in offline mode.
"""

from __future__ import annotations

import gzip
import hashlib
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
from ulis.sources.connectors.eu_cellar import (
    CONSOLIDATED_RE,
    CellarConnector,
    derived_response,
    query_changes_batch,
    query_items_batch,
    query_metadata,
    query_metadata_batch,
    split_rows,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.eu_cellar import ANN, CDM, EuParseError, parse_eu_cellar, resolve_uri
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


# --------------------------------------------------------------------------- batches and consolidated versions
# (the manifest was widened to every act in force; see docs/jurisdictions/eu.md)


def _batch_manifest(tmp_path: Path, **changes: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": "eu-eur-lex",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "sparql_batch": 20,
        "languages": ["en"],
        "works": [
            {"celex": "02016R0679-20160504", "base": "32016R0679", "format": "fmx4", "languages": ["en", "fr"]},
            {"celex": "32008R0593", "format": "fmx4"},
        ],
        **changes,
    }
    path = tmp_path / "batched.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def test_batch_queries_cover_every_work_and_only_validated_celex() -> None:
    celexes = ["32016R0679", "02016R0679-20160504"]
    for query in (
        query_metadata_batch(celexes),
        query_changes_batch(celexes[:1], ["en"]),
        query_items_batch(celexes, ["en", "de"], "fmx4"),
    ):
        assert "VALUES" in query and '"32016R0679"^^xsd:string' in query
    assert '"02016R0679-20160504"^^xsd:string' in query_metadata_batch(celexes)
    assert "?base" in query_changes_batch(celexes[:1], ["en"]) and "?celex ?work" in query_items_batch(
        celexes, ["en"], "fmx4"
    )
    for bad in ('32016R0679" } ; DROP', "../x", "3201"):
        with pytest.raises(ManifestError, match="CELEX"):
            query_metadata_batch([bad])
    assert CONSOLIDATED_RE.match("02016R0679-20160504") and not CONSOLIDATED_RE.match("32016R0679")


def test_a_batched_answer_is_cut_down_to_the_rows_of_each_work() -> None:
    answer = json.dumps(
        {
            "head": {"vars": ["celex", "p"]},
            "results": {
                "bindings": [
                    {"celex": {"value": "A"}, "p": {"value": "1"}},
                    {"celex": {"value": "B"}, "p": {"value": "2"}},
                    {"celex": {"value": "A"}, "p": {"value": "3"}},
                ]
            },
        }
    ).encode()
    variables, grouped = split_rows(answer, "celex")
    assert variables == ["celex", "p"] and sorted(grouped) == ["A", "B"] and len(grouped["A"]) == 2
    again = json.loads(derived_response(variables, grouped["A"]))
    assert [r["p"]["value"] for r in again["results"]["bindings"]] == ["1", "3"]


def test_manifest_validation_for_batches_languages_and_consolidated_versions(tmp_path: Path) -> None:
    def run(**changes: Any) -> None:
        list(
            CellarConnector(
                _batch_manifest(tmp_path, **changes), "eu-eur-lex", tmp_path / "cache", offline=True
            ).fetch()
        )

    base = {"celex": "02016R0679-20160504", "base": "32016R0679", "format": "fmx4"}
    for changes, message in (
        ({"sparql_batch": 0}, "sparql_batch"),
        ({"sparql_batch": 500}, "sparql_batch"),
        ({"crawl_delay_seconds": 0.5}, "crawl_delay_seconds"),
        (
            {"works": [{"celex": "32016R0679", "format": "fmx4"}, {"celex": "32016R0679", "format": "fmx4"}]},
            "repeated CELEX",
        ),
        ({"works": [{"celex": "32016R0679", "format": "fmx4", "languages": ["en", "xx"]}]}, "languages"),
        ({"works": [{"celex": "32016R0679", "format": "fmx4", "base": "32016R0679"}]}, "only a consolidated version"),
        ({"works": [{"celex": "02016R0679-20160504", "format": "fmx4"}]}, "base act"),
        ({"works": [{**base, "base": "02016R0679-20160504"}]}, "base act"),
        ({"works": [{**base, "format": "xhtml"}]}, "Formex 4"),
    ):
        with pytest.raises(ManifestError, match=message):
            run(**changes)


def test_the_committed_manifest_lists_acts_in_force_and_passes_validation(tmp_path: Path) -> None:
    path = REPO_ROOT / "config" / "manifests" / "eu-eur-lex.yaml"
    manifest = CellarConnector(path, "eu-eur-lex", tmp_path)._manifest()  # noqa: SLF001
    works = manifest["works"]
    assert manifest["sparql_batch"] == 20 and manifest["languages"] == ["en"]
    assert [w["celex"] for w in works[:3]] == ["12016M/TXT", "12016E/TXT", "12016P/TXT"]
    assert len(works) > 15000 and len({w["celex"] for w in works}) == len(works)
    consolidated = [w for w in works if CONSOLIDATED_RE.match(w["celex"])]
    assert consolidated and all(w["base"][0] == "3" and w["format"] == "fmx4" for w in consolidated)
    core = {w.get("base") or w["celex"]: w for w in works if w.get("languages")}
    assert {"32016R0679", "32008R0593", "32012R1215", "32011L0083", "32024R1689"} <= set(core)
    assert all(core[c]["languages"] == ["en", "fr", "de"] for c in core)
    # Acts without a Formex 4 file are listed in the text format Cellar has (legacy HTML, OJ XHTML), as adopted.
    formats = {fmt: sum(w["format"] == fmt for w in works) for fmt in ("fmx4", "xhtml", "html")}
    assert formats["html"] > 6000 and formats["xhtml"] > 300 and formats["fmx4"] > 27000
    assert all("base" not in w for w in works if w["format"] in ("html", "xhtml"))


class _FakeHttp:
    """Answers the batched queries from canned rows and serves one tiny file per item URL."""

    crawl_delay = 5.0
    requests_made = 0

    def __init__(self, fail_first_metadata: bool = False, missing: set[str] | None = None):
        self.fail_first_metadata = fail_first_metadata
        self.missing = missing or set()
        self.queries: list[str] = []

    def get(self, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        self.requests_made += 1
        meta = {
            "url": url,
            "status": 200,
            "content_type": "application/json",
            "retrieved_at": "2026-10-15T08:00:00+00:00",
        }
        if cache_key and "/sparql/batch/" in cache_key:
            name = cache_key.rsplit("/", 1)[1].split("-")[0]
            self.queries.append(name)
            if name == "metadata" and self.fail_first_metadata and self.queries.count("metadata") == 1:
                from ulis.sources.connectors.polite_http import FetchError as _Fetch

                raise _Fetch(f"{url}: HTTP 500 (not retried: a refusal is respected)")
            return json.dumps(self._answer(name, url)).encode(), meta
        if url in self.missing:
            return None
        meta["content_type"] = "application/xml"
        return b"<ACT/>", meta

    @staticmethod
    def _answer(name: str, url: str) -> dict[str, Any]:
        import urllib.parse as up

        query = up.parse_qs(up.urlsplit(url).query)["query"][0]
        wanted = [c for c in ("32016R0679", "02016R0679-20160504", "32008R0593") if f'"{c}"^^xsd:string' in query]

        def v(value: str) -> dict[str, str]:
            return {"value": value}

        rows: list[dict[str, Any]] = []
        for celex in wanted:
            if name == "metadata":
                rows.append(
                    {
                        "celex": v(celex),
                        "work": v("urn:" + celex),
                        "p": v("http://publications.europa.eu/ontology/cdm#resource_legal_id_celex"),
                        "o": v(celex),
                    }
                )
            elif name == "changes" and not celex.startswith("0"):
                rows.append({"base": v(celex), "x": v("urn:amending"), "rel": v("rel"), "celex": v("32020R0001")})
            elif name.startswith("items"):
                lang = "http://publications.europa.eu/resource/authority/language/ENG"
                doc = 1 if celex[0] == "0" else 2
                item = f"http://publications.europa.eu/resource/cellar/5f2552c2-cc45-11e6-ad7c-01aa75ed71a1.0022.01/DOC_{doc}"
                rows.append(
                    {
                        "celex": v(celex),
                        "work": v("urn:" + celex),
                        "lang": v(lang),
                        "title": v("T"),
                        "m": v("urn:m"),
                        "item": v(item),
                    }
                )
        return {"head": {"vars": ["celex"]}, "results": {"bindings": rows}}


def test_batched_fetch_splits_answers_reports_what_is_missing_and_splits_a_failing_batch(tmp_path: Path) -> None:
    fake = _FakeHttp()
    connector = CellarConnector(_batch_manifest(tmp_path), "eu-eur-lex", tmp_path / "cache", offline=True)
    connector.http = fake  # type: ignore[assignment]
    docs = list(connector.fetch())
    # Three queries for the two works (metadata, changes, items), one file each; no French file for the consolidated.
    assert fake.queries == ["metadata", "changes", "items"]
    assert [d.external_id for d in docs] == ["cellar:02016R0679-20160504:en", "cellar:32008R0593:en"]
    assert connector.fetch_errors == ["02016R0679-20160504 (fr): Cellar lists no fmx4 file in this language; skipped"]
    consolidated = json.loads(docs[0].content)
    assert consolidated["base_celex"] == "32016R0679"
    assert [p["role"] for p in consolidated["parts"]] == [
        "sparql:metadata",
        "sparql:metadata:base",
        "sparql:items:fmx4",
        "item:fmx4",
    ]
    selection = {p["role"]: p["selection"] for p in consolidated["parts"] if "selection" in p}
    assert (
        selection["sparql:metadata"] == "celex = 02016R0679-20160504"
        and selection["sparql:metadata:base"] == "celex = 32016R0679"
    )
    assert all(len(p["derived_from_sha256"]) == 64 for p in consolidated["parts"] if "derived_from_sha256" in p)
    plain = json.loads(docs[1].content)
    assert [p["role"] for p in plain["parts"]][:3] == ["sparql:metadata", "sparql:changes", "sparql:items:fmx4"]
    assert "base_celex" not in plain
    # Each work's metadata part holds only that work's rows.
    own = json.loads(plain["parts"][0]["content"])["results"]["bindings"]
    assert {r["celex"]["value"] for r in own} == {"32008R0593"}

    # A batch the endpoint cannot answer (HTTP 5xx) is split in halves; a missing file is reported and skipped.
    flaky = _FakeHttp(
        fail_first_metadata=True,
        missing={"https://publications.europa.eu/resource/cellar/5f2552c2-cc45-11e6-ad7c-01aa75ed71a1.0022.01/DOC_2"},
    )
    again = CellarConnector(
        _batch_manifest(tmp_path, works=_batch_manifest_works()), "eu-eur-lex", tmp_path / "cache", offline=True
    )
    again.http = flaky  # type: ignore[assignment]
    docs = list(again.fetch())
    assert [d.external_id for d in docs] == ["cellar:02016R0679-20160504:en"]
    assert flaky.queries.count("metadata") == 3  # the failed batch, then one query for each half
    assert any("the batch is split" in e for e in again.fetch_errors)
    assert any("DOC_2 not found (404); skipped" in e for e in again.fetch_errors)


def _batch_manifest_works() -> list[dict[str, Any]]:
    return [
        {"celex": "02016R0679-20160504", "base": "32016R0679", "format": "fmx4"},
        {"celex": "32008R0593", "format": "fmx4"},
    ]


def test_manifest_generator_reads_the_sparql_rows() -> None:
    from scripts.build_eu_manifest import acts_of_year, consolidated_of_year

    class _Http:
        def __init__(self, rows: list[dict[str, Any]]):
            self.rows = rows

        def get(self, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
            return json.dumps({"results": {"bindings": self.rows}}).encode(), {}

    acts = _Http(
        [
            {"c": {"value": "32016R0679"}, "d": {"value": "2016-04-27"}, "t": {"value": "fmx4"}},
            {"c": {"value": "32016R0679"}, "d": {"value": "2016-04-27"}, "t": {"value": "pdf"}},
            {"c": {"value": "32016L0680"}, "t": {"value": "xhtml"}},
        ]
    )
    # Every English manifestation type of each act; the caller picks the format to fetch.
    assert acts_of_year(acts, 2016) == {  # type: ignore[arg-type]
        "32016R0679": ("2016-04-27", frozenset({"fmx4", "pdf"})),
        "32016L0680": ("", frozenset({"xhtml"})),
    }
    cons = _Http(
        [
            {
                "b": {"value": "32016R0679"},
                "cc": {"value": "02016R0679-20160504"},
                "cd": {"value": "2016-05-04"},
                "t": {"value": "fmx4"},
            },
            {
                "b": {"value": "32016R0679"},
                "cc": {"value": "02016R0679-20160504"},
                "cd": {"value": "2016-05-04"},
                "t": {"value": "xhtml"},
            },
            {
                "b": {"value": "32016R0679"},
                "cc": {"value": "02016R0679-20180523"},
                "cd": {"value": "2018-05-23"},
                "t": {"value": "fmx4"},
            },
        ]
    )
    assert consolidated_of_year(cons, 2016) == {  # type: ignore[arg-type]
        "32016R0679": [
            ("2016-05-04", "02016R0679-20160504", frozenset({"fmx4", "xhtml"})),
            ("2018-05-23", "02016R0679-20180523", frozenset({"fmx4"})),
        ]
    }


@pytest.fixture(scope="module")
def batched(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, RawDocument], CellarConnector]:
    cache = _cache_copy(tmp_path_factory.mktemp("eu-batched") / "cache")
    connector = CellarConnector(FIX / "manifest-batched.yaml", "eu-eur-lex", cache, offline=True)
    docs = {raw.external_id: raw for raw in connector.fetch()}
    return docs, connector


CONS = "02016R0679-20160504"


def test_the_batched_connector_replays_the_cache_and_cuts_the_answers_per_work(
    batched: tuple[dict[str, RawDocument], CellarConnector],
) -> None:
    docs, connector = batched
    assert sorted(docs) == sorted(
        [f"cellar:{CONS}:en", f"cellar:{CONS}:fr", f"cellar:{CONS}:de", "cellar:32008R0593:en"]
    )
    assert connector.http is not None and connector.http.requests_made == 0 and connector.fetch_errors == []
    bundle = json.loads(docs[f"cellar:{CONS}:de"].content)
    assert bundle["base_celex"] == "32016R0679"
    roles = [p["role"] for p in bundle["parts"]]
    assert (
        roles[:3] == ["sparql:metadata", "sparql:metadata:base", "sparql:items:fmx4"] and "sparql:changes" not in roles
    )
    batch_urls = {p["url"] for p in bundle["parts"] if p["role"].startswith("sparql:")}
    assert len(batch_urls) == 2  # the metadata answer (consolidated version and base act) and the items answer
    for part in bundle["parts"][:3]:
        assert part["derived_from_sha256"] and part["selection"].startswith("celex = ")
        rows = json.loads(part["content"])["results"]["bindings"]
        assert rows and {r["celex"]["value"] for r in rows} == {part["selection"].removeprefix("celex = ")}
    plain = json.loads(docs["cellar:32008R0593:en"].content)
    assert [p["role"] for p in plain["parts"]][:3] == ["sparql:metadata", "sparql:changes", "sparql:items:fmx4"]
    assert "base_celex" not in plain


def test_a_consolidated_version_is_a_snapshot_of_the_instrument_of_its_base_act(
    batched: tuple[dict[str, RawDocument], CellarConnector],
) -> None:
    docs, _ = batched
    (doc,) = parse_eu_cellar(docs[f"cellar:{CONS}:en"].content)
    assert (doc.instrument_type, doc.work_uri) == ("regulation", "/eu/reg/2016/679")
    assert doc.expression_uri == "/eu/reg/2016/679/eng@2016-05-04"
    assert (doc.validity_basis, doc.consolidation_kind, doc.text_origin, doc.authoritative) == (
        "revision_snapshot",
        "point_in_time",
        "official_revised",
        False,
    )
    assert (doc.version_date, doc.currency_date) == (date(2016, 5, 4), date(2018, 5, 23))
    assert doc.ext["celex"] == CONS and doc.ext["base_celex"] == "32016R0679" and doc.ext["number"] == "679"
    assert doc.number == "2016/679" and doc.short_title == "Regulation (EU) 2016/679"
    assert get_module("eu").validate_ext(doc.ext)["base_celex"] == "32016R0679"
    # Dates come from INFO.CONSLEG (START.DATE, END.DATE 99999999 = open) and not from the act's own metadata.
    assert {(p.valid_from, p.valid_to) for p in doc.provisions} == {(date(2016, 5, 4), None)}
    assert all("Consolidated version applying from 2016-05-04" in (p.note or "") for p in doc.provisions)
    assert doc.outstanding_changes == []  # the amendments are in the text
    kinds = {e.event_type: e.date for e in doc.events}
    assert kinds["entry_into_force"] == date(2016, 5, 24) and kinds["application"] == date(2018, 5, 25)
    # Corrigendum applied in the consolidated text: "or" where the Official Journal text of 2016 said "and".
    assert "Article 9 or personal data relating to criminal convictions" in _p(doc, "art_37__para_1__point_c").text
    adopted = parse_eu_cellar(docs["cellar:32008R0593:en"].content)[0]
    assert adopted.validity_basis == "source_periods" and adopted.consolidation_kind == "as_adopted"


def test_consolidated_versions_cite_like_the_act_in_every_language(
    batched: tuple[dict[str, RawDocument], CellarConnector],
) -> None:
    docs, _ = batched
    expected = {
        "en": "Regulation (EU) 2016/679, art 6(1)(a)",
        "fr": "Règlement (UE) 2016/679, art. 6, par. 1, point a)",
        "de": "Verordnung (EU) 2016/679, Art. 6 Abs. 1 Buchst. a",
    }
    for lang, citation in expected.items():
        (doc,) = parse_eu_cellar(docs[f"cellar:{CONS}:{lang}"].content)
        assert _cite(doc, "art_6__para_1__point_a") == citation and doc.language == lang
        assert {p.eid for p in doc.provisions} >= {"art_1", "art_6", "art_37", "art_41"}
    # All three language versions share the identifiers, the dates and the work.
    versions = [parse_eu_cellar(docs[f"cellar:{CONS}:{lang}"].content)[0] for lang in expected]
    assert len({d.work_uri for d in versions}) == 1 and len({tuple(p.eid for p in d.provisions) for d in versions}) == 1


def test_a_consolidated_bundle_without_start_date_or_base_is_rejected(
    batched: tuple[dict[str, RawDocument], CellarConnector],
) -> None:
    docs, _ = batched
    bundle = json.loads(docs[f"cellar:{CONS}:en"].content)
    for part in bundle["parts"]:
        if part["role"] == "item:fmx4" and "CONS.ACT" in part["content"]:
            part["content"] = part["content"].replace(' START.DATE="20160504"', "")
            part["sha256"] = hashlib.sha256(part["content"].encode()).hexdigest()
    with pytest.raises(EuParseError, match="START.DATE"):
        parse_eu_cellar(json.dumps(bundle).encode())
    no_base = json.loads(docs[f"cellar:{CONS}:en"].content)
    no_base["content_format"] = "xhtml"
    with pytest.raises(EuParseError, match="Formex 4 with its base act"):
        parse_eu_cellar(json.dumps(no_base).encode())


@pytest.mark.db
def test_a_consolidated_version_is_ingested_searched_and_resolved_by_date(
    session: Session,
    catalog: Catalog,
    store: LocalObjectStore,
    eu_ingested: IngestionReport,
    batched: tuple[dict[str, RawDocument], CellarConnector],
) -> None:
    docs, _ = batched
    # Ingested in the test's own session, which is rolled back afterwards.
    consolidated = {k: v for k, v in docs.items() if CONS in k}
    report = Ingestor(session, catalog, store).ingest_source(
        "eu-eur-lex", actor="pytest", connector=_Fixtures(consolidated)
    )
    assert report.errors == [] and report.conflicts == [] and report.instruments_created == 1
    assert report.instrument_versions_created == 3 and report.provision_versions_created == 3 * 44
    res = _search(
        session,
        catalog,
        q="has given consent to the processing of his or her personal data for one or more specific purposes",
        as_of=date(2026, 10, 14),
        query_language="en",
    )
    hit = _hit(res, "art_6__para_1__point_a")
    assert hit["citation"] == "Regulation (EU) 2016/679, art 6(1)(a)"
    assert hit["version"]["valid_from"] == "2016-05-04" and hit["version"]["text_origin"] == "official_revised"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"SNAPSHOT_DATES", "REVISED_TEXT", "SOURCE_NOT_AUTHENTIC"} <= codes
    assert "NOT_AUTHENTICATED" not in codes or hit["version"]["text_origin"] == "official_revised"
    assert {p["citation"] for p in hit["parallel_versions"]} == {
        "Règlement (UE) 2016/679, art. 6, par. 1, point a)",
        "Verordnung (EU) 2016/679, Art. 6 Abs. 1 Buchst. a",
    }
    uri = "/eu/reg/2016/679/~art_6__para_1__point_a"
    on = provision_at(session, catalog, uri, date(2020, 1, 1), language="en")
    assert on["status_on_date"] == "in_force" and on["version"]["valid_from"] == "2016-05-04"
    before = provision_at(session, catalog, uri, date(2015, 1, 1), language="en")
    assert before["status_on_date"] in ("history_not_indexed", "not_yet_in_force") and before["version"] is None
    # The corrected wording of the consolidated text is what is stored.
    point_c = provision_at(
        session, catalog, "/eu/reg/2016/679/~art_37__para_1__point_c", date(2020, 1, 1), language="en"
    )
    assert "Article 9 or personal data" in point_c["version"]["text"]


def test_formex_with_unnumbered_enacting_terms_and_with_several_acts(bundles: dict[str, RawDocument]) -> None:
    """Constructed minimal Formex 4 files for the two shapes the random sample of real acts showed."""
    from ulis.sources.parsers.eu_cellar import Part, parse_formex

    unnumbered = (
        "<ACT><TITLE><TI><P>Decision A</P></TI></TITLE>"
        "<ENACTING.TERMS><P>The Commission has decided as follows.</P></ENACTING.TERMS></ACT>"
    )
    doc, provs = parse_formex([Part("item:fmx4", "u1", unnumbered.encode())])
    assert [(p.eid, p.text) for p in provs] == [("terms", "The Commission has decided as follows.")]

    def act(title: str) -> str:
        return (
            f"<ACT><TITLE><TI><P>{title}</P></TI></TITLE><ENACTING.TERMS>"
            '<ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART><ALINEA>Text of ' + title + ".</ALINEA></ARTICLE>"
            "</ENACTING.TERMS></ACT>"
        )

    doc, provs = parse_formex(
        [Part("item:fmx4", "u1", act("First").encode()), Part("item:fmx4", "u2", act("Second").encode())]
    )
    assert doc.title == "First" and [p.eid for p in provs] == ["art_1", "art_1-2"]
    assert provs[0].context is None and provs[1].context == "Second"
    empty = "<ACT><TITLE><TI><P>X</P></TI></TITLE><ENACTING.TERMS/></ACT>"
    with pytest.raises(ParseSkipped):
        parse_eu_cellar(_bundle_with_formex(bundles["cellar:32008R0593:en"].content, empty))


def _bundle_with_formex(content: bytes, xml: str) -> bytes:
    """A bundle of the Rome I fixture whose Formex file is replaced by ``xml`` (to reach the parser's guards)."""
    import copy

    raw = json.loads(content)
    parts = copy.deepcopy(raw["parts"])
    for part in parts:
        if part["role"] == "item:fmx4":
            part["content"], part["encoding"] = xml, "utf-8"
            part["sha256"] = hashlib.sha256(xml.encode()).hexdigest()
    raw["parts"] = parts[:4]
    return json.dumps(raw).encode()


def test_a_consolidated_file_gives_the_number_year_and_domain_of_the_act(
    batched: tuple[dict[str, RawDocument], CellarConnector],
) -> None:
    from ulis.sources.parsers.eu_cellar import Part, load_bundle, parse_formex

    bundle, parts = load_bundle(batched[0][f"cellar:{CONS}:en"].content)
    doc, _ = parse_formex([p for p in parts if p.role.startswith("item:")])
    assert (doc.number, doc.year, doc.domain_code) == ("679", 2016, "EU")
    assert (doc.cons_start, doc.cons_end, doc.cons_date) == (date(2016, 5, 4), None, date(2018, 5, 23))
    assert isinstance(parts[0], Part) and bundle["base_celex"] == "32016R0679"


# --------------------------------------------------------------------------- acts held as XHTML or legacy HTML
# (about 7,100 acts in force have no Formex 4 file; see docs/jurisdictions/eu.md)


@pytest.fixture(scope="module")
def text_acts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, RawDocument]:
    cache = _cache_copy(tmp_path_factory.mktemp("eu-text") / "cache")
    connector = CellarConnector(FIX / "manifest-text.yaml", "eu-eur-lex", cache, offline=True)
    docs = {raw.external_id: raw for raw in connector.fetch()}
    assert connector.http is not None and connector.http.requests_made == 0 and connector.fetch_errors == []
    return docs


def test_legacy_html_act_is_structured_from_its_paragraphs(text_acts: dict[str, RawDocument]) -> None:
    raw = text_acts["cellar:32002L0059:en"]
    bundle = json.loads(raw.content)
    assert bundle["content_format"] == "html" and [p["role"] for p in bundle["parts"]] == [
        "sparql:metadata",
        "sparql:changes",
        "sparql:items:html",
        "item:html",
    ]
    (doc,) = parse_eu_cellar(raw.content)
    assert (doc.instrument_type, doc.work_uri, doc.short_title, doc.number) == (
        "directive",
        "/eu/dir/2002/59",
        "Directive 2002/59/EC",
        "2002/59",
    )
    assert doc.ext == {
        "celex": "32002L0059",
        "eli": "http://data.europa.eu/eli/dir/2002/59/oj",
        "resource_type": "DIR",
        "act_type": "directive",
        "year": 2002,
        "number": "59",
        "domain": "EC",
    }
    assert (doc.text_origin, doc.authoritative, doc.validity_basis, doc.consolidation_kind) == (
        "official_conversion",
        True,
        "source_periods",
        "as_adopted",
    )
    assert doc.version_date == date(2002, 6, 27) and doc.expression_uri == "/eu/dir/2002/59/eng@2002-06-27"
    kinds = {k: sum(p.kind == k for p in doc.provisions) for k in ("article", "paragraph", "annex")}
    assert kinds == {"article": 32, "paragraph": 54, "annex": 4} and len(doc.provisions) == 90
    assert [p.eid for p in doc.provisions][:5] == ["art_1", "art_2", "art_2__para_1", "art_2__para_2", "art_3"]
    assert [p.eid for p in doc.provisions][-5:] == ["art_32", "anx_I", "anx_II", "anx_III", "anx_IV"]
    # The heading of an article is the short unpunctuated paragraph after "Article N"; the text starts after it.
    art2 = _p(doc, "art_2")
    assert art2.heading == "Scope" and art2.text.startswith("1. This Directive applies to ships of 300 gross tonnage")
    assert not art2.is_leaf and not art2.has_own_content
    assert _p(doc, "art_2__para_1").text == (
        "This Directive applies to ships of 300 gross tonnage and upwards, unless stated otherwise."
    )
    assert _p(doc, "art_2__para_2").text.startswith("This Directive shall not apply to:\n(a) warships, naval")
    # Divisions ("TITLE I" and its heading line) are the context of the articles that follow them.
    assert _p(doc, "art_3").context_heading is None
    assert _p(doc, "art_4").context_heading == "TITLE I SHIP REPORTING AND MONITORING"
    assert _p(doc, "art_4").heading == "Notification prior to entry into ports of the Member States"
    assert _p(doc, "art_32").context_heading == "TITLE IV ACCOMPANYING MEASURES"
    assert _p(doc, "art_32").text == "This Directive is addressed to the Member States."
    # Preamble, final formula and signatures are not indexed; annexes are one unit each.
    joined = "\n".join(p.text for p in doc.provisions)
    assert "HAVE ADOPTED THIS DIRECTIVE" not in joined and "Done at Luxembourg" not in joined
    assert "Whereas" not in joined.split("\n")[0]
    anx1 = _p(doc, "anx_I")
    assert anx1.kind == "annex" and anx1.top_kind == "annex"
    assert anx1.text.startswith("LIST OF INFORMATION TO BE NOTIFIED")
    # Dates and changes come from the metadata exactly as for Formex acts.
    assert {p.valid_from for p in doc.provisions} == {date(2002, 8, 5)}
    assert [e.event_type for e in doc.events][:2] == ["document", "entry_into_force"]
    assert _p(doc, "anx_IV").valid_to == date(2011, 3, 16) and _p(doc, "art_3").valid_to == date(2009, 5, 31)
    assert len(doc.outstanding_changes) == 34
    assert (_p(doc, "art_1").note or "").startswith(
        "Text from the Publications Office's legacy EUR-Lex HTML rendition of the Official Journal"
    )
    assert _cite(doc, "art_4__para_1") == "Directive 2002/59/EC, art 4(1)"
    assert _cite(doc, "anx_II") == "Directive 2002/59/EC, Annex II"


def test_xhtml_act_with_attached_documents(text_acts: dict[str, RawDocument]) -> None:
    raw = text_acts["cellar:32006D0245:en"]
    assert json.loads(raw.content)["content_format"] == "xhtml"
    (doc,) = parse_eu_cellar(raw.content)
    assert (doc.instrument_type, doc.work_uri, doc.short_title, doc.number) == (
        "decision",
        "/eu/dec/2006/245",
        "Decision 2006/245/EC",
        "2006/245",
    )
    assert doc.title.startswith("2006/245/EC: Council Decision of 27 February 2006 on the conclusion")
    assert doc.version_date == date(2006, 4, 3) and doc.work_date == date(2006, 2, 27)
    assert [p.eid for p in doc.provisions] == [
        "art_1",
        "art_2",
        "att_1",
        "anx_I",
        "anx_II",
        "anx_III",
        "att_2",
        "att_3",
        "att_4",
    ]
    assert _p(doc, "art_1").text.startswith("The Protocol to the Agreement between the European Community")
    art2 = _p(doc, "art_2")
    assert art2.text.startswith("The President of the Council shall") and "(2)" not in art2.text
    assert "OJ" in (art2.note or "")  # the footnote called in the text is kept as a note
    # A document that follows the signature (the Protocol, the declarations) is one unit each, its title the heading;
    # its own annexes are annex units.
    protocol = _p(doc, "att_1")
    assert protocol.kind == "attachment" and protocol.top_kind == "annex" and protocol.is_leaf
    assert (protocol.heading or "").startswith("PROTOCOL to the Agreement between the European Community")
    assert protocol.text.startswith("THE KINGDOM OF BELGIUM,\nTHE CZECH REPUBLIC,")
    assert _p(doc, "anx_I").text.startswith("TRANSITIONAL MEASURES ON THE PURCHASE OF LAND AND SECONDARY RESIDENCE")
    assert (_p(doc, "att_2").heading or "").startswith("Declaration by Switzerland concerning the recognition")
    assert {p.valid_from for p in doc.provisions} == {date(2006, 2, 27)} and doc.outstanding_changes == []
    assert "OJ XHTML rendition" in (_p(doc, "art_1").note or "")
    assert _cite(doc, "art_2") == "Decision 2006/245/EC, art 2"
    assert _cite(doc, "att_1") == "Decision 2006/245/EC"  # an attached document is cited by the instrument


def _bundle_with_text(content: bytes, fmt: str, body: str) -> bytes:
    """A bundle of the Rome I fixture whose file is replaced by ``body`` in format ``fmt`` (xhtml or html)."""
    import copy

    raw = json.loads(content)
    parts = copy.deepcopy(raw["parts"])
    for part in parts:
        if part["role"].startswith("sparql:items:"):
            part["role"] = f"sparql:items:{fmt}"
        if part["role"] == "item:fmx4":
            part["role"], part["content"], part["encoding"] = f"item:{fmt}", body, "utf-8"
            part["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    raw["parts"], raw["content_format"] = parts[:4], fmt
    return json.dumps(raw).encode()


_LEGACY = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><html lang="EN"><head>
<meta name="DC.description" content="Regulation (EEC) No 1/80 of the Council  ">
<title>EUR-Lex - 31980R0001 - EN</title></head>
<body><div id="banner"><p class="bglang"><a href="x">Avis juridique important</a></p></div><h1>31980R0001</h1>
<p><strong>Regulation (EEC) No 1/80 of the Council</strong></p><div id="TexteOnly"><p><TXT_TE>
<p>COUNCIL REGULATION (EEC) No 1/80</p><p> THE COUNCIL OF THE EUROPEAN COMMUNITIES, </p>
<p> Whereas a test is needed; </p>
<p> HAS ADOPTED THIS REGULATION:</p><p> </p><p> TITLE I</p><p> GENERAL PROVISIONS</p><p>   Article 1</p><p> Scope</p>
<p> 1. This Regulation applies to &quot;tests&quot;. </p>
<p> 2.    (a) It applies from 1 January 1980. (1)OJ No L 1, 1.1.1980, p. 1. </p>
<p> The second paragraph continues here. 3. Not a paragraph label: out of sequence. </p><p> 3. A third paragraph. </p>
<p> CHAPTER II</p><p> SPECIAL RULES</p><p> Article 2</p>
<p> This Regulation shall enter into force on 1 January 1980. </p>
<p> Done at Brussels, 1 January 1980.</p><p> For the Council</p><p> The President</p><p> ANNEX I</p><p> LIST</p>
<p>>PIC FILE= "T0000001"></p><p> Item one | Item two </p><p> ANNEX II</p><p> Second annex text. </p>
</div></body></html>"""


def test_legacy_html_structure_rules(bundles: dict[str, RawDocument]) -> None:
    from ulis.sources.parsers.eu_cellar import Part, parse_text_act

    doc, provs = parse_text_act([Part("item:html", "u1", _LEGACY.encode())], "html")
    assert doc.title == "Regulation (EEC) No 1/80 of the Council"  # DC.description, no doc-ti in this format
    assert [p.eid for p in provs] == [
        "art_1",
        "art_1__para_1",
        "art_1__para_2",
        "art_1__para_3",
        "art_2",
        "anx_I",
        "anx_II",
    ]
    art1, p1, p2, p3, art2, anx1, anx2 = provs
    assert art1.heading == "Scope" and art1.context == "TITLE I GENERAL PROVISIONS"
    assert p1.text == 'This Regulation applies to "tests".'  # entities decoded, whitespace normalised
    # A label out of sequence inside a paragraph stays in its text; footnotes printed in the text stay there too.
    assert p2.text == (
        "(a) It applies from 1 January 1980. (1)OJ No L 1, 1.1.1980, p. 1.\n"
        "The second paragraph continues here. 3. Not a paragraph label: out of sequence."
    )
    assert p3.text == "A third paragraph." and art1.text.startswith('1. This Regulation applies to "tests".\n2.')
    assert art2.context == "TITLE I GENERAL PROVISIONS — CHAPTER II SPECIAL RULES" and art2.heading is None
    assert art2.text == "This Regulation shall enter into force on 1 January 1980."
    assert anx1.text == "LIST\nItem one | Item two" and anx2.text == "Second annex text."
    assert doc.notes and "placeholder" in doc.notes[0]
    assert "Whereas" not in "".join(p.text for p in provs) and "Done at" not in "".join(p.text for p in provs)

    # Enacting terms without a numbered article: one provision.
    plain = _LEGACY.split("<p> TITLE I</p>")[0] + (
        "<p> The Council decides.</p><p> Done at Brussels.</p></div></body></html>"
    )
    _, provs = parse_text_act([Part("item:html", "u2", plain.encode())], "html")
    assert [(p.eid, p.text) for p in provs] == [("terms", "The Council decides.")]
    with pytest.raises(EuParseError, match="no text found"):
        parse_text_act([Part("item:html", "u3", b"<html><body><p>nothing</p></body></html>")], "html")
    only_preamble = b'<html><body><div id="TexteOnly"><p>Whereas.</p></div></body></html>'
    with pytest.raises(EuParseError, match="no articles"):
        parse_text_act([Part("item:html", "u4", only_preamble)], "html")


def _point(label: str, text: str, inner: str = "") -> str:
    return (
        f'<table><tbody><tr><td><p class="oj-normal">{label}</p></td>'
        f'<td><p class="oj-normal">{text}</p>{inner}</td></tr></tbody></table>'
    )


_XHTML = (
    '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML//EN" "xhtml-strict.dtd">\n'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>\n'
    '<p class="oj-doc-ti">COUNCIL DECISION</p><p class="oj-doc-ti">of 1 January 2005</p>\n'
    '<p class="oj-normal">Whereas tests are needed,</p><p class="oj-normal">HAS DECIDED AS FOLLOWS:</p>\n'
    '<p class="oj-ti-section-1">CHAPTER I</p><p class="oj-ti-section-2">SCOPE</p>\n'
    '<div class="eli-subdivision" id="art_1"><p class="oj-ti-art">Article 1</p><p class="oj-sti-art">Definitions</p>\n'
    '<div id="001.001"><p class="oj-normal">1.   For the purposes of this Decision:</p>\n'
    + _point("(a)", '‘test’ means a check<a href="#ntr1-x">(<span>1</span>)</a>;', _point("(i)", "of any kind."))
    + _point("(b)", "‘check’ means a test.")
    + "</div>\n"
    '<div id="001.002"><p class="oj-normal">2.   Dashes stay in the text:</p>\n'
    + _point("—", "first indent.")
    + "</div></div>\n"
    '<div class="eli-subdivision" id="art_2"><p class="oj-ti-art">Article 2</p>\n'
    '<div class="oj-quotation"><p class="oj-ti-art">Article 10</p><p class="oj-normal">quoted text</p></div>\n'
    '<div class="oj-enumeration-spacing"><p style="display: inline;">1. </p>'
    '<p style="display: inline;"><span>Enumerated line.</span></p></div></div>\n'
    '<div class="oj-final"><p class="oj-normal">Done at Brussels, 1 January 2005.</p>'
    '<p class="oj-signatory">For the Council</p></div>\n'
    '<p class="oj-note"><a id="ntr1-x" href="#ntc1-x">(<span>1</span>)</a> OJ L 1, 1.1.2005, p. 1.</p>\n'
    '<div id="L_2005001EN.01000201"><p class="oj-doc-ti">ANNEX I</p><p class="oj-doc-ti">ANNEX Ia</p>'
    '<p class="oj-normal">Rates</p>\n'
    '<table class="oj-table"><tbody><tr><td><p>Code</p></td><td><p>Rate</p></td><td><p>Note</p></td></tr>\n'
    "<tr><td><p>0101</p></td><td><p>10 %</p></td><td></td></tr></tbody></table></div>\n"
    '<div id="L_2005001EN.01000301"><p class="oj-doc-ti">AGREEMENT</p><p class="oj-doc-ti">on tests</p>\n'
    '<p class="oj-ti-art">Article 1</p><p class="oj-normal">The Parties agree.</p></div>\n'
    "</body></html>"
)


def test_xhtml_structure_rules(bundles: dict[str, RawDocument]) -> None:
    from ulis.sources.parsers.eu_cellar import Part, parse_text_act

    doc, provs = parse_text_act([Part("item:xhtml", "u1", _XHTML.encode())], "xhtml")
    assert doc.title == "COUNCIL DECISION of 1 January 2005"
    assert [p.eid for p in provs] == [
        "art_1",
        "art_1__para_1",
        "art_1__para_1__point_a",
        "art_1__para_1__point_a__subpoint_i",
        "art_1__para_1__point_b",
        "art_1__para_2",
        "art_2",
        "art_2__para_1",
        "anx_IA",
        "att_1",
    ]
    by = {p.eid: p for p in provs}
    assert by["art_1"].heading == "Definitions" and by["art_1"].context == "CHAPTER I SCOPE"
    assert by["art_1__para_1"].text == (
        "For the purposes of this Decision:\n(a) ‘test’ means a check;\n(i) of any kind.\n(b) ‘check’ means a test."
    )
    point_a = by["art_1__para_1__point_a"]
    assert point_a.text == "‘test’ means a check;\n(i) of any kind." and not point_a.is_leaf
    assert point_a.note == ["OJ L 1, 1.1.2005, p. 1."]  # the footnote called in the text, as for the Treaties
    assert by["art_1__para_1__point_a__subpoint_i"].text == "of any kind." and by["art_1__para_1__point_b"].is_leaf
    assert by["art_1__para_2"].text == "Dashes stay in the text:\n— first indent." and by["art_1__para_2"].is_leaf
    assert by["art_1"].text.startswith("1. For the purposes of this Decision:\n(a) ‘test’")
    # Quoted text (an article inserted into another act) is text of the article; an inline enumeration that opens the
    # article's numbering is its first paragraph.
    assert by["art_2"].text == "Article 10\nquoted text\n1. Enumerated line." and by["art_2"].own_content
    assert by["art_2__para_1"].text == "Enumerated line."
    # "ANNEX I" with nothing of its own over "ANNEX Ia" is no unit; a table is one line per row, cells separated by |.
    assert by["anx_IA"].kind == "annex" and by["anx_IA"].text == "Rates\nCode | Rate | Note\n0101 | 10 % | "
    assert by["att_1"].kind == "attachment" and by["att_1"].heading == "AGREEMENT on tests"
    assert by["att_1"].text == "Article 1\nThe Parties agree."
    assert "Done at" not in "".join(p.text for p in provs) and "Whereas" not in "".join(p.text for p in provs)

    # Through the whole parser: the act's metadata (dates, type, ELI) comes from the bundle as for Formex.
    (parsed,) = parse_eu_cellar(_bundle_with_text(bundles["cellar:32008R0593:en"].content, "xhtml", _XHTML))
    assert parsed.instrument_type == "regulation" and parsed.work_uri == "/eu/reg/2008/593"
    assert parsed.short_title == "Regulation (EC) No 593/2008" and [p.eid for p in parsed.provisions][0] == "art_1"
    assert parsed.provisions[0].valid_from == date(2009, 12, 17)
    assert {p.top_kind for p in parsed.provisions} == {"article", "annex"}
    with pytest.raises(EuParseError, match="not an XHTML"):
        parse_text_act([Part("item:xhtml", "u2", b"<ACT/>")], "xhtml")
    with pytest.raises(EuParseError, match="no xhtml file"):
        parse_text_act([Part("item:fmx4", "u3", b"<ACT/>")], "xhtml")


def test_manifest_accepts_html_acts_but_not_html_consolidated_versions(tmp_path: Path) -> None:
    def works(*entries: dict[str, Any]) -> list[dict[str, Any]]:
        return list(entries)

    ok = _batch_manifest(tmp_path, works=works({"celex": "31964D0300", "format": "html"}))
    assert CellarConnector(ok, "eu-eur-lex", tmp_path / "c")._manifest()["works"][0]["format"] == "html"  # noqa: SLF001
    bad = _batch_manifest(
        tmp_path, works=works({"celex": "02016R0679-20160504", "base": "32016R0679", "format": "html"})
    )
    with pytest.raises(ManifestError, match="Formex 4"):
        CellarConnector(bad, "eu-eur-lex", tmp_path / "c")._manifest()  # noqa: SLF001
    assert query_items_batch(["31964D0300"], ["en"], "html").count('"html"') == 1


def test_takes_effect_dates_count_as_entry_into_force() -> None:
    from ulis.sources.parsers.eu_cellar import parse_metadata

    rows = [
        {"work": "w", "p": f"{CDM}resource_legal_id_celex", "o": "31964D0300"},
        {"work": "w", "p": f"{CDM}work_has_resource-type", "o": "http://x/resource-type/DEC"},
        {
            "work": "w",
            "p": f"{CDM}resource_legal_date_entry-into-force",
            "o": "1964-05-21",
            "ap": f"{ANN}type_of_date",
            "av": "{PE|http://x/fd_335/PE}",
        },
    ]
    meta = parse_metadata(rows, "31964D0300")
    assert [d.value for d in meta.entry_into_force] == [date(1964, 5, 21)] and meta.application == []


@pytest.mark.db
def test_text_acts_are_ingested_and_searched(
    session: Session,
    catalog: Catalog,
    store: LocalObjectStore,
    eu_ingested: IngestionReport,
    text_acts: dict[str, RawDocument],
) -> None:
    report = Ingestor(session, catalog, store).ingest_source(
        "eu-eur-lex", actor="pytest", connector=_Fixtures(text_acts)
    )
    assert report.errors == [] and report.conflicts == [] and report.instruments_created == 2
    assert report.instrument_versions_created == 2 and report.provision_versions_created == 90 + 9
    res = _search(
        session,
        catalog,
        q="vessel traffic monitoring and information system with a view to enhancing the safety",
        as_of=date(2005, 1, 1),
        query_language="en",
    )
    hit = _hit(res, "art_1")
    assert hit["citation"] == "Directive 2002/59/EC, art 1" and hit["version"]["valid_from"] == "2002-08-05"
    assert hit["version"]["text_origin"] == "official_conversion"
    assert {"NOT_AUTHENTICATED", "SOURCE_NOT_AUTHENTIC", "VERSION_NOTE"} <= {lim["code"] for lim in hit["limitations"]}
    assert "legacy EUR-Lex HTML rendition" in hit["version"]["note"]
    on = provision_at(session, catalog, "/eu/dir/2002/59/~art_4__para_1", date(2005, 1, 1), language="en")
    assert on["status_on_date"] == "in_force" and on["provision"]["citation"] == "Directive 2002/59/EC, art 4(1)"
    # Annex IV was replaced with effect from 2011-03-16: no current wording is indexed.
    later = provision_at(session, catalog, "/eu/dir/2002/59/~anx_IV", date(2012, 1, 1), language="en")
    assert later["status_on_date"] == "no_version" and later["timeline"][0]["valid_to"] == "2011-03-16"
    # An attached document (the Protocol) is searchable and cited by its instrument.
    res = _search(
        session,
        catalog,
        q="free movement of persons regarding the participation as contracting parties",
        as_of=date(2026, 10, 14),
        query_language="en",
    )
    hit = next(h for h in res["results"] if h["unit"]["eid"] == "att_1")
    assert hit["citation"] == "Decision 2006/245/EC"
