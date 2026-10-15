"""Lilex (Liechtenstein): parser, connector, citations and search behaviour (real pages of www.gesetze.li)."""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.lilex import BUNDLE_SCHEMA, LilexConnector, bundle_bytes, law_page_info
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.legal_html import HtmlParseError
from ulis.sources.parsers.lilex import abbreviation_of, parse_lilex, version_dates
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "lilex"
SOURCE = "li-lilex"
HOST = "www.gesetze.li"
RETRIEVED = "2026-10-14T13:00:00+00:00"
TODAY = date(2026, 10, 14)


def _gz(name: str) -> bytes:
    return gzip.decompress((FIX / name).read_bytes())


# (fixture, LGBl-Nr, version number, dates as printed in the version list)
VERSIONS = {
    "glg6": ("glg-1999-096-v6.html.gz", "1999.096", 6, "01.01.2017 - 31.12.2021"),
    "glg7": ("glg-1999-096-v7.html.gz", "1999.096", 7, "01.01.2022"),
    "lv": ("verfassung-1921-015-v45.html.gz", "1921.015", 45, "01.01.2026"),
    "urg": ("urg-1999-160-v10.html.gz", "1999.160", 10, "01.01.2026"),
}


def _bundle(key: str) -> bytes:
    fixture, lgbl, version, dates = VERSIONS[key]
    return bundle_bytes(
        {
            "schema": BUNDLE_SCHEMA,
            "lgbl": lgbl,
            "chrono_id": f"{lgbl[:4]}{int(lgbl[5:]):03d}000",
            "version": version,
            "dates": dates,
            "html": _gz(fixture).decode("utf-8"),
        }
    )


def _doc(key: str) -> ParsedDocument:
    docs = parse_lilex(_bundle(key))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    return get_module("li").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="LI",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="de",
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_version_dates() -> None:
    assert version_dates("01.01.2026") == (date(2026, 1, 1), None)
    assert version_dates("01.02.2021 - 31.12.2025") == (date(2021, 2, 1), date(2025, 12, 31))
    with pytest.raises(HtmlParseError):
        version_dates("seit gestern")


def test_abbreviations_come_from_the_title() -> None:
    assert abbreviation_of("Gesetz vom 19. Mai 1999 über das Urheberrecht (Urheberrechtsgesetz, URG)") == "URG"
    assert abbreviation_of("Strafgesetzbuch (StGB) vom 24. Juni 1987") == "StGB"
    assert abbreviation_of("Gesetz vom 25. Oktober 2006 (Behindertengleichstellungsgesetz; BGlG)") == "BGlG"
    assert abbreviation_of("Verfassung des Fürstentums Liechtenstein vom 5. Oktober 1921") is None
    assert abbreviation_of("Gesetz vom 1. September 1919 (LGBl. 1864 Nr. 4) ergänzt") is None


def test_glg_document_facts_and_version_period() -> None:
    old, new = _doc("glg6"), _doc("glg7")
    assert old.work_uri == new.work_uri == "/li/lgbl/1999.096"
    assert old.expression_uri == "/li/lgbl/1999.096/deu@2017-01-01"
    assert new.expression_uri == "/li/lgbl/1999.096/deu@2022-01-01"
    assert old.title == "Gesetz vom 10. März 1999 über die Gleichstellung von Frau und Mann (Gleichstellungsgesetz, GLG)"
    assert (old.instrument_type, old.language, old.jurisdiction_id) == ("act", "de", "LI")
    assert old.short_title == "GLG" and old.number == "1999.096" and old.work_date == date(1999, 3, 10)
    # consolidation for information, no legal effect; every version is a whole-law snapshot
    assert old.authoritative is False and old.text_origin == "official_consolidation"
    assert old.validity_basis == "revision_snapshot" and old.consolidation_kind == "point_in_time"
    assert old.currency_date is None and old.outstanding_changes == []
    assert old.ext["lr_number"] == "105.1" and old.ext["abbreviation"] == "GLG"
    assert old.ext["published_in"] == "LGBl. 1999 Nr. 96" and old.ext["issued_on"] == "1999-05-05"
    assert old.ext["version_number"] == 6 and old.ext["version_label"] == "01.01.2017 - 31.12.2021"
    # "01.01.2017 - 31.12.2021": the day after the last day is the exclusive end; the last version is open
    assert old.version_date == date(2017, 1, 1)
    assert all(p.valid_from == date(2017, 1, 1) and p.valid_to == date(2022, 1, 1) for p in old.provisions)
    assert all(p.valid_from == date(2022, 1, 1) and p.valid_to is None for p in new.provisions)
    assert {e.event_type for e in old.events} == {"enactment", "version_change"}


