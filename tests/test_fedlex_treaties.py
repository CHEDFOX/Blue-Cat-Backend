"""Treaties from the Swiss Classified Compilation (ECHR, Statute of the Council of Europe, UN Charter) and the
sources of the international courts and treaty offices that are recorded but not ingested.

Fixtures are unmodified Fedlex files (see tests/fixtures/fedlex_treaties/README.md), gzip-compressed with the
fetcher's metadata in PoliteHttp's cache layout.
"""

from __future__ import annotations

import gzip
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.fedlex import FedlexConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.fedlex import FedlexParseError, parse_fedlex
from ulis.sources.parsers.fedlex_treaty import STATES, TREATIES, parse_fedlex_treaty, resolve_uri, work_uri
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "fedlex_treaties" / "cache"
STORE = "www.fedlex.admin.ch/filestore/fedlex.data.admin.ch/eli/cc"
MANIFEST = REPO_ROOT / "config" / "manifests" / "ch-fedlex-treaties.yaml"
ECHR = "1974/2151_2151_2151"
P6 = "1987/1807_1807_1807"
STATUTE = "1963/772_769_797"
CHARTER = "2003/160"


def _read(eli: str, day: str, lang: str) -> bytes:
    (path,) = (FIX / STORE / eli / day.replace("-", "") / lang / "xml").glob("*.xml.gz")
    return gzip.decompress(path.read_bytes())


def _doc(eli: str, day: str, lang: str):  # type: ignore[no-untyped-def]
    docs = parse_fedlex_treaty(_read(eli, day, lang))
    assert len(docs) == 1
    return docs[0]


def _parties(doc) -> dict[str, tuple[str | None, str | None, str | None]]:  # type: ignore[no-untyped-def]
    return {
        p.party: (p.consent_type, str(p.consented_on) if p.consented_on else None, str(p.entry_into_force_on))
        for p in doc.treaty_parties
    }


# --------------------------------------------------------------------------- parser


def test_convention_original_french_and_swiss_translations() -> None:
    fr, de, it = (_doc(ECHR, "2022-09-16", lang) for lang in ("fr", "de", "it"))
    for doc in (fr, de, it):
        assert (doc.instrument_type, doc.jurisdiction_id, doc.work_uri) == ("treaty", "COE", "/coe/treaty/ch-sr-0.101")
        assert doc.number == "0.101" and doc.version_date == date(2022, 9, 16) and doc.work_date == date(1950, 11, 4)
        assert doc.validity_basis == "revision_snapshot" and doc.authoritative is False
        assert doc.extent == [] and all(p.extent == [] for p in doc.provisions)
        assert doc.ext["eli"] == "https://fedlex.data.admin.ch/eli/cc/1974/2151_2151_2151"
    assert [d.short_title for d in (fr, de, it)] == ["CEDH", "EMRK", "CEDU"]
    assert [d.ext["original_text"] for d in (fr, de, it)] == [True, False, False]  # "Texte original" / "Übersetzung"
    assert fr.title.startswith("Convention du 4 novembre 1950 de sauvegarde des droits de l'homme")
    assert fr.expression_uri == "/coe/treaty/ch-sr-0.101/fr@2022-09-16"
    assert [p.eid for p in fr.provisions] == [p.eid for p in de.provisions] == [p.eid for p in it.provisions]
    art8 = {p.eid: p for p in fr.provisions}["art_8/para_1"]
    assert art8.text == (
        "Toute personne a droit au respect de sa vie privée et familiale, de son domicile et de sa correspondance."
    )
    assert art8.num_path == ["8", "para:1"] and art8.status == "in_force" and art8.valid_from == date(2022, 9, 16)
    assert {p.eid: p for p in fr.provisions}["art_8"].heading == "Droit au respect de la vie privée et familiale"
    # The wording is exact, including the non-breaking hyphen of the source (U+2011) in the German text.
    assert {p.eid: p for p in de.provisions}["art_8/para_1"].text == (
        "Jede Person hat das Recht auf Achtung ihres Privat\u2011 und Familienlebens, "
        "ihrer Wohnung und ihrer Korrespondenz."
    )
    # The scope table is read into parties, never stored as law text.
    assert not any("Entrée en vigueur" in p.text or "États parties" in p.text for p in fr.provisions)
    assert [e.event_type for e in fr.events] == ["version_change", "adoption"]


