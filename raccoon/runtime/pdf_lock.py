"""PDFium is not thread-safe, even across separate documents."""

import threading

PDFIUM_LOCK = threading.Lock()
