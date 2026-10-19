"""EXPERIMENTAL Tantivy candidate stage: parser, tsquery translation, filters and NULL semantics.

The Tantivy tests need the Rust helper (``cargo build --release`` in scripts/search_eval/tantivy, or
$ULIS_TANTIVY_BIN) and are skipped without it. The equivalence tests build both indexes from the
test database inside a rolled-back session (after rewriting validity, statuses, extents and
languages of some passages) and compare candidate SETS of ``search._candidates`` (PostgreSQL)
and the Tantivy stage for randomized requests: mode pg must be identical, field by field.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport
from ulis.languages import segment_for_search
from ulis.retrieval import tantivy_engine as te
from ulis.retrieval.search import SearchRequest, _Candidate, _candidates, _expansions, _scope, search

TODAY = date(2026, 10, 14)
needs_binary = pytest.mark.skipif(te.find_binary() is None, reason="ulis-tantivy binary not built")


# --------------------------------------------------------------------------- pure Python


def test_days_roundtrip() -> None:
    assert te.to_days(date(1970, 1, 1)) == 0
    assert te.to_days(None) is None
    for d in (date(1900, 1, 1), date(2022, 1, 1), date(2100, 12, 31)):
        assert te.from_days(te.to_days(d)) == d


def test_filter_spec_follows_the_sql_clauses() -> None:
    req = SearchRequest(q="x", jurisdiction="XA", include_historical=True, languages=["en"])
    scope = {"target": "XA", "chain": ["XA"], "case_law": ["XA", "XA-NP"], "treaty_ids": [], "regional": ["XR"]}
    spec = te.filter_spec(req, date(2020, 1, 2), scope)
    assert spec["as_of"] == te.to_days(date(2020, 1, 2))
    assert (spec["hist"], spec["prosp"], spec["proposed"], spec["case_law"]) == (True, False, False, True)
    assert spec["langs"] == ["en"]
    assert spec["scope"] == {"chain": ["XA"], "treaty_ids": [], "system": ["XA", "XA-NP"], "regional": ["XR"]}
    # no scope without a jurisdiction or with foreign law included; no language filter when empty
    assert te.filter_spec(SearchRequest(q="x"), TODAY, {**scope, "target": None})["scope"] is None
    foreign = SearchRequest(q="x", jurisdiction="XA", include_foreign=True)
    assert te.filter_spec(foreign, TODAY, scope)["scope"] is None
    assert te.filter_spec(foreign, TODAY, scope)["langs"] is None


def test_native_specs_mirror_candidates_query_construction() -> None:
    specs = {s["config"]: s for s in te.native_query_specs(SearchRequest(q="personal data"), {})}
    assert specs["english"] == {"config": "english", "match": {"websearch": "personal data"}, "direct": None}
    cjk = {s["config"]: s for s in te.native_query_specs(SearchRequest(q="個人情報 保護"), {"french": ["données"]})}
    terms = [segment_for_search("個人情報"), segment_for_search("保護")]
    assert cjk["simple"]["match"] == {"or": [{"phrase": t} for t in terms]}
    assert cjk["english"]["match"] == {"websearch": "個人情報 保護"}
    assert cjk["french"]["match"] == {"or": [{"websearch": "個人情報 保護"}, {"phrase": "données"}]}
    assert cjk["french"]["direct"] == {"websearch": "個人情報 保護"}


def test_engine_names() -> None:
    assert te.candidate_stage("pg", Path("/nonexistent")) is None
    with pytest.raises(ValueError):
        te.candidate_stage("tantivy-elastic", Path("/nonexistent"))
    with pytest.raises(ValueError):
        te.candidate_stage("tantivy-pg+boost", Path("/nonexistent"))


# --------------------------------------------------------------------------- Rust helper, no database


@pytest.fixture(scope="module")
def analyzer() -> Iterator[te.TantivyProcess]:
    if te.find_binary() is None:
        pytest.skip("ulis-tantivy binary not built")
    with te.TantivyProcess() as proc:
        yield proc


# websearch_to_tsquery(config, q) on PostgreSQL 16
WEBSEARCH = [
    ("english", "401(k)", "'401' & 'k'"),
    ("english", "6(1)(f)", "'6' & '1' & 'f'"),
    ("english", "§ 1983", "'1983'"),
    ("english", "42 U.S.C. § 1983(a)(2)", "'42' & 'u.s.c' & '1983' & '2'"),
    ("english", "L. 121-1", "'l' & '121' <-> '-1'"),
    ("english", '"right to vote" or privacy -tax', "'right' <2> 'vote' | 'privaci' & !'tax'"),
    ("english", "x OR y z", "'x' | 'y' & 'z'"),
    ("english", '-"right to vote"', "!( 'right' <2> 'vote' )"),
    ("english", "x<->y", "'x' & !'y'"),
    ("english", "the", ""),
    ("english", "OR", ""),
    ("english", '"unclosed phrase', "'unclos' <-> 'phrase'"),
    ("english", "self-defense", "'self-defens' <-> 'self' <-> 'defens'"),
    ("german", "Art. 6 Abs. 1 lit. f DSGVO", "'art' & '6' & 'abs' & '1' & 'lit' & 'f' & 'dsgvo'"),
    ("french", '"données personnelles" -santé or L. 121-1', "'don' <-> 'personnel' & !'sant' | '121' <-> '-1'"),
]


@pytest.mark.parametrize(("config", "q", "expected"), WEBSEARCH)
def test_native_websearch_matches_postgresql(analyzer: te.TantivyProcess, config: str, q: str, expected: str) -> None:
    assert analyzer.websearch(config, q) == expected


# to_tsvector(config, text) on PostgreSQL 16 (all positions weight A here)
TSVECTORS = [
    (
        "french",
        "Les données personnelles sont conservées pendant cinq ans.",
        "'an':8 'cinq':7 'conserv':5 'don':2 'le':1 'pend':6 'personnel':3",
    ),
    (
        "german",
        "Die Verarbeitung personenbezogener Daten ist rechtmäßig.",
        "'dat':4 'personenbezog':3 'rechtmass':6 'verarbeit':2",
    ),
    (
        "spanish",
        "Los datos personales serán conservados durante cinco años.",
        "'años':8 'cinc':7 'conserv':5 'dat':2 'personal':3",
    ),
    (
        "swedish",
        "Personuppgifter får behandlas endast för angivna ändamål.",
        "'angivn':6 'behandl':3 'end':4 'får':2 'personuppgift':1 'ändamål':7",
    ),
    (
        "finnish",
        "Henkilötietojen käsittelyn on oltava lainmukaista.",
        "'henkilötieto':1 'käsittely':2 'lainmuk':5 'oltav':4",
    ),
    (
        "simple",
        "Mae Gweinidogion Cymru yn cael gwneud rheoliadau.",
        "'cael':5 'cymru':3 'gweinidogion':2 'gwneud':6 'mae':1 'rheoliadau':7 'yn':4",
    ),
    ("simple", "個人 人情 情報 の 保護", "'の':4 '人情':2 '保護':5 '個人':1 '情報':3"),
]


@pytest.mark.parametrize(("config", "body", "expected"), TSVECTORS)
def test_native_analysis_matches_postgresql(analyzer: te.TantivyProcess, config: str, body: str, expected: str) -> None:
    pg = {}
    for item in expected.split(" "):
        lex, pos = item.rsplit(":", 1)
        pg[lex.strip("'")] = [(int(pos), "A")]
    assert analyzer.tsvector(config, "", body) == pg


def test_context_positions_precede_text(analyzer: te.TantivyProcess) -> None:
    # setweight(context, 'B') || setweight(text, 'A'): text positions continue after the context's last lexeme
    tv = analyzer.tsvector("english", "Part 1: Definitions.", "Definitions apply.")
    assert tv["definit"] == [(3, "B"), (4, "A")]


def test_tsquery_text_roundtrip(analyzer: te.TantivyProcess) -> None:
    for t in ["'a' <-> ( 'b' <-> 'c' )", "( 'a' | 'b' ) <2> 'c'", "!!'x' & 'y':*", "'o''brien':AB"]:
        assert analyzer.request({"op": "parse_tsquery", "tsquery": t})["tsquery"] == t


# --------------------------------------------------------------------------- equivalence against PostgreSQL

CYMRAEG = "Mae Gweinidogion Cymru yn cael gwneud rheoliadau ynghylch cadw data personol."
NIHONGO = "個人情報取扱事業者は、個人データを安全に管理しなければならない。"
MULTILINGUAL = [
    ("es", "spanish", "Los datos personales serán conservados durante cinco años por el responsable."),
    ("sv", "swedish", "Personuppgifter får behandlas endast för angivna ändamål och ska bevaras i fem år."),
    ("fi", "finnish", "Henkilötietojen käsittelyn on oltava lainmukaista ja tiedot säilytetään viisi vuotta."),
    ("cy", "simple", CYMRAEG),
    ("ja", "simple", NIHONGO),
]
QUERIES = [
    "retain personal data",
    "personal data -health",
    '"personal data"',
    "health record or treaty",
    "données conservées",
    "Daten",
    "data protection authority",
    "401(k)",
    "6(1)(f)",
    "§ 1983",
    "L. 121-1",
    '"right to" privacy',
    "-data",
    "court or tribunal appeal",
    "datos personales conservados",
    "personuppgifter behandlas",
    "henkilötietojen käsittely",
    "rheoliadau data personol",
    "個人情報",
    "個人データ 管理",
    "section",
    "the",
]


@pytest.fixture
def edge_session(session: Session, us_ingested: IngestionReport) -> Session:
    """The test corpus with validity, statuses, extents and languages rewritten on some passages."""
    rng = random.Random(1983)
    ids = [r[0] for r in session.execute(text("SELECT id FROM passages WHERE kind = 'provision' ORDER BY id"))]
    dates = [date(2015, 6, 1), date(2020, 1, 1), date(2022, 1, 1), date(2023, 3, 1), date(2026, 10, 14)]
    for i, pid in enumerate(ids[: max(40, len(ids) // 3)]):
        kind = i % 8
        vf = rng.choice(dates)
        vt = vf + timedelta(days=rng.choice([1, 365, 1500]))
        values: dict[str, Any] = {"id": pid}
        if kind == 0:
            sql = "UPDATE passages SET status = 'in_force', valid_from = NULL, valid_to = NULL WHERE id = :id"
        elif kind == 1:
            sql = "UPDATE passages SET status = 'in_force', valid_from = :vf, valid_to = :vt WHERE id = :id"
        elif kind == 2:
            sql = "UPDATE passages SET status = 'proposed', valid_from = NULL WHERE id = :id"
        elif kind == 3:
            sql = "UPDATE passages SET status = 'proposed', valid_from = :vf, valid_to = :vt WHERE id = :id"
        elif kind == 4:
            sql = "UPDATE passages SET status = 'prospective', valid_from = :vf, valid_to = NULL WHERE id = :id"
        elif kind == 5:
            sql = "UPDATE passages SET status = 'repealed', valid_from = :vf, valid_to = :vt WHERE id = :id"
        elif kind == 6:
            sql = "UPDATE passages SET extent = CAST(:ext AS varchar[]) WHERE id = :id"
            values["ext"] = rng.choice([[], ["XA-NP"], ["XA-SP", "XA"], ["US"]])
        else:
            lang, config, body = MULTILINGUAL[(i // 8) % len(MULTILINGUAL)]
            sql = (
                "UPDATE passages SET language = :lang, search_config = CAST(:cfg AS regconfig), text = :body,"
                " search_text = :seg, search_context = NULL WHERE id = :id"
            )
            values.update(lang=lang, cfg=config, body=body, seg=segment_for_search(body))
        session.execute(text(sql), {**values, "vf": vf, "vt": vt})
    session.flush()
    return session


@pytest.fixture
def stages(edge_session: Session, tmp_path: Path) -> Iterator[dict[str, te.TantivyCandidates]]:
    if te.find_binary() is None:
        pytest.skip("ulis-tantivy binary not built")
    out: dict[str, te.TantivyCandidates] = {}
    for mode in te.MODES:
        result = te.build_index(edge_session, tmp_path / mode, mode, te.BuildOptions(commit_every=50, batch=97))
        assert result["docs_total"] == edge_session.execute(text("SELECT count(*) FROM passages")).scalar()
        out[mode] = te.TantivyCandidates(te.TantivyProcess(tmp_path / mode), mode)
    yield out
    for stage in out.values():
        stage.process.close()


def boundary_dates(session: Session) -> list[date]:
    found = set()
    for vf, vt in session.execute(text("SELECT DISTINCT valid_from, valid_to FROM passages")):
        for d in (vf, vt):
            if d is not None:
                found |= {d - timedelta(days=1), d, d + timedelta(days=1)}
    return sorted(found)


def random_requests(session: Session, n: int, seed: int) -> list[SearchRequest]:
    rng = random.Random(seed)
    dates = boundary_dates(session)
    jurisdictions = [None, "XA", "XA-NP", "XA-SP", "XB", "XR", "US", "GB-ENG"]
    reqs = []
    for _ in range(n):
        reqs.append(
            SearchRequest(
                q=rng.choice(QUERIES),
                jurisdiction=rng.choice(jurisdictions),
                as_of=rng.choice([None, *dates, date(2000, 1, 1)]),
                languages=rng.choice([[], [], ["en"], ["fr"], ["en", "de"], ["es", "sv", "fi"], ["cy", "ja"]]),
                include_foreign=rng.random() < 0.2,
                include_proposed=rng.random() < 0.4,
                include_historical=rng.random() < 0.3,
                include_prospective=rng.random() < 0.4,
                include_case_law=rng.random() < 0.8,
                query_language=rng.choice([None, None, "en", "fr"]),
            )
        )
    return reqs


def row(c: _Candidate) -> tuple[Any, ...]:
    return (
        c.kind,
        c.provision_version_id,
        c.judgment_paragraph_id,
        c.instrument_id,
        c.judgment_id,
        c.jurisdiction_id,
        c.doc_type,
        c.language,
        c.status,
        c.valid_from,
        c.valid_to,
        c.direct,
        c.text_length,
    )


def compare(
    session: Session, catalog: Catalog, stage: te.TantivyCandidates, req: SearchRequest
) -> tuple[dict[Any, tuple[Any, ...]], dict[Any, tuple[Any, ...]]]:
    as_of = req.as_of or TODAY
    scope = _scope(session, catalog, req.jurisdiction, as_of)
    expansions = _expansions(session, req.q, req.query_language)[1]
    pg = {c.passage_id: row(c) for c in _candidates(session, req, as_of, scope, expansions, 1_000_000)}
    tv = {c.passage_id: row(c) for c in stage(session, req, as_of, scope, expansions, 1_000_000)}
    return pg, tv


@pytest.mark.db
def test_mode_pg_candidate_sets_are_identical(
    edge_session: Session, catalog: Catalog, stages: dict[str, te.TantivyCandidates]
) -> None:
    nonempty = 0
    for req in random_requests(edge_session, 300, seed=7):
        pg, tv = compare(edge_session, catalog, stages["pg"], req)
        assert tv == pg, req
        nonempty += bool(pg)
    assert nonempty > 60


@pytest.mark.db
def test_mode_native_candidate_sets(
    edge_session: Session, catalog: Catalog, stages: dict[str, te.TantivyCandidates]
) -> None:
    # The corpus' texts and these queries are within what the native analyser copies exactly.
    mismatches, languages = [], set()
    for req in random_requests(edge_session, 300, seed=11):
        pg, tv = compare(edge_session, catalog, stages["native"], req)
        languages |= {r[7] for r in pg.values()}
        if tv != pg:
            mismatches.append((req.q, len(pg), len(tv), len(set(pg) ^ set(tv))))
    assert not mismatches
    assert {"en", "fr", "de", "es", "sv", "fi", "cy", "ja"} <= languages


@pytest.mark.db
@needs_binary
def test_null_and_boundary_semantics(edge_session: Session, catalog: Catalog, tmp_path: Path) -> None:
    s = edge_session
    pid = s.execute(text("SELECT id FROM passages WHERE kind = 'provision' ORDER BY id LIMIT 1")).scalar()
    s.execute(
        text("UPDATE passages SET text = 'zyxwvut boundary probe', search_text = NULL WHERE id = :id"), {"id": pid}
    )
    s.flush()
    cases = [
        # (status, valid_from, valid_to, request flags, as_of, expected)
        ("in_force", None, None, {}, date(1900, 1, 1), True),  # NULL start: attested at the edition date
        ("in_force", date(2020, 1, 1), None, {}, date(2020, 1, 1), True),  # first day
        ("in_force", date(2020, 1, 1), None, {}, date(2019, 12, 31), False),
        ("in_force", date(2020, 1, 1), date(2021, 1, 1), {}, date(2021, 1, 1), False),  # valid_to is exclusive
        ("in_force", date(2020, 1, 1), date(2021, 1, 1), {"include_historical": True}, date(2025, 1, 1), True),
        ("proposed", None, None, {"include_proposed": True}, TODAY, False),  # NULL <= as_of is not TRUE
        ("proposed", date(2026, 1, 1), None, {"include_proposed": True}, TODAY, True),
        ("proposed", date(2026, 1, 1), None, {}, TODAY, False),
        ("prospective", date(2030, 1, 1), None, {"include_prospective": True}, TODAY, True),
        ("prospective", date(2030, 1, 1), None, {}, TODAY, False),
        ("repealed", date(2000, 1, 1), None, {"include_historical": True, "include_prospective": True}, TODAY, False),
    ]
    for status, vf, vt, flags, as_of, expected in cases:
        s.execute(
            text("UPDATE passages SET status = :st, valid_from = :vf, valid_to = :vt WHERE id = :id"),
            {"st": status, "vf": vf, "vt": vt, "id": pid},
        )
        s.flush()
        req = SearchRequest(q="zyxwvut", as_of=as_of, **flags)
        for mode in te.MODES:
            index_dir = tmp_path / f"{mode}-{status}-{vf}-{vt}-{as_of}-{len(flags)}"
            te.build_index(s, index_dir, mode, te.BuildOptions(where="p.id = :probe", params={"probe": pid}))
            with te.TantivyProcess(index_dir) as proc:
                pg, tv = compare(s, catalog, te.TantivyCandidates(proc, mode), req)
            assert (pid in pg) is expected, (status, vf, vt, flags, as_of)
            assert tv == pg, (mode, status, vf, vt, flags, as_of)


@pytest.mark.db
def test_search_runs_unchanged_on_tantivy(
    edge_session: Session, catalog: Catalog, stages: dict[str, te.TantivyCandidates]
) -> None:
    req = SearchRequest(q="retain personal data", jurisdiction="XA", as_of=date(2021, 6, 1), limit=50)
    baseline = search(edge_session, catalog, req, today=TODAY)
    with te.use_engine(stages["pg"]):
        swapped = search(edge_session, catalog, req, today=TODAY)
    assert swapped["total_candidates"] == baseline["total_candidates"] > 0
    assert {r["passage_id"] for r in swapped["results"]} == {r["passage_id"] for r in baseline["results"]}
    assert all(r["score"]["lexical"] > 0 for r in swapped["results"])
    # the production function is restored afterwards
    assert search(edge_session, catalog, req, today=TODAY)["results"] == baseline["results"]