def test_scope_table_gives_parties_with_dates_in_every_language() -> None:
    docs = [_doc(ECHR, "2022-09-16", lang) for lang in ("fr", "de", "it")]
    assert _parties(docs[0]) == _parties(docs[1]) == _parties(docs[2])
    parties = _parties(docs[0])
    assert sorted(parties) == ["AT", "CH", "DE", "DK", "EE", "ES", "FI", "GB", "IE", "IT", "LV", "NL", "PL", "SE"]
    assert parties["DE"] == ("ratification", "1952-12-05", "1953-09-03")
    assert parties["AT"] == ("ratification", "1958-09-03", "1958-09-03")
    assert docs[0].ext["parties_in_table"] > 40 and docs[0].ext["parties_recorded"] == 14
    # Accession and succession markers ("A", "S") and a State that only has an entry-into-force date.
    statute = _parties(_doc(STATUTE, "2022-03-16", "fr"))
    assert statute["DE"] == ("accession", "1950-07-13", "1950-07-13")
    assert statute["SE"] == ("ratification", "1949-07-20", "1949-08-03")
    charter = _parties(_doc(CHARTER, "2015-06-23", "fr"))
    assert charter["DE"] == (None, None, "1973-09-18")
    assert charter["US"] == ("ratification", "1945-08-08", "1945-10-24")
    assert charter["CH"] == (None, None, "2002-09-10")
    assert _parties(_doc(CHARTER, "2015-06-23", "de")) == charter == _parties(_doc(CHARTER, "2015-06-23", "it"))


def test_italian_file_with_the_scope_table_as_an_annex() -> None:
    # In the Italian Protocol No 6 the scope table is an "annex" component: it is read as the scope table and is
    # not indexed as an annex provision. Its Netherlands row has no dates (listed by territory), so it is skipped.
    it, fr = _doc(P6, "2020-06-29", "it"), _doc(P6, "2020-06-29", "fr")
    assert not any(p.kind == "annex" for p in it.provisions)
    assert [p.eid for p in it.provisions] == [p.eid for p in fr.provisions]
    assert set(_parties(fr)) - set(_parties(it)) == {"NL"}
    assert _parties(it)["DE"] == _parties(fr)["DE"]


def test_unlisted_or_malformed_files_are_rejected_loudly() -> None:
    content = _read(ECHR, "2022-09-16", "fr")
    with pytest.raises(FedlexParseError, match="not well-formed"):
        parse_fedlex_treaty(b"<akomaNtoso")
    with pytest.raises(FedlexParseError, match="unsupported root"):
        parse_fedlex_treaty(b"<x/>")
    with pytest.raises(FedlexParseError, match="no scope table"):
        parse_fedlex_treaty(content.replace(b'<doc name="scope">', b'<doc name="declaration">'))
    with pytest.raises(FedlexParseError, match="neither"):
        parse_fedlex_treaty(content.replace(b"Texte original", b"Un texte"))
    with pytest.raises(FedlexParseError, match="not a treaty this parser reads"):
        parse_fedlex_treaty(content.replace(b"eli/cc/1974/2151_2151_2151/20220916", b"eli/cc/1974/9999/20220916"))
    # The Swiss federal-act parser refuses a treaty (it reads only the Constitution and federal acts).
    with pytest.raises(FedlexParseError):
        parse_fedlex(content)


def test_treaty_table_and_state_names() -> None:
    assert {t.jurisdiction for t in TREATIES.values()} == {"COE", "INT"}
    assert len({t.sr for t in TREATIES.values()}) == len(TREATIES) == 11
    assert (
        work_uri(TREATIES[ECHR]) == "/coe/treaty/ch-sr-0.101"
        and work_uri(TREATIES["2003/160"]) == "/int/treaty/ch-sr-0.120"
    )
    # Every State listed has its name in all three languages (profiles are checked in the next test).
    assert all(set(names) == {"de", "fr", "it"} for names in STATES.values())


def test_state_ids_are_profiles(catalog: Catalog) -> None:
    for jid in STATES:
        assert catalog.jurisdictions.get(jid).kind == "sovereign_state"


