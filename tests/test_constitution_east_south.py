"""Constitutions of East and Southern Africa (RW, MG, MU, DJ, AO, SO, TZ, NA, SC): manifests, hints and citations.

The fixtures (``tests/fixtures/constitution_east_south``) are the real text lines of pages of the publishers' PDFs and
pages (``pdftotext``), written back into small PDFs by ``tests/constitution_pdf.py`` (and, for the documents printed in
parallel columns, by ``columns_pdf`` below), so the PDF path, the column crop and the layout mode are exercised without
committing megabytes. The structure hints are read from the committed manifests, so a change of a manifest is tested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from constitution_pdf import make_pdf
from test_constitution_text import bundle, parse, text_bundle

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.constitution_docs import ConstitutionDocsConnector
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.registry import LicenceNotApproved, licence_gate

FIX = REPO_ROOT / "tests" / "fixtures" / "constitution_east_south"
MANIFESTS = REPO_ROOT / "config" / "manifests"

SOURCES = {
    "rw-minijust-constitution": ("www.minijust.gov.rw", {"rw-rw": "rw", "rw-en": "en", "rw-fr": "fr"}),
    "mg-assemblee-constitution": ("www.assemblee-nationale.mg", {"mg-fr": "fr", "mg-mg": "mg"}),
    "mu-assembly-constitution": ("assembly-rra.govmu.org", {"mu-en": "en"}),
    "dj-presidence-constitution": ("www.presidence.dj", {"dj-fr": "fr"}),
    "ao-tribunal-constitucional-constitution": ("www.tribunalconstitucional.ao", {"ao-pt": "pt"}),
    "so-senate-constitution": ("senate.gov.so", {"so-so": "so"}),
    "tz-mocla-constitution": ("www.sheria.go.tz", {"tz-sw": "sw"}),
    "tz-nao-constitution": ("www.nao.go.tz", {"tz-en": "en"}),
    "na-ombudsman-constitution": ("ombudsman.org.na", {"na-en": "en"}),
    "sc-ecs-constitution": ("ecs.sc", {"sc-en": "en"}),
}


def _json(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fixture_pdf(name: str) -> bytes:
    """A PDF of the fixture's pages; a page that would not fit on the sheet is continued on the next one."""
    pages = [lines[i : i + 60] for lines in _json(name) for i in range(0, max(len(lines), 1), 60)]
    return make_pdf(pages)


def _entry(source_id: str, slug: str) -> dict[str, Any]:
    manifest = yaml.safe_load((MANIFESTS / f"{source_id}.yaml").read_text(encoding="utf-8"))
    return next(d for d in manifest["documents"] if d["slug"] == slug)


