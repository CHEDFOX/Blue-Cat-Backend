"""USLM parser rules and the pinned git-snapshot connector (no database needed)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.git_snapshot import GitSnapshotConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.uslm import UslmParseError, parse_uslm, resolve_identifier

FIX = REPO_ROOT / "tests" / "fixtures" / "uslm"
NS = 'xmlns="http://schemas.gpo.gov/xml/uslm" xmlns:dc="http://purl.org/dc/elements/1.1/"'


def _one(name: str):  # type: ignore[no-untyped-def]
    docs = parse_uslm((FIX / name).read_bytes())
    assert len(docs) == 1
    return docs[0]


def test_public_law_as_enacted() -> None:
    d = _one("115publ6-uslm.xml")
    assert (d.work_uri, d.expression_uri) == ("/us/pl/115/6", "/us/pl/115/6/en@2017-02-28")
    assert d.short_title == "Promoting Women in Entrepreneurship Act"
    assert d.validity_basis == "enactment_date_presumed" and d.consolidation_kind == "as_enacted"
    assert d.ext["statutes_at_large"] == ["131 Stat. 11"]
    sec2_1 = next(p for p in d.provisions if p.eid == "sec_2__para_1")
    assert sec2_1.num_path == ["2", "1"] and sec2_1.text.startswith("women make up almost 50 percent")
    assert all(p.status == "in_force" and str(p.valid_from) == "2017-02-28" for p in d.provisions)


def test_quoted_code_title_is_text_of_the_enacting_section() -> None:
    d = _one("113publ287-uslm.xml")
    sec3 = next(p for p in d.provisions if p.eid == "sec_3")
    assert "§ 100101" in sec3.text and sec3.is_leaf
    # Sections of the quoted title 54 are not provisions of Public Law 113-287 itself.
    assert not any("100101" in p.eid or "100904" in p.eid for p in d.provisions)


def test_compilation_is_unofficial_and_dated_by_currency() -> None:
    d = _one("COMPS-1656.xml")
    assert d.work_uri == "/us/pl/89/304"
    assert d.text_origin == "unofficial_compilation" and d.validity_basis == "attested_at_edition"
    assert str(d.currency_date) == "2007-01-12" and d.ext["compiled_through"] == "Public Law 109–479"
    assert all(p.valid_from is None for p in d.provisions)


def test_cfr_volume_yields_one_instrument_per_part() -> None:
    docs = parse_uslm((FIX / "CFR-2018-title27-vol3-uslm.xml").read_bytes())
    assert [d.short_title for d in docs] == [
        "27 CFR Part 447",
        "27 CFR Part 478",
        "27 CFR Part 479",
        "27 CFR Part 555",
        "27 CFR Part 646",
    ]
    p478 = docs[1]
    assert p478.instrument_type == "regulation" and str(p478.currency_date) == "2017-04-01"
    sec = next(p for p in p478.provisions if p.eid == "sec_478.11")
    assert sec.note and sec.note.startswith("Source note:")
    ambiguous = [p for p in p478.provisions if re.search(r"_dup\d+$", p.eid)]
    assert ambiguous and all("Ambiguous pinpoint" in (p.note or "") for p in ambiguous)
    assert any(
        r.relation == "source_rule_document" and r.href.startswith("/us/fr/") for p in p478.provisions for r in p.refs
    )


def test_bill_stages_and_resolution() -> None:
    rh, rfs = _one("HJ37_RH.XML"), _one("HJ37_RFS.XML")
    assert rh.work_uri == rfs.work_uri == "/us/bill/116/hjres/37"
    assert (str(rh.version_date), str(rfs.version_date)) == ("2019-01-30", "2019-02-13")
    assert rfs.ext["version_date_basis"].endswith("lower_bound")
    assert {p.status for p in rh.provisions} == {"proposed"}
    res = _one("HR1000_IH.XML")
    assert res.instrument_type == "resolution" and res.work_uri == "/us/bill/116/hres/1000"


def test_printed_section_number_beats_truncated_value() -> None:
    xml = f"""<cfrDoc {NS}><meta><docNumber>12</docNumber><docReleasePoint>2019-01-01</docReleasePoint></meta>
      <part identifier="/us/cfr/t12/p226"><num value="226">PART 226</num><heading>TRUTH IN LENDING</heading>
        <section><num value="226.5">§ 226.5</num><content>General.</content></section>
        <section><num value="226.5">§ 226.5a</num><content>Card applications.</content></section>
      </part></cfrDoc>""".encode()
    doc = parse_uslm(xml)[0]
    assert [p.eid for p in doc.provisions] == ["sec_226.5", "sec_226.5a"]


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><pLaw>&e;</pLaw>',
        f"<frDoc {NS}/>".encode(),
        b"<pLaw/>",
    ],
)
def test_rejects_unsafe_foreign_or_unsupported(payload: bytes) -> None:
    with pytest.raises(UslmParseError):
        parse_uslm(payload)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("/us/cfr/t27/s478.11/b/1", ("/us/cfr/t27/p478", "sec_478.11__para_b__para_1")),
        ("/us/pl/115/6/s2/a", ("/us/pl/115/6", "sec_2__subsec_a")),
        ("/us/bill/116/hjres/37/s1", ("/us/bill/116/hjres/37", "sec_1")),
        ("/us/pl/113/291", ("/us/pl/113/291", None)),
        ("/us/usc/t15/s78g", ("/us/usc/t15", "sec_78g")),  # Code titles are resolved since the Code is ingested
        # an act of the Statutes at Large and a Federal Register document are named by volume and page; a link
        # resolves only if an instrument with that name exists
        ("/us/stat/61/633", ("/us/stat/61/633", None)),
        ("/us/fr/91/65077", ("/us/fr/91/65077", None)),
        ("/us/pvtl/57/944/s1", ("/us/pvtl/57/944", "sec_1")),
        ("/us/ch/1", None),
    ],
)
def test_identifier_resolution(uri: str, expected: tuple[str, str | None] | None) -> None:
    assert resolve_identifier(uri) == expected


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_git_snapshot_connector_verifies_commit_and_hashes(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    repo = cache / "usgpo__uslm"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")
    (repo / "law.xml").write_bytes((FIX / "115publ11-uslm.xml").read_bytes())
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    commit = _git(repo, "rev-parse", "HEAD")
    import hashlib

    manifest = {
        "schema_version": 1,
        "source_id": "us-gpo-uslm-samples",
        "repository": "https://github.com/usgpo/uslm",
        "commit": commit,
        "documents": [
            {
                "path": "law.xml",
                "external_id": "x",
                "sha256": hashlib.sha256((repo / "law.xml").read_bytes()).hexdigest(),
            }
        ],
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    docs = list(GitSnapshotConnector(path, "us-gpo-uslm-samples", cache).fetch())
    assert docs[0].uri == f"https://github.com/usgpo/uslm/blob/{commit}/law.xml"
    assert docs[0].metadata["commit"] == commit

    manifest["documents"][0]["sha256"] = "0" * 64
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ManifestError, match="does not match manifest pin"):
        list(GitSnapshotConnector(path, "us-gpo-uslm-samples", cache).fetch())
    manifest["repository"] = "https://example.org/repo"
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ManifestError, match="github.com"):
        list(GitSnapshotConnector(path, "us-gpo-uslm-samples", cache).fetch())
