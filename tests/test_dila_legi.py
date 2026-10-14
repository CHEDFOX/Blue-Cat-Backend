"""France: DILA LEGI consolidated legislation (connector, parser, module, ingestion).

Real files from the DILA stock Freemium_legi_global_20250713-140000.tar.gz (Licence Ouverte 2.0), see
tests/fixtures/dila_legi/README.md. The archive-level tests rebuild tiny archives in the layout of the DILA dump from
those files; the synthetic increment in them only exercises the overlay mechanism.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from defusedxml.ElementTree import fromstring
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.fr import FrModule, format_article
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.dila_dump import DilaArchives, archive_from_name, parse_listing
from ulis.sources.connectors.dila_legi import DilaLegiConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import dila_legi as parser
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "dila_legi"
LOI = "JORFTEXT000000886460"
CIVIL = "LEGITEXT000006070721"
STOCK = "Freemium_legi_global_20250713-140000.tar.gz"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"


def bundle(name: str) -> bytes:
    return (FIX / name).read_bytes()


def parse(name: str) -> ParsedDocument:
    (doc,) = parser.parse_dila_legi(bundle(name))
    return doc


def versions(doc: ParsedDocument, eid: str) -> list[Any]:
    return sorted((p for p in doc.provisions if p.eid == eid), key=lambda p: (p.valid_from or date.min, p.status))


# ------------------------------------------------------------------------------------------------------ parser
def test_law_metadata_and_expression() -> None:
    doc = parse("loi-78-17.zip")
    assert doc.work_uri == f"/fr/legi/{LOI}" and doc.expression_uri == f"/fr/legi/{LOI}/fra@2025-07-13"
    assert (doc.instrument_type, doc.jurisdiction_id, doc.language) == ("act", "FR", "fr")
    assert doc.title == "Loi n° 78-17 du 6 janvier 1978 relative à l'informatique, aux fichiers et aux libertés"
    assert doc.short_title == "Loi n° 78-17 du 6 janvier 1978" and doc.number == "78-17"
    assert doc.work_date == date(1978, 1, 6)
    assert [(e.event_type, e.date) for e in doc.events] == [
        ("enactment", date(1978, 1, 6)),
        ("publication", date(1978, 1, 7)),
    ]
    assert (doc.validity_basis, doc.text_origin, doc.consolidation_kind) == (
        "source_periods",
        "official_consolidation",
        "version_history",
    )
    assert doc.authoritative is False and doc.version_date == doc.currency_date == date(2025, 7, 13)
    assert doc.ext["legitext"] == "LEGITEXT000006068624" and doc.ext["nature"] == "LOI"


def test_article_versions_carry_the_source_periods() -> None:
    doc = parse("loi-78-17.zip")
    v = versions(doc, "art-1")
    assert [(p.valid_from, p.valid_to) for p in v] == [
        (date(1978, 7, 23), date(2016, 10, 9)),
        (date(2016, 10, 9), date(2019, 6, 1)),
        (date(2019, 6, 1), None),  # DATE_FIN 2999-01-01 is "no end"
    ]
    assert all(p.status == "in_force" and p.num_path == ["1"] and p.kind == "article" for p in v)
    assert v[0].text.startswith("L'informatique doit être au service de chaque citoyen.")
    assert v[2].note and "ordonnance n° 2018-1125 du 12 décembre 2018" in v[2].note
    # amendment links of the source go to the note, not to the text
    assert "MODIFICATION: Loi n°2004-801 du 6 août 2004 - art. 1" in "\n".join(
        p.note or "" for p in versions(doc, "art-4")
    )
    assert all("MODIFICATION" not in p.text for p in doc.provisions)


def test_sections_are_containers_and_articles_sit_under_them() -> None:
    doc = parse("loi-78-17.zip")
    sections = {p.eid: p for p in doc.provisions if p.kind != "article"}
    assert sections and all(not p.text and not p.is_leaf for p in sections.values())
    chapter = next(p for p in sections.values() if p.heading and p.heading.startswith("Chapitre Ier : Principes"))
    assert (chapter.kind, chapter.num_path, chapter.top_kind) == ("chapter", ["Ier"], "chapter")
    art1 = versions(doc, "art-1")[0]
    assert art1.parent_eid == chapter.eid and art1.heading == chapter.heading
    # a parent is always listed before its children
    seen: set[str] = set()
    for p in doc.provisions:
        assert p.parent_eid is None or p.parent_eid in seen
        seen.add(p.eid)
    assert all(p.sort_order >= 0 for p in doc.provisions)


def test_number_reused_after_the_reform_and_repeal_markers() -> None:
    doc = parse("code-civil.zip")
    assert doc.instrument_type == "code" and doc.work_date is None and doc.events == []
    v = versions(doc, "art-1100")
    assert [(p.status, p.valid_from, p.valid_to) for p in v] == [
        ("in_force", date(1804, 3, 21), date(2002, 3, 5)),
        ("repealed", date(2002, 3, 5), None),  # abrogated in 2002 ...
        ("in_force", date(2016, 10, 1), None),  # ... and the number re-used by the 2016 reform
    ]
    assert v[1].text == "" and v[1].note == "ETAT ABROGE; DATE_FIN 2002-03-05."
    assert v[2].text.startswith("Les obligations naissent d'actes juridiques")
    both = versions(doc, "art-1240")
    assert [(p.valid_from, p.valid_to) for p in both] == [
        (date(1804, 3, 21), date(2016, 10, 1)),
        (date(2016, 10, 1), None),
    ]
    assert both[1].text.startswith("Tout fait quelconque de l'homme, qui cause à autrui un dommage")
    # TRANSFERE: the article left the code; the end of its last version is recorded as a repeal
    moved = versions(doc, "art-2119")
    assert [(p.status, p.valid_from) for p in moved] == [
        ("in_force", date(1804, 3, 21)),
        ("repealed", date(2006, 3, 24)),
    ]
    assert moved[1].note == "ETAT TRANSFERE; DATE_FIN 2006-03-24."


def test_scheduled_change_and_versions_never_in_force() -> None:
    doc = parse("code-civil.zip")
    scheduled = versions(doc, "art-1844-11")
    assert [(p.status, p.valid_from, p.valid_to) for p in scheduled] == [
        ("in_force", date(1978, 7, 1), date(2025, 10, 1)),  # ETAT ABROGE_DIFF, replaced on 2025-10-01
        ("in_force", date(2025, 10, 1), None),  # ETAT VIGUEUR_DIFF
    ]
    assert not [p for p in scheduled if p.status == "repealed"]  # a successor starts on the end date
    # MODIFIE_MORT_NE (DATE_DEBUT 2009-01-01 after DATE_FIN 2008-08-06) is not ingested
    assert [p.valid_from for p in versions(doc, "art-445")] == [date(1965, 6, 15), date(2009, 2, 1)]


def test_placeholder_dates_are_not_dates() -> None:
    assert parser.parse_date("2999-01-01") == (None, "open")
    assert parser.parse_date("2222-02-22") == (None, "placeholder")
    assert parser.parse_date("1804-03-21") == (date(1804, 3, 21), "date")
    assert parser.parse_date("") == (None, "missing") and parser.parse_date("1804-13-45") == (None, "missing")


def test_text_is_the_exact_text_of_the_article() -> None:
    root = fromstring(
        b"<CONTENU><br/>   I. - Une soci\xc3\xa9t\xc3\xa9 est   consid\xc3\xa9r\xc3\xa9e :<br/>\n"
        b"<br/>   1\xc2\xb0 Lorsqu'elle d\xc3\xa9tient ;<br/><p>Alin\xc3\xa9a <i>deux</i> \xc2\xab fin \xc2\xbb.</p>"
        b"<table><tr><td>a</td><td> b </td></tr></table></CONTENU>"
    )
    assert parser.render_text(root) == (
        "I. - Une société est considérée :\n1° Lorsqu'elle détient ;\nAlinéa deux « fin ».\na\tb"
    )
    assert parser.render_text(None) == ""


def test_identifiers() -> None:
    assert parser.article_eid("L. 233-3") == parser.article_eid("L233-3") == "art-L233-3"
    assert parser.article_eid("R*229-8") == parser.article_eid("R229-8") == "art-R229-8"
    assert parser.article_eid("1 A") == "art-1A" and parser.article_eid("***") == "art-x"
    assert parser.section_eid("LEGISCTA000006095881") == "sec-6095881"
    assert parser.section_kind("LIVRE II : Des sociétés") == ("book", ["II"])
    assert parser.section_kind("Sous-section 3 : Des organes") == ("subsection", ["3"])
    assert parser.section_kind("Partie législative") == ("part", ["législative"])
    assert parser.section_kind("Chapitre préliminaire : Des tailles") == ("chapter", ["préliminaire"])
    assert parser.section_kind("Dispositions générales")[0] == "division"


def test_bundle_must_be_a_bundle(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a zip"):
        parser.parse_dila_legi(b"<xml/>")
    for member, data in (
        ("../evil.xml", b"x"),
        ("article/not-an-id.xml", b"x"),
    ):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("bundle.json", "{}")
            zf.writestr(member, data)
        with pytest.raises(ValueError, match="unexpected bundle member"):
            parser.parse_dila_legi(buffer.getvalue())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("bundle.json", json.dumps({"format": "other"}))
    with pytest.raises(ValueError, match="unknown bundle format"):
        parser.parse_dila_legi(buffer.getvalue())
    # a text with no article text is skipped, not an error
    source = zipfile.ZipFile(FIX / "loi-78-17.zip")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name in source.namelist():
            if not name.startswith(("article/", "section_ta/")):
                zf.writestr(name, source.read(name))
    with pytest.raises(ParseSkipped):
        parser.parse_dila_legi(buffer.getvalue())


def _rewritten(name: str, replace: dict[str, tuple[str, str]], add: dict[str, bytes]) -> bytes:
    """A fixture bundle with some members edited (member -> (old, new)) and others added: mechanism tests only."""
    source = zipfile.ZipFile(FIX / name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for member in source.namelist():
            data = source.read(member)
            if member in replace:
                old, new = replace[member]
                assert old.encode() in data
                data = data.replace(old.encode(), new.encode())
            zf.writestr(member, data)
        for member, data in add.items():
            zf.writestr(member, data)
    return buffer.getvalue()


def test_a_text_with_several_version_files_is_named_by_the_latest() -> None:
    # A text can hold the versions of its successive titles ("Code rural", then "Code rural et de la pêche maritime").
    source = zipfile.ZipFile(FIX / "loi-78-17.zip")
    version = source.read("version/LEGITEXT000006068624.xml").decode()
    older = (
        version.replace("<ETAT>VIGUEUR</ETAT>", "<ETAT>MODIFIE</ETAT>")
        .replace("<DATE_DEBUT>1978-01-07</DATE_DEBUT>", "<DATE_DEBUT>1900-01-01</DATE_DEBUT>")
        .replace("<DATE_FIN>2999-01-01</DATE_FIN>", "<DATE_FIN>1978-01-07</DATE_FIN>")
        .replace("<TITREFULL>Loi n°", "<TITREFULL>Ancienne loi n°")
    )
    assert older != version
    content = _rewritten(
        "loi-78-17.zip",
        {},
        {
            "version/LEGITEXT000000000001.xml": older.encode(),
            "struct/LEGITEXT000000000001.xml": source.read("struct/LEGITEXT000006068624.xml"),
        },
    )
    (doc,) = parser.parse_dila_legi(content)
    plain = parse("loi-78-17.zip")
    assert doc.title == plain.title and doc.ext["legitext"] == "LEGITEXT000006068624"
    assert [(p.eid, p.status, p.valid_from) for p in doc.provisions] == [
        (p.eid, p.status, p.valid_from) for p in plain.provisions
    ]


def test_section_versions_starting_the_same_day_keep_the_longest() -> None:
    struct = zipfile.ZipFile(FIX / "loi-78-17.zip").read("struct/LEGITEXT000006068624.xml").decode()
    line = next(row for row in struct.splitlines() if 'id="LEGISCTA000006095881"' in row)
    shorter = line.replace('id="LEGISCTA000006095881"', 'id="LEGISCTA000099999999"').replace(
        'fin="2019-06-01"', 'fin="2010-01-01"'
    )
    content = _rewritten("loi-78-17.zip", {"struct/LEGITEXT000006068624.xml": (line, line + "\n" + shorter)}, {})
    (doc,) = parser.parse_dila_legi(content)
    keys = [(p.eid, p.status, p.valid_from) for p in doc.provisions]
    assert len(keys) == len(set(keys))
    periods = [(p.valid_from, p.valid_to) for p in doc.provisions if p.eid == "sec-6095881"]
    assert periods == [(date(1978, 7, 23), date(2019, 6, 1)), (date(2019, 6, 1), None)]
    assert periods == [(p.valid_from, p.valid_to) for p in parse("loi-78-17.zip").provisions if p.eid == "sec-6095881"]


# ------------------------------------------------------------------------------------------------------ citations
def _cite(module: FrModule, doc: ParsedDocument, eid: str, extensions: dict[str, Any] | None = None) -> str:
    p = versions(doc, eid)[-1]
    return module.provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.short_title or doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="FR",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="fr",
            ext=doc.ext,
            profile_extensions=extensions or {},
            eid=eid,
        )
    )


def test_citations_in_french_style() -> None:
    module = FrModule()
    civil, loi = parse("code-civil.zip"), parse("loi-78-17.zip")
    abbreviations = {"fr": {"abbreviations": {CIVIL: {"title": "Code civil", "cite_as": "C. civ."}}}}
    assert _cite(module, civil, "art-1240", abbreviations) == "C. civ., art. 1240"
    assert _cite(module, civil, "art-1240") == "Code civil, art. 1240"
    assert _cite(module, loi, "art-22") == "Loi n° 78-17 du 6 janvier 1978, art. 22"
    # an abbreviation is only used for the title it was chosen for
    other = {"fr": {"abbreviations": {CIVIL: {"title": "Code civil (autre)", "cite_as": "C. civ."}}}}
    assert _cite(module, civil, "art-1240", other) == "Code civil, art. 1240"
    assert [format_article(n) for n in ("L233-3", "R*229-8", "D. 12", "1240", "1 A", "LO135-1", "A663-21")] == [
        "L. 233-3",
        "R*. 229-8",
        "D. 12",
        "1240",
        "1 A",
        "LO. 135-1",
        "A. 663-21",
    ]
    chapter = next(p for p in loi.provisions if p.heading and p.heading.startswith("Chapitre Ier : Principes"))
    pin = module.pinpoint(
        ProvisionCitationInput("x", "act", "FR", chapter.top_kind, chapter.num_path, "fr", eid=chapter.eid)
    )
    assert pin == "chapitre Ier"


def test_profile_and_catalog_declare_france(catalog: Catalog) -> None:
    profile = catalog.jurisdictions.get("FR")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["fr"]
    assert "code" in profile.instrument_types and {c.id for c in profile.courts} >= {"FR-CC", "FR-CE", "FR-CCASS"}
    assert profile.memberships == []  # left to the lead (EU membership is not recorded by this source)
    abbreviations = profile.extensions["fr"]["abbreviations"]
    assert abbreviations[CIVIL] == {"title": "Code civil", "cite_as": "C. civ."}
    source = catalog.sources.get("fr-dila-legi")
    assert (source.connector, source.parser, source.status) == ("dila_legi", "dila_legi_xml", "active")
    assert source.licence.id == "Etalab-Licence-Ouverte-2.0" and source.licence.attribution_required
    assert "echanges.dila.gouv.fr" in (source.licence.attribution_text or "")


# ------------------------------------------------------------------------------------------------------ archives
def _pairs(base: str) -> str:
    digits = base[8:20]
    return "/".join(digits[i : i + 2] for i in range(0, 10, 2))


def _dump_path(tid: str, tree: str, member: str, src: str, top: str) -> str:
    kind, _, name = member.partition("/")
    base = name.removesuffix(".xml")
    text_dir = f"legi/global/{top}/{tree}/{src}/TEXT/{_pairs(tid)}/{tid}"
    if kind in ("struct", "version"):
        return f"{text_dir}/texte/{kind}/{name}"
    if kind == "article":
        return f"{text_dir}/article/LEGI/ARTI/{_pairs(base)}/{name}"
    return f"{text_dir}/section_ta/LEGI/SCTA/{_pairs(base)}/{name}"


def build_archive(
    path: Path,
    texts: dict[str, tuple[Path, str]],
    prefix: str = "",
    extra: dict[str, bytes] | None = None,
    replace: dict[tuple[str, str], bytes] | None = None,
    only: set[tuple[str, str]] | None = None,
    twice: set[tuple[str, str]] | None = None,
) -> None:
    """A tar.gz in the layout of the DILA dump from fixture bundles ``{tid: (zip, tree)}``."""
    with tarfile.open(path, "w:gz") as tar:

        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        for tid, (zip_path, tree) in texts.items():
            top, src = ("code_et_TNC_en_vigueur", "LEGI" if tid.startswith("LEGI") else "JORF")
            sub = "code_en_vigueur" if tid.startswith("LEGI") else tree
            with zipfile.ZipFile(zip_path) as zf:
                for member in sorted(zf.namelist()):
                    if member == "bundle.json" or (only is not None and (tid, member) not in only):
                        continue
                    data = (replace or {}).get((tid, member), zf.read(member))
                    name = _dump_path(tid, sub, member, src, top)
                    add(name, data)
                    if twice and (tid, member) in twice:  # the same file again, as in the stock's two trees
                        add(name.replace(sub, "TNC_non_vigueur"), data.replace(b"VIGUEUR", b"ABROGE"))
        for name, data in (extra or {}).items():
            add(name, data)


def seed_cache(cache: Path, listing: list[str] | None = None, database: str = "LEGI") -> None:
    """robots.txt answered 404 (no rules) and, optionally, a directory index listing the given archive names."""
    host = cache / "echanges.dila.gouv.fr"
    host.mkdir(parents=True, exist_ok=True)
    (host / "robots.txt").write_bytes(b"")
    (host / "robots.txt.meta.json").write_text(
        json.dumps({"url": "https://echanges.dila.gouv.fr/robots.txt", "status": 404})
    )
    if listing is not None:
        index = host / "OPENDATA" / f"{database}-index.html"
        index.parent.mkdir(parents=True, exist_ok=True)
        rows = "\n".join(f'<a href="{name}">{name}</a> 2026-01-01 00:00 1M' for name in listing)
        index.write_text(f"<html><pre>{rows}</pre></html>")
        index.with_name(index.name + ".meta.json").write_text(json.dumps({"url": "x", "status": 200}))


def manifest_file(tmp_path: Path, stock_size: int, **overrides: Any) -> Path:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source_id": "fr-dila-legi",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "stock": {"file": STOCK, "date": "2025-07-13", "bytes": stock_size},
        "incrementals": {"enabled": False},
        "select": {"required": [{"cid": LOI, "title": "Loi 78-17"}], "natures": ["CODE"], "exclude": []},
    }
    manifest.update(overrides)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def stock_in_cache(cache: Path, **kwargs: Any) -> Path:
    directory = cache / "echanges.dila.gouv.fr" / "OPENDATA" / "LEGI"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STOCK
    build_archive(
        path,
        {LOI: (FIX / "loi-78-17.zip", "TNC_en_vigueur"), CIVIL: (FIX / "code-civil.zip", "code_en_vigueur")},
        **kwargs,
    )
    return path


def run_connector(tmp_path: Path, **kwargs: Any) -> tuple[DilaLegiConnector, list[RawDocument]]:
    cache = tmp_path / "cache"
    seed_cache(cache, kwargs.pop("listing", None))
    stock = stock_in_cache(cache, **kwargs.pop("stock_args", {}))
    manifest = manifest_file(tmp_path, stock.stat().st_size, **kwargs)
    connector = DilaLegiConnector(manifest, "fr-dila-legi", cache, offline=True, sleep=lambda _s: None)
    return connector, list(connector.fetch())


def test_connector_selects_texts_and_builds_bundles(tmp_path: Path) -> None:
    connector, docs = run_connector(tmp_path)
    assert [d.external_id for d in docs] == [LOI, CIVIL]  # required first, then the natures
    assert connector.fetch_errors == [] and connector.report["texts"] == 2
    law, code = docs
    assert (law.media_type, law.metadata["nature"], law.metadata["tree"]) == (
        "application/zip",
        "LOI",
        "TNC_en_vigueur",
    )
    assert law.metadata["stock"]["name"] == STOCK and law.uri.endswith(f"{STOCK}#{LOI}")
    # the XML files are the publisher's, byte for byte
    with zipfile.ZipFile(io.BytesIO(law.content)) as zf, zipfile.ZipFile(FIX / "loi-78-17.zip") as original:
        meta = json.loads(zf.read("bundle.json"))
        assert meta["currency_date"] == "2025-07-13" and meta["stock"]["name"] == STOCK
        assert (
            sorted(zf.namelist()) == sorted(original.namelist() + ["bundle.json"])
            or "bundle.json" in original.namelist()
        )
        for name in original.namelist():
            if name != "bundle.json":
                assert zf.read(name) == original.read(name)
    # the parser reads what the connector built
    (doc,) = parser.parse_dila_legi(code.content)
    assert doc.work_uri == f"/fr/legi/{CIVIL}"
    # a second run yields identical bytes (cached bundles)
    again = [
        d.content
        for d in DilaLegiConnector(connector.manifest_path, "fr-dila-legi", connector.cache_dir, offline=True).fetch()
    ]
    assert again == [d.content for d in docs]


def test_selection_by_nature_only_and_missing_required(tmp_path: Path) -> None:
    connector, docs = run_connector(
        tmp_path,
        select={"required": [{"cid": "JORFTEXT000000000001", "title": "absent"}], "natures": ["CODE"], "exclude": []},
    )
    assert [d.external_id for d in docs] == [CIVIL]
    assert connector.fetch_errors == ["JORFTEXT000000000001 (absent) is not in the stock"]
    connector, docs = run_connector(
        tmp_path / "b", select={"required": [], "natures": ["CODE", "LOI"], "exclude": [CIVIL]}
    )
    assert [d.external_id for d in docs] == [LOI]


def test_duplicate_files_in_the_stock_keep_the_first(tmp_path: Path) -> None:
    with zipfile.ZipFile(FIX / "loi-78-17.zip") as zf:
        first = next(n for n in zf.namelist() if n.startswith("article/"))
    connector, docs = run_connector(tmp_path, stock_args={"twice": {(LOI, first)}})
    law = docs[0]
    with zipfile.ZipFile(io.BytesIO(law.content)) as zf, zipfile.ZipFile(FIX / "loi-78-17.zip") as original:
        assert zf.read(first) == original.read(first)  # the second copy (state ABROGE) was ignored
    assert connector.report["texts"] == 2


INC1, INC2 = "LEGI_20250714-205013.tar.gz", "LEGI_20250715-205701.tar.gz"


def increment_cache(tmp_path: Path) -> tuple[Path, int, str, str, bytes]:
    """A cache with the stock and two increments: the first changes one article file (synthetic change, only to see
    the overlay at work) and deletes another, the second holds nothing for the selected texts. Returns the cache, the
    stock size, the changed and the removed article members and the changed bytes."""
    with zipfile.ZipFile(FIX / "loi-78-17.zip") as zf:
        articles = [n for n in zf.namelist() if n.startswith("article/")]
        changed, removed = articles[0], articles[1]
        new = zf.read(changed).replace(b"</ARTICLE>", b"</ARTICLE>\n")
        assert new != zf.read(changed)
    cache = tmp_path / "cache"
    seed_cache(cache, [STOCK, "LEGI_20250712-211706.tar.gz", INC1, INC2])
    stock = stock_in_cache(cache)
    deleted = _dump_path(LOI, "TNC_en_vigueur", removed, "JORF", "code_et_TNC_en_vigueur").removesuffix(".xml")
    build_archive(
        stock.parent / INC1,
        {LOI: (FIX / "loi-78-17.zip", "TNC_en_vigueur")},
        prefix="20250714-205013/",
        only={(LOI, changed)},
        replace={(LOI, changed): new},
        extra={"20250714-205013/liste_suppression_legi.dat": (deleted + "\n").encode()},
    )
    build_archive(
        stock.parent / INC2, {}, prefix="20250715-205701/", extra={"20250715-205701/legi/global/eli/x.xml": b"<x/>"}
    )
    return cache, stock.stat().st_size, changed, removed, new


def test_increments_overlay_the_stock_and_deletions_apply(tmp_path: Path) -> None:
    cache, size, changed, removed, new = increment_cache(tmp_path)
    manifest = manifest_file(tmp_path, size, incrementals={"enabled": True, "through": "2026-01-01"})
    law = list(DilaLegiConnector(manifest, "fr-dila-legi", cache, offline=True, sleep=lambda _s: None).fetch())[0]
    with zipfile.ZipFile(io.BytesIO(law.content)) as zf:
        meta = json.loads(zf.read("bundle.json"))
        assert (meta["increments_applied"], meta["currency_date"]) == (
            2,
            "2025-07-15",
        )  # the archive before the stock is skipped
        assert meta["last_archive"]["name"] == INC2
        assert zf.read(changed) == new and removed not in zf.namelist()
    assert law.metadata["last_archive"]["date"] == "2025-07-15"
    # a second run applies nothing twice and yields the same bundle
    again = list(DilaLegiConnector(manifest, "fr-dila-legi", cache, offline=True, sleep=lambda _s: None).fetch())
    assert again[0].content == law.content
    (doc,) = parser.parse_dila_legi(law.content)
    assert doc.currency_date == date(2025, 7, 15) and doc.expression_uri.endswith("/fra@2025-07-15")


def test_incrementals_stop_at_the_date_in_the_manifest(tmp_path: Path) -> None:
    cache, size, changed, removed, new = increment_cache(tmp_path)
    manifest = manifest_file(tmp_path, size, incrementals={"enabled": True, "through": "2025-07-14"})
    law = list(DilaLegiConnector(manifest, "fr-dila-legi", cache, offline=True, sleep=lambda _s: None).fetch())[0]
    with zipfile.ZipFile(io.BytesIO(law.content)) as zf:
        assert json.loads(zf.read("bundle.json"))["last_archive"]["name"] == INC1 and zf.read(changed) == new


def test_manifest_validation(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    good = manifest_file(tmp_path, 10)
    assert DilaLegiConnector(good, "fr-dila-legi", cache).manifest().stock.file == STOCK
    with pytest.raises(ManifestError, match="source_id"):
        DilaLegiConnector(good, "other", cache).manifest()
    bad = {
        "stock": {"file": "Freemium_legi_global_20250713-140000.tar.gz", "date": "2025-07-14", "bytes": 10},
    }
    with pytest.raises(ManifestError, match="stock.date"):
        DilaLegiConnector(manifest_file(tmp_path / "a", 10, **bad), "fr-dila-legi", cache).manifest()
    with pytest.raises(ManifestError, match="stock file name"):
        DilaLegiConnector(
            manifest_file(tmp_path / "b", 10, stock={"file": "x.tar.gz", "date": "2025-07-13", "bytes": 10}),
            "fr-dila-legi",
            cache,
        ).manifest()
    with pytest.raises(ManifestError, match="selects nothing"):
        DilaLegiConnector(
            manifest_file(tmp_path / "c", 10, select={"required": [], "natures": [], "exclude": []}),
            "fr-dila-legi",
            cache,
        ).manifest()
    with pytest.raises(ManifestError):
        DilaLegiConnector(manifest_file(tmp_path / "d", 10, crawl_delay_seconds=1), "fr-dila-legi", cache).manifest()
    with pytest.raises(ManifestError, match="user_agent"):
        DilaLegiConnector(manifest_file(tmp_path / "e", 10, user_agent="bot (x)"), "fr-dila-legi", cache).manifest()


def test_committed_manifest_is_valid(catalog: Catalog) -> None:
    source = catalog.sources.get("fr-dila-legi")
    assert source.access.endpoint
    connector = DilaLegiConnector(REPO_ROOT / source.access.endpoint, "fr-dila-legi", Path("/nonexistent"))
    manifest = connector.manifest()
    assert manifest.stock.file == STOCK and manifest.stock.sha256 and manifest.incrementals.enabled
    required = {t.cid for t in manifest.select.required}
    assert {"JORFTEXT000000571356", CIVIL, "LEGITEXT000006070719", "LEGITEXT000006072050", LOI} <= required
    assert {"CODE", "CONSTITUTION", "LOI", "ORDONNANCE"} <= set(manifest.select.natures)
    # the abbreviations of the profile are for texts the manifest requires
    profile = catalog.jurisdictions.get("FR")
    assert set(profile.extensions["fr"]["abbreviations"]) <= required


def test_offline_connector_needs_its_cache(tmp_path: Path) -> None:
    manifest = manifest_file(tmp_path, 10)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(DilaLegiConnector(manifest, "fr-dila-legi", tmp_path / "empty", offline=True).fetch())
    seed_cache(tmp_path / "c2")
    with pytest.raises(FetchError, match="offline and not cached"):
        list(DilaLegiConnector(manifest, "fr-dila-legi", tmp_path / "c2", offline=True).fetch())


# ------------------------------------------------------------------------------------------------------ the host
def test_listing_names_and_robots(tmp_path: Path) -> None:
    html = (
        '<a href="?C=N;O=D">Name</a><a href="LEGI_20250715-205701.tar.gz">x</a><a href="%20DILA.pdf">x</a>'
        f'<a href="{STOCK}">x</a><a href="LEGI_20250713-205013.tar.gz">x</a><a href="JADE_20250713-205013.tar.gz">x</a>'
    )
    archives = parse_listing(html, "LEGI")
    assert [(a.name, a.kind) for a in archives] == [
        (STOCK, "stock"),
        ("LEGI_20250713-205013.tar.gz", "increment"),
        ("LEGI_20250715-205701.tar.gz", "increment"),
    ]
    assert archive_from_name("LEGI_20250713-205013.tar.gz", "JADE") is None
    cache = tmp_path / "cache"
    seed_cache(cache)
    robots = cache / "echanges.dila.gouv.fr" / "robots.txt"
    robots.write_text("User-agent: *\nDisallow: /OPENDATA/LEGI/\n")
    robots.with_name("robots.txt.meta.json").write_text(json.dumps({"url": "x", "status": 200}))
    with pytest.raises(FetchError, match="robots.txt of echanges.dila.gouv.fr disallows"):
        DilaArchives("LEGI", cache, offline=True).ensure(STOCK)
    with pytest.raises(FetchError, match="unexpected archive name"):
        DilaArchives("LEGI", cache, offline=True).path("../x.tar.gz")


class _Response(io.BytesIO):
    def __init__(self, data: bytes, status: int, headers: dict[str, str]):
        super().__init__(data)
        self.status = status
        self.headers = headers

    def __enter__(self) -> _Response:
        return self


def test_download_resumes_after_an_interrupted_transfer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = bytes(range(256)) * 20
    calls: list[str | None] = []

    class _Headers(dict):  # type: ignore[type-arg]
        def get(self, key: str, default: Any = None) -> Any:
            return super().get(key, default)

    def fake(request: urllib.request.Request, timeout: float = 0) -> _Response:
        assert request.get_header("User-agent") == UA
        rng = request.get_header("Range")
        calls.append(rng)
        if rng is None:  # the first transfer is cut after 100 bytes

            class _Cut(_Response):
                def read(self, *_: Any) -> bytes:
                    if getattr(self, "done", False):
                        raise ConnectionResetError("cut")
                    self.done = True
                    return payload[:100]

            return _Cut(b"", 200, _Headers({"Content-Length": str(len(payload))}))
        start = int(rng.removeprefix("bytes=").removesuffix("-"))
        return _Response(
            payload[start:], 206, _Headers({"Content-Range": f"bytes {start}-{len(payload) - 1}/{len(payload)}"})
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    cache = tmp_path / "cache"
    seed_cache(cache)
    archives = DilaArchives("LEGI", cache, sleep=lambda _s: None, crawl_delay=0)
    path = archives.ensure("LEGI_20250713-205013.tar.gz", len(payload))
    assert (
        path.read_bytes() == payload
        and calls == [None, "bytes=100-"]
        and not path.with_name(path.name + ".part").exists()
    )
    assert archives.ensure("LEGI_20250713-205013.tar.gz", len(payload)) == path and len(calls) == 2  # cached
    with pytest.raises(FetchError, match="manifest pins"):
        archives.ensure("LEGI_20250713-205013.tar.gz", len(payload) + 1)


def test_download_stops_on_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: urllib.request.Request, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    cache = tmp_path / "cache"
    seed_cache(cache)
    with pytest.raises(FetchError, match="HTTP 403"):
        DilaArchives("LEGI", cache, sleep=lambda _s: None, crawl_delay=0).ensure("LEGI_20250713-205013.tar.gz")


# ------------------------------------------------------------------------------------------------------ PostgreSQL
class _Bundles:
    def __init__(self, names: dict[str, str]):
        self.names = names

    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 14, tzinfo=UTC)
        for tid, name in self.names.items():
            yield RawDocument(
                tid,
                f"https://echanges.dila.gouv.fr/OPENDATA/LEGI/{STOCK}#{tid}",
                "application/zip",
                bundle(name),
                now,
                {},
            )


@pytest.fixture(scope="module")
def fr_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "fr-dila-legi", actor="pytest", connector=_Bundles({LOI: "loi-78-17.zip", CIVIL: "code-civil.zip"})
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, q: str, as_of: date) -> dict[str, Any]:
    return search(
        session,
        catalog,
        SearchRequest(q=q, jurisdiction="FR", as_of=as_of, query_language="fr"),
        today=date(2026, 10, 14),
    )


@pytest.mark.db
def test_search_finds_an_article_with_its_citation_and_limitations(
    session: Session, catalog: Catalog, fr_ingested: IngestionReport
) -> None:
    assert fr_ingested.errors == [] and fr_ingested.conflicts == []
    assert fr_ingested.documents_seen == 2 and fr_ingested.documents_new + fr_ingested.documents_unchanged == 2
    res = _search(session, catalog, "tout fait quelconque de l'homme qui cause à autrui un dommage", date(2024, 1, 1))
    hit = res["results"][0]
    assert hit["citation"] == "C. civ., art. 1240"
    assert hit["text"].startswith("Tout fait quelconque de l'homme, qui cause à autrui un dommage")
    assert hit["version"]["language"] == "fr" and hit["version"]["valid_from"] == "2016-10-01"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"PROFILE_UNVERIFIED", "CURRENCY"} <= codes and "AS_OF_AFTER_CURRENCY" not in codes
    later = _search(session, catalog, "tout fait quelconque de l'homme qui cause à autrui un dommage", date(2026, 6, 1))
    assert "AS_OF_AFTER_CURRENCY" in {lim["code"] for lim in later["results"][0]["limitations"]}  # currency 2025-07-13
    assert hit["source"]["attribution"].startswith("Source: Direction de l'information légale et administrative (DILA)")
    # an article of a law, found by its words as of its date
    res = _search(session, catalog, "L'informatique doit être au service de chaque citoyen", date(2010, 1, 1))
    first = res["results"][0]
    assert first["citation"] == "Loi n° 78-17 du 6 janvier 1978, art. 1"
    assert first["version"]["valid_from"] == "1978-07-23" and first["version"]["valid_to"] == "2016-10-09"


@pytest.mark.db
def test_point_in_time_follows_the_source_periods(
    session: Session, catalog: Catalog, fr_ingested: IngestionReport
) -> None:
    uri = f"/fr/legi/{CIVIL}/~art-1100"
    in_1990 = provision_at(session, catalog, uri, date(1990, 1, 1))
    assert in_1990["status_on_date"] == "in_force" and "personnes interposées" in in_1990["version"]["text"]
    assert in_1990["version"]["valid_to"] == "2002-03-05" and in_1990["version"]["valid_to_basis"] == "explicit"
    gap = provision_at(session, catalog, uri, date(2010, 1, 1))
    assert gap["status_on_date"] == "repealed"  # abrogated in 2002, the number is free until the 2016 reform
    boundary = provision_at(session, catalog, uri, date(2016, 10, 1))
    assert boundary["status_on_date"] == "in_force" and boundary["version"]["text"].startswith(
        "Les obligations naissent"
    )
    moved = f"/fr/legi/{CIVIL}/~art-2119"
    assert provision_at(session, catalog, moved, date(2000, 1, 1))["status_on_date"] == "in_force"
    assert provision_at(session, catalog, moved, date(2006, 3, 24))["status_on_date"] == "repealed"
    assert provision_at(session, catalog, moved, date(1800, 1, 1))["status_on_date"] in (
        "not_yet_in_force",
        "history_not_indexed",
    )
    # the law's article 22 had nine wordings; 2016-01-28 was a boundary
    art22 = f"/fr/legi/{LOI}/~art-22"
    before = provision_at(session, catalog, art22, date(2016, 1, 27))
    on_day = provision_at(session, catalog, art22, date(2016, 1, 28))
    assert before["version"]["id"] != on_day["version"]["id"]
    assert on_day["provision"]["citation"] == "Loi n° 78-17 du 6 janvier 1978, art. 22"
    assert "LATER_VERSION_EXISTS" in {lim["code"] for lim in on_day["limitations"]}


@pytest.mark.db
def test_stored_provision_texts_come_from_the_raw_bundle(
    session: Session, catalog: Catalog, store: LocalObjectStore, fr_ingested: IngestionReport
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    pv = session.scalars(
        select(m.ProvisionVersion).join(m.Provision).where(m.Provision.uri == f"/fr/legi/{CIVIL}/~art-1240").limit(1)
    ).first()
    assert pv is not None
    doc = session.get(m.SourceDocument, pv.source_document_id)
    assert doc is not None and doc.external_id == CIVIL
    assert store.get(doc.storage_key) == bundle("code-civil.zip")
