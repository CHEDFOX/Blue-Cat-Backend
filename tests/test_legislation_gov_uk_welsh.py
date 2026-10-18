"""legislation.gov.uk: the Welsh texts of Welsh legislation, and the never-revised instruments as made.

Real documents (Open Government Licence v3.0, retrieved 2026-10-18, stored gzip-compressed): the Children (Abolition of
Defence of Reasonable Punishment) (Wales) Act 2020 (``anaw/2020/3``) in English and in Welsh (the site's current revised
text of each; the two texts are of equal standing, Government of Wales Act 2006 s 156(1)), and the Elections and Elected
Bodies (Wales) Act 2024 (Commencement No. 1) Order 2024 (``wsi/2024/1337``) as made, in English and in Welsh: an
instrument the site has never revised, law in force in its as-made text unless revoked.
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import shutil
import urllib.error
from datetime import date
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.ops.loading import LoadOptions, select_sources
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import legislation_gov_uk as conn_mod
from ulis.sources.connectors.legislation_gov_uk import LegislationGovUkConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.legislation_gov_uk import UkParseError, parse_legislation_gov_uk, resolve_uri
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "legislation_gov_uk"
ANAW = FIX / "cache" / "legislation.gov.uk" / "anaw" / "2020" / "3"
WSI = FIX / "cache" / "legislation.gov.uk" / "wsi" / "2024" / "1337"
WSI_EN_TITLE = "The Elections and Elected Bodies (Wales) Act 2024 (Commencement No. 1) Order 2024"
WSI_CY_TITLE = "Gorchymyn Deddf Etholiadau a Chyrff Etholedig (Cymru) 2024 (Cychwyn Rhif 1) 2024"
HRA = FIX / "cache" / "legislation.gov.uk" / "ukpga" / "1998" / "42"
SITE = "https://www.legislation.gov.uk"
EN_TITLE = "Children (Abolition of Defence of Reasonable Punishment) (Wales) Act 2020"
CY_TITLE = "Deddf Plant (Diddymu Amddiffyniad Cosb Resymol) (Cymru) 2020"


def _read(path: Path) -> bytes:
    return gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def _one(path: Path):  # type: ignore[no-untyped-def]
    docs = parse_legislation_gov_uk(_read(path))
    assert len(docs) == 1
    return docs[0]


def _cite(doc, eid: str, language: str | None = None) -> str:  # type: ignore[no-untyped-def]
    p = {x.eid: x for x in doc.provisions}[eid]
    return get_module("gb").provision_citation(
        ProvisionCitationInput(
            doc.short_title,
            doc.instrument_type,
            doc.jurisdiction_id,
            p.top_kind,
            p.num_path,
            language or doc.language,
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_welsh_text_is_a_parallel_expression_of_the_same_work() -> None:
    en, cy = _one(ANAW / "current.akn"), _one(ANAW / "current.cy.akn")
    assert (en.language, cy.language) == ("en", "cy")
    assert en.work_uri == cy.work_uri == "/gb/anaw/2020/3" and en.jurisdiction_id == cy.jurisdiction_id == "GB-WLS"
    assert (
        en.expression_uri == "/gb/anaw/2020/3/eng@2022-03-21" and cy.expression_uri == "/gb/anaw/2020/3/cym@2022-03-21"
    )
    assert en.authoritative and cy.authoritative  # equal standing: both texts are authentic
    assert en.short_title == EN_TITLE and cy.short_title == CY_TITLE  # the site's own titles, nothing translated
    assert (
        (en.text_origin, en.consolidation_kind)
        == (cy.text_origin, cy.consolidation_kind)
        == (
            "official_revised",
            "point_in_time",
        )
    )
    pe, pc = {p.eid: p for p in en.provisions}, {p.eid: p for p in cy.provisions}
    assert set(pe) == set(pc) and len(pe) == 24  # the same eIds: parallel versions of one provision
    for eid in pe:  # language-neutral structure; the dates come from the same lifecycle
        assert (pe[eid].num_path, pe[eid].valid_from, pe[eid].valid_to, pe[eid].extent, pe[eid].status) == (
            pc[eid].num_path,
            pc[eid].valid_from,
            pc[eid].valid_to,
            pc[eid].extent,
            pc[eid].status,
        )
    assert pe["section-1-1"].text.startswith("The common law defence of reasonable punishment is abolished")
    assert pc["section-1-1"].text.startswith("Mae amddiffyniad cosb resymol yn y gyfraith gyffredin wedi ei ddiddymu")
    assert pc["section-6"].heading == "Enw byr" and pe["section-6"].heading == "Short title"
    assert pe["section-1"].valid_from == pc["section-1"].valid_from == date(2022, 3, 21)


def test_english_only_works_are_unchanged() -> None:
    hra = _one(HRA / "current.akn")
    assert (hra.language, hra.authoritative) == ("en", False) and hra.expression_uri.startswith(
        "/gb/ukpga/1998/42/eng@"
    )
    assert hra.currency_date is not None  # a revised text is up to date to the site's modification date


def test_a_welsh_document_must_come_from_a_welsh_address() -> None:
    forged = _read(ANAW / "current.cy.akn").replace(
        b"anaw/2020/3/welsh</dc:identifier>", b"anaw/2020/3</dc:identifier>"
    )
    with pytest.raises(UkParseError, match="Welsh document"):
        parse_legislation_gov_uk(forged)
    with pytest.raises(UkParseError, match="unsupported language"):
        parse_legislation_gov_uk(_read(ANAW / "current.akn").replace(b'language="eng"', b'language="fra"'))


def test_an_instrument_as_made_is_in_force_as_made_with_no_dates_in_both_languages() -> None:
    en, cy = _one(WSI / "made.akn"), _one(WSI / "made.cy.akn")
    for doc in (en, cy):
        assert (doc.work_uri, doc.jurisdiction_id) == ("/gb/wsi/2024/1337", "GB-WLS")
        assert doc.instrument_type == "statutory_instrument"
        assert (doc.consolidation_kind, doc.text_origin, doc.authoritative) == ("as_made", "official_conversion", True)
        assert doc.version_date == date(2024, 12, 13) and doc.work_date == date(2024, 12, 13)
        # never revised: no start dates, and no currency claimed (dc:modified is the date of the record, not of the law)
        assert doc.currency_date is None and doc.validity_basis == "source_periods"
        assert doc.provisions and all(p.valid_from is None and p.valid_to is None for p in doc.provisions)
        assert all(p.status == "in_force" for p in doc.provisions)
    assert (en.language, cy.language) == ("en", "cy")
    assert en.short_title == WSI_EN_TITLE and cy.short_title == WSI_CY_TITLE
    assert en.expression_uri == "/gb/wsi/2024/1337/eng@2024-12-13"
    assert cy.expression_uri == "/gb/wsi/2024/1337/cym@2024-12-13"
    assert [p.eid for p in en.provisions] == [p.eid for p in cy.provisions]
    assert _cite(en, "article-2-a") == f"{WSI_EN_TITLE}, SI 2024/1337, art 2(a)"
    assert _cite(cy, "article-2-a") == f"{WSI_CY_TITLE}, OS 2024/1337, erthygl 2(a)"


def test_resolve_uri_ignores_the_welsh_suffix() -> None:
    assert resolve_uri("http://www.legislation.gov.uk/anaw/2020/3/welsh") == ("/gb/anaw/2020/3", None)
    assert resolve_uri("http://www.legislation.gov.uk/anaw/2020/3/2022-03-21/welsh") == ("/gb/anaw/2020/3", None)
    assert resolve_uri("http://www.legislation.gov.uk/id/anaw/2020/3/section/1/welsh") == (
        "/gb/anaw/2020/3",
        "section-1",
    )
    assert resolve_uri("http://www.legislation.gov.uk/wsi/2024/1337/made/welsh") == ("/gb/wsi/2024/1337", None)


# --------------------------------------------------------------------------- citations


def test_welsh_citations() -> None:
    en, cy = _one(ANAW / "current.akn"), _one(ANAW / "current.cy.akn")
    assert _cite(en, "section-1-1") == f"{EN_TITLE}, s 1(1)"
    assert _cite(cy, "section-1-1") == f"{CY_TITLE}, adran 1(1)"
    assert _cite(cy, "section-1-5-a") == f"{CY_TITLE}, adran 1(5)(a)"
    module = get_module("gb")

    def cite(language: str, leg_type: str, top_kind: str, num_path: list[str], eid: str) -> str:
        ext = {"leg_type": leg_type, "year": 2024, "number": "1337"}
        return module.provision_citation(
            ProvisionCitationInput(
                "Rheoliadau X (Cymru) 2024",
                "statutory_instrument",
                "GB-WLS",
                top_kind,
                num_path,
                language,
                ext=ext,
                eid=eid,
            )
        )

    assert (
        cite("cy", "wsi", "regulation", ["2", "(1)"], "regulation-2-1")
        == "Rheoliadau X (Cymru) 2024, OS 2024/1337, rheoliad 2(1)"
    )
    assert (
        cite("en", "wsi", "regulation", ["2", "(1)"], "regulation-2-1")
        == "Rheoliadau X (Cymru) 2024, SI 2024/1337, reg 2(1)"
    )
    assert (
        cite("cy", "wsi", "paragraph", ["2"], "schedule-1-paragraph-2")
        == "Rheoliadau X (Cymru) 2024, OS 2024/1337, Atodlen 1, paragraff 2"
    )
    assert cite("cy", "wsi", "article", ["3"], "article-3") == "Rheoliadau X (Cymru) 2024, OS 2024/1337, erthygl 3"
    assert cite("cy", "ssi", "regulation", ["2"], "regulation-2").endswith(
        ", SSI 2024/1337, rheoliad 2"
    )  # no Welsh series


# --------------------------------------------------------------------------- connector


def _cache_copy(dest: Path) -> Path:
    shutil.copytree(FIX / "cache", dest)
    for gz in dest.rglob("*.akn.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _connector(tmp_path: Path, works: list[Any], cache: Path | None = None, **top: Any) -> LegislationGovUkConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "gb-legislation-gov-uk",
        "base_url": SITE,
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 1,
        "works": works,
        **top,
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return LegislationGovUkConnector(
        path, "gb-legislation-gov-uk", cache or tmp_path / "cache", offline=cache is not None, sleep=lambda _s: None
    )


def test_manifest_entries_mark_works_with_a_welsh_text(tmp_path: Path) -> None:
    works = [
        "wsi/2024/1337@made+cy",
        "wsi/2024/1@made",
        "anaw/2020/3+cy",
        {"path": "asc/2021/1", "welsh": True, "title": "x"},
        {"path": "ukpga/1998/42"},
    ]
    normalised = _connector(tmp_path, works, history_default=True)._manifest()["works"]  # noqa: SLF001
    assert [(w["path"], w["history"], w.get("version"), w["welsh"]) for w in normalised] == [
        ("wsi/2024/1337", False, "made", True),
        ("wsi/2024/1", False, "made", False),
        ("anaw/2020/3", True, None, True),
        ("asc/2021/1", True, None, True),
        ("ukpga/1998/42", True, None, False),
    ]
    for bad, message in (("anaw/2020/3+fr", "invalid work entry"), ({"path": "anaw/2020/3", "welsh": "yes"}, "welsh")):
        with pytest.raises(ManifestError, match=message):
            _connector(tmp_path / "bad", [bad])._manifest()  # noqa: SLF001


def test_connector_yields_the_welsh_expression_from_the_cache(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    docs = list(_connector(tmp_path, [{"path": "anaw/2020/3", "history": False, "welsh": True}], cache).fetch())
    assert [d.external_id for d in docs] == [
        "legislation.gov.uk:anaw/2020/3@2022-03-21",
        "legislation.gov.uk:anaw/2020/3@2022-03-21/cy",
    ]
    assert [d.metadata["language"] for d in docs] == ["en", "cy"]
    assert docs[1].uri == f"{SITE}/anaw/2020/3/welsh/data.akn" and docs[0].uri == f"{SITE}/anaw/2020/3/data.akn"
    assert b'language="cym"' in docs[1].content and b'language="eng"' in docs[0].content
    # the same work without the flag: English only, as before
    english = list(_connector(tmp_path / "en", [{"path": "anaw/2020/3", "history": False}], cache).fetch())
    assert [d.external_id for d in english] == ["legislation.gov.uk:anaw/2020/3@2022-03-21"]


class _Response:
    def __init__(self, url: str, body: bytes):
        self.url, self.body, self.status = url, body, 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


def _akn(expression_date: str, language: str = "eng", events: list[str] = ()) -> bytes:  # type: ignore[assignment]
    refs = "".join(f'<eventRef date="{d}" eId="date-{i}" source="#"/>' for i, d in enumerate(events))
    return (
        "<akomaNtoso><meta><identification><FRBRExpression>"
        f'<FRBRdate date="{expression_date}" name="validFrom"/><FRBRlanguage language="{language}"/>'
        f"</FRBRExpression></identification><lifecycle>{refs}</lifecycle></meta></akomaNtoso>"
    ).encode()


def test_welsh_requests_follow_each_english_version_and_a_missing_welsh_text_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: dict[str, bytes] = {
        f"{SITE}/robots.txt": b"User-agent: *\nCrawl-delay: 5\nDisallow: */data.pdf\n",
        f"{SITE}/wsi/2024/1337/made/data.akn": _akn("2024-12-18"),
        f"{SITE}/wsi/2024/1337/made/welsh/data.akn": _akn("2024-12-18", "cym"),
        f"{SITE}/anaw/2021/1/data.akn": _akn("2023-01-01", events=["2021-04-01", "2023-01-01"]),
        f"{SITE}/anaw/2021/1/welsh/data.akn": _akn("2023-01-01", "cym"),
        f"{SITE}/anaw/2021/1/2021-04-01/data.akn": _akn("2021-04-01"),
        f"{SITE}/anaw/2021/1/2021-04-01/welsh/data.akn": _akn("2021-04-01", "cym"),
        f"{SITE}/wsi/2024/2/made/data.akn": _akn("2024-01-05"),  # flagged welsh, but the site has no Welsh text
        f"{SITE}/wsi/2019/432/data.akn": _akn("2026-04-01"),
        f"{SITE}/wsi/2019/432/welsh/data.akn": _akn("2026-04-01"),  # the site answers with the English text
    }
    requested: list[str] = []

    def urlopen(request: Any, timeout: float = 0) -> _Response:
        url = request.full_url
        requested.append(url)
        if url not in routes:
            raise urllib.error.HTTPError(url, 404, "stub", Message(), io.BytesIO(b""))
        return _Response(url, routes[url])

    monkeypatch.setattr(conn_mod.urllib.request, "urlopen", urlopen)
    works = ["wsi/2024/1337@made+cy", "anaw/2021/1+cy", "wsi/2024/2@made+cy", "wsi/2024/3@made", "wsi/2019/432+cy"]
    conn = _connector(tmp_path, works, history_default=True)
    docs = list(conn.fetch())
    assert [d.external_id for d in docs] == [
        "legislation.gov.uk:wsi/2024/1337@2024-12-18",
        "legislation.gov.uk:wsi/2024/1337@2024-12-18/cy",
        "legislation.gov.uk:anaw/2021/1@2021-04-01",
        "legislation.gov.uk:anaw/2021/1@2021-04-01/cy",
        "legislation.gov.uk:anaw/2021/1@2023-01-01",
        "legislation.gov.uk:anaw/2021/1@2023-01-01/cy",
        "legislation.gov.uk:wsi/2024/2@2024-01-05",
        "legislation.gov.uk:wsi/2019/432@2026-04-01",  # the English answer at the Welsh address is not stored
    ]
    assert requested[1:3] == [f"{SITE}/wsi/2024/1337/made/data.akn", f"{SITE}/wsi/2024/1337/made/welsh/data.akn"]
    assert (
        f"{SITE}/anaw/2021/1/2021-04-01/welsh/data.akn" in requested
        and f"{SITE}/anaw/2021/1/welsh/data.akn" in requested
    )
    assert f"{SITE}/wsi/2024/3/made/welsh/data.akn" not in requested  # not marked: English only
    assert conn.fetch_errors == [
        "wsi/2024/2: welsh text of the made version not found (404)",
        "wsi/2024/3: made version not found (404)",
        "wsi/2019/432: no welsh text of the current version (the site answered with the English text)",
    ]
    # a second run: nothing is requested again, the missing Welsh text included
    n = len(requested)
    again = list(_connector(tmp_path, ["wsi/2024/1337@made+cy", "wsi/2024/2@made+cy"], history_default=True).fetch())
    assert len(again) == 3 and len(requested) == n


def test_manifest_builder_marks_welsh_texts_from_the_feed() -> None:
    spec = importlib.util.spec_from_file_location(
        "build_uk_manifest", REPO_ROOT / "scripts" / "build_legislation_gov_uk_manifest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    site = "http://www.legislation.gov.uk"
    feed = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>{site}/id/wsi/2024/1337</id>
      <title>The X (Wales) Regulations 2024</title><link rel="self" href="{site}/id/wsi/2024/1337"/>
      <link href="{site}/wsi/2024/1337/made"/>
      <link rel="alternate" type="application/akn+xml" href="{site}/wsi/2024/1337/made/data.akn"/>
      <link rel="alternate" type="application/akn+xml" href="{site}/wsi/2024/1337/made/welsh/data.akn" hreflang="cy"/>
      </entry><entry><id>{site}/id/uksi/2024/1</id><title>The Y Regulations 2024</title>
      <link href="{site}/uksi/2024/1/2025-01-01"/>
      <link rel="alternate" type="application/akn+xml" href="{site}/uksi/2024/1/2025-01-01/data.akn"/>
      <link rel="alternate" type="application/pdf" href="{site}/uksi/2024/1/2025-01-01/welsh/data.pdf" hreflang="cy"/>
      </entry></feed>""".encode()
    entries, has_next = module.parse_feed(feed)
    assert not has_next and [(e["path"], e["version"], e["welsh"]) for e in entries] == [
        ("wsi/2024/1337", "made", True),
        ("uksi/2024/1", "2025-01-01", False),  # a Welsh PDF alone is no Welsh text for ULIS
    ]
    assert [module.classify(e) for e in entries] == ["single", "history"]