def test_resolve_uri_maps_listed_treaties_only() -> None:
    assert resolve_uri("https://fedlex.data.admin.ch/eli/cc/1974/2151_2151_2151") == ("/coe/treaty/ch-sr-0.101", None)
    assert resolve_uri("https://www.fedlex.admin.ch/eli/cc/2003/160/") == ("/int/treaty/ch-sr-0.120", None)
    assert resolve_uri("https://fedlex.data.admin.ch/eli/cc/1999/404") is None
    # In the pipeline's resolver a Swiss act still resolves to a Swiss work URI, a listed treaty to its own.
    assert pipeline_resolve_uri("https://fedlex.data.admin.ch/eli/cc/1999/404") == ("/ch/cc/1999/404", None)
    assert pipeline_resolve_uri("https://fedlex.data.admin.ch/eli/cc/2009/377") == ("/coe/treaty/ch-sr-0.101.094", None)


def test_citations_in_the_swiss_convention_for_each_language() -> None:
    for module_id in ("coe", "int"):
        module = get_module(module_id)
        cases = {"fr": "art. 8 al. 1 CEDH", "de": "Art. 8 Abs. 1 EMRK", "it": "art. 8 cpv. 1 CEDU"}
        short = {"fr": "CEDH", "de": "EMRK", "it": "CEDU"}
        for lang, expected in cases.items():
            c = ProvisionCitationInput(short[lang], "treaty", "COE", "article", ["8", "para:1"], lang)
            assert module.provision_citation(c) == expected
        letter = ProvisionCitationInput("EMRK", "treaty", "COE", "article", ["5", "para:1", "let:c"], "de")
        assert module.provision_citation(letter) == "Art. 5 Abs. 1 lit. c EMRK"
        ext = {
            "sr_number": "0.101",
            "eli": "https://fedlex.data.admin.ch/eli/cc/1974/2151_2151_2151",
            "original_text": True,
            "parties_in_table": 46,
            "parties_recorded": 14,
        }
        assert module.validate_ext(ext) == ext


# --------------------------------------------------------------------------- manifest and connector


def test_committed_manifest_is_valid_and_complete_for_the_treaty_table(tmp_path: Path) -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["source_id"] == "ch-fedlex-treaties" and manifest["languages"] == ["de", "fr", "it"]
    assert [w["eli"].removeprefix("https://fedlex.data.admin.ch/eli/cc/") for w in manifest["works"]] == list(TREATIES)
    assert {w["jurisdiction"] for w in manifest["works"]} == {"COE", "INT"}
    for w in manifest["works"]:
        assert w["sr"] == TREATIES[w["eli"].removeprefix("https://fedlex.data.admin.ch/eli/cc/")].sr
    # The connector's own validation accepts it (ELIs, one file per language, oldest first, public mapping).
    connector = FedlexConnector(MANIFEST, "ch-fedlex-treaties", tmp_path, offline=True)
    assert len(connector._manifest()["works"]) == 11
    with pytest.raises(ManifestError, match="source_id"):
        FedlexConnector(MANIFEST, "ch-fedlex", tmp_path, offline=True)._manifest()


def _cache_copy(dest: Path) -> Path:
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.xml.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _manifest(tmp_path: Path, works: list[tuple[str, list[str]]]) -> Path:
    full = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    chosen = []
    for eli, days in works:
        work = next(w for w in full["works"] if w["eli"].endswith("/" + eli))
        chosen.append({**work, "consolidations": [c for c in work["consolidations"] if str(c["date"]) in days]})
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({**full, "works": chosen}, allow_unicode=True))
    return path


