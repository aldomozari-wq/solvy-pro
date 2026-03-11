import asyncio
import os
import re
from urllib.parse import urlsplit, urlunsplit, quote

import requests

COPERATO_PROXY = os.getenv("COPERATO_PROXY", "")


def _normalize_url(url: str) -> str:
    """Нормализовать URL: убрать лишние слеши только из пути."""
    url = url.strip()
    try:
        parts = urlsplit(url)
        path = re.sub(r'/{2,}', '/', parts.path)
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    except Exception:
        return url


def _encode_proxy_url(proxy_url: str) -> str:
    """Percent-encode special chars in proxy credentials so URL parsers don't choke."""
    try:
        scheme, rest = proxy_url.split("://", 1)
        at_pos = rest.rfind("@")
        if at_pos == -1:
            return proxy_url  # no credentials
        userinfo = rest[:at_pos]
        hostport = rest[at_pos + 1:]
        colon_pos = userinfo.find(":")
        if colon_pos == -1:
            username, password = userinfo, ""
        else:
            username = userinfo[:colon_pos]
            password = userinfo[colon_pos + 1:]
        return f"{scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{hostport}"
    except Exception:
        return proxy_url


def _sync_download(url: str) -> tuple[int, bytes]:
    proxies = {}
    if COPERATO_PROXY:
        encoded = _encode_proxy_url(COPERATO_PROXY)
        proxies = {"http": encoded, "https": encoded}
    resp = requests.get(url, proxies=proxies, allow_redirects=True, timeout=60)
    return resp.status_code, resp.content


async def download_recording(url: str) -> tuple[int, bytes]:
    """Скачать запись с Coperato через SOCKS5 прокси (если задан COPERATO_PROXY)."""
    normalized = _normalize_url(url)
    print(f"[coperato] download normalized={normalized!r}")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_download, normalized)
