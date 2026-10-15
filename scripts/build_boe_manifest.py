"""Write config/manifests/es-boe-consolidada.yaml from the BOE open-data API's list endpoint.

    python -m scripts.build_boe_manifest            # every State (estatal) consolidated norm in the API list

The list endpoint (https://www.boe.es/datosabiertos/api/legislacion-consolidada, documented in
https://www.boe.es/datosabiertos/documentos/APIconsolidada.pdf, section 2.1) returns the norms ordered by the date
of their last update; ``limit=-1`` asks for all of them, but the service stops at 10,000, so the script asks again
with ``to=<date of the oldest update received>`` until a page comes back short. Every response is cached under
``var/sources/www.boe.es/`` (3 requests the first time, 0 afterwards), after robots.txt has been read.

Scope: the norms whose ``ambito`` is "Estatal" (code 1), every rango. The list also holds the norms of the autonomous
communities that the AEBOE consolidates (``ambito`` "Autonómico", about 3,600); ULIS has no profile for the
communities, so they are left out and counted in the manifest header. A norm that robots.txt names for the API path is
left out as well (none today).
"""

# ruff: noqa: E501 - the generated manifest header and the pilot entries are written as long lines

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from typing import Any

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.boe_consolidada import API, HOST, robots_rules_by_id
from ulis.sources.connectors.polite_http import PoliteHttp

OUT = REPO_ROOT / "config" / "manifests" / "es-boe-consolidada.yaml"
CACHE = REPO_ROOT / "var" / "sources"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
PAGE_CAP = 10_000
# The norms that were in the pilot manifest come first, then the rest by rank and date.
FIRST = [
    "BOE-A-1978-31229",
    "BOE-A-1889-4763",
    "BOE-A-1995-25444",
    "BOE-A-2018-16673",
    "BOE-A-2015-10565",
    "BOE-A-2015-11430",
]
RANK = [
    "Constitución",
    "Ley Orgánica",
    "Ley",
    "Real Decreto Legislativo",
    "Real Decreto-ley",
    "Decreto-ley",
    "Real Decreto",
    "Decreto",
    "Reglamento",
    "Orden",
    "Resolución",
    "Instrucción",
    "Circular",
    "Acuerdo",
    "Acuerdo Internacional",
]
REQUEST_SECONDS = 5.0


def fetch_list(http: PoliteHttp) -> list[dict[str, Any]]:
    """All norms of the list endpoint, newest update first, paging by ``to`` past the service's cap."""
    norms: dict[str, dict[str, Any]] = {}
    to: str | None = None
    for page in range(1, 20):
        query = "limit=-1" + (f"&to={to}" if to else "")
        got = http.get(
            f"{API}?{query}",
            cache_key=f"legislacion-consolidada/index-{page}.json",
            headers={"Accept": "application/json"},
        )
        if got is None:
            raise SystemExit(f"list endpoint not found: {query}")
        body = json.loads(got[0])
        if str(body.get("status", {}).get("code")) != "200":
            raise SystemExit(f"list endpoint answered {body.get('status')}")
        rows = body["data"]
        new = [r for r in rows if r["identificador"] not in norms]
        for row in new:
            norms[row["identificador"]] = row
        print(f"page {page} ({query}): {len(rows)} norms, {len(new)} new")
        if len(rows) < PAGE_CAP:
            return list(norms.values())
        if not new:
            raise SystemExit("a full page added nothing new: more than 10,000 norms share one update day")
        to = rows[-1]["fecha_actualizacion"][:8]
    raise SystemExit("too many pages")


def main() -> int:
    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=REQUEST_SECONDS)
    robots = http.get(f"https://{HOST}/robots.txt")
    by_id, generic = robots_rules_by_id(robots[0].decode("utf-8", "replace")) if robots else ({}, [])
    rows = fetch_list(http)
    ambitos = Counter(r["ambito"]["texto"] for r in rows)
    state = [r for r in rows if r["ambito"]["codigo"] == "1"]
    blocked = []
    for r in list(state):
        path = f"/datosabiertos/api/legislacion-consolidada/id/{r['identificador']}"
        if any(rule.match(path) for rule in (*by_id.get(r["identificador"], ()), *generic)):
            blocked.append(r["identificador"])
            state.remove(r)
    if any(not re.fullmatch(r"BOE-[A-Z]-\d{4}-\d{1,6}", r["identificador"]) for r in state):
        raise SystemExit("an identifier does not match BOE-<letter>-<year>-<number>")

    def key(r: dict[str, Any]) -> tuple[int, int, str, str]:
        first = FIRST.index(r["identificador"]) if r["identificador"] in FIRST else len(FIRST)
        rank = RANK.index(r["rango"]["texto"]) if r["rango"]["texto"] in RANK else len(RANK)
        return first, rank, r.get("fecha_disposicion") or "", r["identificador"]

    state.sort(key=key)
    by_rango = Counter(r["rango"]["texto"] for r in state)
    exhausted = sum(1 for r in state if r.get("vigencia_agotada") == "S")
    requests = len(state) + 1
    regional = ambitos.get("Autonómico", 0)
    header = "\n".join(
        [
            "# Consolidated Spanish STATE legislation from the BOE open-data API: every norm of the API's list endpoint",
            '# whose ambito is "Estatal", any rango, with the full version history of each (one request per norm returns',
            "# every version of every block). Reuse: AEBOE reuse conditions",
            "# (https://www.boe.es/informacion/aviso_legal/index.php#reutilizacion).",
            f"# Generated by `python -m scripts.build_boe_manifest` on {date.today().isoformat()}; the list changes daily",
            "# (new norms; norms that become fully repealed stay listed): regenerate to refresh. Order: the six pilot",
            "# norms, then by rank and date.",
            "#",
            f"# Scale: {len(state):,} norms ({exhausted:,} with vigencia_agotada = S: all their provisions repealed or",
            "# spent, kept because their consolidated history is still published); "
            f"{requests:,} requests at 5 s = {requests * REQUEST_SECONDS / 3600:.1f} h.",
            f"# Rango: {', '.join(f'{k} {v:,}' for k, v in by_rango.most_common())}.",
            f"# Left out: the {regional:,} norms of the autonomous communities that the list also holds (ambito",
            f"# Autonómico; ULIS has no profile for the communities) and {len(blocked)} norms robots.txt names for the",
            "# API path.",
            "# Size, estimated from a uniform random sample of 80 of these norms (2026-10-15): about 130 provision",
            "# versions per norm (median 33), so ~1.15 million provision versions and ~4 GB of PostgreSQL (3.3 KB per",
            "# version with its search index); raw API documents ~1 MB on average (median 78 KB, largest 63 MB), ~8 GB",
            "# in the object store.",
        ]
    )
    lines = [
        header.rstrip("\n"),
        "schema_version: 1",
        "source_id: es-boe-consolidada",
        f"api_base: {API}",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 5",
        "documents:",
    ]
    for r in state:
        lines.append(
            f"  - {{id: {r['identificador']}, title: {json.dumps(' '.join(r['titulo'].split()), ensure_ascii=False)}}}"
        )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(state)} State norms written to {OUT.relative_to(REPO_ROOT)} ({regional} regional left out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
