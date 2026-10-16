"""Paragraphs of a court judgment from the text layer of its PDF (shared by the UK court parsers).

``units_from_pdf_pages(pages)`` takes the text of each page as ``pdftotext`` gives it (``pdf_text.pdf_pages``) and
returns the
judgment as units ``(kind, number, text)``:

* page footers ("Page 17") are removed and the lines of a paragraph are joined (a hyphen at the end of a line before a
  lower-case letter is joined without a space);
* the court's numbered paragraphs are ``considerations`` units with their number. Two styles are recognised and the
  one that
  yields more numbered paragraphs is used: ``dot`` ("1." alone on a line, or "1. Text") of the Supreme Court and the
  Privy
  Council, and ``bracket`` ("[1]" alone on a line, or "[1] Text") of the Northern Ireland and Scottish courts. A
  number counts
  only if it continues the sequence (1, 2, 3 ...) or restarts at 1 and is followed by 2 (another opinion), so a quoted
  paragraph number or a number in a table is not taken for one;
* short lines that follow a finished sentence and precede a numbered paragraph are headings ("Conclusion"), and the
  opinion
  lines ("LORD REED: (with whom ...)") are headings; everything before paragraph 1 (cover page, counsel) is
  unnumbered ``text``.

The PDF is not tagged: footnotes at the bottom of a page stay in the text where the PDF puts them (the end of the
paragraph
that ends the page), and a table or a quotation in another layout may be joined into its paragraph. A PDF without a text
layer (a scan) is skipped by the caller (ULIS does no OCR).
"""

from __future__ import annotations

import re

FOOTER = re.compile(r"^(?:Page )?\d{1,3}$|^Page \d+$")
STYLES = {
    "dot": (re.compile(r"^(\d{1,4})\.$"), re.compile(r"^(\d{1,4})\.\s+(\S.*)$")),
    "bracket": (re.compile(r"^\[(\d{1,4})\]$"), re.compile(r"^\[(\d{1,4})\]\s+(\S.*)$")),
}
OPINION = re.compile(
    r"^(?:(?:LORD|LADY|BARONESS|SIR|JUSTICE|MR JUSTICE|MRS JUSTICE)\b[A-Z ,.'’-]*(?:\b|:|\()"
    r"|(?:Mc|Mac)?[A-Z]{2,}(?:[ '’-](?:Mc|Mac)?[A-Z]+)* (?:LJ|LCJ|J|JA)\b)"  # "COLTON LJ (delivering the judgment ...)"
)
SENTENCE_END = (".", "?", "!", "”", '"', "’", ")", "]")
SPACE = re.compile(r"[ \t\r\n\xa0    ]+")

Unit = tuple[str, str | None, str]


def _join(lines: list[str]) -> str:
    out = ""
    for line in (ln for ln in lines if ln):
        if out.endswith("-") and line[:1].islower():
            out += line
        else:
            out = f"{out} {line}" if out else line
    return out


def _is_heading_line(line: str) -> bool:
    return (
        0 < len(line) <= 70
        and (line[0].isupper() or line[0].isdigit())
        and not line.endswith((",", ";", ":", ".", "?", "!", "”", '"', "’", ")", "]"))
    )


def _split_trailing_headings(buf: list[str]) -> tuple[list[str], list[list[str]]]:
    """(lines of the paragraph, heading blocks that follow it): short lines after a finished sentence, or an
    opinion line."""
    lines = list(buf)
    while lines and not lines[-1]:
        lines.pop()
    headings: list[list[str]] = []
    for start in range(max(0, len(lines) - 4), len(lines)):
        if OPINION.match(lines[start]) and (
            start == 0 or not lines[start - 1] or lines[start - 1].endswith(SENTENCE_END)
        ):
            headings.insert(0, [ln for ln in lines[start:] if ln])
            lines = lines[:start]
            break
    while lines:
        tail: list[str] = []
        j = len(lines)
        while j > 0 and lines[j - 1] and _is_heading_line(lines[j - 1]) and len(tail) < 2:
            tail.insert(0, lines[j - 1])
            j -= 1
        before = next((ln for ln in reversed(lines[:j]) if ln), "")
        if tail and (j == 0 or not lines[j - 1] or before.endswith(SENTENCE_END)) and j > 0:
            headings.insert(0, tail)
            lines = lines[:j]
            while lines and not lines[-1]:
                lines.pop()
            continue
        break
    return lines, headings


def page_lines(pages: list[str]) -> list[str]:
    """The lines of all pages, whitespace collapsed, page footers removed (blank lines kept)."""
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = SPACE.sub(" ", raw).strip()
            if not FOOTER.match(line):
                lines.append(line)
    return lines


def _units(lines: list[str], alone: re.Pattern[str], inline: re.Pattern[str]) -> list[Unit]:
    def number_at(i: int) -> tuple[int, str | None] | None:
        a, b = alone.match(lines[i]), inline.match(lines[i])
        if a:
            return int(a.group(1)), None
        if b:
            return int(b.group(1)), b.group(2)
        return None

    def restarts(i: int) -> bool:
        for j in range(i + 1, len(lines)):
            found = number_at(j)
            if found:
                return found[0] == 2
        return False

    units: list[Unit] = []
    buf: list[str] = []
    current: int | None = None  # the number of the paragraph being collected, None before paragraph 1
    expected = 1

    def flush(last: bool = False) -> None:
        nonlocal buf
        body, headings = (buf, []) if last else _split_trailing_headings(buf)
        if current is None:
            # Front matter: blocks separated by blank lines; an opinion line is a heading.
            block: list[str] = []
            for ln in [*body, ""]:
                if ln:
                    block.append(ln)
                    continue
                if block:
                    text = _join(block)
                    if text:
                        units.append(("heading" if OPINION.match(block[0]) else "text", None, text))
                block = []
        else:
            text = _join(body)
            if text:
                units.append(("considerations", str(current), text))
        for h in headings:
            units.append(("heading", None, _join(h)))
        buf = []

    for i, line in enumerate(lines):
        found = number_at(i)
        if found and (found[0] == expected or (found[0] == 1 and expected > 1 and restarts(i))):
            flush()
            current = found[0]
            expected = found[0] + 1
            buf = [found[1]] if found[1] else []
            continue
        buf.append(line)
    flush(last=True)
    return units


def units_from_lines(lines: list[str]) -> list[Unit]:
    """The units of the numbering style that finds the most numbered paragraphs (``dot`` on a tie)."""
    best: list[Unit] = []
    best_count = -1
    for alone, inline in STYLES.values():
        units = _units(lines, alone, inline)
        count = sum(1 for u in units if u[0] == "considerations")
        if count > best_count:
            best, best_count = units, count
    return best
