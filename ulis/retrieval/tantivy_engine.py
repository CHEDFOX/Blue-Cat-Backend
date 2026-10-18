"""EXPERIMENTAL Tantivy candidate stage, for evaluation next to the PostgreSQL search.

Production search is unchanged: ``ulis.retrieval.search.search`` always calls the PostgreSQL
``_candidates``. This module provides a drop-in replacement for that one function, backed by a
Tantivy index served by the Rust helper ``scripts/search_eval/tantivy`` (binary ``ulis-tantivy``),
so the real ``search()`` (classification, grouping, limitations) can run on either engine:

    with use_engine(TantivyCandidates(TantivyProcess(index_dir), "pg")):
        search(session, catalog, request, today=today)

Index layout (one Tantivy document per passage, one index for all jurisdictions):
  * ``all``: every lexeme at its tsvector position (context = weight B first, then text = weight A,
    shifted like ``tsvector || tsvector``), positions indexed: used for matching, so ``<->``/``<N>``
    behave exactly like tsquery phrases (also across the context/text boundary);
  * ``a`` / ``b``: weight-A / weight-B lexemes with frequencies: used for ranking only;
  * fast fields for every predicate and ranking input of ``_candidates``/``_classify``: passage,
    provision version, judgment paragraph, instrument and judgment ids (u64 hi/lo pairs), kind,
    status, language, search_config, jurisdiction_id, doc_type, extent (multi-valued) +
    extent_empty, valid_from/valid_to (days since 1970-01-01) + has_valid_from/has_valid_to (NULL),
    text_length (characters).

Two modes:
  * ``pg`` (exact parity): the index holds PostgreSQL's own lexemes (``passages.tsv::text``, positions
    and weights unchanged) and every query is the tsquery PostgreSQL itself builds
    (``websearch_to_tsquery`` per configuration, ``phraseto_tsquery`` for CJK terms and concept
    labels, combined with ``||`` exactly as in ``_candidates``), translated operator by operator.
    PostgreSQL is used at query time only as the analyser (one ``SELECT`` of tsquery texts).
  * ``native`` (no PostgreSQL in the lexical stage): the Rust side copies PostgreSQL's pipeline:
    default-parser token types relevant to legal text, PostgreSQL's stopword files, Snowball
    stemmers (rust-stemmers) per configuration, ``simple`` for Welsh and other languages, CJK
    bigrams from ``search_text``/``search_context`` (``segment_for_search``) with a whitespace
    split, and a ``websearch_to_tsquery`` copy (quotes, ``or``, ``-``, punctuation never fails).
    Known gaps are listed in ``docs``/the evaluation report (HTML tags, e-mail, scientific floats,
    Snowball version differences).

Ranking: BM25 (Tantivy defaults k1 = 1.2, b = 0.75) over the positive lexemes of the expanded
query, field ``a`` weighted 1.0 plus field ``b`` weighted 0.4; IDF and lengths are collection-wide
(all jurisdictions, all configurations). Optional (``proximity`` > 0): for each pair of adjacent
query lexemes (joined by AND or a phrase operator) a sloppy phrase (slop 2) on ``all`` adds
``proximity`` x its BM25 score, a stand-in for the cover density of ``ts_rank_cd``. The score
becomes ``_Candidate.lexical``; the budget keeps the best ``budget`` passages ordered by score,
then passage id (as ``ORDER BY lexical DESC, p.id``). Filters (validity on the date, statuses,
include_* flags, case law, languages, jurisdiction chain/extent, treaties, regional
organisations, foreign law) are the WHERE clause of ``_candidates`` evaluated on fast fields with SQL NULL semantics.

Build an index (streams passages in id order, commits every N rows, resumable after a crash):
    python scripts/search_eval/build_index.py --db URL --index-root DIR --mode both
Run the real search() on an engine:
    python scripts/search_eval/run_search.py --db URL --engine tantivy-pg --index-root DIR --q "..."
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ulis.languages import FALLBACK_CONFIG, all_search_configs, segment_for_search
from ulis.retrieval import search as search_module
from ulis.retrieval.search import SearchRequest, _Candidate
from ulis.settings import REPO_ROOT

BIN_ENV = "ULIS_TANTIVY_BIN"
CRATE_DIR = REPO_ROOT / "scripts" / "search_eval" / "tantivy"
DEFAULT_BIN = CRATE_DIR / "target" / "release" / "ulis-tantivy"
MODES = ("pg", "native")
_EPOCH = date(1970, 1, 1).toordinal()

CandidatesFn = Callable[[Session, SearchRequest, date, dict[str, Any], dict[str, list[str]], int], list[_Candidate]]


class TantivyError(RuntimeError):
    pass


def find_binary() -> Path | None:
    """The ulis-tantivy binary: $ULIS_TANTIVY_BIN, else the crate's release build; None if absent."""
    env = os.environ.get(BIN_ENV)
    path = Path(env) if env else DEFAULT_BIN
    return path if path.is_file() and os.access(path, os.X_OK) else None


