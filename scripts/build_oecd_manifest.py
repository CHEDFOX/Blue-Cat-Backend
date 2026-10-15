"""Write the manifest of the OECD instruments ULIS takes, from the Compendium's own index.

python scripts/build_oecd_manifest.py                       # config/manifests/int-oecd-legal-instruments.yaml: the curated selection
python scripts/build_oecd_manifest.py --ids 187,668 --out /tmp/slice.yaml    # another selection, outside the repository
python scripts/build_oecd_manifest.py --count               # scale only: how many instruments the Compendium lists, nothing written

The Compendium says it "shall not be reproduced in whole or significant part without express permission from the OECD" and
that individual instruments may be reproduced unaltered (see config/sources/int-oecd-legal-instruments.yaml). So this script
writes a SELECTION of at most ``MAX_INSTRUMENTS`` instruments and refuses to write more; it never lists the whole Compendium.
The selection is made by hand (below): the instruments that matter most to a lawyer researching anti-bribery, tax
co-operation, privacy and data, competition, consumer protection, regulatory and corporate-governance standards, plus one
abrogated instrument so that the history of an instrument is represented.

One request for the robots.txt and one for the index (``/api/instruments/keys?lang=en``, the list the Compendium's own web
application loads; robots.txt disallows nothing). Entries are written sorted by key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ulis.sources.connectors.constitution_pages_b import RobotsPolicy  # noqa: E402
from ulis.sources.connectors.oecd_legal_instruments import HOST, KEY, MAX_INSTRUMENTS  # noqa: E402
from ulis.sources.connectors.polite_http import PoliteHttp  # noqa: E402

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
SOURCE_ID = "int-oecd-legal-instruments"

# Numeric ids of the Compendium (its keys in the comment). 16 instruments, 3% of the 504 listed on 2026-10-15.
SELECTION = (
    187,  # 0162 Recommendation on restrictive business practices relating to trademark licences (abrogated 2023)
    114,  # 0188 Privacy Guidelines (Recommendation)
    96,  # 0235 Convention on Mutual Administrative Assistance in Tax Matters
    317,  # 0382 Protocol amending that Convention
    205,  # 0293 Anti-Bribery Convention
    258,  # 0378 Recommendation for further combating bribery of foreign public officials
    261,  # 0383 Declaration on Propriety, Integrity and Transparency in the Conduct of International Business and Finance
    233,  # 0307 Decision on the Guidelines for Multinational Enterprises on Responsible Business Conduct
    304,  # 0404 Declaration on Automatic Exchange of Information in Tax Matters
    322,  # 0413 Principles of Corporate Governance (Recommendation)
    273,  # 0390 Recommendation on Regulatory Policy and Governance
    652,  # 0452 Recommendation on Hard Core Cartels
    336,  # 0422 Recommendation on Consumer Protection in E-commerce
    272,  # 0389 Recommendation on Children in the Digital Environment
    668,  # 0463 Recommendation on Enhancing Access to and Sharing of Data
    648,  # 0449 Recommendation on Artificial Intelligence
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", help="comma-separated numeric ids (default: the curated selection in this script)")
    parser.add_argument("--count", action="store_true", help="only report how many instruments the index lists")
    parser.add_argument("--out", type=Path, default=ROOT / "config" / "manifests" / f"{SOURCE_ID}.yaml")
    parser.add_argument("--cache", type=Path, default=ROOT / "var" / "sources")
    args = parser.parse_args()
    wanted = [int(x) for x in args.ids.split(",")] if args.ids else list(SELECTION)
    if len(wanted) > MAX_INSTRUMENTS and not args.count:
        sys.exit(f"{len(wanted)} instruments asked; at most {MAX_INSTRUMENTS} (the Compendium may not be reproduced in significant part)")
    client = PoliteHttp(args.cache, USER_AGENT, allowed_hosts={HOST})
    robots = client.get(f"https://{HOST}/robots.txt")
    if robots is not None and RobotsPolicy(robots[0].decode("utf-8", "replace")).closes_site:
        sys.exit("robots.txt disallows the site; nothing fetched")
    got = client.get(f"https://{HOST}/api/instruments/keys?lang=en")
    if got is None:
        sys.exit("the index was not found")
    index = json.loads(got[0])
    valid = [e for e in index if KEY.fullmatch(e["key"])]
    if args.count:
        print(f"the Compendium's index lists {len(valid)} instruments (selection: {len(SELECTION)})")
        return 0
    by_id = {e["id"]: e for e in valid}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        sys.exit(f"ids not in the Compendium's index: {missing}")
    rows = sorted((by_id[i] for i in dict.fromkeys(wanted)), key=lambda e: e["key"])
    header = (
        f"# OECD Compendium of Legal Instruments (legalinstruments.oecd.org): a SELECTION of {len(rows)} individual instruments\n"
        f"# out of the {len(valid)} the Compendium lists. The Compendium \"shall not be reproduced in whole or significant part without\n"
        "# express permission from the OECD\", individual instruments may be reproduced unaltered; the connector refuses more than 25.\n"
        "# Built by scripts/build_oecd_manifest.py from the Compendium's own index (/api/instruments/keys?lang=en, one request);\n"
        "# `title` is the index's English title and is documentation only. Each instrument is read at /api/instruments/<id> and its\n"
        "# English and French HTML texts: three requests per instrument, 5 s apart.\n"
    )
    body = {
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "host": HOST,
        "user_agent": USER_AGENT,
        "crawl_delay_seconds": 5,
        "languages": ["en", "fr"],
        "instruments": [{"id": e["id"], "key": e["key"], "title": " ".join(e["title"].split())} for e in rows],
    }
    args.out.write_text(header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")
    print(f"wrote {len(rows)} instruments to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
