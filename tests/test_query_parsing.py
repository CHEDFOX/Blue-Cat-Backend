"""Query cleaning, clause splitting and citation parsing (no database)."""

from __future__ import annotations

from typing import Any

import pytest

from ulis.retrieval.citations import normalise, parse_citation
from ulis.retrieval.query import Clause, cjk_units, clean_query, parse_clauses
from ulis.retrieval.search import _cjk_strict_query


def test_clean_query_drops_control_and_invisible_characters() -> None:
    assert clean_query("contract\x00law") == "contract law"
    assert clean_query("​pri­vacy​﻿") == "privacy"
    assert clean_query("line\nbreak\ttab") == "line\nbreak\ttab"
    assert clean_query("\x00\x01") == ""


def test_parse_clauses_follows_websearch_syntax() -> None:
    assert parse_clauses('"personal data" breach OR leak -employee notification') == [
        Clause('"personal data"', '"personal data"', quoted=True),
        Clause("breach or leak", "breach OR leak"),
        Clause("employee", "employee", negated=True),
        Clause("notification", "notification"),
    ]
    negated_phrase = Clause('"trade secret"', '"trade secret"', negated=True, quoted=True)
    assert parse_clauses('-"trade secret" or') == [negated_phrase]
    assert parse_clauses('"" or') == []


def test_cjk_units_split_content_words_into_character_pairs() -> None:
    assert cjk_units("個人情報の第三者提供") == [
        ("個人", "個人情報"),
        ("人情", "個人情報"),
        ("情報", "個人情報"),
        ("第三", "第三者提供"),
        ("三者", "第三者提供"),
        ("者提", "第三者提供"),
        ("提供", "第三者提供"),
    ]
    assert cjk_units("ひらがなだけ") == [("ひら", "ひらがなだけ"), ("らが", "ひらがなだけ"), ("がな", "ひらがなだけ"),
                                       ("なだ", "ひらがなだけ"), ("だけ", "ひらがなだけ")]  # fmt: skip
    assert cjk_units("法") == [("法", "法")]


def test_cjk_minus_term_excludes() -> None:
    params: dict[str, Any] = {}
    tsq = _cjk_strict_query("個人情報 -第三者", params)
    assert tsq is not None and "!!(" in tsq
    positive, negative = tsq.split("&& !!")
    assert ":q_seg_0" in positive and ":q_seg_1" in negative
    assert params == {"q_seg_0": " 個人 人情 情報 ", "q_seg_1": " 第三 三者 "}
    assert _cjk_strict_query("privacy", {}) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("§ 823", ("", "823")),
        ("(1)", ("", "1")),
        ("1.", ("", "1")),
        ("a)", ("", "a")),
        ("art_6", ("art", "6")),
        ("point_f", ("point", "f")),
        ("ch:3", ("ch", "3")),
        ("s:49 f", ("s", "49f")),
        ("Art 1", ("art", "1")),
        ("825.305", ("", "825-305")),
        ("1.401(k)-1", ("", "1-401k-1")),
        ("(ii.1)", ("", "ii-1")),
        ("9_2", ("", "9-2")),
        ("1.º", ("", "1")),
        ("102 bis", ("", "102bis")),
        ("L212-1", ("", "l212-1")),
        ("def:accounting document", ("def", "accountingdocument")),
    ],
)
def test_normalise_number_path_elements(raw: str, expected: tuple[str, str]) -> None:
    assert normalise(raw) == expected


