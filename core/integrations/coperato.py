import os
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

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
    print(f"[coperato] download normalized={normalized!r}")

    kwargs = {}
    if COPERATO_PROXY:
        kwargs["proxy"] = COPERATO_PROXY

    async with httpx.AsyncClient(**kwargs, follow_redirects=True) as client:
        resp = await client.get(normalized)
        return resp.status_code, resp.content
