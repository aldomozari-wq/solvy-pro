import aiohttp
import os
from datetime import datetime, timedelta

VOISO_CLUSTER = os.getenv("VOISO_CLUSTER")    # e.g. "cc-ams03"
VOISO_API_KEY = os.getenv("VOISO_API_KEY")    # user API key (для CDR)
VOISO_EMAIL   = os.getenv("VOISO_EMAIL")      # email для web auth (запису)
VOISO_PASSWORD = os.getenv("VOISO_PASSWORD")  # пароль для web auth

_voiso_session_cookie: str | None = None


def _base_url() -> str:
    return f"https://{VOISO_CLUSTER}.voiso.com"


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _dur_to_secs(duration: str) -> int:
    """Convert 'HH:MM:SS' to seconds"""
    try:
        parts = (duration or "").split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        pass
    return 0


async def _fetch_all_cdr(params: dict) -> list:
    """Fetch all CDR records with search_token pagination"""
    all_records = []
    page = 1
    search_token = None
    PER_PAGE = 1000

    async with aiohttp.ClientSession() as session:
        for _ in range(200):  # safety limit: 200 pages × 1000 = 200k records
            req_params = {
                "key": VOISO_API_KEY,
                "per_page": PER_PAGE,
                "page": page,
                **params,
            }
            if search_token:
                req_params["search_token"] = search_token

            async with session.get(f"{_base_url()}/api/v2/cdr", params=req_params) as resp:
                data = await resp.json(content_type=None)

            if not isinstance(data, dict):
                break

            records = data.get("records", [])
            all_records.extend(records)
            total = data.get("total", 0)

            # search_token зберігаємо тільки з першої сторінки — він незмінний для всього запиту
            if page == 1:
                search_token = data.get("search_token")

            print(f"[Voiso CDR page={page}] got={len(records)} total={total} fetched={len(all_records)}")

            if not records or len(records) < PER_PAGE or len(all_records) >= total:
                break

            page += 1

    return all_records


async def voiso_raw(params: dict = {}) -> tuple[int, str]:
    """Raw request to /api/v2/cdr — для дебагу"""
    req_params = {"key": VOISO_API_KEY, "per_page": 5, **params}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{_base_url()}/api/v2/cdr", params=req_params) as resp:
            text = await resp.text()
            return resp.status, text


def _norm_phone(phone: str) -> str:
    """Останні 9 цифр номера для порівняння"""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


async def search_voiso_recordings(phone: str, days: int = 30) -> list:
    """Пошук дзвінків по номеру в Voiso CDR за останні N днів"""
    now = datetime.now()
    from_dt = now - timedelta(days=days - 1)

    records = await _fetch_all_cdr({
        "start_date": _date(from_dt),
        "end_date": _date(now),
    })

    needle = _norm_phone(phone)
    result = []
    for r in records:
        if needle and (
            needle in _norm_phone(r.get("from") or "")
            or needle in _norm_phone(r.get("to") or "")
        ):
            result.append(r)

    # сортуємо від найновіших
    result.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return result


async def _voiso_login() -> str | None:
    """Логін через web (Devise). Повертає значення cookie _session або None."""
    global _voiso_session_cookie
    if not VOISO_EMAIL or not VOISO_PASSWORD:
        return None

    sign_in_url = f"{_base_url()}/users/sign_in"

    async with aiohttp.ClientSession() as session:
        # Спочатку GET щоб отримати CSRF token
        async with session.get(sign_in_url) as resp:
            html_text = await resp.text()
            import re
            m = re.search(r'name="authenticity_token"[^>]+value="([^"]+)"', html_text)
            csrf = m.group(1) if m else ""

        # POST логін
        async with session.post(sign_in_url, data={
            "user[email]": VOISO_EMAIL,
            "user[password]": VOISO_PASSWORD,
            "authenticity_token": csrf,
        }, allow_redirects=True) as resp:
            cookies = {k: v.value for k, v in resp.cookies.items()}
            # Шукаємо session cookie (зазвичай _session_id або _callcenter_session)
            for k, v in cookies.items():
                if "session" in k.lower():
                    _voiso_session_cookie = f"{k}={v}"
                    print(f"[Voiso] logged in, cookie={k}")
                    return _voiso_session_cookie

    return None


