"""Fetch verifiable public source text through an explicit proxy, with pinned DNS."""
import ipaddress
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from web_files import FileProblem

SLOTS = threading.BoundedSemaphore(2)
PARSE_SLOT = threading.BoundedSemaphore(1)


def target(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None,443) or parsed.username or parsed.password or len(url)>2048:
            raise ValueError()
        addresses = {a[4][0] for a in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError()
        address = sorted(addresses)[0]
        return httpx.URL(url).copy_with(host=address), parsed.hostname
    except (ValueError,OSError):
        raise FileProblem(400,'Source URL must resolve only to public HTTPS addresses') from None


def fetch(url):
    proxy=os.environ.get('MANAGER_TOOLS_PROXY') or os.environ.get('MANAGER_E2B_PROXY','')
    p=urlsplit(proxy)
    if p.scheme not in {'http','https','socks5','socks5h'} or not p.hostname or not p.port:
        raise FileProblem(503,'Source verification requires an explicit outbound proxy')
    if not SLOTS.acquire(blocking=False):
        raise FileProblem(429,'Source verification is busy')
    try:
        deadline = time.monotonic() + 12
        with httpx.Client(proxy=proxy,trust_env=False,timeout=8,follow_redirects=False) as client:
            for _ in range(3):
                pinned,host=target(url)
                with client.stream('GET',pinned,headers={'Host':host,'User-Agent':'WebCC-Sources/1','Accept-Encoding':'identity'},extensions={'sni_hostname':host}) as response:
                    if response.is_redirect:
                        url=str(httpx.URL(url).join(response.headers.get('location','')))
                        continue
                    response.raise_for_status()
                    parts=[];size=0
                    for part in response.iter_bytes():
                        if time.monotonic() >= deadline: raise FileProblem(504, 'Source verification deadline exceeded')
                        size+=len(part)
                        if size>1048576:raise FileProblem(413,'Source page exceeds 1 MiB')
                        parts.append(part)
                    if not PARSE_SLOT.acquire(timeout=2):raise FileProblem(429, 'Source extraction is busy')
                    try:
                        result=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('html_text.py'))],input=b''.join(parts),capture_output=True,timeout=6,cwd='/tmp',env={})
                    finally:
                        PARSE_SLOT.release()
                    if result.returncode or not result.stdout or len(result.stdout)>262144:
                        raise FileProblem(502,'Source page could not be extracted within limits')
                    return {'url':url,'text':result.stdout.decode()}
            raise FileProblem(502,'Source redirect limit exceeded')
    except (httpx.HTTPError,subprocess.TimeoutExpired):
        raise FileProblem(502,'Source verification failed') from None
    finally:
        SLOTS.release()


def text(manager,owner,url):
    return manager.processing_cache.memo(owner,'source-text',url,lambda:fetch(url),ttl=60)


def quote(original,context):
    import re
    words=set(re.findall(r'\w{3,}',context.lower()))
    paragraphs=[p.strip() for p in original.splitlines() if p.strip()]
    if not paragraphs:return ''
    candidate=max(paragraphs,key=lambda p:len(words & set(re.findall(r'\w{3,}',p.lower()))))
    result=candidate[:256]
    if result not in original:raise FileProblem(502,'Source quote could not be verified')
    return result
