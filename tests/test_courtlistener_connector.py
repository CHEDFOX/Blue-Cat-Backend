"""CourtListener bulk connector: CSV reading, scope, grouping and the resumable download, on synthetic CSV files.

The rows below are test data written for these tests (invented case names and numbers) in the layout of the publisher's
files (PostgreSQL COPY CSV, every field quoted, backslash as escape character); real rows are in the parser tests. Court
ids are real CourtListener ids that the court table of ``ulis/sources/parsers/courtlistener_courts.py`` models.
"""

from __future__ import annotations

import bz2
import csv
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ulis.settings import REPO_ROOT
from ulis.sources.connectors import courtlistener_bulk as cl
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError

MANIFEST = REPO_ROOT / "config" / "manifests" / "us-courtlistener-bulk-s3.yaml"
SOURCE = "us-courtlistener-bulk-s3"
GEN = "2026-09-30"

COURT_HEADER = ["id", "full_name", "short_name", "citation_string", "jurisdiction", "start_date", "end_date"]
COURTS = [
    ["scotus", "Supreme Court of the United States", "Supreme Court", "U.S.", "F", "1789-09-24", ""],
    ["ca9", "Court of Appeals for the Ninth Circuit", "Ninth Circuit", "9th Cir.", "F", "1891-03-03", ""],
    ["wash", "Washington Supreme Court", "Washington Supreme Court", "Wash.", "S", "1854-01-01", ""],
    ["nysd", "District Court, S.D. New York", "S.D. New York", "S.D.N.Y.", "FD", "", ""],
    ["unknowncourt", "A court the profile does not model", "Unknown", "Unk.", "ST", "", ""],
]
CLUSTER_HEADER = [
    "id",
    "docket_id",
    "date_filed",
    "date_filed_is_approximate",
    "case_name",
    "case_name_full",
    "source",
    "precedential_status",
    "blocked",
    "date_blocked",
    "judges",
    "syllabus",
    "disposition",
    "scdb_id",
    "filepath_json_harvard",
]


def cluster(cid: int, docket: int, filed: str, name: str, status: str = "Published", blocked: str = "f") -> list[str]:
    return [str(cid), str(docket), filed, "f", name, "", "U", status, blocked, "", "", "", "Affirmed", "", ""]


CLUSTERS = [
    cluster(1, 10, "1973-01-22", "Alpha v. Beta"),
    cluster(2, 11, "2001-05-02", "Gamma v. Delta"),
    cluster(3, 12, "2019-03-04", 'Epsilon v. "Zeta"'),
    cluster(4, 11, "1999-01-01", "Eta v. Theta", status="Unpublished"),
    cluster(5, 13, "2010-06-01", "Iota v. Kappa", blocked="t"),
    cluster(6, 14, "2012-02-02", "Lambda v. Mu"),  # court not modelled
    cluster(7, 15, "1850-07-04", "Nu v. Xi"),  # before the from_year of its rule
    cluster(8, 16, "2015-08-08", "Omicron v. Pi"),  # its docket is blocked
    cluster(9, 17, "", "No date v. Source"),
]
DOCKET_HEADER = ["id", "court_id", "docket_number", "case_name", "blocked", "date_blocked"]
DOCKETS = [
    ["10", "scotus", "71-18", "Alpha v. Beta", "f", ""],
    ["11", "ca9", "No. 99-1234", "Gamma v. Delta", "f", ""],
    ["12", "wash", "97654-1", "Epsilon v. Zeta", "f", ""],
    ["13", "ca9", "13-1", "Iota v. Kappa", "f", ""],
    ["14", "unknowncourt", "14-1", "Lambda v. Mu", "f", ""],
    ["15", "wash", "15-1", "Nu v. Xi", "f", ""],
    ["16", "nysd", "16-1", "Omicron v. Pi", "t", ""],
    ["17", "scotus", "17-1", "No date", "f", ""],
    ["99", "scotus", "99-9", "Never referenced", "f", ""],
]
CITATION_HEADER = ["cluster_id", "volume", "reporter", "page", "type"]
CITATIONS = [
    ["1", "410", "U.S.", "113", "1"],
    ["1", "93", "S. Ct.", "705", "1"],
    ["2", "250", "F.3d", "1", "1"],
    ["3", "2019", "WL", "5", "7"],
    ["99", "1", "U.S.", "1", "1"],
]
OPINION_HEADER = [
    "id",
    "type",
    "author_str",
    "joined_by_str",
    "per_curiam",
    "sha1",
    "download_url",
    "extracted_by_ocr",
    "cluster_id",
    "html_columbia",
    "html_lawbox",
    "xml_harvard",
    "html",
    "plain_text",
]


