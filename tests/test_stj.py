"""Brazilian case law from the STJ open data portal: parser, citations, connector, manifest and search behaviour.

The fixtures in ``tests/fixtures/stj`` are real: the text files and metadata records are verbatim members of the
portal's files for 2023-11-09 (texts in a folder, metadata inside the ZIP, dates in milliseconds) and 2026-10-07 (flat
texts, separate metadata with ISO dates), fetched 2026-10-15, repackaged into small ZIPs; the dataset page excerpt is a
set of real resource entries of the page. ``ROBOTS`` is the portal's robots.txt as read on that day.
"""

from __future__ import annotations

import json
import zipfile
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
from ulis.ingestion.pipeline import PARSERS, IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.languages import search_config
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.stj_dados_abertos import (
    DATASET_PAGE,
    ROBOTS_URL,
    StjConnector,
    build_file_set,
    download_url,
    parse_resources,
    robots_crawl_delay,
)
from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParseSkipped
from ulis.sources.parsers.stj_integras import (
    StjParseError,
    document_type,
    encode_envelope,
    parse_date,
    parse_stj_integra,
    split_paragraphs,
    work_uri_for,
)
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "br-stj-dados-abertos"
HOST = "dadosabertos.web.stj.jus.br"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "stj"
DATASET = "a2cd85cc-1391-4ebc-aeed-a45dd75a7987"
ROBOTS = (
    "User-agent: *\nDisallow: /dataset/rate/\nDisallow: /revision/\nDisallow: /dataset/*/history\n"
    "Disallow: /api/\nCrawl-Delay: 10"
)
RID_2023, RID_2026, RID_2026_JSON = (
    "4c8964a6-faa3-4fa2-9e05-e26d9010d5d0",
    "db6c0bd7-bb35-448b-9c6d-9551cf031cc5",
    "ac13c29f-e42e-464d-825e-5b742cba692e",
)
FAZENDA = "216734026"  # REsp 2097990, decisão, Fazenda Nacional
INVESTCO = "216938120"  # REsp 1923281, short decisão
ESTADO_RJ = "215363158"  # AREsp 2401605, decisão on embargos de declaração
ACORDAO = "215894556"  # AREsp 2297789, acórdão on embargos de declaração
UNIMED = "378802014"  # AREsp 3196481, decisão of 2026 (ISO dates)
NO_TEXT = "402726084"  # a 2026 metadata record whose text the portal does not publish
ALL_WITH_TEXT = [FAZENDA, INVESTCO, ESTADO_RJ, ACORDAO, UNIMED]


# --------------------------------------------------------------------------- fixtures


def _zip(name: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(FIX / name)


def _records_2023() -> list[dict[str, Any]]:
    z = _zip("texts_20231109.zip")
    return json.loads(z.read("metadados20231109.json"))


def _records_2026() -> list[dict[str, Any]]:
    return json.loads((FIX / "metadados20261007.json").read_text(encoding="utf-8"))


def _seq(record: dict[str, Any]) -> str:
    return str(record.get("SeqDocumento") or record.get("seqDocumento"))


def _envelope(seq: str, *, edit: Any = None) -> bytes:
    name, records = (
        ("texts_20261007.zip", _records_2026())
        if seq in (UNIMED, NO_TEXT)
        else (
            "texts_20231109.zip",
            _records_2023(),
        )
    )
    z = _zip(name)
    member = next(n for n in z.namelist() if n.endswith(f"{seq}.txt"))
    mine = [r for r in records if _seq(r) == seq]
    text = z.read(member).decode("utf-8")
    if edit:
        mine, text = edit(mine, text)
    return encode_envelope(seq, mine, text)


def _doc(seq: str, **kw: Any) -> ParsedDocument:
    docs = parse_stj_integra(_envelope(seq, **kw))
    assert len(docs) == 1
    return docs[0]


def _citation(doc: ParsedDocument, paragraph: str | None = None) -> str:
    assert doc.judgment is not None
    return get_module("br").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            "Superior Tribunal de Justiça",
            paragraph,
            "pt",
            "BR",
            doc.ext,
        )
    )


# --------------------------------------------------------------------------- parser


def test_decision_of_2023_metadata_dates_in_milliseconds_and_publisher_identifiers() -> None:
    doc = _doc(FAZENDA)
    assert doc.doc_kind == "judgment" and doc.language == "pt" and doc.jurisdiction_id == "BR"
    assert doc.judgment == ParsedJudgmentMeta(
        court_id="BR-STJ",
        case_name="REsp 2097990",  # the data has no case name: class and number stand in
        neutral_citation=None,
        decision_date=date(2023, 11, 9),  # dataPublicacao 1699495200000 ms, i.e. 2023-11-09 02:00 UTC
        docket="REsp 2097990",
    )
    assert doc.work_uri == work_uri_for(FAZENDA) == f"/br/stj/{FAZENDA}"
    assert doc.expression_uri == f"/br/stj/{FAZENDA}/por@2023-11-09" and doc.version_date == date(2023, 11, 9)
    assert doc.title == "REsp 2097990 (DECISÃO)" and doc.number == "REsp 2097990"
    assert doc.authoritative is False and doc.text_origin == "official" and doc.extent == ["BR"]
    assert doc.ext_module == "br" and doc.instrument_type == "judgment"
    assert doc.ext == {
        "seq_documento": FAZENDA,
        "numero_registro": doc.ext["numero_registro"],
        "processo": "REsp 2097990",
        "tipo_documento": "DECISÃO",
        "document_type": "decisao",
        "recurso": [],
        "ministros": ["BENEDITO GONÇALVES"],
        "teor": ["Concedendo"],
        "descricao_monocratica": ["Conhecido o recurso e provido"],
        "assuntos": ["6060;6060"],
        "data_publicacao": "2023-11-09",
        "data_recebimento": "2023-09-14",
        "data_distribuicao": "2023-09-29",
        "date_kind": "dje_publication",
    }
    assert len(doc.ext["numero_registro"]) == 12 and doc.ext["numero_registro"].startswith("2023")


