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
from typing import Any

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


PAGE_SIZE = re.compile(r"^Page\s+(\d+) size:\s+([0-9.]+) x ([0-9.]+) pts", re.MULTILINE)


def pdf_pages(pdf: bytes, *, layout: bool = False, soft_hyphen: str = "drop", columns: int = 1) -> list[str]:
    """The text of each PDF page, as ``pdftotext`` extracts it (reading order), glyph forms normalised.

    ``layout=True`` asks for ``pdftotext -layout`` (physical layout kept: numbers printed in a margin column stay on
    the line of their text). ``soft_hyphen="hyphen"`` reads U+00AD as the visible hyphen it is in some fonts
    ("undang-undang" printed with a soft-hyphen glyph) instead of dropping it. The defaults leave the text every
    existing caller gets unchanged. ``columns=2`` (or more) reads a page printed in that many equal text columns (an
    official gazette): each page is cut into vertical strips and the strips are read left to right, so the columns do
    not interleave; the full-width lines of a title page are cut across the strips, which is only furniture."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise PdfTextError("pdftotext (poppler-utils) is not installed; it is needed to read PDF files")
    if not pdf.startswith(b"%PDF-"):
        raise PdfTextError("the document is not a PDF file")
    with tempfile.TemporaryDirectory(prefix="ulis-pdf-") as tmp:
        source = Path(tmp) / "document.pdf"
        source.write_bytes(pdf)

        def run(args: list[str], what: str) -> subprocess.CompletedProcess[bytes]:
            try:
                done = subprocess.run(  # noqa: S603 - fixed argument list, no shell, own temporary directory
                    [exe, "-q", *args],
                    capture_output=True,
                    timeout=PDFTOTEXT_TIMEOUT,
                    cwd=tmp,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise PdfTextError(f"pdftotext timed out after {PDFTOTEXT_TIMEOUT} s") from exc
            if done.returncode != 0:
                raise PdfTextError(
                    f"pdftotext failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace')[:200]}"
                )
            return done

        base = [*(["-layout"] if layout else []), "-enc", "UTF-8"]
        if columns <= 1:
            pages = run([*base, str(source), "-"], "text").stdout.decode("utf-8", "replace").split("\f")
        else:
            pages = _column_pages(source, base, columns, run)
    if pages and not pages[-1].strip():
        pages.pop()
    if soft_hyphen == "hyphen":
        pages = [page.replace("­", "-") for page in pages]
    return [normalise_glyphs(page) for page in pages]


def _column_pages(source: Path, base: list[str], columns: int, run: Any) -> list[str]:
    """Each page read as ``columns`` vertical strips, left to right (page sizes from ``pdfinfo``)."""
    exe = shutil.which("pdfinfo")
    if exe is None:
        raise PdfTextError("pdfinfo (poppler-utils) is not installed; it is needed to read columns")
    info = subprocess.run(  # noqa: S603 - fixed argument list, no shell, own temporary directory
        [exe, "-f", "1", "-l", "100000", str(source)], capture_output=True, timeout=PDFTOTEXT_TIMEOUT, check=False
    )
    sizes = [(int(n), float(w), float(h)) for n, w, h in PAGE_SIZE.findall(info.stdout.decode("utf-8", "replace"))]
    if not sizes:
        raise PdfTextError("pdfinfo reported no page sizes")
    pages: list[str] = []
    for number, width, height in sizes:
        strips = []
        for k in range(columns):
            args = [
                *base,
                *("-f", str(number), "-l", str(number)),
                *("-x", str(int(width * k / columns)), "-y", "0"),
                *("-W", str(int(width / columns) + 1), "-H", str(int(height) + 1)),
                str(source),
                "-",
            ]
            strips.append(run(args, "column").stdout.decode("utf-8", "replace").replace("\f", ""))
        pages.append("\n".join(strips))
    return pages