def opinion(oid: int, cid: int, kind: str = "020lead", **texts: str) -> list[str]:
    row = {k: "" for k in OPINION_HEADER}
    row.update(id=str(oid), type=kind, author_str="Judge", per_curiam="f", sha1="0" * 40, extracted_by_ocr="f")
    row["cluster_id"] = str(cid)
    row.update(texts)
    return [row[k] for k in OPINION_HEADER]


OPINIONS = [
    opinion(100, 1, plain_text='Text of the lead opinion.\n\nSecond paragraph with a "quote" and a back\\slash.'),
    opinion(101, 1, "040dissent", xml_harvard="<opinion><p>Dissent text.</p></opinion>"),
    opinion(102, 2, html="<p>Gamma.</p>", plain_text="ignored because html comes first? no: html_columbia first"),
    opinion(103, 3, "010combined", plain_text="Epsilon text."),
    opinion(104, 4, plain_text="Unpublished text."),
    opinion(105, 5, plain_text="Blocked cluster text."),
    opinion(106, 6, plain_text="Court not modelled."),
    opinion(107, 7, plain_text="Too early."),
    opinion(108, 8, plain_text="Docket blocked."),
    opinion(109, 9, plain_text="No date."),
    opinion(110, 99, plain_text="Cluster that is in no index."),
    opinion(111, 1, "030concurrence"),  # no text in any chosen column: skipped
]


def write_csv(path: Path, header: list[str], rows: list[list[str]], *, bz: bool = False) -> None:
    opener: Any = bz2.open if bz else open
    with opener(path, "wt", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, escapechar="\\", quoting=csv.QUOTE_ALL, doublequote=False, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def make_manifest(tmp_path: Path, rules: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest["scope"] = {"statuses": ["Published"], "rules": rules, **extra.pop("scope", {})}
    manifest.update(extra)
    return manifest


@pytest.fixture
def local_dir(tmp_path: Path) -> Path:
    d = tmp_path / "files"
    d.mkdir()
    write_csv(d / f"courts-{GEN}.csv", COURT_HEADER, COURTS)
    write_csv(d / f"opinion-clusters-{GEN}.csv", CLUSTER_HEADER, CLUSTERS)
    write_csv(d / f"dockets-{GEN}.csv", DOCKET_HEADER, DOCKETS)
    write_csv(d / f"citations-{GEN}.csv", CITATION_HEADER, CITATIONS)
    write_csv(d / f"opinions-{GEN}.csv", OPINION_HEADER, OPINIONS)
    return d


def run(tmp_path: Path, local_dir: Path, rules: list[dict[str, Any]], **extra: Any) -> tuple[list[dict[str, Any]], Any]:
    manifest = make_manifest(tmp_path, rules, **extra)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    connector = cl.CourtListenerBulkConnector(path, SOURCE, tmp_path / "cache", local_dir=local_dir)
    connector.progress = lambda message: None
    docs = [json.loads(raw.content) | {"_id": raw.external_id, "_uri": raw.uri} for raw in connector.fetch()]
    return docs, connector


# --------------------------------------------------------------------------- the committed manifest


def test_committed_manifest_loads_and_names_one_generation_of_five_files() -> None:
    manifest = cl.load_manifest(MANIFEST, SOURCE)
    assert set(manifest["files"]) == set(cl.FILE_KEYS)
    assert all(GEN in spec["name"] and spec["bytes"] > 0 for spec in manifest["files"].values())
    assert manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]
    scope = cl._scope_from(manifest)
    assert scope.rules and scope.statuses
    # the one text column never read: html_with_citations (CourtListener's own citation links) is not even selectable,
    # and html_anon_2020 (third-party editorial summaries) is not in the default order
    assert "html_with_citations" not in cl.TEXT_COLUMNS_ALL and "html_anon_2020" not in scope.text_columns


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"base_url": "https://www.courtlistener.com"}, "base_url"),
        ({"user_agent": "bot"}, "user_agent"),
        ({"crawl_delay_seconds": 1}, "crawl_delay"),
        ({"generation": "latest"}, "generation"),
        ({"limit": 0}, "limit"),
        ({"files": {"courts": {"name": "courts-2026-09-30.csv.bz2", "bytes": 1}}}, "files must list"),
        ({"source_id": "other"}, "mismatch"),
        ({"only_rules": ["no-such-rule"]}, "only_rules"),
    ],
)
def test_manifest_validation(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) | change
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ManifestError, match=message):
        cl.load_manifest(path, SOURCE)


