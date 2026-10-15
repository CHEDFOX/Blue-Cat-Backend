"""Parser of the ``us-govinfo-bulk-cfr-fr`` source: a CFR annual-edition volume or a Federal Register issue.

The connector yields both kinds of file; the root element says which one it is (``CFRDOC`` or ``FEDREG``).
"""

from __future__ import annotations

from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers import cfr_annual, federal_register

PARSER_ID = "govinfo_cfr_fr"
PARSER_VERSION = f"cfr-{cfr_annual.PARSER_VERSION}+fr-{federal_register.PARSER_VERSION}"


def parse_govinfo_cfr_fr(content: bytes) -> list[ParsedDocument]:
    head = content[:4096]
    if b"<CFRDOC" in head:
        return cfr_annual.parse_cfr_volume(content)
    if b"<FEDREG" in head:
        return federal_register.parse_issue(content)
    raise ValueError("neither a CFR annual-edition volume (CFRDOC) nor a Federal Register issue (FEDREG)")
