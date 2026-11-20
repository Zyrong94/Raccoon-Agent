"""Bounded PDF page rendering for browsers without a sandboxed PDF viewer."""

import io
from pathlib import Path

import pypdfium2 as pdfium

from raccoon.runtime.pdf_lock import PDFIUM_LOCK


def render_pdf_page(path: Path, number: int) -> tuple[bytes, int]:
    # PDFium calls must not run concurrently in this process.
    with PDFIUM_LOCK, pdfium.PdfDocument(path) as document:
        count = len(document)
        if number < 0 or number >= count:
            raise IndexError("PDF 页码不存在")
        page = document[number]
        try:
            width, height = page.get_size()
            if width <= 0 or height <= 0:
                raise ValueError("PDF 页面尺寸无效")
            bitmap = page.render(scale=min(1.5, 1200 / width, 1600 / height))
            try:
                image = bitmap.to_pil()
                try:
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    return output.getvalue(), count
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
