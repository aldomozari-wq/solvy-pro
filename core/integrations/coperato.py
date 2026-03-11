import os
import re

import aiohttp
from yarl import URL

COPERATO_PROXY = os.getenv("COPERATO_PROXY", "")


def _normalize_url(url: str) -> str:
    """Убрать лишние слеши в пути (https:// не трогать)."""
    return re.sub(r'(?<!:)/{2,}', '/', url)


async def download_recording(url: str) -> tuple[int, bytes]:
    """Скачать запись с Coperato через SOCKS5 прокси (если задан COPERATO_PROXY)."""
    normalized = _normalize_url(url.strip())
    print(f"[coperato] download url={normalized!r} proxy={COPERATO_PROXY!r}")
    parsed = URL(normalized, encoded=True)

    kwargs = {}
    if COPERATO_PROXY:
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(COPERATO_PROXY)
        kwargs["connector"] = connector

    async with aiohttp.ClientSession(**kwargs) as session:
        async with session.get(parsed) as resp:
            return resp.status, await resp.read()
