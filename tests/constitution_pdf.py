"""A minimal PDF writer for tests of the constitution parser: pages of text lines in the built-in Helvetica font.

The lines come from real constitutions (extracted from the publishers' PDFs); only the PDF container is made here, so
the fixtures stay a few kilobytes. ``pdftotext`` (as the parser calls it) reads the lines back in order.
"""

from __future__ import annotations


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], *, size: int = 9, leading: int = 11) -> bytes:
    """A PDF whose page ``i`` shows ``pages[i]`` one line per row from the top of an A4 page, left margin 40 pt.

    Characters outside Windows-1252 are not supported (the built-in font has no glyphs for them)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # patched below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids: list[int] = []
    for lines in pages:
        content = ["BT", f"/F1 {size} Tf", f"{leading} TL", "40 800 Td"]
        for line in lines:
            content.append(f"({_escape(line)}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("cp1252")
        data = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        resources = f"<< /Font << /F1 {font} 0 R >> >>"
        page = add(
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 595 842] /Resources {resources} "
            f"/Contents {data} 0 R >>".encode()
        )
        kids.append(page)
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objects[pages_obj - 1] = (
        "<< /Type /Pages /Kids [" + " ".join(f"{k} 0 R" for k in kids) + f"] /Count {len(kids)} >>"
    ).encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