# --------------------------------------------------------------------------- load-all


def test_the_as_made_instruments_are_loaded_by_default_after_the_other_uk_legislation(catalog: Catalog) -> None:
    selected = [item.source.id for item in select_sources(catalog, LoadOptions()).selected]
    uk = [s for s in selected if s.startswith("gb-legislation-gov-uk")]
    assert uk == ["gb-legislation-gov-uk", "gb-legislation-gov-uk-secondary", "gb-legislation-gov-uk-secondary-made"]
    made = catalog.sources.get("gb-legislation-gov-uk-secondary-made")
    assert made.load_by_default and "cy" in made.languages
    skipped = dict(select_sources(catalog, LoadOptions(skip=("gb-legislation-gov-uk-secondary-made",))).skipped)
    assert skipped["gb-legislation-gov-uk-secondary-made"] == "--skip"  # the quick go-live switch


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def welsh_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("uk-welsh")
    cache = _cache_copy(tmp / "cache")
    connector = _connector(tmp, [{"path": "anaw/2020/3", "history": False, "welsh": True}], cache)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk", actor="pytest", connector=connector
        )
        session.commit()
    return report


@pytest.fixture(scope="module")
def made_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("uk-made")
    cache = _cache_copy(tmp / "cache")
    manifest = tmp / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "source_id": "gb-legislation-gov-uk-secondary-made",
                "base_url": SITE,
                "user_agent": "ULIS-test (+https://example.org)",
                "history_default": True,
                "works": ["wsi/2024/1337@made+cy"],
            }
        )
    )
    connector = LegislationGovUkConnector(manifest, "gb-legislation-gov-uk-secondary-made", cache, offline=True)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk-secondary-made", actor="pytest", connector=connector
        )
        session.commit()
    return report