def test_scope_rejects_unknown_text_columns_and_empty_rules(tmp_path: Path) -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for scope in (
        {"rules": []},
        {"rules": [{"name": "x", "courts": ["scotus"]}], "text_columns": ["html_with_citations"]},
    ):
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(manifest | {"scope": scope}), encoding="utf-8")
        with pytest.raises(ManifestError):
            cl.load_manifest(path, SOURCE)


# --------------------------------------------------------------------------- CSV reading


def test_csv_rows_reads_postgres_copy_csv_plain_and_bz2(tmp_path: Path) -> None:
    header = ["id", "text", "other"]
    rows = [["1", 'a "quoted" word', "x"], ["2", "two\nlines and a back\\slash", ""], ["3", "é–ü", "z"]]
    for name, bz in (("t.csv", False), ("t.csv.bz2", True)):
        write_csv(tmp_path / name, header, rows, bz=bz)
        got = list(cl.csv_rows(tmp_path / name))
        assert [r["text"] for r in got] == [r[1] for r in rows]
        assert list(cl.csv_rows(tmp_path / name, ("id", "other")))[1] == {"id": "2", "other": ""}


def test_csv_rows_reports_a_truncated_row_after_the_complete_ones(tmp_path: Path) -> None:
    path = tmp_path / "cut.csv"
    path.write_text('"id","text"\n"1","ok"\n"2"\n', encoding="utf-8")
    it = cl.csv_rows(path)
    assert next(it)["id"] == "1"
    with pytest.raises(ValueError, match="2 fields|1 fields"):
        next(it)


# --------------------------------------------------------------------------- scope and bundles


RULES = [
    {"name": "supreme-court", "courts": ["scotus"]},
    {"name": "appeals-and-state-high", "groups": ["F", "S"], "from_year": 1900},
]


def test_scope_selects_published_unblocked_dated_clusters_of_modelled_courts(tmp_path: Path, local_dir: Path) -> None:
    docs, connector = run(tmp_path, local_dir, RULES)
    assert sorted(d["_id"] for d in docs) == ["cl:cluster:1", "cl:cluster:2", "cl:cluster:3"]
    stats = connector.index_stats
    assert stats.clusters_read == 9 and stats.clusters_undated == 1
    assert stats.clusters_blocked == 2  # the cluster flag (5) and the docket flag (8)
    assert stats.clusters_court_not_modelled == 1 and stats.clusters_in_scope == 3
    assert stats.by_rule == {"supreme-court": 1, "appeals-and-state-high": 2}
    assert connector.fetch_errors == []


