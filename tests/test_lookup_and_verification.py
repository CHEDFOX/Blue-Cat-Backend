"""Point-in-time lookups, quotation checks across documents and source integrity (PostgreSQL)."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.retrieval.lookup import NotFound, instrument_detail, judgment_detail, provision_at
from ulis.storage.object_store import LocalObjectStore
from ulis.temporal.validity import resolve_at
from ulis.verification.integrity import verify_source_document
from ulis.verification.quotes import check_quote

pytestmark = pytest.mark.db
S12_1 = "/akn/xa/act/2015/12/~sec_12__subsec_1"


def codes(result: dict) -> set[str]:  # type: ignore[type-arg]
    return {lim["code"] for lim in result["limitations"]}


def test_what_law_applied_on_the_date(session: Session, catalog: Catalog) -> None:
    old = provision_at(session, catalog, S12_1, date(2019, 5, 1))
    new = provision_at(session, catalog, S12_1, date(2024, 5, 1))
    assert "seven years" in old["version"]["text"] and "five years" in new["version"]["text"]
    assert old["provision"]["citation"] == "Personal Data Act 2015 (XA) s 12(1)"
    assert [t["valid_from"] for t in old["timeline"]] == ["2015-07-01", "2022-01-01"]
    assert "LATER_VERSION_EXISTS" in codes(old)


def test_provision_and_judgment_lookups_carry_the_source_attribution_fields(session: Session, catalog: Catalog) -> None:
    """Licences that require attribution need it on every result, not only on search hits."""
    expected = catalog.sources.get("ulis-fictional-sample").licence
    source = provision_at(session, catalog, S12_1, date(2024, 5, 1))["version"]["source"]
    assert source["licence_name"] == expected.name and source["licence_id"] == expected.id
    assert source["attribution"] == expected.attribution_text  # None here: the fictional sample requires none
    assert {"source_id", "source_name", "sha256", "retrieved_at", "locator"} <= set(source)
    judgment = judgment_detail(session, catalog, "/akn/xa/judgment/xasc/2023/7")
    jsource = judgment["versions"][0]["source"]
    assert jsource["source_id"] == "ulis-fictional-sample" and jsource["licence_name"] == expected.name
    assert "attribution" in jsource and jsource["sha256"]


def test_repealed_prospective_and_not_yet_in_force(session: Session, catalog: Catalog) -> None:
    repealed = provision_at(session, catalog, "/akn/xa/act/2015/12/~sec_20", date(2024, 1, 1))
    assert repealed["status_on_date"] == "repealed" and "REPEALED" in codes(repealed)
    assert (
        provision_at(session, catalog, "/akn/xa/act/2015/12/~sec_20", date(2016, 1, 1))["status_on_date"] == "in_force"
    )
    prospective = provision_at(session, catalog, "/akn/xa/act/2015/12/~sec_12A__subsec_1", date(2024, 1, 1))
    assert prospective["status_on_date"] == "prospective_only" and prospective["version"] is None
    assert "NO_VERSION_ON_DATE" in codes(prospective)
    early = provision_at(session, catalog, "/akn/xa/constitution/1990/~art_14__para_1", date(1989, 1, 1))
    assert early["status_on_date"] == "not_yet_in_force"
    with pytest.raises(NotFound):
        provision_at(session, catalog, "/akn/xa/act/2015/12/~sec_99", date(2024, 1, 1))


def test_language_selection(session: Session, catalog: Catalog) -> None:
    fr = provision_at(session, catalog, "/akn/xb/act/code/civil/~art_28__para_2", date(2024, 1, 1))
    de = provision_at(session, catalog, "/akn/xb/act/code/civil/~art_28__para_2", date(2024, 1, 1), "de")
    assert fr["language"] == "fr" and fr["provision"]["citation"] == "art. 28 al. 2 CC-XB"
    assert de["provision"]["citation"] == "Art. 28 Abs. 2 ZGB-XB" and "aufbewahrt" in de["version"]["text"]
    assert fr["available_languages"] == ["de", "fr"]


def test_judgment_quotation_checked_against_the_version_then_in_force(session: Session) -> None:
    """Re Calder [3] quotes s 12(1). The quote is exact against the 2015 text, not the current one."""
    para = session.scalar(
        select(m.JudgmentParagraph)
        .where(m.JudgmentParagraph.eid == "para_3")
        .join(m.JudgmentVersion)
        .join(m.Judgment)
        .where(m.Judgment.neutral_citation == "[2020] XANPHC 15")
    )
    assert para is not None
    quote = para.text.split('"')[1]
    versions = session.scalars(select(m.ProvisionVersion).join(m.Provision).where(m.Provision.uri == S12_1)).all()
    at_decision = resolve_at(versions, date(2020, 11, 3)).version
    today = resolve_at(versions, date(2026, 10, 14)).version
    assert at_decision is not None and today is not None
    assert check_quote(quote, at_decision.text).result == "exact"  # type: ignore[attr-defined]
    assert check_quote(quote, today.text).result == "not_found"  # type: ignore[attr-defined]


def test_instrument_and_judgment_detail(session: Session, catalog: Catalog) -> None:
    act = instrument_detail(session, catalog, "/akn/xa/act/2015/12")
    assert [v["version_date"] for v in act["versions"]] == ["2015-06-01", "2022-01-01", "2023-03-01"]
    assert [p["eid"] for p in act["provisions"]] == ["sec_1", "sec_2", "sec_3", "sec_12", "sec_12A", "sec_13", "sec_20"]
    treaty = instrument_detail(session, catalog, "/akn/xr/treaty/2010/1")
    assert {p["party"] for p in treaty["treaty_parties"]} == {"XA", "XB"}
    j = judgment_detail(session, catalog, "/akn/xa/judgment/xasc/2023/7")
    assert j["citation"] == "Minerva Holdings Ltd v Information Commissioner [2023] XASC 7"
    assert j["versions"][0]["paragraphs"][3]["citation"].endswith("[4]")


def test_source_integrity_passes_for_every_document(
    session: Session, catalog: Catalog, store: LocalObjectStore
) -> None:
    for doc_id in session.scalars(select(m.SourceDocument.id)):
        result = verify_source_document(session, catalog, store, doc_id)
        assert result["result"] == "pass", result
        assert result["checked_units"] + result["checked_events"] > 0


def test_source_integrity_detects_tampering(
    session: Session, catalog: Catalog, store_dir: Path, tmp_path: Path
) -> None:
    copy = LocalObjectStore(tmp_path / "objects")
    shutil.copytree(store_dir, tmp_path / "objects", dirs_exist_ok=True)
    doc = session.scalar(
        select(m.SourceDocument).where(m.SourceDocument.external_id == "xa-act-2015-12-eng-2023-03-01")
    )
    assert doc is not None
    path = tmp_path / "objects" / "sha256" / doc.sha256[:2] / doc.sha256[2:4] / doc.sha256
    path.write_bytes(path.read_bytes().replace(b"five years", b"four years"))
    result = verify_source_document(session, catalog, copy, doc.id)
    assert result["result"] == "fail" and "altered" in result["problems"][0]
    # Tampering with the *stored* text is detected too.
    pv = session.scalar(
        select(m.ProvisionVersion)
        .join(m.Provision)
        .where(m.Provision.uri == S12_1, m.ProvisionVersion.valid_from == date(2022, 1, 1))
    )
    assert pv is not None
    pv.text = pv.text.replace("five", "four")
    session.flush()
    result = verify_source_document(session, catalog, LocalObjectStore(store_dir), doc.id)
    assert result["result"] == "fail"
    assert any("does not match its own hash" in p for p in result["problems"])


def test_attribution_placeholder_is_filled_with_the_retrieval_date() -> None:
    from datetime import UTC, datetime

    from ulis.retrieval.details import _render_attribution

    text = "Based on content from the X website at {retrieved_date}. Licensed under CC BY 4.0."
    got = _render_attribution(text, datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
    assert got == "Based on content from the X website at 2026-10-14. Licensed under CC BY 4.0."
    assert _render_attribution(None, datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_no_attribution_text_keeps_an_unfilled_placeholder() -> None:
    import re
    from pathlib import Path

    import yaml

    from ulis.settings import REPO_ROOT

    for path in (REPO_ROOT / "config" / "sources").glob("*.yaml"):
        text = (yaml.safe_load(path.read_text(encoding="utf-8")).get("licence") or {}).get("attribution_text") or ""
        for token in re.findall(r"\{[a-z_]+\}|\[[^\]]*date of download[^\]]*\]", text):
            assert token == "{retrieved_date}", f"{path.name}: unfilled placeholder {token}"
        assert isinstance(path, Path)
