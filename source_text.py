"""Fetch verifiable public source text through an explicit proxy, with pinned DNS."""
import ipaddress
import json
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


def target(url, client=None):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None,443) or parsed.username or parsed.password or len(url)>2048:
            raise ValueError()
        if client is None:
            addresses = {a[4][0] for a in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)}
        else:
            try:
                addresses = {str(ipaddress.ip_address(parsed.hostname))}
            except ValueError:
                response = client.get('https://dns.google/resolve', params={'name': parsed.hostname, 'type': 'A'},
                                      headers={'Accept': 'application/dns-json', 'User-Agent': 'WebCC-Sources/1'})
                response.raise_for_status()
                if len(response.content) > 65536:
                    raise ValueError()
                data = response.json()
                if data.get('Status') != 0:
                    raise ValueError()
                addresses = {a['data'] for a in data.get('Answer', []) if a.get('type') == 1}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError()
        address = sorted(addresses, key=lambda a: (ipaddress.ip_address(a).version, a))[0]
        return httpx.URL(url).copy_with(host=address), parsed.hostname
    except (ValueError,OSError):
        raise FileProblem(400,'Source URL must resolve only to public HTTPS addresses') from None


def fetch(url):
    proxy=os.environ.get('MANAGER_SOURCE_PROXY') or os.environ.get('MANAGER_TOOLS_PROXY') or os.environ.get('MANAGER_E2B_PROXY','')
    p=urlsplit(proxy)
    if p.scheme not in {'http','https','socks5','socks5h'} or not p.hostname or not p.port:
        raise FileProblem(503,'Source verification requires an explicit outbound proxy')
    if not SLOTS.acquire(blocking=False):
        raise FileProblem(429,'Source verification is busy')
    try:
        deadline = time.monotonic() + 12
        with httpx.Client(proxy=proxy,trust_env=False,timeout=8,follow_redirects=False) as client:
            for _ in range(3):
                pinned,host=target(url, client)
                with client.stream('GET',pinned,headers={'Host':host,'User-Agent':'WebCC-Sources/1','Accept-Encoding':'identity'},extensions={'sni_hostname':host}) as response:
                    if response.is_redirect:
                        url=str(httpx.URL(url).join(response.headers.get('location','')))
                        continue
                    response.raise_for_status()
                    parts=[];size=0
                    for part in response.iter_bytes():
                        if time.monotonic() >= deadline: raise FileProblem(504, 'Source verification deadline exceeded')
                        size+=len(part)
                        if size>2097152:raise FileProblem(413,'Source page exceeds 2 MiB')
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
    except (httpx.HTTPError,subprocess.TimeoutExpired) as error:
        response = getattr(error, 'response', None)
        print(json.dumps({'event': 'source_fetch_failed', 'host': urlsplit(url).hostname,
                          'category': type(error).__name__, 'status': getattr(response, 'status_code', None)}), flush=True)
        category = 'proxy connection' if isinstance(error, httpx.ProxyError) else 'timeout' if isinstance(error, (httpx.TimeoutException, subprocess.TimeoutExpired)) else 'upstream HTTP or transport'
        raise FileProblem(502,'Source verification failed: ' + category + '; source was not verified') from None
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