def _binary(binary: Path | None) -> Path:
    found = binary or find_binary()
    if found is None:
        raise TantivyError(
            f"ulis-tantivy not found: build it with `cargo build --release` in {CRATE_DIR} or set ${BIN_ENV}"
        )
    return found


def to_days(d: date | None) -> int | None:
    return None if d is None else d.toordinal() - _EPOCH


def from_days(n: int | None) -> date | None:
    return None if n is None else date.fromordinal(int(n) + _EPOCH)


class TantivyProcess:
    """A long-running ``ulis-tantivy serve`` process (JSON lines over stdin/stdout)."""

    def __init__(self, index_dir: Path | None = None, binary: Path | None = None) -> None:
        argv = [str(_binary(binary)), "serve"]
        if index_dir is not None:
            if not (Path(index_dir) / "meta.json").exists():
                raise TantivyError(f"no Tantivy index at {index_dir}")
            argv += ["--index", str(index_dir)]
        self.index_dir = index_dir
        self._lock = threading.Lock()
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        ready = self._read()
        self.mode: str | None = ready.get("mode")

    def _read(self) -> dict[str, Any]:
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise TantivyError(f"ulis-tantivy exited: {err.strip()}")
        resp: dict[str, Any] = json.loads(line)
        if not resp.get("ok"):
            raise TantivyError(resp.get("error", "unknown error"))
        return resp

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            assert self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            return self._read()

    def websearch(self, config: str, q: str) -> str:
        return str(self.request({"op": "websearch", "config": config, "q": q})["tsquery"])

    def phraseto(self, config: str, q: str) -> str:
        return str(self.request({"op": "phraseto", "config": config, "q": q})["tsquery"])

    def tsvector(self, config: str, context: str, body: str) -> dict[str, list[tuple[int, str]]]:
        tv = self.request({"op": "tsvector", "config": config, "context": context, "text": body})["tsvector"]
        return {k: [(int(p), str(w)) for p, w in v] for k, v in tv.items()}

    def close(self) -> None:
        if self._proc.poll() is None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        for stream in (self._proc.stdout, self._proc.stderr):
            if stream is not None:
                stream.close()

    def __enter__(self) -> TantivyProcess:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- index building

_ROW_SQL = (
    "SELECT p.id::text AS id, p.kind, p.provision_version_id::text AS pv, p.judgment_paragraph_id::text AS jp,"
    " p.instrument_id::text AS inst, p.judgment_id::text AS judg, p.jurisdiction_id AS jur, p.doc_type,"
    " p.language AS lang, p.search_config::text AS cfg, p.status,"
    " (p.valid_from - DATE '1970-01-01') AS vf, (p.valid_to - DATE '1970-01-01') AS vt,"
    " CAST(p.extent AS text[]) AS extent, length(p.text) AS len, {payload}"
    " FROM passages p WHERE {after} {where}"
    " ORDER BY p.id LIMIT :batch"
)
_PAYLOAD = {
    "pg": "p.tsv::text AS tsv",
    "native": "coalesce(p.search_context, p.context, '') AS ctx, coalesce(p.search_text, p.text) AS txt",
}


