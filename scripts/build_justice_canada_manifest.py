"""Build config/manifests/ca-justice-laws-xml.yaml from a checkout of github.com/justicecanada/laws-lois-xml.

Each Act (``eng/acts/<N>.xml`` and ``fra/lois/<N>.xml``) and each regulation (``eng/regulations/<id>.xml`` and
``fra/reglements/<id>.xml``, where the French file name writes ``DORS`` for ``SOR``, ``TR`` for ``SI`` and
``C.R.C.,_ch._`` for ``C.R.C.,_c._``) is pinned in both official languages and must parse; a file that does not is an
error, not a silent omission.

    python scripts/build_justice_canada_manifest.py /path/to/laws-lois-xml A-1 P-21 SOR-2001-227 C.R.C.,_c._870 ...
    python scripts/build_justice_canada_manifest.py /path/to/laws-lois-xml --all    # every Act and regulation

With ``--all`` an instrument is included only if both language files exist and parse. Instruments whose consolidation
holds only a repeal notice (the parser skips them: nothing to ingest) and files that fail to parse are listed under
``excluded`` with the reason, and the header states how many files the repository holds against how many are listed,
so nothing is dropped silently.
"""

from __future__ import annotations

import hashlib
import subprocess  # noqa: S404
import sys
from pathlib import Path

import yaml

from ulis.sources.normalized import ParseSkipped
from ulis.sources.parsers.justice_canada import parse_justice_canada

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "manifests" / "ca-justice-laws-xml.yaml"
FOLDERS = {
    "act": {"eng": "eng/acts", "fra": "fra/lois"},
    "regulation": {"eng": "eng/regulations", "fra": "fra/reglements"},
}
# English regulation file-name prefix -> French one (the repository names each language's file after its own number).
FRENCH_PREFIX = (("SOR-", "DORS-"), ("SI-", "TR-"), ("C.R.C.,_c._", "C.R.C.,_ch._"))


def kind_of(identifier: str) -> str:
    return "regulation" if identifier.startswith(tuple(en for en, _ in FRENCH_PREFIX)) else "act"


def french_file_stem(identifier: str) -> str:
    """``SOR-2001-227`` -> ``DORS-2001-227``; ``C.R.C.,_c._870`` -> ``C.R.C.,_ch._870``; Acts keep their chapter."""
    for en, fr in FRENCH_PREFIX:
        if identifier.startswith(en):
            return fr + identifier[len(en) :]
    return identifier


def stems(repo: Path, folder: str) -> list[str]:
    return sorted(p.stem for p in (repo / folder).glob("*.xml"))


def main(checkout: str, identifiers: list[str]) -> int:
    repo = Path(checkout).resolve()
    all_mode = identifiers == ["--all"]
    in_repo = {kind: stems(repo, FOLDERS[kind]["eng"]) for kind in FOLDERS}
    if all_mode:
        identifiers = in_repo["act"] + in_repo["regulation"]
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,  # noqa: S603, S607
        capture_output=True,
        text=True,
    ).stdout.strip()
    documents = []
    excluded: list[dict[str, str]] = []
    listed = {"act": 0, "regulation": 0}
    skipped = {"act": 0, "regulation": 0}
    for identifier in identifiers:
        kind = kind_of(identifier)
        entries = []
        try:
            for lang, folder in FOLDERS[kind].items():
                stem = identifier if lang == "eng" else french_file_stem(identifier)
                rel = f"{folder}/{stem}.xml"
                path = repo / rel
                if not path.exists():
                    raise FileNotFoundError(f"{rel} is not in the repository")
                content = path.read_bytes()
                doc = parse_justice_canada(content)[0]
                entries.append(
                    {
                        "path": rel,
                        "external_id": f"justice-canada:{lang}:{stem}",
                        "media_type": "application/xml",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "title": doc.title,
                        "point_in_time": doc.version_date.isoformat(),
                    }
                )
        except ParseSkipped as exc:
            if not all_mode:
                raise
            skipped[kind] += 1
            excluded.append({kind: identifier, "reason": f"nothing to ingest: {str(exc)[:160]}"})
            continue
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            if not all_mode:
                raise
            excluded.append({kind: identifier, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        documents.extend(entries)
        listed[kind] += 1
    manifest = {
        "schema_version": 1,
        "source_id": "ca-justice-laws-xml",
        "repository": "https://github.com/justicecanada/laws-lois-xml",
        "commit": commit,
        "sparse": True,
        "documents": documents,
    }
    if excluded:
        manifest["excluded"] = excluded
    failed = len(excluded) - skipped["act"] - skipped["regulation"]
    repo_acts, repo_regs = len(in_repo["act"]), len(in_repo["regulation"])
    header = (
        "# Consolidated Acts and regulations of Canada (English and French), pinned to one commit.\n"
        "# Generated by scripts/build_justice_canada_manifest.py. Open Government Licence - Canada.\n"
        f"# Repository at this commit: {repo_acts} Acts and {repo_regs} regulations (English files). Listed in both\n"
        f"# languages: {listed['act']} Acts and {listed['regulation']} regulations. Not listed: {skipped['act']} Acts\n"
        f"# and {skipped['regulation']} regulations whose consolidation holds only a repeal notice (no text to\n"
        f"# ingest) and {failed} that failed to parse or lack a language; each is named under `excluded` with the\n"
        "# reason.\n"
    )
    OUT.write_text(header + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(
        f"{len(documents)} documents pinned at {commit}: {listed['act']} Acts and {listed['regulation']} regulations "
        f"in both languages; {len(excluded)} excluded ({skipped['act']} Acts and {skipped['regulation']} regulations "
        f"hold only a repeal notice, {failed} failed)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