@pytest.mark.db
def test_welsh_search_finds_the_welsh_text_with_a_welsh_citation(
    session: Session, catalog: Catalog, welsh_ingested: IngestionReport
) -> None:
    assert welsh_ingested.errors == [] and welsh_ingested.conflicts == []
    assert welsh_ingested.documents_seen == 2 and welsh_ingested.instrument_versions_created == 2
    res = search(
        session,
        catalog,
        SearchRequest(
            q="amddiffyniad cosb resymol wedi ei ddiddymu",
            jurisdiction="GB-WLS",
            as_of=date(2026, 10, 1),
            query_language="cy",
        ),
        today=date(2026, 10, 18),
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/gb/anaw/2020/3")
    assert hit["version"]["language"] == "cy" and hit["citation"] == f"{CY_TITLE}, adran 1(1)"
    assert hit["text"].startswith("Mae amddiffyniad cosb resymol")
    assert [(p["language"], p["authoritative"]) for p in hit["parallel_versions"]] == [("en", True)]
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "EQUALLY_AUTHENTIC_VERSIONS", "PROFILE_UNVERIFIED"} <= codes
    equal = next(lim for lim in hit["limitations"] if lim["code"] == "EQUALLY_AUTHENTIC_VERSIONS")
    assert "en" in equal["message"]
    # the English text of the same provision, found with an English query, keeps the English citation
    en = search(
        session,
        catalog,
        SearchRequest(
            q="defence of reasonable punishment is abolished",
            jurisdiction="GB-WLS",
            as_of=date(2026, 10, 1),
            query_language="en",
        ),
        today=date(2026, 10, 18),
    )
    hit_en = next(h for h in en["results"] if h["document"]["uri"] == "/gb/anaw/2020/3")
    assert hit_en["version"]["language"] == "en" and hit_en["citation"] == f"{EN_TITLE}, s 1(1)"