def passage_rows(
    conn: Session | Connection,
    mode: str,
    *,
    where: str | None = None,
    params: dict[str, Any] | None = None,
    after_id: str | None = None,
    batch: int = 5000,
) -> Iterator[dict[str, Any]]:
    """Passages as builder rows, in ascending id order (keyset pagination: no long snapshot).

    ``where`` is an extra SQL condition on ``p`` (trusted input, e.g. ``p.jurisdiction_id = :j``).
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    extra = f"AND ({where})" if where else ""
    first = text(_ROW_SQL.format(payload=_PAYLOAD[mode], after="TRUE", where=extra))
    rest = text(_ROW_SQL.format(payload=_PAYLOAD[mode], after="p.id > CAST(:after AS uuid)", where=extra))
    while True:
        sql = first if after_id is None else rest
        rows = conn.execute(sql, {**(params or {}), "after": after_id, "batch": batch}).mappings().all()
        if not rows:
            return
        for r in rows:
            yield dict(r)
        after_id = rows[-1]["id"]


def index_info(index_dir: Path, binary: Path | None = None) -> dict[str, Any] | None:
    if not (Path(index_dir) / "meta.json").exists():
        return None
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(_binary(binary)), "info", "--index", str(index_dir)], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise TantivyError(out.stderr.strip())
    info: dict[str, Any] = json.loads(out.stdout)
    return info


@dataclass
class BuildOptions:
    commit_every: int = 100_000
    heap_mb: int = 1000
    threads: int = 2
    merge: bool = True
    progress: bool = False
    batch: int = 5000
    where: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


def build_index(
    conn: Session | Connection,
    index_dir: Path,
    mode: str,
    options: BuildOptions | None = None,
    binary: Path | None = None,
) -> dict[str, Any]:
    """Builds (or resumes) the index at ``index_dir`` from the passages visible to ``conn``.

    Resuming continues after the last passage id of the last commit, so rows inserted meanwhile with
    a smaller id are only picked up by a fresh build.
    """
    opts = options or BuildOptions()
    info = index_info(index_dir, binary)
    after = info["payload"].get("last_id") if info and isinstance(info.get("payload"), dict) else None
    argv = [str(_binary(binary)), "build", "--index", str(index_dir), "--mode", mode]
    argv += ["--commit-every", str(opts.commit_every), "--heap-mb", str(opts.heap_mb), "--threads", str(opts.threads)]
    argv += [flag for flag, on in (("--merge", opts.merge), ("--progress", opts.progress)) if on]
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    assert proc.stdin is not None and proc.stdout is not None
    sent = 0
    try:
        for row in passage_rows(conn, mode, where=opts.where, params=opts.params, after_id=after, batch=opts.batch):
            proc.stdin.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            sent += 1
    finally:
        proc.stdin.close()
    out = proc.stdout.read()
    if proc.wait() != 0:
        raise TantivyError(f"ulis-tantivy build failed after {sent} rows")
    result: dict[str, Any] = json.loads(out)
    result["resumed_after"] = after
    return result


# --------------------------------------------------------------------------- candidate stage


def filter_spec(req: SearchRequest, as_of: date, scope: dict[str, Any]) -> dict[str, Any]:
    """The WHERE clause of ``_candidates`` as data for the Rust filter."""
    spec: dict[str, Any] = {
        "as_of": to_days(as_of),
        "hist": req.include_historical,
        "prosp": req.include_prospective,
        "proposed": req.include_proposed,
        "case_law": req.include_case_law,
        "langs": list(req.languages) if req.languages else None,
        "scope": None,
    }
    if scope["target"] is not None and not req.include_foreign:
        spec["scope"] = {
            "chain": list(scope["chain"]),
            "treaty_ids": [str(t) for t in scope["treaty_ids"]],
            "system": list(scope["case_law"]),
            "regional": list(scope["regional"]),
        }
    return spec


def _cjk_terms(q: str) -> list[str]:
    """The per-term CJK search forms ``_candidates`` builds (empty when the query has no CJK run)."""
    if segment_for_search(q) is None:
        return []
    return [segment_for_search(term) or term for term in q.split() if term]


def native_query_specs(req: SearchRequest, expansions: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Per-configuration query specs for mode native (analysed by the Rust copy of PostgreSQL)."""
    cjk = _cjk_terms(req.q)
    specs = []
    for config in all_search_configs():
        direct: dict[str, Any] = (
            {"or": [{"phrase": t} for t in cjk]} if cjk and config == FALLBACK_CONFIG else {"websearch": req.q}
        )
        labels = expansions.get(config, [])
        match = {"or": [direct, *({"phrase": label} for label in labels)]} if labels else direct
        specs.append({"config": config, "match": match, "direct": direct if labels else None})
    return specs


