"""Recompute the SHA-256 pins in sample_corpus/MANIFEST.yaml after editing corpus files."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sample_corpus" / "MANIFEST.yaml"


def main() -> int:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    changed = 0
    for doc in data["documents"]:
        digest = hashlib.sha256((MANIFEST.parent / doc["path"]).read_bytes()).hexdigest()
        if doc.get("sha256") != digest:
            doc["sha256"] = digest
            changed += 1
    header = MANIFEST.read_text(encoding="utf-8").split("schema_version:")[0]
    MANIFEST.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"updated {changed} hash(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
