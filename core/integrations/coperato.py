import os
import re
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from yarl import URL

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


async def download_recording(url: str) -> tuple[int, bytes]:
    """Скачать запись с Coperato через SOCKS5 прокси (если задан COPERATO_PROXY)."""
    normalized = _normalize_url(url)
    print(f"[coperato] normalized={normalized!r}")
    parsed = URL(normalized, encoded=True)

    kwargs = {}
    if COPERATO_PROXY:
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(COPERATO_PROXY)
        kwargs["connector"] = connector

    async with aiohttp.ClientSession(**kwargs) as session:
        async with session.get(parsed) as resp:
            return resp.status, await resp.read()