def pg_query_specs(session: Session, req: SearchRequest, expansions: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Per-configuration tsquery texts built by PostgreSQL itself, exactly as in ``_candidates``."""
    params: dict[str, Any] = {"q": req.q}
    cjk_names: list[str] = []
    for n, term in enumerate(_cjk_terms(req.q)):
        params[f"q_seg_{n}"] = term
        cjk_names.append(f"q_seg_{n}")
    columns, configs = [], all_search_configs()
    for config in configs:
        if cjk_names and config == FALLBACK_CONFIG:
            tsq = "(" + " || ".join(f"phraseto_tsquery(CAST('{config}' AS regconfig), :{n})" for n in cjk_names) + ")"
        else:
            tsq = f"websearch_to_tsquery(CAST('{config}' AS regconfig), :q)"
        expanded = tsq
        for i, label in enumerate(expansions.get(config, [])):
            name = f"x_{config}_{i}"
            params[name] = label
            expanded += f" || phraseto_tsquery(CAST('{config}' AS regconfig), :{name})"
        columns += [f"CAST(({tsq}) AS text)", f"CAST(({expanded}) AS text)"]
    row = session.execute(text(f"SELECT {', '.join(columns)}"), params).one()
    specs = []
    for i, config in enumerate(configs):
        direct, match = row[2 * i], row[2 * i + 1]
        specs.append(
            {"config": config, "match": {"tsquery": match}, "direct": None if direct == match else {"tsquery": direct}}
        )
    return specs


def _uuid(v: str | None) -> uuid.UUID | None:
    return None if v is None else uuid.UUID(v)


@dataclass
class TantivyCandidates:
    """A replacement for ``search._candidates`` backed by a Tantivy index (mode ``pg`` or ``native``)."""

    process: TantivyProcess
    mode: str
    # weight of the optional proximity clauses (adjacent query lexemes within 2 positions); 0 = BM25 only
    proximity: float = 0.0
    last: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if self.process.mode is not None and self.process.mode != self.mode:
            raise TantivyError(f"index {self.process.index_dir} is a {self.process.mode} index, not {self.mode}")

    def request(
        self,
        session: Session,
        req: SearchRequest,
        as_of: date,
        scope: dict[str, Any],
        expansions: dict[str, list[str]],
        budget: int,
    ) -> dict[str, Any]:
        specs = pg_query_specs(session, req, expansions) if self.mode == "pg" else native_query_specs(req, expansions)
        return {
            "op": "search",
            "configs": specs,
            "filters": filter_spec(req, as_of, scope),
            "budget": budget,
            "proximity": self.proximity,
        }

    def __call__(
        self,
        session: Session,
        req: SearchRequest,
        as_of: date,
        scope: dict[str, Any],
        expansions: dict[str, list[str]],
        budget: int,
    ) -> list[_Candidate]:
        resp = self.process.request(self.request(session, req, as_of, scope, expansions, budget))
        self.last = {k: v for k, v in resp.items() if k != "hits"}
        return [
            _Candidate(
                uuid.UUID(h["id"]),
                h["kind"],
                _uuid(h["pv"]),
                _uuid(h["jp"]),
                _uuid(h["inst"]),
                _uuid(h["judg"]),
                h["jur"],
                h["doc_type"],
                h["lang"],
                h["status"],
                from_days(h["vf"]),
                from_days(h["vt"]),
                float(h["score"]),
                bool(h["direct"]),
                int(h["len"]),
            )
            for h in resp["hits"]
        ]


def candidate_stage(engine: str, index_root: Path, proximity: float = 1.0) -> TantivyCandidates | None:
    """The stage for an engine name: ``pg`` (None: PostgreSQL), ``tantivy-pg``, ``tantivy-native``, with
    ``+prox`` appended for the proximity clauses (weight ``proximity``); indexes in <index_root>/<mode>."""
    if engine == "pg":
        return None
    name, _, extra = engine.partition("+")
    mode = name.removeprefix("tantivy-")
    if mode not in MODES or extra not in ("", "prox"):
        raise ValueError(f"unknown engine {engine!r}")
    return TantivyCandidates(TantivyProcess(index_root / mode), mode, proximity=proximity if extra else 0.0)


@contextmanager
def use_engine(candidates: CandidatesFn | None) -> Iterator[None]:
    """Runs ``search.search`` with another candidate stage; ``None`` keeps PostgreSQL."""
    if candidates is None:
        yield
        return
    original = search_module._candidates
    search_module._candidates = candidates  # type: ignore[assignment]
    try:
        yield
    finally:
        search_module._candidates = original