def test_structure_labels_and_context() -> None:
    doc = _doc("glg6")
    art = _prov(doc, "art-4a")
    assert art.kind == "article" and art.top_kind == "article" and art.num_path == ["art:4a"]
    assert art.context_heading == "III. Gleichstellung beim Zugang zu und bei der Versorgung mit Gütern und Dienstleistungen"
    para = _prov(doc, "art-4a-abs5")
    assert para.kind == "paragraph" and para.num_path == ["art:4a", "abs:5"] and para.parent_eid == "art-4a"
    assert para.text.startswith("Keine Diskriminierung liegt vor, wenn:\na) angemessene Massnahmen")
    assert para.has_own_content and not para.is_leaf
    letter = _prov(doc, "art-4a-abs5-bstc")
    assert letter.kind == "point" and letter.num_path == ["art:4a", "abs:5", "bst:c"]
    number = _prov(doc, "art-4a-abs5-bstc-ziff1")
    assert number.kind == "subpoint" and number.num_path == ["art:4a", "abs:5", "bst:c", "ziff:1"]
    assert number.text == "das Geschlecht ein bestimmender Faktor in einer Risikobewertung ist;" and number.is_leaf
    # the numbering is part of the composite text of the parent, not of the point itself
    assert "\n1. das Geschlecht ein bestimmender Faktor" in letter.text
    assert not number.text.startswith("1.")


def test_footnotes_are_notes_not_text() -> None:
    doc = _doc("glg7")
    first = _prov(doc, "art-1-abs2")
    assert first.text == (
        "Es bezweckt die Gleichstellung von Frauen und Männern in der Arbeitswelt sowie beim Zugang zu und bei der "
        "Versorgung mit Gütern und Dienstleistungen."
    )
    assert first.note == "Art. 1 Abs. 2 eingefügt durch LGBl. 2011 Nr. 212."
    assert all(not re.search(r"\w\d{1,3}$", p.text.split("\n")[0]) or p.kind == "annex" for p in doc.provisions[:30])
    assert _prov(doc, "art-1").heading == "Gegenstand und Zweck"
    assert _prov(doc, "art-1").note == "Art. 1 Sachüberschrift abgeändert durch LGBl. 2011 Nr. 212."


def test_repealed_places_keep_their_number_and_the_repeal_note() -> None:
    old, new = _doc("glg6"), _doc("glg7")
    gone = _prov(new, "art-4a-abs5-bstc")
    assert gone.status == "repealed" and gone.text == ""
    assert gone.note == "Art. 4a Abs. 5 Bst. c aufgehoben durch LGBl. 2021 Nr. 257."
    assert "art-4a-abs5-bstc-ziff1" not in {p.eid for p in new.provisions}  # its numbers went with it
    assert _prov(old, "art-4a-abs5-bstc").status == "in_force"
    article = _prov(new, "art-18")
    assert article.status == "repealed" and article.text == "" and article.note == "Art. 18 aufgehoben durch LGBl. 2016 Nr. 505."
    assert _prov(new, "art-4-abs3").status == "repealed"
    # an unnumbered paragraph in an article is the article's own text
    assert _prov(new, "art-21").text == "Dieses Gesetz tritt am Tage der Kundmachung in Kraft."
    assert _prov(new, "art-21").heading == "Inkrafttreten" and _prov(new, "art-21").is_leaf