def test_bundle_carries_verbatim_metadata_and_only_the_chosen_text_column(tmp_path: Path, local_dir: Path) -> None:
    docs, _ = run(tmp_path, local_dir, RULES)
    first = next(d for d in docs if d["_id"] == "cl:cluster:1")
    assert first["schema"] == "ulis-us-courtlistener/1" and first["continuation"] is False
    assert first["court"]["id"] == "scotus" and first["court"]["citation_string"] == "U.S."
    assert first["docket"] == {"id": 10, "docket_number": "71-18", "case_name": "Alpha v. Beta"}
    assert first["cluster"]["date_filed"] == "1973-01-22" and first["cluster"]["precedential_status"] == "Published"
    assert [(c["volume"], c["reporter"], c["page"]) for c in first["citations"]] == [
        ("410", "U.S.", "113"),
        ("93", "S. Ct.", "705"),
    ]
    # lead opinion first, then the dissent; the opinion without text is left out; text kept exactly
    assert [(o["id"], o["type"], o["text_column"]) for o in first["opinions"]] == [
        (100, "020lead", "plain_text"),
        (101, "040dissent", "xml_harvard"),
    ]
    assert (
        first["opinions"][0]["text"]
        == 'Text of the lead opinion.\n\nSecond paragraph with a "quote" and a back\\slash.'
    )
    assert "html_with_citations" not in json.dumps(first)
    # the order of text_columns decides: html comes before plain_text by default
    second = next(d for d in docs if d["_id"] == "cl:cluster:2")
    assert second["opinions"][0]["text_column"] == "html" and second["opinions"][0]["text"] == "<p>Gamma.</p>"
    assert first["_uri"].endswith(f"opinions-{GEN}.csv.bz2#cluster-1")


def test_bundles_are_deterministic_so_a_rerun_is_unchanged(tmp_path: Path, local_dir: Path) -> None:
    one = [d.pop("_id") and d for d in run(tmp_path, local_dir, RULES)[0]]
    two = [d.pop("_id") and d for d in run(tmp_path / "again", local_dir, RULES)[0]]
    assert one == two


def test_year_range_and_status_per_rule(tmp_path: Path, local_dir: Path) -> None:
    rules = [
        {"name": "old", "courts": ["scotus"], "to_year": 1980},
        {"name": "unpublished", "groups": ["F"], "statuses": ["Unpublished"]},
    ]
    docs, _ = run(tmp_path, local_dir, rules)
    assert sorted(d["_id"] for d in docs) == ["cl:cluster:1", "cl:cluster:4"]
    docs, _ = run(tmp_path / "x", local_dir, RULES, scope={"exclude_courts": ["wash"]})
    assert sorted(d["_id"] for d in docs) == ["cl:cluster:1", "cl:cluster:2"]


def test_only_rules_runs_one_part_of_the_scope_from_the_same_index(tmp_path: Path, local_dir: Path) -> None:
    docs, connector = run(tmp_path, local_dir, RULES, only_rules=["appeals-and-state-high"])
    assert sorted(d["_id"] for d in docs) == ["cl:cluster:2", "cl:cluster:3"]
    assert connector.index_stats.clusters_in_scope == 3  # the index itself covers every rule


def test_min_chars_leaves_out_stubs_such_as_orders_and_counts_them(tmp_path: Path, local_dir: Path) -> None:
    rules = [{"name": "supreme-court", "courts": ["scotus"], "min_chars": 100_000}, RULES[1]]
    docs, connector = run(tmp_path, local_dir, rules)
    assert sorted(d["_id"] for d in docs) == ["cl:cluster:2", "cl:cluster:3"]
    assert connector.skipped_small == 1 and connector.fetch_errors == []
    # the threshold does not change which cases the index holds, so the index is reused
    docs, _ = run(tmp_path, local_dir, [{**rules[0], "min_chars": 1}, RULES[1]])
    assert "cl:cluster:1" in [d["_id"] for d in docs]
    with pytest.raises(ManifestError, match="min_chars"):
        run(tmp_path / "bad", local_dir, [{**rules[0], "min_chars": -5}, RULES[1]])


def test_limit_stops_after_that_many_documents(tmp_path: Path, local_dir: Path) -> None:
    docs, _ = run(tmp_path, local_dir, RULES, limit=2)
    assert len(docs) == 2