def test_connector_reads_the_cache_offline_in_chronological_order(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    manifest = _manifest(tmp_path, [(ECHR, ["2022-02-01", "2022-09-16"])])
    docs = list(FedlexConnector(manifest, "ch-fedlex-treaties", cache, offline=True).fetch())
    assert [d.metadata["consolidation"] for d in docs] == ["2022-02-01"] * 3 + ["2022-09-16"] * 3
    assert docs[0].external_id == "fedlex:cc/1974/2151_2151_2151@2022-02-01:de"
    assert all(d.uri and d.uri.startswith("https://www.fedlex.admin.ch/filestore/") for d in docs)
    assert all(parse_fedlex_treaty(d.content)[0].version_date.isoformat() == d.metadata["consolidation"] for d in docs)


# --------------------------------------------------------------------------- sources that are not ingested


PLANNED = {
    "coe-echr-hudoc": "pending_review",
    "coe-treaty-office": "pending_review",
    "icj-cij": "pending_review",
    "icc-legal-texts": "pending_review",
    "iacthr-judgments": "pending_review",
    "african-court-decisions": "pending_review",
    "un-web-treaties": "rejected",
}


@pytest.mark.parametrize("source_id", sorted(PLANNED))
def test_sources_of_international_courts_and_treaty_offices_are_refused_by_the_gate(
    catalog: Catalog, source_id: str
) -> None:
    source = catalog.sources.get(source_id)
    assert source.status in ("planned", "rejected") and source.connector is None and source.parser is None
    assert source.licence.review_status == PLANNED[source_id]
    with pytest.raises(LicenceNotApproved, match=PLANNED[source_id]):
        licence_gate(source)
    assert source.licence.reviewed_on == date(2026, 10, 14) and "2026-10-14" in (source.licence.reviewed_by or "")


def test_recorded_findings_of_the_blocked_sources(catalog: Catalog) -> None:
    hudoc = catalog.sources.get("coe-echr-hudoc").licence
    assert hudoc.url == "https://www.echr.coe.int/copyright-and-disclaimer" and hudoc.commercial_use == "not_allowed"
    assert hudoc.bulk_download == "unknown" and "prior written permission" in (hudoc.reviewed_by or "")
    assert catalog.sources.get("iacthr-judgments").licence.id == "CC-BY-NC-ND-3.0"
    assert catalog.sources.get("icj-cij").licence.commercial_use == "not_allowed"
    assert "Cloudflare" in (catalog.sources.get("coe-treaty-office").rate_limits or "")
    assert "Browser Validation" in (catalog.sources.get("icc-legal-texts").rate_limits or "")
    assert "personal, non-commercial use" in (catalog.sources.get("un-web-treaties").licence.reviewed_by or "")
    assert catalog.sources.get("african-court-decisions").licence.id == "AFCHPR-ALL-RIGHTS-RESERVED"


def test_the_swiss_treaty_source_passes_the_gate_and_the_profiles_name_it(catalog: Catalog) -> None:
    licence_gate(catalog.sources.get("ch-fedlex-treaties"))
    for jid in ("COE", "INT"):
        assert "ch-fedlex-treaties" in catalog.jurisdictions.get(jid).publication.official_sources
    coe = catalog.jurisdictions.get("COE")
    assert coe.kind == "regional_organisation" and coe.parent == "INT" and [c.id for c in coe.courts] == ["COE-ECTHR"]
    assert coe.verification.status == "draft_unverified" and coe.coverage.corpus_status == "pilot"
    assert any("HUDOC" in gap for gap in coe.coverage.known_gaps)


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def treaties_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("treaties")
    cache = _cache_copy(tmp / "cache")
    manifest = _manifest(
        tmp,
        [
            (ECHR, ["2022-02-01", "2022-09-16"]),
            (P6, ["2020-06-29"]),
            (STATUTE, ["2022-03-16"]),
            (CHARTER, ["2015-06-23"]),
        ],
    )
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "ch-fedlex-treaties",
            actor="pytest",
            connector=FedlexConnector(manifest, "ch-fedlex-treaties", cache, offline=True),
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, jurisdiction: str, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction=jurisdiction, **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingestion_stores_treaties_with_parties(session: Session, treaties_ingested: IngestionReport) -> None:
    assert treaties_ingested.errors == [] and treaties_ingested.conflicts == []
    assert treaties_ingested.documents_new == 15 and treaties_ingested.instruments_created == 4
    echr = session.scalar(select(m.Instrument).where(m.Instrument.uri == "/coe/treaty/ch-sr-0.101"))
    assert echr is not None and echr.jurisdiction_id == "COE" and echr.instrument_type == "treaty"
    rows = {r.party_id: r for r in session.scalars(select(m.TreatyParty).where(m.TreatyParty.treaty_id == echr.id))}
    assert len(rows) == 14 and rows["DE"].consented_on == date(1952, 12, 5)
    assert rows["DE"].entry_into_force_on == date(1953, 9, 3) and rows["DE"].withdrawn_on is None
    charter = session.scalar(select(m.Instrument).where(m.Instrument.uri == "/int/treaty/ch-sr-0.120"))
    assert charter is not None and charter.jurisdiction_id == "INT"
    # Unchanged wording continues across the two ECHR consolidations: one version per provision and language.
    versions = session.scalars(
        select(m.ProvisionVersion)
        .join(m.Provision)
        .where(m.Provision.uri == "/coe/treaty/ch-sr-0.101/~art_8/para_1", m.ProvisionVersion.language == "fr")
    ).all()
    assert [(v.valid_from, v.valid_to) for v in versions] == [(date(2022, 2, 1), None)]


@pytest.mark.db
def test_point_in_time_follows_the_swiss_consolidations(
    session: Session, catalog: Catalog, treaties_ingested: IngestionReport
) -> None:
    uri = "/coe/treaty/ch-sr-0.101/~art_8/para_1"
    during = provision_at(session, catalog, uri, date(2022, 5, 1), language="fr")
    assert during["status_on_date"] == "in_force" and during["version"]["valid_from"] == "2022-02-01"
    assert during["provision"]["citation"] == "art. 8 al. 1 CEDH"
    gap = provision_at(session, catalog, uri, date(2015, 1, 1), language="fr")
    assert gap["status_on_date"] == "history_not_indexed"  # earliest ingested consolidation: 2022-02-01 in this test


@pytest.mark.db
def test_search_finds_the_convention_for_a_party_with_citation_and_limitations(
    session: Session, catalog: Catalog, treaties_ingested: IngestionReport
) -> None:
    res = _search(
        session,
        catalog,
        "DE",
        q="Toute personne a droit au respect de sa vie privée et familiale",
        as_of=date(2026, 1, 1),
        query_language="fr",
        languages=["fr"],
    )
    # Other corpora in the shared test database (the EU Charter art. 7 for DE) may rank first on the same words.
    hit = next(r for r in res["results"] if r["citation"] == "art. 8 al. 1 CEDH")
    assert hit["citation"] == "art. 8 al. 1 CEDH" and hit["version"]["language"] == "fr"
    assert hit["authority"]["category"] == "treaty_obligation"
    assert "In force for DE since 1953-09-03" in hit["authority"]["rationale"]
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"SOURCE_NOT_AUTHENTIC", "NOT_AUTHENTIC_TEXT", "SNAPSHOT_DATES", "PROFILE_UNVERIFIED", "AUTHORITY"} <= codes
    assert hit["source"]["attribution"].startswith("Dies ist keine amtliche Veröffentlichung.")
    de = _search(
        session,
        catalog,
        "CH",
        q="Recht auf Achtung des Privat- und Familienlebens",
        as_of=date(2026, 1, 1),
        query_language="de",
        languages=["de"],
    )
    assert "Art. 8 Abs. 1 EMRK" in [r["citation"] for r in de["results"]]


