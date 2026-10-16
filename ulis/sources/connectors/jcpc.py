"""Fetches Judicial Committee of the Privy Council judgments from the Committee's own website (https://jcpc.uk).

The site has the design and the terms of the UK Supreme Court's (same Crown copyright notice, same robots.txt): the
connectors are those of ``uksc.py`` (judgment pages in HTML, ``/cases/judgments/jcpc-<year>-<number>``) and
``uksc_pdf.py`` (case pages ``/cases/jcpc-<year>-<number>`` and the judgment PDFs they link), with the host and the
case id prefix (``JCPC/2023/0044``) changed. Manifests: ``config/manifests/gb-jcpc-judgments.yaml`` and
``gb-jcpc-judgments-pdf.yaml``, built from the sitemap by ``scripts/build_uksc_pdf_manifest.py --court jcpc``.
"""

from __future__ import annotations

from ulis.sources.connectors.uksc import UkscConnector
from ulis.sources.connectors.uksc_pdf import UkscPdfConnector

__all__ = ["JcpcConnector", "JcpcPdfConnector"]


class JcpcConnector(UkscConnector):
    host = "jcpc.uk"
    prefix = "JCPC"


class JcpcPdfConnector(UkscPdfConnector):
    host = "jcpc.uk"
    prefix = "JCPC"