def columns_pdf(pages: list[tuple[tuple[int, int], list[tuple[int, list[str]]]]], size: int = 9, leading: int = 11) -> bytes:
    """A PDF whose pages have text in columns: ``(width, height), [(x, lines), ...]``, every column from the top."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for (width, height), columns in pages:
        content = ["BT", f"/F1 {size} Tf"]
        for x, lines in columns:
            content.append(f"1 0 0 1 {x} {height - 40} Tm {leading} TL")
            for line in lines:
                esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                content.append(f"({esc}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("cp1252")
        data = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(
            add(
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {data} 0 R >>".encode()
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objects[pages_obj - 1] = (
        "<< /Type /Pages /Kids [" + " ".join(f"{k} 0 R" for k in kids) + f"] /Count {len(kids)} >>"
    ).encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def doc_from(
    source_id: str, slug: str, body: bytes, media: str = "pdf", *, drop: tuple[str, ...] = (), **structure: Any
) -> ParsedDocument:
    """Parse a fixture with the committed manifest's entry (hints, title, languages); ``drop`` names hints that only
    make sense for the whole publisher file (page ranges, the start of the body), ``structure`` overrides hints."""
    entry = _entry(source_id, slug)
    hints = {k: v for k, v in entry["structure"].items() if k not in drop}
    hints.update(structure)
    meta = {k: entry[k] for k in ("adopted_on", "edition_date", "edition_label", "events") if k in entry}
    return parse(
        bundle(
            body,
            media,
            cc=entry["jurisdiction"],
            language=entry["language"],
            structure=hints,
            title=entry["title"],
            citation_name=entry["citation_name"],
            **meta,
        )
    )


def prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def cite(doc: ParsedDocument, eid: str, module_id: str = "constitution") -> str:
    p = prov(doc, eid)
    return get_module(module_id).provision_citation(
        ProvisionCitationInput(
            doc.short_title or "", "constitution", doc.jurisdiction_id, p.top_kind, p.num_path, doc.language, eid=eid
        )
    )


def eids(doc: ParsedDocument, kind: str = "article") -> list[str]:
    return [p.eid for p in doc.provisions if p.kind == kind]


# --------------------------------------------------------------------------- manifests and descriptors


@pytest.mark.parametrize("source_id", sorted(SOURCES))
def test_committed_manifest_is_valid_and_names_its_host(source_id: str, tmp_path: Path) -> None:
    host, slugs = SOURCES[source_id]
    connector = ConstitutionDocsConnector(MANIFESTS / f"{source_id}.yaml", source_id, tmp_path, offline=True)
    manifest = connector._manifest()  # the connector's own validation of slugs, hosts, https, formats and dates
    assert manifest["hosts"] == [host]
    assert {d["slug"]: d["language"] for d in manifest["documents"]} == slugs
    assert manifest["user_agent"].startswith("ULIS-research-bot/")


@pytest.mark.parametrize("source_id", sorted(SOURCES))
def test_descriptor_is_active_with_the_publishers_words_and_the_gate_lets_it_in(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == "active" and source.connector == "constitution_docs" and source.parser == "constitution_text"
    assert source.document_kinds == ["constitution"] and source.authority == "official_non_authentic"
    licence_gate(source)
    assert source.licence.commercial_use == "unknown"
    reviewed = source.licence.reviewed_by or ""
    assert "robots.txt" in reviewed and "Reading:" in reviewed
    assert "{retrieved_date}" in (source.licence.attribution_text or "")
    assert source.connector in CONNECTORS and source.parser in PARSERS


# --------------------------------------------------------------------------- Somali, Malagasy, Kinyarwanda


def test_somalia_ordinal_numbers_preamble_and_chapter_context() -> None:
    doc = doc_from("so-senate-constitution", "so-so", fixture_pdf("so-pages.json"))
    assert doc.provisions[0].eid == "preamble" and "Annaga oo ah dadka Soomaaliyeed" in (doc.provisions[0].text or "")
    assert eids(doc)[:5] == [f"art-{n}" for n in range(1, 6)]  # 'Qodobka 1aad', 'Qodobka 2aad' ... (contents page skipped)
    first = prov(doc, "art-1")
    assert first.heading == "Jamhuuriyadda Federaalka Soomaaliya"
    assert (first.text or "").startswith("(1) Soomaaliya waa Jamhuuriyad federaal ah")
    assert "CUTUBKA KOOWAAD" in (first.context_heading or "")
    assert (prov(doc, "art-1-par2").text or "").startswith("Soomaaliya waa Jamhuuriyad dimuqraadi ah")
    assert cite(doc, "art-1") == "Somalia Constitution art. 1"
    assert cite(doc, "art-1-par2") == "Somalia Constitution art. 1(2)"
    assert doc.language == "so" and [e.event_type for e in doc.events] == ["adoption"]


def test_madagascar_malagasy_first_article_word_and_a_misprinted_heading_are_kept() -> None:
    doc = doc_from("mg-assemblee-constitution", "mg-mg", fixture_pdf("mg-mg-pages.json"), drop=("first_page",))
    # 'Andininy voalohany' (first) is article 1; the misprint 'Adnininy 4' is not a heading, its text stays in article 3
    assert eids(doc) == ["art-1", "art-2", "art-3", "art-5", "art-6", "art-7"]
    assert (prov(doc, "art-1").text or "").startswith("Firenena iray ny Vahoaka Malagasy")
    assert "Adnininy 4.-" in (prov(doc, "art-3").text or "")
    assert "article 4 was not recognised" in (prov(doc, "art-3").note or "")
    assert cite(doc, "art-1") == "Madagascar Constitution art. 1"
    assert doc.provisions[0].eid == "preamble" and doc.provisions[0].heading == "SAVARANONANDO"


def test_madagascar_french_part_and_the_heading_printed_one_word_per_line() -> None:
    doc = doc_from(
        "mg-assemblee-constitution", "mg-fr", fixture_pdf("mg-fr-pages.json"), drop=("last_page", "tail_regex")
    )
    assert eids(doc)[:3] == ["art-1", "art-2", "art-3"]
    assert (prov(doc, "art-1").text or "").startswith("Le peuple Malagasy constitue une Nation organisée")
    assert "TITRE PREMIER" in (prov(doc, "art-1").context_heading or "")
    assert cite(doc, "art-2") == "Madagascar Constitution art. 2"


def test_rwanda_three_languages_are_read_from_their_columns_of_one_gazette() -> None:
    cols = _json("rw-columns.json")
    cover = ((595, 842), [(40, ["Official Gazette n° Special of 04/08/2023"])])
    sheets = [
        ((842, 595), [(90, cols["rw"][i : i + 40]), (316, cols["en"][i : i + 40]), (536, cols["fr"][i : i + 40])])
        for i in range(0, max(len(v) for v in cols.values()), 40)
    ]
    pdf = columns_pdf([cover, *sheets])
    en = doc_from("rw-minijust-constitution", "rw-en", pdf)
    fr = doc_from("rw-minijust-constitution", "rw-fr", pdf)
    rw = doc_from("rw-minijust-constitution", "rw-rw", pdf)
    for doc in (en, fr, rw):
        assert eids(doc)[:3] == ["art-1", "art-2", "art-3"], doc.language
    assert prov(en, "art-2").heading == "Suffrage"
    assert (prov(en, "art-2").text or "").startswith("(1) Suffrage is universal and equal for")
    assert prov(fr, "art-2").heading == "Suffrage"
    assert "Article" not in "".join(p.text or "" for p in rw.provisions if p.kind == "article")  # no other column leaks in
    assert "Ingingo" not in "".join(p.text or "" for p in en.provisions)
    assert "Tout pouvoir émane du peuple" in (prov(fr, "art-1").text or "")  # 'Article premier' is article 1
    assert "All power emanates from the people" in (prov(en, "art-1").text or "")  # 'Article One' is article 1
    assert "Ubutegetsi bwose bukomoka ku" in (prov(rw, "art-1").text or "")  # 'Ingingo ya mbere' is article 1
    assert cite(en, "art-2") == "Rwanda Constitution art. 2" == cite(fr, "art-2") == cite(rw, "art-2")
    assert en.version_date.isoformat() == "2023-08-04"


# --------------------------------------------------------------------------- English, Portuguese, French


def test_mauritius_sections_with_the_heading_below_the_number() -> None:
    doc = doc_from("mu-assembly-constitution", "mu-en", fixture_pdf("mu-pages.json"))
    assert eids(doc, "section")[:4] == ["sec-1", "sec-2", "sec-3", "sec-4"]
    assert prov(doc, "sec-1").heading == "The State"
    assert (prov(doc, "sec-2").text or "").startswith("This Constitution is the supreme law of Mauritius")
    assert "CHAPTER II" in (prov(doc, "sec-3").context_heading or "")
    assert cite(doc, "sec-1", "mu") == "Mauritius Constitution s. 1"
    assert [(e.event_type, e.date.isoformat()) for e in doc.events] == [("commencement", "1968-03-12")]


def test_seychelles_heading_above_the_number_and_chapter_context() -> None:
    doc = doc_from("sc-ecs-constitution", "sc-en", fixture_pdf("sc-pages.json"), drop=("last_page",))
    assert eids(doc)[:3] == ["art-1", "art-2", "art-3"]
    assert prov(doc, "art-2").heading == "National territory"
    assert (prov(doc, "art-1").text or "").startswith("Seychelles is a sovereign democratic Republic")
    assert cite(doc, "art-2") == "Seychelles Constitution art. 2"
    assert doc.version_date.isoformat() == "2020-06-01"  # 'CONSOLIDATED TO 1 JUNE 2020'


def test_angola_parenthesised_headings_and_dot_numbered_paragraphs() -> None:
    doc = doc_from("ao-tribunal-constitucional-constitution", "ao-pt", fixture_pdf("ao-pages.json"))
    assert doc.provisions[0].eid == "preamble"
    assert eids(doc)[:3] == ["art-1", "art-2", "art-3"]
    assert prov(doc, "art-1").heading == "República de Angola"
    assert prov(doc, "art-2").heading == "Estado Democrático de Direito"
    assert (prov(doc, "art-2-par1").text or "").startswith("A República de Angola é um Estado Democrático de Direito")
    assert cite(doc, "art-2-par1") == "Angola Constitution art. 2(1)"
    assert doc.version_date.isoformat() == "2022-02-28"
    assert sorted(e.event_type for e in doc.events) == ["adoption", "amendment", "promulgation"]


def test_namibia_layout_mode_keeps_paragraph_numbers_on_their_lines() -> None:
    doc = doc_from("na-ombudsman-constitution", "na-en", fixture_pdf("na-pages.json"), drop=("start_regex",))
    assert doc.provisions[0].eid == "preamble"
    assert eids(doc)[:3] == ["art-1", "art-2", "art-3"]
    assert prov(doc, "art-1").heading == "Establishment of the Republic of Namibia and"
    assert (prov(doc, "art-1-par6").text or "").startswith("This Constitution shall be the Supreme Law of Namibia")
    assert cite(doc, "art-1-par1") == "Namibia Constitution art. 1(1)"
    assert doc.version_date.isoformat() == "2016-02-29"


def test_tanzania_swahili_section_numbers_with_a_dash_before_the_subsection() -> None:
    # '2.-(1) Eneo la ...' is section 2, subsection 1
    doc = doc_from("tz-mocla-constitution", "tz-sw", fixture_pdf("tz-sw-pages.json"))
    assert eids(doc)[:4] == ["art-1", "art-2", "art-3", "art-4"]
    assert (prov(doc, "art-1").text or "").startswith("Tanzania ni nchi moja na ni Jamhuri ya Muungano")
    assert (prov(doc, "art-2-par1").text or "").startswith("Eneo la Jamhuri ya Muungano ni eneo lote")
    assert cite(doc, "art-2-par1") == "Tanzania Constitution art. 2(1)"
    assert doc.version_date.isoformat() == "2000-11-01"


def test_tanzania_english_margin_column_is_cut_off() -> None:
    pdf = columns_pdf([((595, 842), [(88, page["margin"]), (190, page["body"])]) for page in _json("tz-en-columns.json")])
    doc = doc_from("tz-nao-constitution", "tz-en", pdf, drop=())
    assert eids(doc)[:3] == ["art-1", "art-2", "art-3"]
    text = " ".join(p.text or "" for p in doc.provisions)
    assert "Act No.15" not in text and "of 1984" not in text  # the notes of the amending Acts are not text of the articles
    assert (prov(doc, "art-1").text or "").startswith("Tanzania is one State and is a sovereign United Republic")
    assert cite(doc, "art-2-par1") == "Tanzania Constitution art. 2(1)"
    assert doc.version_date.isoformat() == "2005-12-31"


def test_djibouti_page_articles_in_capitals() -> None:
    doc = doc_from(
        "dj-presidence-constitution", "dj-fr", ("<html><body>" + "".join(f"<p>{x}</p>" for x in _json("dj-lines.json")) + "</body></html>").encode(), "html"
    )
    assert doc.provisions[0].eid == "preamble"
    arts = eids(doc)
    assert arts[:2] == ["art-1", "art-2"] and arts[-1] == "art-10"
    assert prov(doc, "art-1").heading is None  # the text follows the number; it is not a heading
    assert (prov(doc, "art-1").text or "").startswith("L'État de Djibouti est une République démocratique")
    assert "Ses langues officielles sont: l'arabe et le français." in (prov(doc, "art-1").text or "")
    assert cite(doc, "art-10") == "Djibouti Constitution art. 10"


def test_new_number_forms_do_not_change_other_headings() -> None:
    # 'Article One', 'Ingingo ya mbere', 'Andininy voalohany' and '1aad' are first/ordinal forms; ordinary headings stay as they were
    lines = []
    for n in range(1, 8):
        lines += [f"Article {n}", f"Words of article {n}, long enough to count as text."]
    doc = parse(text_bundle(lines, structure={"keywords": ["Article"]}))
    assert eids(doc) == [f"art-{n}" for n in range(1, 8)]
    lines = ["Article One", "Words of the first article, long enough to count as text."]
    for n in range(2, 8):
        lines += [f"Article {n}", f"Words of article {n}, long enough to count as text."]
    doc = parse(text_bundle(lines, structure={"keywords": ["Article"]}))
    assert eids(doc) == [f"art-{n}" for n in range(1, 8)]


# --------------------------------------------------------------------------- publishers that could not be loaded


def test_consolidated_dates_and_the_not_loaded_sources_are_recorded_in_the_doc() -> None:
    text = (REPO_ROOT / "docs" / "constitutions-africa-east-south-deep.md").read_text(encoding="utf-8")
    for country in ("Ethiopia", "Eritrea", "Uganda", "Zambia", "Zimbabwe", "Mozambique", "Botswana", "Lesotho", "Malawi"):
        assert country in text, country
    assert LicenceNotApproved  # the gate test of the planned descriptors is in test_constitution_east_south_blocked.py