async def download_voiso_recording(uuid: str) -> tuple[int, bytes]:
    """Завантажити запис дзвінка по UUID через web сесію."""
    global _voiso_session_cookie

    url = f"{_base_url()}/recordings/{uuid}.mp3"

    async def _fetch(cookie: str | None) -> tuple[int, bytes]:
        headers = {"Cookie": cookie} if cookie else {}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, allow_redirects=True) as resp:
                return resp.status, await resp.read()

    # Спробуємо з поточною сесією
    status, data = await _fetch(_voiso_session_cookie)

    # Якщо 401 — логінимось і пробуємо ще раз
    if status == 401 or (status == 200 and data[:20].lstrip().lower().startswith(b"you need")):
        cookie = await _voiso_login()
        if cookie:
            status, data = await _fetch(cookie)

    return status, data


async def debug_recording_urls(uuid: str) -> list[dict]:
    """Пробує різні URL варіанти для запису — для дебагу"""
    candidates = [
        {"url": f"{_base_url()}/recordings/{uuid}.mp3",    "params": {"key": VOISO_API_KEY}, "headers": {}},
        {"url": f"{_base_url()}/recordings/{uuid}",         "params": {"key": VOISO_API_KEY}, "headers": {}},
        {"url": f"{_base_url()}/api/v2/recordings/{uuid}",  "params": {"key": VOISO_API_KEY}, "headers": {}},
        {"url": f"{_base_url()}/recordings/{uuid}.mp3",    "params": {}, "headers": {"Authorization": f"Bearer {VOISO_API_KEY}"}},
        {"url": f"{_base_url()}/api/v2/recordings/{uuid}/download", "params": {"key": VOISO_API_KEY}, "headers": {}},
    ]
    results = []
    async with aiohttp.ClientSession() as session:
        for c in candidates:
            try:
                async with session.get(c["url"], params=c["params"], headers=c["headers"]) as resp:
                    body = await resp.read()
                    results.append({
                        "url": str(resp.url),
                        "status": resp.status,
                        "size": len(body),
                        "content_type": resp.headers.get("Content-Type", "?"),
                        "preview": body[:80].decode("utf-8", errors="replace"),
                    })
            except Exception as e:
                results.append({"url": c["url"], "error": str(e)})
    return results


async def get_stats(period: str = "today") -> dict:
    """Voiso call statistics for period: today, yesterday, week, month"""
    now = datetime.now()

    if period == "today":
        from_dt = to_dt = now
    elif period == "yesterday":
        from_dt = to_dt = now - timedelta(days=1)
    elif period == "week":
        from_dt = now - timedelta(days=now.weekday())
        to_dt = now
    elif period == "month":
        from_dt = now.replace(day=1)
        to_dt = now
    else:
        from_dt = to_dt = now

    records = await _fetch_all_cdr({
        "start_date": _date(from_dt),
        "end_date": _date(to_dt),
    })

    # Only voice records (exclude SMS, digital channels)
    voice_types = {"inbound", "outbound", "campaign", "callback", "queue-callback", "scheduled-callback"}
    records = [r for r in records if r.get("type") in voice_types]

    total = len(records)
    by_status: dict[str, int] = {}
    by_duration: dict[str, int] = {}

    for r in records:
        disp = (r.get("disposition") or "unknown").lower()
        by_status[disp] = by_status.get(disp, 0) + 1
        dur_secs = _dur_to_secs(r.get("duration") or "")
        by_duration[disp] = by_duration.get(disp, 0) + dur_secs

    answered = by_status.get("answered", 0)
    ans_dur = by_duration.get("answered", 0)
    avg_duration = round(ans_dur / answered) if answered > 0 else 0

    return {
        "total": total,
        "by_status": by_status,
        "by_duration": by_duration,
        "avg_duration": avg_duration,
        "period": period,
    }
