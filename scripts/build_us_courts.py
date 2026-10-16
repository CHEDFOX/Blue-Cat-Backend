"""Generate the US court list that the CourtListener source uses, from CourtListener's own court table.

Usage (from the repository root, after ``scripts/courtlistener_scope_report.py`` or any run has downloaded the file)::

    python scripts/build_us_courts.py --courts var/sources/.../courts-2026-09-30.csv.bz2 [--groups F,FD,...]

It rewrites two things, deterministically:

* ``ulis/sources/parsers/courtlistener_courts.py`` - CourtListener court id -> ULIS court id, name and citation form;
* the block between the ``BEGIN/END GENERATED COURTS`` markers of ``config/jurisdictions/us.yaml`` - the courts of the
  US profile (rank and ``appeals_to``).

Court ids: the Supreme Court, the thirteen courts of appeals and the four state apex courts that the repository already
models keep their ULIS ids (``US-SC``, ``US-CA9``, ``US-WA-SC``...); every other court is ``US-CL-<CourtListener id>``.

``appeals_to`` is set only where the law fixes it and the CourtListener court table lets the script derive it:
district courts to the circuit of their State (28 U.S.C. 41), bankruptcy courts to the district court of the same
district,
bankruptcy appellate panels to their circuit, a few specialised federal courts, state courts of last resort to the
Supreme
Court (federal questions), and state appellate courts to the highest court whose id they start with when their name says
they are appellate. Everything else is left empty rather than guessed.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUPS = ("F", "FD", "FB", "FBP", "FS", "S", "SA", "MA", "TS", "TA")
EXISTING = {
    "scotus": "US-SC",
    **{f"ca{n}": f"US-CA{n}" for n in range(1, 12)},
    "cadc": "US-CADC",
    "cafc": "US-CAFC",
    "wash": "US-WA-SC",
    "tex": "US-TX-SC",
    "or": "US-OR-SC",
    "utah": "US-UT-SC",
}
# 28 U.S.C. 41: the circuit of each State (and territory) for its district courts.
CIRCUIT_OF_STATE = {
    **dict.fromkeys(("me", "ma", "nh", "ri", "pr"), "ca1"),
    **dict.fromkeys(("ct", "ny", "vt"), "ca2"),
    **dict.fromkeys(("de", "nj", "pa", "vi"), "ca3"),
    **dict.fromkeys(("md", "nc", "sc", "va", "wv"), "ca4"),
    **dict.fromkeys(("la", "ms", "tx", "cz"), "ca5"),
    **dict.fromkeys(("ky", "mi", "oh", "tn"), "ca6"),
    **dict.fromkeys(("il", "in", "wi"), "ca7"),
    **dict.fromkeys(("ar", "ia", "mn", "mo", "ne", "nd", "sd"), "ca8"),
    **dict.fromkeys(("ak", "az", "ca", "hi", "id", "mt", "nv", "or", "wa", "gu", "nm"), "ca9"),
    **dict.fromkeys(("co", "ks", "ok", "ut", "wy"), "ca10"),
    **dict.fromkeys(("al", "fl", "ga"), "ca11"),
    "dc": "cadc",
}
# nmid is the District Court for the Northern Mariana Islands (ninth circuit); "nm" alone is New Mexico (tenth).
CIRCUIT_OVERRIDE = {"nmid": "ca9", "nm": "ca10", "nmd": "ca10"}
SPECIAL_APPEALS = {
    "uscfc": ["cafc"],
    "cit": ["cafc"],
    "cavc": ["cafc"],
    "mspb": ["cafc"],
    "ttab": ["cafc"],
    "ptab": ["cafc"],
    "bpai": ["cafc"],
    "asbca": ["cafc"],
    "bva": ["cavc"],
    "fisc": ["fiscr"],
    "fiscr": ["scotus"],
    "cc": ["scotus"],
    "ccpa": ["scotus"],
    "armfor": ["scotus"],
    "cma": ["scotus"],
}
APPELLATE_NAME = re.compile(r"appeal|appellate|error", re.I)
RANK = {"F": 2, "FD": 3, "FB": 4, "FBP": 3, "FS": 3, "S": 2, "SA": 3, "MA": 3, "TS": 2, "TA": 3}


def read_courts(path: Path) -> list[dict[str, str]]:
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:  # type: ignore[operator]
        return list(csv.DictReader(handle))


def ulis_id(clid: str) -> str:
    return EXISTING.get(clid) or f"US-CL-{clid.upper()}"


def appeals(court: dict[str, str], by_id: dict[str, dict[str, str]], supreme: list[str]) -> list[str]:
    clid, group = court["id"], court["jurisdiction"]
    if clid in EXISTING:
        return []  # defined by hand in the profile
    if clid in SPECIAL_APPEALS:
        return [t for t in SPECIAL_APPEALS[clid] if t in by_id]
    if group == "F":
        return []  # historical circuit courts and the like: the hierarchy is not derived
    if group == "FD":
        state = CIRCUIT_OVERRIDE.get(clid) or CIRCUIT_OF_STATE.get(clid[:2])
        return [state] if state in by_id else []
    if group == "FB":
        district = clid[:-1] + "d"
        return [district] if district in by_id and by_id[district]["jurisdiction"] == "FD" else []
    if group == "FBP":
        m = re.fullmatch(r"bap(\d+)", clid)
        if m:
            return [f"ca{m.group(1)}"] if f"ca{m.group(1)}" in by_id else []
        state = CIRCUIT_OF_STATE.get(clid[3:5])
        return [state] if state in by_id else []
    if group == "S":
        return ["scotus"]
    if group == "SA" and APPELLATE_NAME.search(court["full_name"]):
        if clid.startswith("txctapp"):
            return [c for c in ("tex", "texcrimapp") if c in by_id]
        match = next((s for s in supreme if clid.startswith(s) and s != clid), None)
        return [match] if match else []
    if group == "MA":
        return ["armfor"] if clid not in ("armfor",) and clid != "cma" and "armfor" in by_id else []
    return []


def build(courts: list[dict[str, str]], groups: tuple[str, ...]) -> list[dict[str, object]]:
    by_id = {c["id"]: c for c in courts}
    supreme = sorted((c["id"] for c in courts if c["jurisdiction"] == "S"), key=len, reverse=True)
    out = []
    for court in sorted(
        courts, key=lambda c: (groups.index(c["jurisdiction"]) if c["jurisdiction"] in groups else 99, c["id"])
    ):
        if court["jurisdiction"] not in groups or court["id"] == "usdistct":
            continue
        name = " ".join((court["full_name"] or court["short_name"] or court["id"]).split())
        out.append(
            {
                "cl": court["id"],
                "id": ulis_id(court["id"]),
                "name": name,
                "group": court["jurisdiction"],
                "citation": " ".join(court["citation_string"].split()),
                "rank": RANK[court["jurisdiction"]],
                "appeals_to": [ulis_id(t) for t in appeals(court, by_id, supreme)],
                "existing": court["id"] in EXISTING,
            }
        )
    modelled = {str(r["id"]) for r in out}
    for r in out:
        r["appeals_to"] = [t for t in r["appeals_to"] if t in modelled]  # type: ignore[union-attr]
    return out


def write_module(rows: list[dict[str, object]], path: Path) -> None:
    lines = [
        '"""CourtListener courts that ULIS models: generated by scripts/build_us_courts.py, do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        "",
        "@dataclass(frozen=True)",
        "class CourtInfo:",
        "    ulis_id: str",
        "    name: str",
        "    group: str  # CourtListener jurisdiction code: F appellate, FD district, FB bankruptcy, S/SA state ...",
        "    citation: str  # CourtListener's citation_string, the court's form in a Bluebook parenthetical",
        "",
        "",
        "COURTS: dict[str, CourtInfo] = {",
    ]
    for r in rows:
        lines.append(
            f"    {json.dumps(r['cl'])}: CourtInfo({json.dumps(r['id'])}, {json.dumps(r['name'], ensure_ascii=False)}, "
            f"{json.dumps(r['group'])}, {json.dumps(r['citation'], ensure_ascii=False)}),"
        )
    lines += ["}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def yaml_block(rows: list[dict[str, object]]) -> str:
    lines = [
        "  # BEGIN GENERATED COURTS (scripts/build_us_courts.py from CourtListener's court table; do not edit by hand)"
    ]
    for r in rows:
        if r["existing"]:
            continue
        lines.append(f"  - id: {r['id']}")
        lines.append(f"    name: {json.dumps(r['name'], ensure_ascii=False)}")
        lines.append(f"    rank: {r['rank']}")
        if r["appeals_to"]:
            lines.append(f"    appeals_to: [{', '.join(r['appeals_to'])}]")  # type: ignore[arg-type]
    lines.append("  # END GENERATED COURTS")
    return "\n".join(lines) + "\n"


def write_profile(rows: list[dict[str, object]], path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    block = yaml_block(rows)
    pattern = re.compile(r"  # BEGIN GENERATED COURTS.*?  # END GENERATED COURTS\n", re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _m: block, text)
    else:
        marker = "precedent:\n"
        text = text.replace(marker, block + marker, 1)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courts", type=Path, required=True)
    parser.add_argument("--groups", default=",".join(DEFAULT_GROUPS))
    args = parser.parse_args(argv)
    groups = tuple(g for g in args.groups.split(",") if g)
    rows = build(read_courts(args.courts), groups)
    write_module(rows, ROOT / "ulis/sources/parsers/courtlistener_courts.py")
    write_profile(rows, ROOT / "config/jurisdictions/us.yaml")
    print(f"{len(rows)} courts ({sum(1 for r in rows if not r['existing'])} new in the profile)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
