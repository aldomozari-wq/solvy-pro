import asyncio
import os
import re
from urllib.parse import urlsplit, urlunsplit

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


def _sync_download(url: str) -> tuple[int, bytes]:
    proxies = {"http": COPERATO_PROXY, "https": COPERATO_PROXY} if COPERATO_PROXY else {}
    resp = requests.get(url, proxies=proxies, allow_redirects=True, timeout=60)
    return resp.status_code, resp.content


async def download_recording(url: str) -> tuple[int, bytes]:
    """Скачать запись с Coperato через SOCKS5 прокси (если задан COPERATO_PROXY)."""
    normalized = _normalize_url(url)
    print(f"[coperato] download normalized={normalized!r}")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_download, normalized)