def test_decision_text_is_split_at_the_files_line_breaks_and_headings_become_parts() -> None:
    doc = _doc(FAZENDA)
    assert [(p.eid, p.part, p.num) for p in doc.paragraphs] == [("dec-1", "decisao", None), ("em-1", "ementa", None)]
    first = doc.paragraphs[0].text
    assert first.startswith(
        "Trata-se de recurso especial interposto pela FAZENDA NACIONAL contra acórdão proferido pelo Tribunal "
        "Regional Federal da 3ª Região"
    )
    assert "DECISÃO" not in first.splitlines()[0] and "<br>" not in first and "\n" in first  # lines joined, no markup
    assert doc.paragraphs[1].text.endswith("FALTAS JUSTIFICADAS. INCIDÊNCIA. RECURSO PROVIDO.")
    raw = _zip("texts_20231109.zip").read(f"20231109/{FAZENDA}.txt").decode("utf-8")
    assert "\n".join(p.text for p in doc.paragraphs).replace("\n", "") == "".join(
        line.strip() for line in raw.replace("<br>", "\n").splitlines() if line.strip() not in ("DECISÃO", "EMENTA")
    )  # nothing is dropped or added besides the two headings


def test_acordao_sections_and_carriage_return_separators() -> None:
    doc = _doc(ACORDAO)
    assert doc.ext["document_type"] == "acordao" and doc.ext["recurso"] == ["EMBARGOS DE DECLARAÇÃO"]
    assert doc.ext["ministros"] == ["RAUL ARAÚJO"] and doc.ext["teor"] == ["Admitindo Embargo"]
    assert doc.ext["descricao_monocratica"] == [] and "descricao_monocratica" in doc.ext
    # The ementa is printed twice (after the proclamation and after the report); a section that returns keeps counting.
    assert [(p.eid, p.part) for p in doc.paragraphs] == [
        ("ac-1", "acordao"),
        ("em-1", "ementa"),
        ("rel-1", "relatorio"),
        ("rel-2", "relatorio"),
        ("em-2", "ementa"),
        ("vt-1", "voto"),
    ]
    by = {p.eid: p.text for p in doc.paragraphs}
    assert by["ac-1"].startswith("Vistos e relatados estes autos em que são partes as acima indicadas, acordam")
    assert "QUARTA TURMA" in by["ac-1"] and "sessão virtual de 24/10/2023 a 30/10/2023" in by["ac-1"]
    assert by["em-1"] == by["em-2"] and by["em-1"].startswith(
        "EMBARGOS DE DECLARAÇÃO NO AGRAVO INTERNO NO AGRAVO EM RECURSO ESPECIAL. PROCESSUAL CIVIL"
    )
    assert by["rel-1"].startswith(
        "O EXMO. SR. MINISTRO RAUL ARAÚJO (Relator):\nTrata-se de embargos declaratórios opostos pela ADFJ"
    )
    assert by["vt-1"].endswith("consequente majoração dos honorários recursais.\nÉ como voto.")
    assert all("\r" not in t and "<br>" not in t for t in by.values())


def test_decision_of_2026_iso_dates_other_field_names_and_masked_party_template() -> None:
    doc = _doc(UNIMED)
    assert doc.judgment is not None and doc.judgment.decision_date == date(2026, 10, 7)
    assert doc.ext["data_recebimento"] == "2026-03-06" and doc.ext["data_distribuicao"] == "2026-03-19"
    assert doc.ext["ministros"] == ["NANCY ANDRIGHI"]  # NM_MINISTRO
    assert doc.ext["assuntos"] == ["12480.12482.12486.12487."]  # kept as published, not interpreted
    assert doc.ext["descricao_monocratica"] == ["Não conhecido o recurso de #{nome_da_parte} #{campo_livre_final}"]
    assert [p.part for p in doc.paragraphs] == ["decisao", "decisao"]  # 3,871 characters in two units of at most 2,500
    assert all(len(p.text) <= 2500 for p in doc.paragraphs)
    assert doc.paragraphs[0].text.startswith("Examina-se agravo em recurso especial interposto por UNIMED C. GRANDE")