def test_late_rows_of_a_cluster_become_a_continuation_document(
    tmp_path: Path, local_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # cluster 1's dissent comes after more than PENDING_CLUSTERS other clusters: a second document, no syllabus repeated
    rows = [
        opinion(100, 1, plain_text="Lead."),
        opinion(103, 3, "010combined", plain_text="Epsilon."),
        opinion(102, 2, plain_text="Gamma."),
        opinion(101, 1, "040dissent", plain_text="Dissent."),
    ]
    write_csv(local_dir / f"opinions-{GEN}.csv", OPINION_HEADER, rows)
    monkeypatch.setattr(cl, "PENDING_CLUSTERS", 1)
    docs, _ = run(tmp_path, local_dir, RULES)
    ids = [d["_id"] for d in docs]
    assert ids.count("cl:cluster:1") == 1 and "cl:cluster:1:101" in ids
    late = next(d for d in docs if d["_id"] == "cl:cluster:1:101")
    assert late["continuation"] is True and late["cluster"]["syllabus"] == ""
    assert [o["id"] for o in late["opinions"]] == [101]


def test_a_truncated_opinions_file_is_reported_not_fatal(tmp_path: Path, local_dir: Path) -> None:
    path = local_dir / f"opinions-{GEN}.csv"
    path.write_text(path.read_text(encoding="utf-8") + '"999","x"\n', encoding="utf-8")
    docs, connector = run(tmp_path, local_dir, RULES)
    assert len(docs) == 3 and any("the rest of the file was not read" in e for e in connector.fetch_errors)


def test_a_court_missing_from_the_court_file_has_no_group_so_a_group_rule_does_not_select_it(
    tmp_path: Path, local_dir: Path
) -> None:
    write_csv(local_dir / f"courts-{GEN}.csv", COURT_HEADER, [c for c in COURTS if c[0] != "wash"])
    docs, connector = run(tmp_path, local_dir, RULES)
    assert sorted(d["_id"] for d in docs) == ["cl:cluster:1", "cl:cluster:2"]
    assert connector.fetch_errors == []


def test_everything_scope_counts_all_clusters_for_the_scope_report(tmp_path: Path, local_dir: Path) -> None:
    manifest = cl.load_manifest(MANIFEST, SOURCE)
    files = cl.CourtListenerFiles(manifest, tmp_path / "cache", local_dir=local_dir)
    index = tmp_path / "all.sqlite"
    stats = cl.build_index(files, cl.Scope.everything(), index, keep_text=False)
    assert stats.clusters_in_scope == 6  # all clusters with a date, an unblocked docket and no block flag
    rows = cl.count_clusters(index)
    assert ("scotus", "U", 1973, "Published", 1) in rows
    assert sum(r[4] for r in rows) == 6


# --------------------------------------------------------------------------- download


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self.data, self.status, self.pos = data, status, 0
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, n: int) -> bytes:
        chunk, self.pos = self.data[self.pos : self.pos + n], self.pos + n
        return chunk


def make_files(tmp_path: Path, payload: bytes) -> cl.CourtListenerFiles:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest["files"]["courts"]["bytes"] = len(payload)
    return cl.CourtListenerFiles(manifest, tmp_path / "cache", sleep=lambda s: None)


def test_download_resumes_a_partial_file_with_a_range_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = bytes(range(256)) * 40
    files = make_files(tmp_path, payload)
    target = files.path("courts")
    target.parent.mkdir(parents=True)
    target.with_name(target.name + ".part").write_bytes(payload[:1000])
    calls: list[int] = []

    def fake_open(url: str, start: int, method: str = "GET") -> FakeResponse:
        calls.append(start)
        assert url.startswith("https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/courts-")
        return FakeResponse(payload[start:], 206 if start else 200)

    monkeypatch.setattr(files, "_open", fake_open)
    assert files.ensure("courts") == target
    assert calls == [1000] and target.read_bytes() == payload
    assert files.ensure("courts") == target and calls == [1000]  # complete: nothing requested again


def test_download_refuses_a_size_that_differs_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = make_files(tmp_path, b"x" * 100)
    monkeypatch.setattr(files, "_open", lambda url, start, method="GET": FakeResponse(b"x" * 90))
    with pytest.raises(FetchError, match="manifest says 100|could not download"):
        files.ensure("courts")


def test_download_stops_on_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    files = make_files(tmp_path, b"x" * 100)

    def refuse(url: str, start: int, method: str = "GET") -> FakeResponse:
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(files, "_open", refuse)
    with pytest.raises(FetchError, match="HTTP 403 .*refusal is respected"):
        files.ensure("courts")


def test_offline_mode_never_downloads(tmp_path: Path) -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    files = cl.CourtListenerFiles(manifest, tmp_path / "cache", offline=True)
    with pytest.raises(FetchError, match="offline and not downloaded"):
        files.ensure("courts")
