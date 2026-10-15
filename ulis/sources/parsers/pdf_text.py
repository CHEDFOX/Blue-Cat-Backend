"""Text of a PDF's text layer, by page, with ``pdftotext`` (poppler-utils).

ULIS does no OCR: a PDF without a text layer yields (almost) no text and the callers skip it. ``pdftotext`` runs on a
file in a private temporary directory, without a shell, with a timeout and no network.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # noqa: S404 - runs pdftotext only, on a file in a private temporary directory, without a shell
import tempfile
from pathlib import Path

PDFTOTEXT_TIMEOUT = 300

# Presentation forms that typesetters' PDFs put in the text layer; they are expanded to the letters they stand for.
LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
LIGATURE = re.compile("[ﬀ-ﬆ]")

__all__ = ["PdfTextError", "normalise_glyphs", "pdf_pages"]


class PdfTextError(ValueError):
    pass


def normalise_glyphs(text: str) -> str:
    """Join words broken at a soft (discretionary) hyphen and expand ligature glyphs; nothing else is changed."""
    text = text.replace("­\n", "").replace("­", "")
    return LIGATURE.sub(lambda m: LIGATURES[m.group()], text)


def pdf_pages(pdf: bytes, *, layout: bool = False, soft_hyphen: str = "drop") -> list[str]:
    """The text of each PDF page, as ``pdftotext`` extracts it (reading order), glyph forms normalised.

    ``layout=True`` asks for ``pdftotext -layout`` (physical layout kept: numbers printed in a margin column stay on
    the line of their text). ``soft_hyphen="hyphen"`` reads U+00AD as the visible hyphen it is in some fonts
    ("undang-undang" printed with a soft-hyphen glyph) instead of dropping it. The defaults leave the text every
    existing caller gets unchanged."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise PdfTextError("pdftotext (poppler-utils) is not installed; it is needed to read PDF files")
    if not pdf.startswith(b"%PDF-"):
        raise PdfTextError("the document is not a PDF file")
    with tempfile.TemporaryDirectory(prefix="ulis-pdf-") as tmp:
        source = Path(tmp) / "document.pdf"
        source.write_bytes(pdf)
        try:
            done = subprocess.run(  # noqa: S603 - fixed argument list, no shell, own temporary directory
                [exe, "-q", *(["-layout"] if layout else []), "-enc", "UTF-8", str(source), "-"],
                capture_output=True,
                timeout=PDFTOTEXT_TIMEOUT,
                cwd=tmp,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfTextError(f"pdftotext timed out after {PDFTOTEXT_TIMEOUT} s") from exc
    if done.returncode != 0:
        raise PdfTextError(f"pdftotext failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace')[:200]}")
    pages = done.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if soft_hyphen == "hyphen":
        pages = [page.replace("­", "-") for page in pages]
    return [normalise_glyphs(page) for page in pages]