def test_split_paragraphs_separators_entities_headings_and_unit_size() -> None:
    text = (
        "ACÓRDÃO<br>um &quot;dois&quot;\r\ntrês&mldr;\rquatro<BR />\n\n   <br>"
        "EMENTA<br>cinco<br>VOTO-VISTA<br>seis<br>EMENTA<br>sete"
    )
    assert split_paragraphs(text) == [
        ("acordao", 'um "dois"\ntrês…\nquatro'),
        ("ementa", "cinco"),
        ("voto_vista", "seis"),
        ("ementa", "sete"),
    ]
    assert split_paragraphs("antes<br>DECISÃO<br>depois") == [("text", "antes"), ("decisao", "depois")]
    assert split_paragraphs("Ementa<br>VOTOS<br>x &amp;co &zzz; a&b") == [
        ("text", "Ementa\nVOTOS\nx &co &zzz; a&b")
    ]  # only exact upper-case headings; unknown entities stay
    long = "x" * 3000
    assert split_paragraphs(f"a<br>{long}<br>b<br>c", limit=10) == [("text", "a"), ("text", long), ("text", "b\nc")]
    assert split_paragraphs("   <br> \r\n ") == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1699495200000, date(2023, 11, 9)),  # 02:00 UTC
        (1651460400000, date(2022, 5, 2)),  # 03:00 UTC
        ("1651460400000", date(2022, 5, 2)),
        ("2021-01-04", date(2021, 1, 4)),
        ("2021-01-04T00:00:00", date(2021, 1, 4)),
        (None, None),
        ("  ", None),
    ],
)
def test_dates_as_iso_strings_or_milliseconds(value: Any, expected: date | None) -> None:
    assert parse_date(value, "x") == expected


@pytest.mark.parametrize("value", ["04/01/2021", "ontem", 10**30, True, -62135596800000, 32503680000000])
def test_dates_that_cannot_be_read_are_refused(value: Any) -> None:
    with pytest.raises(StjParseError, match="invalid x|implausible x"):
        parse_date(value, "x")


def test_placeholder_receipt_dates_are_absent_but_the_publication_date_must_be_real() -> None:
    assert parse_date(-2209150800000, "x", placeholder_ok=True) is None  # 1900-01-01, as in some 2022 records

    def placeholder(records: list[dict[str, Any]], text: str) -> tuple[list[dict[str, Any]], str]:
        return [dict(records[0], dataRecebimento=-2209150800000, dataDistribuicao=-2209150800000)], text

    doc = _doc(FAZENDA, edit=placeholder)
    assert doc.ext["data_recebimento"] is None and doc.ext["data_distribuicao"] is None
    assert doc.ext["data_publicacao"] == "2023-11-09"
    with pytest.raises(StjParseError, match="implausible dataPublicacao"):
        _doc(FAZENDA, edit=lambda r, t: ([dict(r[0], dataPublicacao=-2209150800000)], t))


def test_document_types_with_and_without_accents() -> None:
    assert [document_type(v) for v in ("DECISÃO", "DECISAO", "ACÓRDÃO", "ACORDAO", " acórdão ")] == [
        "decisao",
        "decisao",
        "acordao",
        "acordao",
        "acordao",
    ]
    with pytest.raises(StjParseError, match="unknown tipoDocumento"):
        document_type("DESPACHO")


def test_repeated_metadata_records_are_merged_and_must_agree() -> None:
    def twice(records: list[dict[str, Any]], text: str) -> tuple[list[dict[str, Any]], str]:
        other = dict(records[0], teor="Admitindo Embargo")
        return [records[0], other, dict(records[0])], text

    doc = _doc(FAZENDA, edit=twice)
    assert doc.ext["teor"] == ["Concedendo", "Admitindo Embargo"] and doc.ext["ministros"] == ["BENEDITO GONÇALVES"]

    def conflicting(records: list[dict[str, Any]], text: str) -> tuple[list[dict[str, Any]], str]:
        return [records[0], dict(records[0], processo="REsp 1")], text

    with pytest.raises(StjParseError, match="disagree on processo"):
        _doc(FAZENDA, edit=conflicting)


def test_unpadded_values_and_key_variants() -> None:
    def padded(records: list[dict[str, Any]], text: str) -> tuple[list[dict[str, Any]], str]:
        rec = dict(records[0])
        rec["seqDocumento"] = int(rec["seqDocumento"])
        rec["processo"] = "  REsp   2097990   "
        rec["recurso"] = "EDcl  "
        rec["dataPublicacao"] = "2023-11-09"
        return [rec], text

    doc = _doc(FAZENDA, edit=padded)
    assert (
        doc.number == "REsp 2097990" and doc.ext["recurso"] == ["EDcl"] and doc.title == "REsp 2097990 (DECISÃO, EDcl)"
    )


