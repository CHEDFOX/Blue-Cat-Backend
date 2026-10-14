"""Likumi.lv (Latvia): parser, connector, citations and search behaviour (real page excerpts)."""

from __future__ import annotations

import hashlib
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
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.likumi_lv import LikumiLvConnector, robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.likumi_lv import LikumiParseError, parse_likumi_lv, resolve_uri
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "likumi_lv"
META = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
SOURCE = "lv-likumi"
NAMES = ("satversme", "kriminallikums", "darba-likums", "civillikums-1", "administrativa-procesa-likums")
IDS = {
    "satversme": 57980,
    "kriminallikums": 88966,
    "darba-likums": 26019,
    "civillikums-1": 90223,
    "administrativa-procesa-likums": 55567,
}
TODAY = date(2026, 10, 14)


def _html(name: str) -> bytes:
    return (FIX / f"{name}.html").read_bytes()


def _doc(name: str) -> ParsedDocument:
    docs = parse_likumi_lv(_html(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(name: str, eid: str) -> str:
    doc = _doc(name)
    p = _prov(doc, eid)
    return get_module("lv").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="LV",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="lv",
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_satversme_document_facts() -> None:
    doc = _doc("satversme")
    assert doc.work_uri == "/lv/likumi/57980" and doc.expression_uri == "/lv/likumi/57980/lav@2019-01-01"
    assert (doc.instrument_type, doc.language, doc.jurisdiction_id) == ("constitution", "lv", "LV")
    assert doc.title == "Latvijas Republikas Satversme" and doc.short_title == "Satversme"
    assert doc.work_date == date(1922, 2, 15) and doc.version_date == date(2019, 1, 1)
    # informative systematisation by the official publisher: not authentic, no stated currency date
    assert doc.authoritative is False and doc.text_origin == "official_revised" and doc.currency_date is None
    assert doc.validity_basis == "revision_snapshot" and doc.consolidation_kind == "point_in_time"
    assert doc.ext["likumi_id"] == 57980 and doc.ext["genitive"] == "Satversmes" and "part" not in doc.ext
    assert doc.ext["issuer"] == "Satversmes Sapulce" and doc.ext["published_in"].startswith("Latvijas Vēstnesis, 43")
    kinds = {e.event_type: e.date for e in doc.events}
    assert kinds == {
        "enactment": date(1922, 2, 15),
        "entry_into_force": date(1922, 11, 7),
        "version_change": date(2019, 1, 1),
    }
    # the current edition is open-ended and no later edition exists
    assert all(p.valid_from == date(2019, 1, 1) and p.valid_to is None for p in doc.provisions)
    assert doc.outstanding_changes == []


def test_satversme_provisions_and_repeal() -> None:
    doc = _doc("satversme")
    assert [p.eid for p in doc.provisions] == ["preamble", "art-1", "art-2", "art-68", "art-69", "art-81", "art-91"]
    pre = _prov(doc, "preamble")
    assert pre.kind == "preamble" and pre.text.startswith("1918.gada 18.novembrī proklamētā Latvijas valsts")
    one = _prov(doc, "art-1")
    assert one.text == "Latvija ir neatkarīga demokrātiska republika." and one.heading is None
    assert one.context_heading == "I nodaļa. Vispārējie noteikumi"
    # unnumbered paragraphs stay text of the article (the source numbers none)
    assert "\n" in _prov(doc, "art-68").text and _prov(doc, "art-68").is_leaf
    assert _prov(doc, "art-69").text.endswith("ja likumā nav noteikts cits termiņš.")
    gone = _prov(doc, "art-81")
    assert gone.status == "repealed" and gone.text == "" and gone.note.startswith("(Izslēgts ar 03.05.2007. likumu")
    assert not gone.refs and "stājas spēkā 31.05.2007" in gone.note
    # the amendment annotation is a note, never text
    assert _prov(doc, "art-69").note.startswith("(23.09.2004. likuma redakcijā, kas stājas spēkā 21.10.2004.)")
    assert all("likuma redakcijā" not in p.text for p in doc.provisions)


def test_kriminallikums_structure_dates_and_outstanding_edition() -> None:
    doc = _doc("kriminallikums")
    assert doc.instrument_type == "act" and doc.work_date == date(1998, 6, 17)
    assert doc.version_date == date(2026, 7, 10)
    # the source states the shown edition runs 10.07.2026-14.10.2026: the day after is the exclusive end
    assert all(p.valid_from == date(2026, 7, 10) and p.valid_to == date(2026, 10, 15) for p in doc.provisions)
    assert len(doc.outstanding_changes) == 1
    assert "in force from 2026-10-15" in doc.outstanding_changes[0]["description"]
    assert doc.outstanding_changes[0]["affects_eid"] is None
    art = _prov(doc, "art-117")
    assert art.heading == "Slepkavība pastiprinošos apstākļos" and art.kind == "article" and art.top_kind == "article"
    assert art.context_heading == "SEVIŠĶĀ DAĻA › XII nodaļa Nonāvēšana"
    # a chapeau, twelve points and a closing paragraph: the article is indexed as a whole and by point
    assert art.has_own_content and not art.is_leaf
    assert art.text.startswith("Par slepkavību, ja:\n1) noslepkavota sieviete")
    assert art.text.endswith("konfiscējot mantu vai bez mantas konfiskācijas.")
    points = [p for p in doc.provisions if p.parent_eid == "art-117"]
    assert [p.num_path for p in points][:2] == [["art:117", "p:1"], ["art:117", "p:2"]] and len(points) == 12
    assert points[0].text == "noslepkavota sieviete, vainīgajam apzinoties, ka viņa ir grūtniecības stāvoklī;"
    assert points[0].eid == "art-117-p1" and points[0].is_leaf and points[0].kind == "point"


def test_kriminallikums_parts_superscripts_and_repealed_units() -> None:
    doc = _doc("kriminallikums")
    one_one = _prov(doc, "art-1-d1")
    assert one_one.num_path == ["art:1", "d:1"] and one_one.text.startswith("Pie kriminālatbildības saucama")
    # an article inserted between others: number 38.¹, part (2¹) - superscripts become "_" in identifiers
    ins = _prov(doc, "art-38_1")
    assert ins.num_path == ["art:38_1"] and ins.heading == "Probācijas uzraudzība"
    assert "(2¹)" in ins.text and _prov(doc, "art-38_1-d2_1").num_path == ["art:38_1", "d:2_1"]
    # a repealed article keeps its old heading, has no text, and the annotation as note
    gone = _prov(doc, "art-45")
    assert gone.status == "repealed" and gone.text == "" and gone.heading == "Policijas kontrole"
    assert gone.note.startswith("(Izslēgts ar 08.07.2011. likumu, kas stājas spēkā 01.10.2011.")
    # a repealed part inside a live article; its parent still reads "(2)" in order
    part = _prov(doc, "art-70_4-d2")
    assert part.status == "repealed" and part.text == "" and part.note.startswith("(Izslēgta ar 10.03.2016. likumu)")
    assert _prov(doc, "art-70_4").status == "in_force" and "(2)\n" in _prov(doc, "art-70_4").text + "\n"
    # a repealed point and a repealed transitional provision
    assert any(p.kind == "point" and p.status == "repealed" and p.parent_eid == "art-118" for p in doc.provisions)
    assert _prov(doc, "pn-25_3").status == "repealed" and _prov(doc, "pn-25_3").text == ""


def test_kriminallikums_transitional_and_informative_reference() -> None:
    doc = _doc("kriminallikums")
    pn = _prov(doc, "pn-1")
    assert pn.kind == "transitional" and pn.num_path == ["pn:1"] and pn.context_heading == "Pārejas noteikumi"
    assert pn.text == "Šā likuma spēkā stāšanās laiku un kārtību nosaka ar īpašu likumu."  # printed "1." removed
    ref = _prov(doc, "eu-ref")
    assert ref.kind == "informative_reference" and ref.heading == "Informatīva atsauce uz Eiropas Savienības direktīvām"
    assert ref.text.startswith("Likumā iekļautas tiesību normas, kas izriet no:\n1) Padomes 1979.gada 2.aprīļa")
    # the enacting formula, adoption statement and signatures are not ingested
    assert not any("Ulmanis" in p.text or "izsludina šādu likumu" in p.text for p in doc.provisions)


def test_darba_likums_points_and_heading_hierarchy() -> None:
    doc = _doc("darba-likums")
    assert doc.outstanding_changes == [] and doc.version_date == date(2024, 10, 22)
    p = _prov(doc, "art-101-d1-p1")
    assert p.num_path == ["art:101", "d:1", "p:1"] and p.parent_eid == "art-101-d1" and p.is_leaf
    assert p.text == "darbinieks bez attaisnojoša iemesla būtiski pārkāpis darba līgumu vai noteikto darba kārtību;"
    art = _prov(doc, "art-101")
    assert (
        art.context_heading
        == "C DAĻA DARBA LĪGUMS › PIEKTĀ SADAĻA DARBA TIESISKO ATTIECĪBU IZBEIGŠANĀS › 26.nodaļa Uzteikums"
    )
    assert not art.has_own_content and not art.is_leaf  # only parts: indexed through them
    assert _prov(doc, "art-101-d1").has_own_content  # a chapeau before the points


def test_subpoints_and_parts_of_the_civil_law() -> None:
    apl = _doc("administrativa-procesa-likums")
    sub = _prov(apl, "art-317-d1-p2-apa")
    assert sub.kind == "subpoint" and sub.num_path == ["art:317", "d:1", "p:2", "ap:a"] and sub.is_leaf
    civ = _doc("civillikums-1")
    assert (
        civ.title == "Civillikums. PIRMĀ DAĻA. Ģimenes tiesības" and civ.ext["part"] == "PIRMĀ DAĻA. Ģimenes tiesības"
    )
    assert civ.ext["genitive"] == "Civillikuma" and civ.ext["issuer"] == "Ministru kabinets"
    assert civ.work_date == date(1937, 1, 28) and civ.version_date == date(2025, 12, 23)
    art = _prov(civ, "art-27")  # the number is printed in bold before the text, no heading
    assert art.heading is None and art.text.startswith("Ja saderināšanos atceļ vai ja kāds saderinātais")
    assert [p.num_path for p in civ.provisions if p.parent_eid == "art-27"] == [
        ["art:27", "p:1"],
        ["art:27", "p:2"],
        ["art:27", "p:3"],
    ]
    assert civ.provisions[0].context_heading == "PIRMĀ NODAĻA Laulība › Pirmā apakšnodaļa Saderināšanās"


def test_references_resolve_to_work_uris() -> None:
    doc = _doc("satversme")
    refs = [r.href for p in doc.provisions for r in p.refs]
    assert "https://likumi.lv/ta/id/57980" in refs
    assert resolve_uri("https://likumi.lv/ta/id/88966#p117") == ("/lv/likumi/88966", "art-117")
    assert resolve_uri("https://likumi.lv/ta/id/88966-kriminallikums#p38_1") == ("/lv/likumi/88966", "art-38_1")
    assert resolve_uri("https://likumi.lv/ta/id/88966#pn11") == ("/lv/likumi/88966", "pn-11")
    assert resolve_uri("https://likumi.lv/ta/id/88966-kriminallikums/redakcijas-datums/2024/10/22") == (
        "/lv/likumi/88966",
        None,
    )
    assert resolve_uri("https://example.org/ta/id/88966") is None
    assert pipeline_resolve_uri("https://likumi.lv/ta/id/26019#p101") == ("/lv/likumi/26019", "art-101")


def test_parser_rejects_unexpected_pages() -> None:
    page = _html("satversme").decode()
    with pytest.raises(LikumiParseError, match="not a Likumi.lv document page"):
        parse_likumi_lv(b"<html><body><p>hello</p></body></html>")
    with pytest.raises(LikumiParseError, match="not UTF-8"):
        parse_likumi_lv(b"\xff\xfe")
    with pytest.raises(LikumiParseError, match="unsupported block class TV218"):
        parse_likumi_lv(page.replace("<div class='TV216'", "<div class='TV218'").encode())
    with pytest.raises(LikumiParseError, match="not in the edition list"):
        parse_likumi_lv(page.replace('"selected":{"value":"01.01.2019"', '"selected":{"value":"02.02.2020"').encode())
    with pytest.raises(LikumiParseError, match="not 'Spēkā esošs'"):
        parse_likumi_lv(re.sub("(?i)spēkā esošs", "zaudējis spēku", page).encode())
    with pytest.raises(LikumiParseError, match="lists no editions"):
        parse_likumi_lv(page.replace('id="ver_date"', 'id="other"').encode())


# --------------------------------------------------------------------------- citations


def test_citation_styles() -> None:
    assert _cite("kriminallikums", "art-117") == "Krimināllikuma 117. pants"
    assert _cite("kriminallikums", "art-117-p1") == "Krimināllikuma 117. panta 1. punkts"
    assert _cite("kriminallikums", "art-1-d2") == "Krimināllikuma 1. panta otrā daļa"
    assert _cite("kriminallikums", "art-38_1") == "Krimināllikuma 38.¹ pants"
    assert _cite("kriminallikums", "art-38_1-d2_2-p3") == "Krimināllikuma 38.¹ panta 2.² daļas 3. punkts"
    assert _cite("kriminallikums", "pn-1") == "Krimināllikuma pārejas noteikumu 1. punkts"
    assert _cite("kriminallikums", "eu-ref") == "Krimināllikuma informatīvā atsauce uz Eiropas Savienības direktīvām"
    assert _cite("satversme", "art-91") == "Satversmes 91. pants"
    assert _cite("satversme", "preamble") == "Satversmes ievads"
    assert _cite("darba-likums", "art-101-d1-p1") == "Darba likuma 101. panta pirmās daļas 1. punkts"
    assert _cite("darba-likums", "pn-1-p1") == "Darba likuma pārejas noteikumu 1. punkta 1) apakšpunkts"
    assert _cite("civillikums-1", "art-27-p2") == "Civillikuma 27. panta 2. punkts"
    assert (
        _cite("administrativa-procesa-likums", "art-317-d1-p2-apa")
        == "Administratīvā procesa likuma 317. panta pirmās daļas 2. punkta a) apakšpunkts"
    )


def test_citation_without_a_genitive_and_long_part_numbers() -> None:
    module = get_module("lv")

    def cite(num_path: list[str], ext: dict[str, Any]) -> str:
        return module.provision_citation(
            ProvisionCitationInput("Ministru kabineta noteikumi", "act", "LV", "article", num_path, "lv", ext)
        )

    assert cite(["art:5"], {}) == "5. pants (Ministru kabineta noteikumi)"
    assert cite(["art:5", "d:21"], {"genitive": "X likuma"}) == "X likuma 5. panta 21. daļa"
    assert cite(["art:5", "d:20", "p:1"], {"genitive": "X likuma"}) == "X likuma 5. panta divdesmitās daļas 1. punkts"
    assert module.validate_ext({"likumi_id": 1, "short_name": "X"}) == {"likumi_id": 1, "short_name": "X"}
    with pytest.raises(ValueError):
        module.validate_ext({"likumi_id": 1, "short_name": "X", "unknown": 1})


# --------------------------------------------------------------------------- connector

ROBOTS = """User-agent: AhrefsBot
Disallow: /
User-agent: *
Disallow: /css/
Disallow: /search.php*
Disallow: /*/redakcijas-datums/
Disallow: /*&version_date=
Disallow: /*id=27582$
Disallow: /*id=27582&
Disallow: /ta/id/26019*
Sitemap: https://likumi.lv/sitemap-index.xml
Crawl-delay: 1
"""


def test_robots_rules() -> None:
    rules = robots_disallowed(ROBOTS)

    def blocked(path: str) -> bool:
        return any(r.match(path) for r in rules)

    assert blocked("/css/site.css") and blocked("/search.php?x=1") and blocked("/doc.php?id=1&version_date=2020/01/01")
    assert blocked("/ta/id/88966-kriminallikums/redakcijas-datums/2024/10/22")
    assert blocked("/doc.php?id=27582") and blocked("/doc.php?id=27582&x=1") and not blocked("/doc.php?id=275821")
    assert (
        not blocked("/ta/id/88966") and not blocked("/ta/id/57980-latvijas-republikas-satversme") and not blocked("/")
    )
    assert not blocked("/robots.txt")  # the AhrefsBot group (Disallow: /) does not apply to other agents


def _cache(tmp_path: Path, robots: str = ROBOTS) -> Path:
    cache = tmp_path / "cache"
    host = cache / "likumi.lv"
    (host / "ta" / "id").mkdir(parents=True)
    (host / "robots.txt").write_text(robots)
    (host / "robots.txt.meta.json").write_text(
        json.dumps({"url": "https://likumi.lv/robots.txt", "status": 200, "retrieved_at": "2026-10-14T11:00:00+00:00"})
    )
    for name, doc_id in IDS.items():
        target = host / "ta" / "id" / str(doc_id)
        target.write_bytes(_html(name))
        target.with_name(f"{doc_id}.meta.json").write_text(
            json.dumps(
                {
                    "url": f"https://likumi.lv/ta/id/{doc_id}",
                    "final_url": f"https://likumi.lv/ta/id/{doc_id}",
                    "status": 200,
                    "content_type": "text/html; charset=utf-8",
                    "retrieved_at": META[name]["retrieved_at"],
                }
            )
        )
    return cache


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://likumi.lv",
        "user_agent": "ULIS-test (+https://example.org)",
        "laws": laws,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    manifest = _manifest(tmp_path, [{"id": 57980, "title": "Satversme"}, {"id": 88966}])
    connector = LikumiLvConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["likumi-lv:57980", "likumi-lv:88966"]
    assert docs[0].content == _html("satversme")  # exact bytes as received
    assert docs[0].uri == "https://likumi.lv/ta/id/57980" and docs[0].media_type == "text/html"
    assert docs[0].retrieved_at.year == 2026 and docs[0].metadata["title"] == "Satversme"
    assert connector.http is not None and connector.http.requests_made == 0
    with pytest.raises(FetchError, match="offline and not cached"):
        list(LikumiLvConnector(_manifest(tmp_path, [{"id": 1234}]), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_robots_disallowed_ids_and_other_pages(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(FetchError, match="disallowed by robots.txt"):
        list(LikumiLvConnector(_manifest(tmp_path, [{"id": 26019}]), SOURCE, cache, offline=True).fetch())
    page = cache / "likumi.lv" / "ta" / "id" / "88966"
    page.write_text("<html>captcha</html>")
    with pytest.raises(FetchError, match="not a Likumi.lv document page"):
        list(LikumiLvConnector(_manifest(tmp_path, [{"id": 88966}]), SOURCE, cache, offline=True).fetch())
    page.write_bytes(_html("satversme"))  # a page for another id
    with pytest.raises(FetchError, match="not a Likumi.lv document page for id 88966"):
        list(LikumiLvConnector(_manifest(tmp_path, [{"id": 88966}]), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = [{"id": 57980}]
    for laws, extra, message in (
        ([{"id": "57980/../x"}], {}, "invalid document id"),
        ([{"id": 0}], {}, "invalid document id"),
        (good, {"base_url": "http://likumi.lv"}, "base_url"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(LikumiLvConnector(_manifest(tmp_path, laws, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(LikumiLvConnector(_manifest(tmp_path, good, source_id="other"), SOURCE, tmp_path).fetch())


def test_real_manifest_and_registration() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), LikumiLvConnector)
    manifest = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text(encoding="utf-8"))
    ids = [law["id"] for law in manifest["laws"]]
    assert len(ids) == 10 and len(set(ids)) == 10
    assert {57980, 88966, 26019, 300099, 55567} <= set(ids) and {90220, 90221, 90222, 90223, 90224} <= set(ids)
    assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    profile = catalog.jurisdictions.get("LV")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["lv"]
    assert [m.organisation for m in profile.memberships] == ["EU"] and profile.citation.module == "lv"


def test_other_sources_remain_gated() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    with pytest.raises(LicenceNotApproved):
        licence_gate(catalog.sources.get("in-india-code"))


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert "likumi.lv" in text and "Autortiesību likums" in text and "2026-10-14" in text
    for name, info in META.items():
        assert info["url"].startswith("https://likumi.lv/ta/id/") and info["retrieved_at"].startswith("2026-10-14")
        assert hashlib.sha256(_html(name)).hexdigest()  # present and readable


# --------------------------------------------------------------------------- database


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in NAMES:
            info = META[name]
            yield RawDocument(
                f"likumi-lv:fixture/{name}",
                info["final_url"],
                "text/html",
                _html(name),
                datetime.fromisoformat(info["retrieved_at"]).astimezone(UTC),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def lv_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="LV", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(lv_ingested: IngestionReport) -> None:
    assert lv_ingested.errors == [] and lv_ingested.conflicts == []
    assert lv_ingested.documents_new == len(NAMES) and lv_ingested.instruments_created == len(NAMES)
    assert lv_ingested.provision_versions_created == sum(len(_doc(n).provisions) for n in NAMES)
    assert lv_ingested.passages_indexed > 100


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, lv_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="noslepkavota sieviete vainīgajam apzinoties grūtniecības stāvoklī")
    hit = res["results"][0]
    assert hit["citation"] == "Krimināllikuma 117. panta 1. punkts"
    assert hit["text"] == "noslepkavota sieviete, vainīgajam apzinoties, ka viņa ir grūtniecības stāvoklī;"
    assert hit["jurisdiction"]["id"] == "LV" and all(h["jurisdiction"]["id"] == "LV" for h in res["results"])
    assert hit["authority"]["category"] == "binding_legislation"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert "OUTSTANDING_CHANGES_INSTRUMENT" in codes  # the 2026-10-15 edition
    assert hit["source"]["attribution"].startswith("Avots: Likumi.lv")
    equality = _search(session, catalog, q="Visi cilvēki Latvijā ir vienlīdzīgi likuma un tiesas priekšā")
    assert equality["results"][0]["citation"] == "Satversmes 91. pants"
    assert equality["results"][0]["text"].startswith("Visi cilvēki Latvijā ir vienlīdzīgi likuma un tiesas priekšā.")
    pregnant = _search(session, catalog, q="saderināšanās ir savstarpējs solījums savienoties laulībā")
    assert pregnant["results"][0]["citation"] == "Civillikuma 26. pants"
    dismissal = _search(session, catalog, q="darbinieks bez attaisnojoša iemesla būtiski pārkāpis darba līgumu")
    top = dismissal["results"][0]
    assert top["citation"] == "Darba likuma 101. panta pirmās daļas 1. punkts"
    # the edition is open-ended and the page states no "up to date" date
    assert "CURRENCY_UNKNOWN" in {lim["code"] for lim in top["limitations"]}
    assert "OUTSTANDING_CHANGES_INSTRUMENT" not in {lim["code"] for lim in top["limitations"]}
    sub = _search(session, catalog, q="Administratīvā procesa likums apelācijas sūdzība")
    assert all(h["jurisdiction"]["id"] == "LV" for h in sub["results"])


@pytest.mark.db
def test_point_in_time_follows_the_edition_the_source_states(
    session: Session, catalog: Catalog, lv_ingested: IngestionReport
) -> None:
    uri = "/lv/likumi/88966/~art-117"
    start = provision_at(session, catalog, uri, date(2026, 7, 10))
    assert start["status_on_date"] == "in_force" and start["version"]["valid_from"] == "2026-07-10"
    assert start["provision"]["citation"] == "Krimināllikuma 117. pants"
    last_day = provision_at(session, catalog, uri, date(2026, 10, 14))
    assert last_day["status_on_date"] == "in_force" and last_day["version"]["valid_to"] == "2026-10-15"
    after = provision_at(session, catalog, uri, date(2026, 10, 15))
    assert after["status_on_date"] != "in_force"  # the edition we hold ended on 14.10.2026
    assert {"OUTSTANDING_CHANGES_INSTRUMENT"} <= {lim["code"] for lim in last_day["limitations"]}
    # the act exists from 1998, but earlier editions are not indexed: a history gap, not "not yet in force"
    earlier = provision_at(session, catalog, uri, date(2010, 1, 1))
    assert earlier["status_on_date"] == "history_not_indexed" and earlier["version"] is None
    before_act = provision_at(session, catalog, uri, date(1990, 1, 1))
    assert before_act["status_on_date"] != "in_force"
    open_ended = provision_at(session, catalog, "/lv/likumi/57980/~art-91", date(2026, 10, 14))
    assert open_ended["status_on_date"] == "in_force" and open_ended["version"]["valid_to"] is None
    repealed = provision_at(session, catalog, "/lv/likumi/57980/~art-81", date(2026, 10, 14))
    assert repealed["status_on_date"] == "repealed"


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, lv_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(NAMES) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(NAMES) and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == lv_ingested.provision_versions_created
        session.rollback()
