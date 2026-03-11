import os

import aiohttp

COPERATO_PROXY = os.getenv("COPERATO_PROXY", "")


async def download_recording(url: str) -> tuple[int, bytes]:
    """Скачать запись с Coperato через SOCKS5 прокси (если задан COPERATO_PROXY)."""
    kwargs = {}
    if COPERATO_PROXY:
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(COPERATO_PROXY)
        kwargs["connector"] = connector

    async with aiohttp.ClientSession(**kwargs) as session:
        async with session.get(url) as resp:
            return resp.status, await resp.read()