@pytest.mark.parametrize(
    ("edit", "error", "message"),
    [
        (lambda r, t: ([], t), StjParseError, "no metadata record"),
        (lambda r, t: ([dict(r[0], seqDocumento=1)], t), StjParseError, "belongs to another document"),
        (lambda r, t: ([dict(r[0], tipoDocumento="DESPACHO")], t), StjParseError, "unknown tipoDocumento"),
        (lambda r, t: ([dict(r[0], processo="  ")], t), StjParseError, "no processo"),
        (lambda r, t: ([dict(r[0], dataPublicacao=None)], t), StjParseError, "no dataPublicacao"),
        (lambda r, t: ([dict(r[0], dataRecebimento="ontem")], t), StjParseError, "invalid dataRecebimento"),
        (lambda r, t: (r, "DECISÃO<br> <br>"), ParseSkipped, "empty"),
    ],
    ids=["no-record", "other-document", "type", "processo", "published", "received", "empty-text"],
)
def test_parser_refuses_what_it_cannot_read(edit: Any, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        _doc(FAZENDA, edit=edit)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\xfe", "not a JSON envelope"),
        (b"[]", "not an stj-integra-1 envelope"),
        (json.dumps({"format": "other"}).encode(), "not an stj-integra-1 envelope"),
        (json.dumps({"format": "stj-integra-1", "seq": "x1", "records": [{}], "text": "a"}).encode(), "invalid SeqD"),
        (json.dumps({"format": "stj-integra-1", "seq": "1", "records": [{"seqDocumento": 1}]}).encode(), "no text"),
    ],
)
def test_envelope_is_checked(content: bytes, message: str) -> None:
    with pytest.raises(StjParseError, match=message):
        parse_stj_integra(content)


# --------------------------------------------------------------------------- citations, profile, source


def test_judgment_citations(catalog: Catalog) -> None:
    assert _citation(_doc(ACORDAO)) == "STJ, AREsp 2297789, Rel. Min. RAUL ARAÚJO, DJe de 9/11/2023"
    assert (
        _citation(_doc(FAZENDA)) == "STJ, REsp 2097990, decisão monocrática, Min. BENEDITO GONÇALVES, DJe de 9/11/2023"
    )
    assert _citation(_doc(UNIMED)).endswith("Min. NANCY ANDRIGHI, DJe de 7/10/2026")
    assert _citation(_doc(FAZENDA), "2").endswith("DJe de 9/11/2023, n. 2")
    module = get_module("br")
    bare = JudgmentCitationInput(
        "x", None, date(2021, 1, 4), "HC 637258", "BR-STJ", "Superior Tribunal de Justiça", None, "pt", "BR"
    )
    assert module.judgment_citation(bare) == "STJ, HC 637258, 4/1/2021"  # no ext: the date is not called a DJe date
    other = JudgmentCitationInput(
        "x", None, date(2021, 1, 4), None, "BR-TJ", "Tribunais de Justiça", None, "pt", "BR", {}
    )
    assert module.judgment_citation(other) == "Tribunais de Justiça, x, 4/1/2021"
    assert [
        module.judgment_unit_label(None, p, None) for p in ("acordao", "ementa", "relatorio", "voto", "decisao")
    ] == [
        "acórdão",
        "ementa",
        "relatório",
        "voto",
        "decisão",
    ]
    assert module.judgment_unit_label("3", "voto_vista", None) == "voto-vista, n. 3"


def test_module_validates_the_judgment_extension_and_nothing_else() -> None:
    module = get_module("br")
    ext = _doc(FAZENDA).ext
    assert module.validate_ext(ext)["processo"] == "REsp 2097990"
    assert module.validate_ext({}) == {}
    with pytest.raises(ValueError):
        module.validate_ext({**ext, "unknown": 1})
    with pytest.raises(ValueError):
        module.validate_ext({**ext, "date_kind": "judgment"})


def test_profile_is_a_draft_with_portuguese_and_the_courts_of_the_hierarchy(catalog: Catalog) -> None:
    profile = catalog.jurisdictions.get("BR")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["pt"]
    assert profile.citation.module == "br" and profile.precedent.doctrine == "jurisprudence_constante"
    courts = {c.id: c for c in profile.courts}
    assert {"BR-STF", "BR-STJ", "BR-TRF", "BR-TJ"} <= set(courts)
    assert (
        courts["BR-TRF"].appeals_to == ["BR-STJ", "BR-STF"]
        and catalog.jurisdictions.court_jurisdiction("BR-STJ") == "BR"
    )
    gaps = " ".join(profile.coverage.known_gaps)
    assert "No Brazilian legislation is loaded" in gaps and "judgment date" in gaps and "early 2026" in gaps
    assert search_config("pt") == "portuguese"