@pytest.mark.db
def test_a_state_that_is_not_a_party_does_not_get_the_convention(
    session: Session, catalog: Catalog, treaties_ingested: IngestionReport
) -> None:
    q = "Toute personne a droit au respect de sa vie privée et familiale"
    res = _search(session, catalog, "US", q=q, as_of=date(2026, 1, 1), query_language="fr", languages=["fr"])
    assert not [r for r in res["results"] if r["citation"].endswith("CEDH")]
    foreign = _search(
        session, catalog, "US", q=q, as_of=date(2026, 1, 1), query_language="fr", languages=["fr"], include_foreign=True
    )
    top = next(r for r in foreign["results"] if r["citation"].endswith("CEDH"))
    assert top["authority"]["category"] == "foreign"


@pytest.mark.db
def test_charter_entry_into_force_decides_the_authority_by_date(
    session: Session, catalog: Catalog, treaties_ingested: IngestionReport
) -> None:
    q = "menace ou à l’emploi de la force contre l’intégrité territoriale"
    now = _search(session, catalog, "JP", q=q, as_of=date(2026, 1, 1), query_language="fr", languages=["fr"])
    hit = next(r for r in now["results"] if r["citation"].startswith("art. 2 al. 4 "))
    assert hit["citation"] == "art. 2 al. 4 Charte des Nations Unies du 26 juin 1945"
    assert hit["authority"]["category"] == "treaty_obligation" and "since 1956-12-18" in hit["authority"]["rationale"]
    # Before it came into force for Japan the same provision is not in force for it.
    earlier = _search(session, catalog, "JP", q=q, as_of=date(1950, 1, 1), query_language="fr", languages=["fr"])
    assert not earlier["results"] or earlier["results"][0]["authority"]["category"] != "treaty_obligation"