@pytest.mark.parametrize(
    ("q", "units", "reference", "uri"),
    [
        ("§ 823 Abs. 1 BGB", ["823", "1"], "BGB", None),
        ("§ 823 Abs. 1 S. 2 BGB", ["823", "1"], "BGB", None),
        ("Art. 6(1)(f) GDPR legitimate interests", ["6", "1", "f"], "GDPR legitimate interests", None),
        ("Art. 6 Abs. 1 lit. f DSGVO", ["6", "1", "f"], "DSGVO", None),
        ("3 kap. 1 § brottsbalken", ["ch:3", "1"], "brottsbalken", None),
        ("5 kap. 2 § 3 st. miljöbalken", ["ch:5", "2", "3"], "miljöbalken", None),
        ("36 § avtalslagen", ["36"], "avtalslagen", None),
        ("RL 28:1", ["ch:28", "1"], "RL", None),
        ("TSL 7 luku 3 §", ["ch:7", "3"], "TSL", None),
        ("hallintolain 49 f § oikaisuvaatimus", ["49f"], "hallintolain oikaisuvaatimus", None),
        ("art. 1902 CC", ["1902"], "CC", None),
        ("art. 56.1 ET despido improcedente", ["56-1"], "ET despido improcedente", None),
        ("art. L. 212-1 C. consom. clauses abusives", ["l212-1"], "C. consom. clauses abusives", None),
        ("article 1240 du Code civil", ["1240"], "du Code civil", None),
        ("s. 718.2 aggravating circumstances", ["718-2"], "aggravating circumstances", None),
        ("section 7 of the Data Protection Act 1998", ["7"], "of the Data Protection Act 1998", None),
        ("adran 173 Deddf Rhentu Cartrefi (Cymru) 2016", ["173"], "Deddf Rhentu Cartrefi (Cymru) 2016", None),
        ("a.5 Deddf Deddfwriaeth (Cymru) 2019", ["5"], "Deddf Deddfwriaeth (Cymru) 2019", None),
        ("刑法第百九十九条", ["199"], "刑法", None),
        ("第9条の2第1項 民法", ["9-2", "1"], "民法", None),
        ("個人情報保護法第23条", ["23"], "個人情報保護法", None),
        ("29 CFR 825.305(a)", ["825-305", "a"], "", "/us/cfr/t29/p825"),
        ("26 C.F.R. § 1.401(k)-1(d)(3)", ["1-401k-1", "d", "3"], "", "/us/cfr/t26/p1"),
        ("42 U.S.C. § 1983", ["1983"], "", "/us/usc/t42"),
    ],
)
def test_parse_citation(q: str, units: list[str], reference: str, uri: str | None) -> None:
    parsed = parse_citation(q)
    assert parsed is not None
    assert [f"ch:{u.value}" if u.kind == "chapter" else u.value for u in parsed.units] == units
    assert parsed.reference == reference
    assert parsed.uri == uri


@pytest.mark.parametrize(
    "q",
    [
        "401(k) hardship distribution",
        "What medical certification can an employer require?",
        "29 CFR Part 825",
        "data protection 1998",
        "Brottsbalk (1962:700)",
    ],
)
def test_not_a_citation(q: str) -> None:
    assert parse_citation(q) is None


def test_name_index_expressions_match_the_migration() -> None:
    import importlib.util

    from ulis.retrieval import citations
    from ulis.settings import REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "m0007", REPO_ROOT / "migrations" / "versions" / "0007_instrument_name_indexes.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert citations.INSTRUMENT_NAMES.replace("i.", "") == migration.INSTRUMENT_NAMES
    assert citations.VERSION_NAMES.replace("iv.", "") == migration.VERSION_NAMES


def test_citation_aliases_file_is_well_formed() -> None:
    import yaml

    from ulis.catalog import Catalog
    from ulis.retrieval.citations import ALIASES_FILE, _aliases
    from ulis.settings import REPO_ROOT

    jurisdictions = Catalog.load(REPO_ROOT / "config").jurisdictions
    data = yaml.safe_load(ALIASES_FILE.read_text("utf-8"))
    for jurisdiction, entries in data.items():
        assert jurisdiction in jurisdictions
        for entry in entries:
            assert entry["names"] and len({"uri", "number", "title", "ext"} & entry.keys()) == 1, entry
    assert {a.jurisdiction for a in _aliases()["gdpr"]} == {"EU"}
    assert {a.jurisdiction for a in _aliases()["cc"]} == {"ES"}
