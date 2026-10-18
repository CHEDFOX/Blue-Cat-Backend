"""Akoma Ntoso parser, manifest connector and object store (no database needed)."""

from __future__ import annotations

import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.local_manifest import LocalManifestConnector, ManifestError
from ulis.sources.parsers.akn import AknParseError, parse_akn
from ulis.storage.object_store import IntegrityError, LocalObjectStore

CORPUS = REPO_ROOT / "sample_corpus"


def _parse(name: str):  # type: ignore[no-untyped-def]
    return parse_akn((CORPUS / name).read_bytes())


def test_parses_point_in_time_versions() -> None:
    enacted = {p.eid: p for p in _parse("xa/act-2015-12.eng@2015-06-01.xml").provisions}
    amended = {p.eid: p for p in _parse("xa/act-2015-12.eng@2022-01-01.xml").provisions}
    current = {p.eid: p for p in _parse("xa/act-2015-12.eng@2023-03-01.xml").provisions}
    assert "seven years" in enacted["sec_12__subsec_1"].text
    assert enacted["sec_12__subsec_1"].valid_from == date(2015, 7, 1)
    assert "five years" in amended["sec_12__subsec_1"].text
    assert amended["sec_12__subsec_1"].valid_from == date(2022, 1, 1)
    # The containing section's wording changed when its subsection did.
    assert amended["sec_12"].valid_from == date(2022, 1, 1)
    assert amended["sec_12A"].status == "prospective" and amended["sec_12A"].valid_from is None
    assert amended["sec_12A__subsec_1"].status == "prospective"
    assert current["sec_20"].status == "repealed" and current["sec_20"].valid_from == date(2023, 3, 1)
    assert current["sec_20"].text == "" and not current["sec_20"].indexable


def test_extracts_metadata_refs_and_outstanding_changes() -> None:
    doc = _parse("xa/act-2015-12.eng@2023-03-01.xml")
    assert doc.work_uri == "/akn/xa/act/2015/12"
    assert doc.expression_uri == "/akn/xa/act/2015/12/eng@2023-03-01"
    assert (doc.language, doc.authoritative, doc.currency_date) == ("en", True, date(2026, 9, 30))
    assert doc.ext == {"chapter": "Cap. 512"}
    assert doc.outstanding_changes[0]["affects_eid"] == "sec_13__subsec_2"
    assert {(e.event_type, e.affecting_uri) for e in doc.events} >= {("amendment", "/akn/xa/act/2021/4")}
    sec3 = next(p for p in doc.provisions if p.eid == "sec_3")
    assert sec3.refs[0].href == "/akn/xr/treaty/2010/1" and sec3.refs[0].relation == "implements"


def test_parses_bill_judgment_treaty_and_bilingual_code() -> None:
    bill = _parse("xa/bill-2026-9.eng.xml")
    assert bill.instrument_type == "bill" and {p.status for p in bill.provisions} == {"proposed"}
    judgment = _parse("xa/judgment-2023-xasc-7.eng.xml")
    assert judgment.judgment is not None and judgment.judgment.neutral_citation == "[2023] XASC 7"
    assert [p.num for p in judgment.paragraphs] == ["1", "2", "3", "4", "5", "6", "7"]
    assert judgment.paragraphs[5].refs[0].relation == "overrules"
    treaty = _parse("xr/treaty-2010-1.fra.xml")
    assert {p.party for p in treaty.treaty_parties} == {"XA", "XB"}
    fr, de = _parse("xb/code-civil.fra.xml"), _parse("xb/code-civil.deu.xml")
    assert fr.work_uri == de.work_uri and fr.language == "fr" and de.language == "de"
    assert fr.authoritative and de.authoritative


def test_text_extraction_is_deterministic() -> None:
    a = _parse("xa/act-2021-4.eng.xml")
    b = _parse("xa/act-2021-4.eng.xml")
    assert [p.text for p in a.provisions] == [p.text for p in b.provisions]
    sec1 = next(p for p in a.provisions if p.eid == "sec_1")
    assert sec1.text.splitlines()[0] == "(1) This Act may be cited as the Personal Data (Amendment) Act 2021."


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>',
        b"<notAkn/>",
        b"not xml at all",
    ],
)
def test_parser_rejects_unsafe_or_foreign_xml(payload: bytes) -> None:
    with pytest.raises(AknParseError):
        parse_akn(payload)


def test_manifest_connector_verifies_hash_pins(tmp_path: Path) -> None:
    shutil.copytree(CORPUS, tmp_path / "corpus")
    docs = list(LocalManifestConnector(tmp_path / "corpus/MANIFEST.yaml", "ulis-fictional-sample").fetch())
    assert len(docs) == 14
    target = tmp_path / "corpus/xa/act-2015-12.eng@2023-03-01.xml"
    target.write_bytes(target.read_bytes().replace(b"five years", b"four years"))
    with pytest.raises(ManifestError, match="does not match manifest pin"):
        list(LocalManifestConnector(tmp_path / "corpus/MANIFEST.yaml", "ulis-fictional-sample").fetch())


def test_manifest_connector_rejects_wrong_source_and_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="belongs to"):
        list(LocalManifestConnector(CORPUS / "MANIFEST.yaml", "other-source").fetch())
    (tmp_path / "MANIFEST.yaml").write_text(
        "schema_version: 1\nsource_id: s\ndocuments:\n- path: ../etc/passwd\n  external_id: x\n"
        "  media_type: text/plain\n  sha256: x\n"
    )
    with pytest.raises(ManifestError, match="escapes"):
        list(LocalManifestConnector(tmp_path / "MANIFEST.yaml", "s").fetch())


def test_object_store_is_content_addressed_and_detects_tampering(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    key = store.put(b"original bytes")
    assert key == "sha256:" + hashlib.sha256(b"original bytes").hexdigest()
    assert store.put(b"original bytes") == key
    assert store.get(key) == b"original bytes"
    path = tmp_path / "sha256" / key[7:9] / key[9:11] / f"{key[7:]}.zst"  # the address stays the original bytes' hash
    assert store.locate(key) == path
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    path.write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        store.get(key)
    with pytest.raises(ValueError):
        store.get("sha256:../../etc/passwd")