@pytest.mark.db
def test_welsh_and_english_are_parallel_versions_of_one_provision(
    session: Session, catalog: Catalog, welsh_ingested: IngestionReport
) -> None:
    cy = provision_at(session, catalog, "/gb/anaw/2020/3/~section-6", date(2026, 10, 1), language="cy")
    en = provision_at(session, catalog, "/gb/anaw/2020/3/~section-6", date(2026, 10, 1))  # GB-WLS: en first
    assert cy["version"]["text"].startswith("Enw byr y Ddeddf hon yw") and cy["language"] == "cy"
    assert en["version"]["text"].startswith("The short title of this Act is") and en["language"] == "en"
    assert cy["version"]["valid_from"] == en["version"]["valid_from"] == "2020-03-21"
    assert cy["provision"]["citation"] == f"{CY_TITLE}, adran 6"
    assert en["provision"]["citation"] == f"{EN_TITLE}, s 6"
    assert cy["available_languages"] == en["available_languages"] == ["cy", "en"]


@pytest.mark.db
def test_an_as_made_instrument_is_reported_as_made_with_revocation_unknown(
    session: Session, catalog: Catalog, made_ingested: IngestionReport
) -> None:
    assert made_ingested.errors == [] and made_ingested.conflicts == [] and made_ingested.documents_new == 2
    res = search(
        session,
        catalog,
        SearchRequest(
            q="Welsh elections information platform",
            jurisdiction="GB-WLS",
            as_of=date(2026, 10, 1),
            query_language="en",
        ),
        today=date(2026, 10, 18),
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/gb/wsi/2024/1337")
    assert hit["citation"].startswith(f"{WSI_EN_TITLE}, SI 2024/1337, art 2")
    assert hit["version"]["consolidation"] == "as_made" and hit["version"]["currency_date"] is None
    # status as made, revocation unknown: in force at the source date, applicability on the date unverified
    assert hit["authority"]["category"] == "applicability_unverified"
    codes = {lim["code"]: lim["message"] for lim in hit["limitations"]}
    assert {"EFFECTIVE_DATE_UNKNOWN", "CURRENCY_UNKNOWN", "NOT_AUTHENTICATED", "PROFILE_UNVERIFIED"} <= set(codes)
    assert "(as made text)" in codes["CURRENCY_UNKNOWN"] and "CURRENCY" not in codes
    assert hit["source"]["attribution"].startswith("Contains public sector information")
    cy = search(
        session,
        catalog,
        SearchRequest(
            q="platfform gwybodaeth etholiadau Cymru",
            jurisdiction="GB-WLS",
            as_of=date(2026, 10, 1),
            query_language="cy",
        ),
        today=date(2026, 10, 18),
    )
    hit_cy = next(h for h in cy["results"] if h["document"]["uri"] == "/gb/wsi/2024/1337")
    assert hit_cy["version"]["language"] == "cy"
    assert hit_cy["citation"].startswith(f"{WSI_CY_TITLE}, OS 2024/1337, erthygl 2")
    assert hit_cy["authority"]["category"] == "applicability_unverified"
    assert "EQUALLY_AUTHENTIC_VERSIONS" in {lim["code"] for lim in hit_cy["limitations"]}


@pytest.mark.db
def test_both_language_texts_verify_against_their_source_documents(
    session: Session, catalog: Catalog, store: LocalObjectStore, welsh_ingested: IngestionReport
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    ids = session.scalars(
        select(m.SourceDocument.id).where(m.SourceDocument.external_id.like("legislation.gov.uk:anaw/2020/3@%"))
    ).all()
    assert len(ids) == 2
    for doc_id in ids:
        res = verify_source_document(session, catalog, store, doc_id, checker="pytest")
        assert res["result"] == "pass" and res["checked_units"] > 0, res