def test_constitution_is_exact_text_with_headings() -> None:
    doc = _doc("lv")
    assert doc.instrument_type == "constitution" and doc.work_date == date(1921, 10, 5)
    assert doc.title == "Verfassung des Fürstentums Liechtenstein vom 5. Oktober 1921"
    assert doc.short_title == "Verfassung des Fürstentums Liechtenstein" and "abbreviation" not in doc.ext
    art2 = _prov(doc, "art-2")
    assert art2.text == (
        "Das Fürstentum ist eine konstitutionelle Erbmonarchie auf demokratischer und parlamentarischer Grundlage "
        "(Art. 79 und 80); die Staatsgewalt ist im Fürsten und im Volke verankert und wird von beiden nach Massgabe "
        "der Bestimmungen dieser Verfassung ausgeübt."
    )
    assert art2.context_heading == "I. Hauptstück › Das Fürstentum" and art2.is_leaf
    assert _prov(doc, "art-1-abs2").text == "Vaduz ist der Hauptort und der Sitz des Landtages und der Regierung."
    assert _prov(doc, "preamble").text.startswith("Wir, Johann II. von Gottes Gnaden souveräner Fürst")
    repealed = _prov(doc, "art-16-abs6")
    assert repealed.status == "repealed" and repealed.note == "Art. 16 Abs. 6 aufgehoben durch LGBl. 1972 Nr. 8."
    # the signatures and the appended letters are kept as text, not dropped
    assert any(p.kind == "closing" and "gez." in p.text for p in doc.provisions)


def test_urg_exclusion_of_official_works_is_article_5() -> None:
    doc = _doc("urg")
    assert doc.ext["abbreviation"] == "URG" and doc.ext["lgbl"] == "1999.160"
    art5 = _prov(doc, "art-5")
    assert art5.heading == "Nicht geschützte Werke"
    point = _prov(doc, "art-5-abs1-bsta")
    assert point.text == "Gesetze, Verordnungen, völkerrechtliche Verträge und andere amtliche Erlasse;"
    assert _prov(doc, "art-5-abs2").text.startswith("Ebenfalls nicht geschützt sind amtliche oder gesetzlich geforderte")
    assert _prov(doc, "art-51").status == "repealed"
    nested = [p for p in doc.provisions if p.num_path[-1].startswith("ziff:") and len(p.num_path) == 4]
    assert nested and all(p.parent_eid for p in nested)


def test_citations() -> None:
    glg, urg, lv = _doc("glg7"), _doc("urg"), _doc("lv")
    assert _cite(urg, "art-5-abs1-bsta") == "Art. 5 Abs. 1 Bst. a URG"
    assert _cite(glg, "art-4a-abs5-bstc") == "Art. 4a Abs. 5 Bst. c GLG"
    assert _cite(glg, "art-1") == "Art. 1 GLG"
    assert _cite(lv, "art-2") == "Art. 2 Verfassung des Fürstentums Liechtenstein"
    old = _doc("glg6")
    assert _cite(old, "art-4a-abs5-bstc-ziff1") == "Art. 4a Abs. 5 Bst. c Ziff. 1 GLG"
    module = get_module("li")
    assert module.validate_ext(glg.ext)["lgbl"] == "1999.096"
    with pytest.raises(ValueError):
        module.validate_ext({**glg.ext, "unexpected": 1})


def test_parser_rejects_foreign_input() -> None:
    with pytest.raises(HtmlParseError, match="not a Lilex bundle"):
        parse_lilex(b"<html></html>")
    bad = json.loads(_bundle("glg6"))
    bad["schema"] = "other/1"
    with pytest.raises(HtmlParseError, match="schema"):
        parse_lilex(json.dumps(bad).encode())
    bad = json.loads(_bundle("glg6"))
    bad["html"] = "<html><body><div class='htit1'>Gesetz</div></body></html>"
    with pytest.raises(HtmlParseError, match="no article"):
        parse_lilex(json.dumps(bad).encode())
    bad = json.loads(_bundle("glg6"))
    bad["dates"] = "irgendwann"
    with pytest.raises(HtmlParseError, match="version dates"):
        parse_lilex(json.dumps(bad).encode())


