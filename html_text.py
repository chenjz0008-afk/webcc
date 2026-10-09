"""Bounded extraction subprocess; downloads happen in the guarded parent."""
import sys
import trafilatura
raw=sys.stdin.buffer.read(1048577)
if len(raw)>1048576:raise SystemExit(1)
text=trafilatura.extract(raw,include_tables=True,include_comments=False)
if not text or len(text.encode())>262144:raise SystemExit(1)
sys.stdout.write(text)
