import os
import aiohttp
from datetime import datetime, timedelta

CROCO_BASE_URL = "https://api.portal-crococalls.com/v2"
CROCO_API_KEY = os.getenv("CROCO_API_KEY", "")

def _headers() -> dict:
    return {
        "X-API-KEY": CROCO_API_KEY,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }


async def croco_request(endpoint: str, params: dict = {}) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CROCO_BASE_URL}{endpoint}", params=params, headers=_headers()) as resp:
            return await resp.json(content_type=None)


async def croco_raw(params: dict = {}) -> tuple[int, str]:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CROCO_BASE_URL}/calls", params=params, headers=_headers()) as resp:
            text = await resp.text()
            return resp.status, text


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _norm_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


async def _fetch_all_calls(filter_str: str) -> list:
    all_calls = []
    offset = 0
    limit = 5000

    while True:
        data = await croco_request("/calls", {
            "limit": limit,
            "offset": offset,
            "filter": filter_str,
            "sort": "-starttime",
        })

        if not isinstance(data, dict):
            break

        page = data.get("data", [])
        if not page:
            break

        all_calls.extend(page)
        print(f"[Croco] offset={offset} got={len(page)} total={len(all_calls)}")

        if len(page) < limit:
            break
        offset += limit

    return all_calls


async def get_stats(period: str = "today") -> dict:
    now = datetime.now()

    if period == "today":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = now
    elif period == "yesterday":
        y = now - timedelta(days=1)
        from_dt = y.replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = y.replace(hour=23, minute=59, second=59)
    elif period == "week":
        from_dt = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = now
    elif period == "month":
        from_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        to_dt = now
    else:
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = now

    filter_str = f"starttime>{_fmt_dt(from_dt)},starttime<{_fmt_dt(to_dt)}"
    calls = await _fetch_all_calls(filter_str)

    total = len(calls)
    by_status: dict[str, int] = {}
    by_duration: dict[str, int] = {}
    by_type: dict[str, int] = {}

    for c in calls:
        st = (c.get("status") or "unknown").lower()
        direction = (c.get("direction") or "unknown").lower()
        by_status[st] = by_status.get(st, 0) + 1
        by_duration[st] = by_duration.get(st, 0) + int(c.get("duration_sec") or 0)
        by_type[direction] = by_type.get(direction, 0) + 1

    answered = by_status.get("answer", 0)
    ans_dur = by_duration.get("answer", 0)
    avg_duration = round(ans_dur / answered) if answered > 0 else 0

    return {
        "total": total,
        "by_status": by_status,
        "by_duration": by_duration,
        "by_type": by_type,
        "avg_duration": avg_duration,
        "period": period,
    }


async def download_recording(audio_url: str) -> tuple[int, bytes]:
    async with aiohttp.ClientSession() as session:
        async with session.get(audio_url, headers=_headers()) as resp:
            return resp.status, await resp.read()


async def search_recordings(phone: str, days: int = 30) -> list:
    now = datetime.now()
    from_dt = now - timedelta(days=days)
    filter_str = f"starttime>{_fmt_dt(from_dt)},starttime<{_fmt_dt(now)}"

    calls = await _fetch_all_calls(filter_str)

    needle = _norm_phone(phone)
    result = []
    for c in calls:
        caller = c.get("caller") or {}
        callee = c.get("callee") or {}
        caller_norm = _norm_phone(caller.get("cid") or caller.get("number") or "")
        callee_norm = _norm_phone(callee.get("number") or "")

        if needle and (needle in caller_norm or needle in callee_norm):
            result.append(c)

    return result