def test_source_is_approved_and_uses_this_connector(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.licence.id == "CC-BY"
    assert source.licence.attribution_required is True and "Portal de Dados Abertos" in (
        source.licence.attribution_text or ""
    )
    assert source.languages == ["pt"] and source.document_kinds == ["judgment"] and source.jurisdictions == ["BR"]
    assert isinstance(build_connector(source), StjConnector) and source.parser in PARSERS
    assert "/api/" in (source.rate_limits or "") and "Crawl-Delay 10" in (source.rate_limits or "")
    # the portal's one statement on personal data (no cases under secrecy of justice) and the stored date are recorded
    conditions = " ".join(source.licence.conditions)
    assert "secrecy of justice" in conditions and "publication date" in conditions
    assert any("party names" in text for text in source.known_limitations)


# --------------------------------------------------------------------------- robots.txt, dataset page, manifest


def test_robots_rules_of_the_portal() -> None:
    assert robots_crawl_delay(ROBOTS, UA) == 10.0 and robots_crawl_delay("User-agent: *\nDisallow: /x", UA) is None
    assert robots_crawl_delay("User-agent: other\nCrawl-delay: 99\n\nUser-agent: *\nCrawl-delay: 3", UA) == 3.0
    assert robots_allows(ROBOTS, UA, download_url(DATASET, f"{RID_2023}/20231109.zip"))
    assert robots_allows(ROBOTS, UA, DATASET_PAGE)
    assert not robots_allows(ROBOTS, UA, f"https://{HOST}/api/3/action/package_show?id=x")
    assert not robots_allows(ROBOTS, UA, f"https://{HOST}/dataset/integras/history")
    assert not robots_allows(ROBOTS, UA, f"https://{HOST}/revision/x")


def test_dataset_page_entries_become_the_file_set() -> None:
    resources = parse_resources((FIX / "dataset_page_excerpt.html").read_text(encoding="utf-8"))
    assert resources[0].kind is None and resources[0].name == "dicionariointegrasdecisoes.csv"  # the data dictionary
    files, without_texts = build_file_set(resources)
    ids = [f["id"] for f in files]
    assert ids == ["20210104", "20210205", "20210205-b", "202202", "20231109", "20240203"]
    by = {f["id"]: f for f in files}
    # titles say "20210104.zip" but the download is textos20210104.zip: the link's name is what counts
    assert by["20210104"]["zip"] == "253f0e4e-aee2-41ef-a956-f046dd72966a/textos20210104.zip"
    assert by["20210104"]["json"] == "fcade939-0b58-41ba-bbb9-1a4709c16c05/metadados20210104.json"
    # a second upload of the same day is a second entry, pairing the second ZIP with the second JSON
    assert by["20210205-b"]["zip"].startswith("3bf6df54") and by["20210205-b"]["json"].startswith("97d9ce89")
    assert by["202202"]["zip"] == "2b640cb2-cd3f-4737-999b-efece1196fbe/___"  # the portal's literal download name
    assert by["20240203"]["json"].endswith("/metadados20240203.json")  # listed as "Metadados 20240203"
    assert without_texts == ["20240204"]
    assert download_url(DATASET, by["202202"]["zip"] or "").endswith(
        "/resource/2b640cb2-cd3f-4737-999b-efece1196fbe/download/___"
    )


def test_committed_manifest_covers_the_whole_open_corpus_and_passes_the_connectors_validation(tmp_path: Path) -> None:
    source = yaml.safe_load((REPO_ROOT / "config/sources/br-stj-dados-abertos.yaml").read_text(encoding="utf-8"))
    path = REPO_ROOT / source["access"]["endpoint"]
    manifest = StjConnector(path, SOURCE, tmp_path)._manifest()
    files = manifest["files"]
    ids = [f["id"] for f in files]
    assert len(files) >= 1300 and len(set(ids)) == len(ids) and ids[0] == "20210104"
    assert ids == sorted(ids, key=lambda i: (i[:8].ljust(8, "0"), i)), "oldest first"
    assert max(i[:8] for i in ids) >= "20260101" and {"202202", "202203", "202204", "202207"} <= set(ids)
    assert manifest["dataset_id"] == DATASET and manifest["crawl_delay_seconds"] >= 10
    assert manifest["without_texts"] == ["20240204", "20260611", "20260615"]
    assert all(f["zip"] for f in files) and sum(1 for f in files if f.get("json")) >= 1300
    urls = [download_url(DATASET, f["zip"]) for f in files]
    assert all(u.startswith(f"https://{HOST}/dataset/{DATASET}/resource/") and "/download/" in u for u in urls)
    assert not any("/api/" in u for u in urls)
    assert all(robots_allows(ROBOTS, UA, u) for u in urls)


# --------------------------------------------------------------------------- connector


def _cache_file(cache: Path, url: str, key: str, content: bytes, status: int = 200) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    meta = {"url": url, "final_url": url, "status": status, "content_type": "application/zip"}
    meta["retrieved_at"] = "2026-10-15T08:00:00+00:00"
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _write_cache(cache: Path, robots: str | None = ROBOTS) -> None:
    _cache_file(cache, ROBOTS_URL, "robots.txt", (robots or "").encode(), 200 if robots is not None else 404)
    _cache_file(
        cache,
        download_url(DATASET, f"{RID_2023}/20231109.zip"),
        "texts/20231109.zip",
        (FIX / "texts_20231109.zip").read_bytes(),
    )
    _cache_file(
        cache,
        download_url(DATASET, f"{RID_2026}/textos20261007.zip"),
        "texts/20261007.zip",
        (FIX / "texts_20261007.zip").read_bytes(),
    )
    _cache_file(
        cache,
        download_url(DATASET, f"{RID_2026_JSON}/metadados20261007.json"),
        "metadata/20261007.json",
        (FIX / "metadados20261007.json").read_bytes(),
    )


def _manifest_files() -> list[dict[str, Any]]:
    return [
        {"id": "20231109", "zip": f"{RID_2023}/20231109.zip", "json": "335b7324-449c-4393-82da-103934a2b0d3/x.json"},
        {
            "id": "20261007",
            "zip": f"{RID_2026}/textos20261007.zip",
            "json": f"{RID_2026_JSON}/metadados20261007.json",
        },
    ]


def _manifest(tmp_path: Path, files: list[dict[str, Any]] | None = None, **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 10,
        "dataset_id": DATASET,
        "files": files if files is not None else _manifest_files(),
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_yields_one_envelope_per_decision_with_text(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache)
    connector = StjConnector(_manifest(tmp_path), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert {d.external_id for d in docs} == {f"stj:{s}" for s in ALL_WITH_TEXT}
    assert len(docs) == 5 and connector.fetch_errors == []
    # the second file's metadata record without a text is counted, not passed on
    assert connector.stats == {
        "files": 2,
        "records": 4 + 2,
        "documents": 5,
        "records_without_text": 1,
        "texts_without_record": 0,
    }
    by = {d.external_id: d for d in docs}
    fazenda = by[f"stj:{FAZENDA}"]
    assert fazenda.media_type == "application/json" and fazenda.retrieved_at == datetime(2026, 10, 15, 8, tzinfo=UTC)
    assert fazenda.uri == f"{download_url(DATASET, f'{RID_2023}/20231109.zip')}#20231109/{FAZENDA}.txt"
    assert fazenda.metadata["file"] == "20231109" and fazenda.metadata["tipo"] == "DECISÃO"
    assert fazenda.metadata["metadata_source"] == "embedded:metadados20231109.json"  # no JSON request was needed
    assert len(fazenda.metadata["texts_sha256"]) == 64 and len(fazenda.metadata["metadata_sha256"]) == 64
    assert by[f"stj:{UNIMED}"].metadata["metadata_source"].endswith("/download/metadados20261007.json")
    # the envelope holds the verbatim text file and metadata record
    envelope = json.loads(fazenda.content)
    assert envelope["text"] == _zip("texts_20231109.zip").read(f"20231109/{FAZENDA}.txt").decode("utf-8")
    assert envelope["records"] == [r for r in _records_2023() if _seq(r) == FAZENDA]
    assert envelope["seq"] == FAZENDA and envelope["format"] == "stj-integra-1" and "file" not in envelope
    # same input, same bytes: a re-run changes nothing
    again = list(StjConnector(_manifest(tmp_path), SOURCE, cache, offline=True).fetch())
    assert [d.content for d in again] == [d.content for d in docs]
    # every document parses
    assert all(len(parse_stj_integra(d.content)) == 1 for d in docs)


def _write_second_upload(cache: Path) -> list[dict[str, Any]]:
    """The portal uploaded some days twice: same content, another resource id and a later ZIP timestamp."""
    rid = "3bf6df54-c32c-49b3-9fa2-493d3a9422ba"
    _cache_file(
        cache,
        download_url(DATASET, f"{rid}/20231109.zip"),
        "texts/20231109-b.zip",
        (FIX / "texts_20231109.zip").read_bytes(),
    )
    return [
        {"id": "20231109", "zip": f"{RID_2023}/20231109.zip"},
        {"id": "20231109-b", "zip": f"{rid}/20231109.zip"},
    ]


def test_a_second_upload_of_a_day_gives_identical_documents(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache)
    files = _write_second_upload(cache)
    docs = list(StjConnector(_manifest(tmp_path, files), SOURCE, cache, offline=True).fetch())
    assert len(docs) == 8
    first, second = docs[:4], docs[4:]
    assert [d.content for d in first] == [d.content for d in second]  # the envelope does not name the file
    assert {d.metadata["file"] for d in first} == {"20231109"} and {d.metadata["file"] for d in second} == {
        "20231109-b"
    }


def test_connector_makes_no_request_when_everything_is_cached(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache)
    connector = StjConnector(_manifest(tmp_path), SOURCE, cache, offline=False)
    assert len(list(connector.fetch())) == 5
    assert connector.http is not None and connector.http.requests_made == 0 and connector.http.crawl_delay == 10.0


def test_connector_resumes_from_the_cache_and_offline_refuses_what_is_missing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache)
    files = [{"id": "20240101", "zip": f"{RID_2023}/textos20240101.zip", "json": f"{RID_2026_JSON}/m.json"}]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(StjConnector(_manifest(tmp_path, files), SOURCE, cache, offline=True).fetch())


def test_connector_reports_missing_and_broken_files_and_continues(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache)
    other = download_url(DATASET, f"{RID_2023}/textos20240101.zip")
    _cache_file(cache, other, "texts/20240101.zip", b"", status=404)
    _cache_file(cache, download_url(DATASET, f"{RID_2023}/textos20240102.zip"), "texts/20240102.zip", b"<html>nope")
    _cache_file(cache, download_url(DATASET, f"{RID_2023}/textos20240103.zip"), "texts/20240103.zip", b"PK\x03\x04bad")
    nojson = download_url(DATASET, f"{RID_2026_JSON}/metadados20240104.json")
    _cache_file(cache, nojson, "metadata/20240104.json", b"", status=404)
    _cache_file(
        cache,
        download_url(DATASET, f"{RID_2026}/textos20240104.zip"),
        "texts/20240104.zip",
        (FIX / "texts_20261007.zip").read_bytes(),
    )
    ref = lambda i, n: {"id": i, "zip": f"{RID_2023}/{n}", "json": f"{RID_2026_JSON}/metadados{i}.json"}  # noqa: E731
    files = [
        ref("20240101", "textos20240101.zip"),
        ref("20240102", "textos20240102.zip"),
        ref("20240103", "textos20240103.zip"),
        {"id": "20240104", "zip": f"{RID_2026}/textos20240104.zip", "json": f"{RID_2026_JSON}/metadados20240104.json"},
        {"id": "20231109", "zip": f"{RID_2023}/20231109.zip"},
    ]
    connector = StjConnector(_manifest(tmp_path, files), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert len(docs) == 4  # only the 2023 file with its embedded metadata; the others were skipped
    errors = " | ".join(connector.fetch_errors)
    assert "textos20240101.zip: not found (404)" in errors
    assert "not a ZIP archive" in errors and "bad ZIP archive" in errors
    assert "metadados20240104.json: not found (404); the day's texts are skipped" in errors
    assert len(connector.fetch_errors) == 4


def test_connector_obeys_robots_txt(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    _write_cache(blocked, robots="User-agent: *\nDisallow: /dataset/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(StjConnector(_manifest(tmp_path), SOURCE, blocked, offline=True).fetch())
    # a robots.txt that disallows the API only, as the portal's does, does not block the downloads
    allowed = tmp_path / "allowed"
    _write_cache(allowed)
    assert len(list(StjConnector(_manifest(tmp_path), SOURCE, allowed, offline=True).fetch())) == 5
    # no robots.txt at all (404) means no restrictions
    none = tmp_path / "none"
    _write_cache(none, robots=None)
    connector = StjConnector(_manifest(tmp_path), SOURCE, none, offline=True)
    assert len(list(connector.fetch())) == 5 and connector.robots is None


def test_connector_uses_the_larger_of_the_manifests_and_robots_delay(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache, robots=ROBOTS.replace("Crawl-Delay: 10", "Crawl-Delay: 30"))
    connector = StjConnector(_manifest(tmp_path), SOURCE, cache, offline=True)
    list(connector.fetch())
    assert connector.http is not None and connector.http.crawl_delay == 30.0


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = {"id": "20231109", "zip": f"{RID_2023}/20231109.zip"}
    for files, extra, message in (
        ([{"id": "2023-11-09", "zip": good["zip"]}], {}, "invalid file id"),
        ([good, good], {}, "listed twice"),
        ([{"id": "20231109", "zip": "nonsense/a.zip"}], {}, "invalid zip reference"),
        ([{"id": "20231109", "zip": f"{RID_2023}/../x.zip"}], {}, "invalid zip reference"),
        ([{"id": "20231109", "zip": f"{RID_2023}/a b.zip"}], {}, "invalid zip reference"),
        ([{"id": "20231109", "zip": f"{RID_2023}/x.zip", "json": "bad"}], {}, "invalid json reference"),
        ([good], {"dataset_id": "../../api"}, "dataset_id"),
        ([good], {"base_url": "http://dadosabertos.web.stj.jus.br"}, "base_url"),
        ([good], {"user_agent": "python-urllib"}, "user_agent"),
        ([good], {"crawl_delay_seconds": 5}, "at least 10"),
        ([], {}, "no files"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(StjConnector(_manifest(tmp_path, files, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(StjConnector(_manifest(tmp_path, source_id="other"), SOURCE, tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def case_law(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("stj")
    cache = tmp / "cache"
    _write_cache(cache)
    connector = StjConnector(_manifest(tmp), SOURCE, cache, offline=True)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=connector)
        session.commit()
    return report


class _Fixed:
    """The same documents again, from the fixtures (a re-run), plus a second upload of the 2023 day."""

    def __init__(self, tmp: Path):
        cache = tmp / "cache"
        _write_cache(cache)
        files = [*_manifest_files(), *_write_second_upload(cache)[1:]]
        self.connector = StjConnector(_manifest(tmp, files), SOURCE, cache, offline=True)
        self.fetch_errors: list[str] = []

    def fetch(self) -> Iterator[Any]:
        return self.connector.fetch()


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="BR", **kw), today=date(2026, 10, 15))


@pytest.mark.db
def test_ingest_report(case_law: IngestionReport) -> None:
    assert case_law.errors == [] and case_law.conflicts == []
    assert case_law.documents_seen == case_law.documents_new == case_law.judgments_created == 5
    assert case_law.paragraphs_created == case_law.passages_indexed == 2 + 1 + 1 + 6 + 2
    assert case_law.instruments_created == 0 and case_law.citation_links_created == 0


@pytest.mark.db
def test_search_finds_a_decision_with_citation_and_limitations(
    session: Session, catalog: Catalog, case_law: IngestionReport
) -> None:
    res = _search(session, catalog, q="contribuição previdenciária patronal faltas justificadas")
    hit = res["results"][0]
    assert hit["kind"] == "judgment_paragraph" and hit["unit"]["eid"] == "em-1"
    assert hit["citation"] == "STJ, REsp 2097990, decisão monocrática, Min. BENEDITO GONÇALVES, DJe de 9/11/2023"
    assert hit["text"] == (
        "PROCESSUAL CIVIL. RECURSO ESPECIAL. CONTRIBUIÇÃO PREVIDENCIÁRIA PATRONAL. FALTAS JUSTIFICADAS. INCIDÊNCIA. "
        "RECURSO PROVIDO."
    )
    assert hit["unit"]["label"] == "ementa" and hit["unit"]["heading"] == "ementa"
    assert (hit["document"]["court_id"], hit["document"]["court"]) == ("BR-STJ", "Superior Tribunal de Justiça")
    assert hit["document"]["decision_date"] == "2023-11-09" and hit["document"]["title"] == "REsp 2097990"
    assert hit["version"]["language"] == "pt" and hit["version"]["authoritative"] is False
    assert hit["authority"]["category"] == "persuasive_precedent"
    assert hit["authority"]["rationale"].startswith("BR follows jurisprudence constante: decisions are not formally")
    assert {lim["code"] for lim in hit["limitations"]} >= {"AUTHORITY", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"}
    assert hit["source"]["attribution"].startswith("Superior Tribunal de Justiça, Portal de Dados Abertos")

    body = _search(session, catalog, q="contrarrazões recorrida admitido origem")["results"][0]
    assert body["unit"]["eid"] == "dec-1" and body["unit"]["label"] == "decisão"
    assert body["text"].startswith("Trata-se de recurso especial interposto pela FAZENDA NACIONAL contra acórdão")
    assert body["citation"] == hit["citation"]


@pytest.mark.db
def test_search_finds_the_proclamation_of_an_acordao_and_a_decision_with_iso_dates(
    session: Session, catalog: Catalog, case_law: IngestionReport
) -> None:
    ac = _search(session, catalog, q="acordam Ministros QUARTA TURMA sessão virtual")["results"][0]
    assert ac["unit"]["eid"] == "ac-1" and ac["unit"]["label"] == "acórdão"
    assert ac["citation"] == "STJ, AREsp 2297789, Rel. Min. RAUL ARAÚJO, DJe de 9/11/2023"
    unimed = _search(session, catalog, q="Unimed Campina Grande cooperativa agravo em recurso especial")["results"][0]
    assert unimed["citation"] == "STJ, AREsp 3196481, decisão monocrática, Min. NANCY ANDRIGHI, DJe de 7/10/2026"
    assert unimed["document"]["decision_date"] == "2026-10-07"


@pytest.mark.db
def test_the_portuguese_search_configuration_is_used(
    session: Session, catalog: Catalog, case_law: IngestionReport
) -> None:
    # the plural "recursos" finds "recurso", "prejudicado" finds "prejudicado": stemming of the portuguese configuration
    res = _search(session, catalog, q="recursos prejudicado perda superveniente do objeto")
    assert res["results"][0]["citation"].startswith("STJ, REsp 1923281, decisão monocrática, Min. MARCO AURÉLIO")


@pytest.mark.db
def test_judgments_are_stored_with_court_date_docket_and_publisher_fields(
    session: Session, case_law: IngestionReport
) -> None:
    j = session.scalar(select(m.Judgment).where(m.Judgment.uri == work_uri_for(ACORDAO)))
    assert j is not None
    assert (j.court_id, j.decision_date, j.docket, j.case_name, j.neutral_citation) == (
        "BR-STJ",
        date(2023, 11, 9),
        "AREsp 2297789",
        "AREsp 2297789",
        None,
    )
    assert j.ext["document_type"] == "acordao" and j.ext["date_kind"] == "dje_publication"
    assert j.ext["seq_documento"] == ACORDAO and j.ext["ministros"] == ["RAUL ARAÚJO"]
    version = session.scalar(select(m.JudgmentVersion).where(m.JudgmentVersion.judgment_id == j.id))
    assert version is not None and version.language == "pt" and version.authoritative is False
    assert version.uri == f"{work_uri_for(ACORDAO)}/por@2023-11-09"
    parts = [
        p.part
        for p in session.scalars(
            select(m.JudgmentParagraph)
            .where(m.JudgmentParagraph.judgment_version_id == version.id)
            .order_by(m.JudgmentParagraph.sort_order)
        )
    ]
    assert parts == ["acordao", "ementa", "relatorio", "relatorio", "ementa", "voto"]
    no_text = session.scalar(select(m.Judgment).where(m.Judgment.uri == work_uri_for(NO_TEXT)))
    assert no_text is None  # a metadata record without a text is not a document


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    case_law: IngestionReport,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixed(tmp_path))
        # 5 documents again and the 4 of the day the portal uploaded twice: nothing new, no conflict
        assert again.documents_seen == again.documents_unchanged == 9 and again.errors == []
        assert again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 5 and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == case_law.paragraphs_created
        row = session.scalar(select(m.SourceDocument).where(m.SourceDocument.external_id == f"stj:{FAZENDA}"))
        assert row is not None and row.media_type == "application/json" and row.licence_id == "CC-BY"
        assert row.metadata_["file"] == "20231109" and row.uri is not None and row.uri.endswith(f"{FAZENDA}.txt")
        session.rollback()