# --------------------------------------------------------------------------- connector


UA = "ULIS-test (+https://example.org)"


def _write(http: PoliteHttp, url: str, data: bytes, status: int = 200) -> None:
    path = http.cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = {"url": url, "final_url": url, "status": status, "content_type": "text/html;charset=UTF-8", "retrieved_at": RETRIEVED}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _page_with_versions(keep: set[int]) -> bytes:
    text = _gz("glg-1999-096-page.html.gz").decode("utf-8")
    for n in (3, 4, 5, 6, 7):
        if n not in keep:
            text = re.sub(rf'\s*<option[^>]*value="{n}"[^>]*>[^<]*</option>', "", text)
    return text.encode("utf-8")


def _cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    http = PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)
    _write(http, f"https://{HOST}/robots.txt", b"", status=404)  # the host has no robots.txt
    _write(http, f"https://{HOST}/konso/1999.096", _page_with_versions({6, 7}))
    _write(http, f"https://{HOST}/konso/html/1999096000?version=6", _gz("glg-1999-096-v6.html.gz"))
    _write(http, f"https://{HOST}/konso/html/1999096000?version=7", _gz("glg-1999-096-v7.html.gz"))
    _write(http, f"https://{HOST}/konso/1921.015", _gz("verfassung-1921-015-page.html.gz"))
    _write(http, f"https://{HOST}/konso/html/1921015000?version=45", _gz("verfassung-1921-015-v45.html.gz"))
    _write(http, f"https://{HOST}/konso/2020.999", b"", status=404)
    _write(http, f"https://{HOST}/konso/2020.998", b"<html><body>Wartungsarbeiten</body></html>")
    return cache


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "laws": laws,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_law_page_lists_versions_oldest_first() -> None:
    chrono, versions = law_page_info(_page_with_versions({4, 5, 6, 7}), "1999.096")
    assert chrono == "1999096000"
    assert versions == [
        (4, "08.06.2011 - 30.06.2015"),
        (5, "01.07.2015 - 31.12.2016"),
        (6, "01.01.2017 - 31.12.2021"),
        (7, "01.01.2022"),
    ]
    with pytest.raises(HtmlParseError, match="belongs to"):
        law_page_info(_page_with_versions({7}), "1999.097")
    with pytest.raises(HtmlParseError, match="not a Lilex law page"):
        law_page_info(b"<html><body>x</body></html>", "1999.096")


