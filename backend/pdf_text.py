"""Bounded PDF text extraction subprocess; no server credentials are inherited."""
import io
import json
import resource
import sys
resource.setrlimit(resource.RLIMIT_AS, (134217728, 134217728))
resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
from pypdf import PdfReader
reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(20971521)))
if reader.is_encrypted or not 1 <= len(reader.pages) <= 20:
    raise ValueError('PDF requires one to 20 unencrypted pages')
pages = [page.extract_text() or '' for page in reader.pages]
if sum(len(p.encode()) for p in pages) > 65536 or not any(p.strip() for p in pages):
    raise ValueError('PDF text exceeds limit or requires OCR')
print(json.dumps(pages, ensure_ascii=False))