def test_connector_offline_yields_bundles_oldest_first(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    manifest = _manifest(
        tmp_path,
        [{"lgbl": "1999.096", "versions": "all", "title": "GLG"}, {"lgbl": "1921.015"}],
        default_versions="current",
    )
    connector = LilexConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["li-lilex:1999.096@v6", "li-lilex:1999.096@v7", "li-lilex:1921.015@v45"]
    bundle = json.loads(docs[0].content)
    assert bundle["schema"] == BUNDLE_SCHEMA and bundle["dates"] == "01.01.2017 - 31.12.2021" and bundle["version"] == 6
    assert bundle["html"].encode("utf-8") == _gz("glg-1999-096-v6.html.gz")  # exactly the page as received
    assert docs[0].uri == f"https://{HOST}/konso/html/1999096000?version=6" and docs[0].media_type == "application/json"
    assert docs[0].retrieved_at == datetime(2026, 10, 14, 13, 0, tzinfo=UTC) and docs[0].metadata["title"] == "GLG"
    assert connector.http is not None and connector.http.requests_made == 0 and connector.fetch_errors == []
    # the default is the current version only
    only_current = list(LilexConnector(_manifest(tmp_path, [{"lgbl": "1999.096"}]), SOURCE, cache, offline=True).fetch())
    assert [d.external_id for d in only_current] == ["li-lilex:1999.096@v7"]


def test_connector_reports_missing_laws_and_goes_on(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    manifest = _manifest(tmp_path, [{"lgbl": "2020.999"}, {"lgbl": "2020.998"}, {"lgbl": "1999.096"}])
    connector = LilexConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["li-lilex:1999.096@v7"]
    assert len(connector.fetch_errors) == 2
    assert "2020.999" in connector.fetch_errors[0] and "404" in connector.fetch_errors[0]
    assert "2020.998" in connector.fetch_errors[1] and "not a Lilex law page" in connector.fetch_errors[1]


def test_connector_is_offline_and_refuses_robots_disallowed_paths(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(LilexConnector(_manifest(tmp_path, [{"lgbl": "1988.037"}]), SOURCE, cache, offline=True).fetch())
    # should the host ever publish a robots.txt, its rules apply
    http = PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)
    _write(http, f"https://{HOST}/robots.txt", b"User-agent: *\nDisallow: /konso/html/\n")
    connector = LilexConnector(_manifest(tmp_path, [{"lgbl": "1999.096"}]), SOURCE, cache, offline=True)
    assert list(connector.fetch()) == []
    assert "disallowed by robots.txt" in connector.fetch_errors[0]


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = [{"lgbl": "1999.096"}]
    for laws, extra, message in (
        ([{"lgbl": "1999.096/../x"}], {}, "invalid LGBl-Nr"),
        ([{"lgbl": "1999.096"}, {"lgbl": "1999.096"}], {}, "listed twice"),
        ([{"lgbl": "1999.096", "versions": "some"}], {}, "versions must be"),
        (good, {"default_versions": "newest"}, "default_versions"),
        (good, {"base_url": "http://www.gesetze.li"}, "base_url"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        (good, {"crawl_delay_seconds": 1}, "crawl_delay_seconds"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(LilexConnector(_manifest(tmp_path, laws, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(LilexConnector(_manifest(tmp_path, good, source_id="other"), SOURCE, tmp_path).fetch())


def test_real_manifest_and_registration() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), LilexConnector)
    manifest = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text(encoding="utf-8"))
    laws = manifest["laws"]
    ids = [law["lgbl"] for law in laws]
    assert len(ids) == len(set(ids)) == 1227
    assert {"1921.015", "1003.001", "1926.004", "1988.037", "1999.160", "1999.096"} <= set(ids)
    assert {law["lgbl"] for law in laws if law.get("versions") == "all"} == {
        "1921.015", "1003.001", "1926.004", "1988.037", "1988.062", "2018.272", "1999.160", "1999.096",
    }  # fmt: skip
    assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
    assert manifest["default_versions"] == "current" and manifest["base_url"] == f"https://{HOST}"
    # the connector's own validation accepts the committed manifest
    assert LilexConnector(REPO_ROOT / source.access.endpoint, SOURCE, Path("/nonexistent"))._manifest()["laws"] == laws
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    profile = catalog.jurisdictions.get("LI")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["de"]
    assert profile.citation.module == "li" and profile.coverage.corpus_status == "pilot"


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert "www.gesetze.li" in text and "Art. 5" in text and "2026-10-14" in text
    for name in ("glg-1999-096-v6", "glg-1999-096-v7", "verfassung-1921-015-v45", "urg-1999-160-v10"):
        assert name in text


# --------------------------------------------------------------------------- database


class _Fixtures:
    """The versions of the Gleichstellungsgesetz, oldest first, as the connector would yield them."""

    def __init__(self, keys: tuple[str, ...] = ("glg6", "glg7", "urg", "lv")):
        self.keys = keys

    def fetch(self) -> Iterator[RawDocument]:
        for key in self.keys:
            fixture, lgbl, version, dates = VERSIONS[key]
            yield RawDocument(
                f"li-lilex:{lgbl}@v{version}",
                f"https://{HOST}/konso/html/{lgbl[:4]}{int(lgbl[5:]):03d}000?version={version}",
                "application/json",
                _bundle(key),
                datetime.fromisoformat(RETRIEVED),
                {"fixture": key},
            )


@pytest.fixture(scope="module")
def li_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="LI", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(li_ingested: IngestionReport) -> None:
    assert li_ingested.errors == [] and li_ingested.conflicts == []
    assert li_ingested.documents_new == 4 and li_ingested.instruments_created == 3
    assert li_ingested.instrument_versions_created == 4
    assert li_ingested.passages_indexed > 100


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, li_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="Gesetze, Verordnungen, völkerrechtliche Verträge und andere amtliche Erlasse")
    hit = next(h for h in res["results"] if h["citation"] == "Art. 5 Abs. 1 Bst. a URG")
    assert hit["text"] == "Gesetze, Verordnungen, völkerrechtliche Verträge und andere amtliche Erlasse;"
    assert hit["jurisdiction"]["id"] == "LI" and all(h["jurisdiction"]["id"] == "LI" for h in res["results"])
    assert hit["authority"]["category"] == "binding_legislation"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"SOURCE_NOT_AUTHENTIC", "NOT_AUTHENTIC_TEXT", "PROFILE_UNVERIFIED", "CURRENCY_UNKNOWN"} <= codes
    assert {"SNAPSHOT_DATES", "OFFICIAL_CONSOLIDATION_REPRODUCED"} <= codes
    assert hit["source"]["attribution"].startswith("Quelle: Lilex")
    state = _search(session, catalog, q="Das Fürstentum ist eine konstitutionelle Erbmonarchie auf demokratischer Grundlage")
    assert state["results"][0]["citation"] == "Art. 2 Verfassung des Fürstentums Liechtenstein"


@pytest.mark.db
def test_point_in_time_follows_the_version_list(session: Session, catalog: Catalog, li_ingested: IngestionReport) -> None:
    uri = "/li/lgbl/1999.096/~art-4a-abs5-bstc"
    before = provision_at(session, catalog, uri, date(2021, 12, 31))
    assert before["status_on_date"] == "in_force"
    assert before["version"]["valid_from"] == "2017-01-01" and before["version"]["valid_to"] == "2022-01-01"
    assert before["provision"]["citation"] == "Art. 4a Abs. 5 Bst. c GLG"
    assert "Faktor Geschlecht" in before["version"]["text"]
    after = provision_at(session, catalog, uri, date(2022, 1, 1))  # the new version applies on its first day
    assert after["status_on_date"] == "repealed" and after["version"]["valid_from"] == "2022-01-01"
    assert "aufgehoben durch LGBl. 2021 Nr. 257" in (after["version"]["note"] or "")
    # a number of the repealed letter ends with it
    ziff = provision_at(session, catalog, uri + "-ziff1", date(2022, 6, 1))
    assert ziff["status_on_date"] != "in_force"
    early = provision_at(session, catalog, uri, date(2010, 1, 1))  # before the first version we hold
    assert early["status_on_date"] == "history_not_indexed" and early["version"] is None
    open_ended = provision_at(session, catalog, "/li/lgbl/1921.015/~art-2", date(2026, 10, 14))
    assert open_ended["status_on_date"] == "in_force" and open_ended["version"]["valid_to"] is None


@pytest.mark.db
def test_unchanged_wording_continues_across_versions(
    session: Session, catalog: Catalog, li_ingested: IngestionReport
) -> None:
    uri = "/li/lgbl/1999.096/~art-3"
    on_old = provision_at(session, catalog, uri, date(2019, 5, 1))
    on_new = provision_at(session, catalog, uri, date(2023, 5, 1))
    assert on_old["status_on_date"] == on_new["status_on_date"] == "in_force"
    assert on_old["version"]["text"] == on_new["version"]["text"]


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, li_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == 4 and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 4 and all(r["result"] == "pass" for r in results)
        session.rollback()
