import aiohttp
import os
from datetime import datetime, timedelta

DIDPBX_URL = os.getenv("DIDPBX_URL")      # https://b.didpbx.com/ui
DIDPBX_PHONE = os.getenv("DIDPBX_PHONE")  # повний логін: 12317/martin
DIDPBX_PASSWORD = os.getenv("DIDPBX_PASSWORD")


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _dt(dt: datetime) -> str:
    """Формат з слешами — єдиний що API приймає з часом"""
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def _norm_phone(phone: str) -> str:
    """Останні 9 цифр номера для порівняння"""
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


async def didpbx_request(action: str, params: dict = {}) -> dict:
    async with aiohttp.ClientSession() as session:
        payload = {
            "phone": DIDPBX_PHONE,
            "pw": DIDPBX_PASSWORD,
            "action": action,
            "df": "json",
            **params
        }
        async with session.get(DIDPBX_URL, params=payload) as resp:
            return await resp.json(content_type=None)


async def didpbx_raw(action: str, params: dict = {}) -> tuple[int, str]:
    """Повертає (status_code, raw_text) для дебагу"""
    async with aiohttp.ClientSession() as session:
        payload = {
            "phone": DIDPBX_PHONE,
            "pw": DIDPBX_PASSWORD,
            "action": action,
            "df": "json",
            **params
        }
        async with session.get(DIDPBX_URL, params=payload) as resp:
            text = await resp.text()
            return resp.status, text


async def _fetch_all_cdr(params: dict, chunk_hours: int = 2) -> list:
    """CDR чанками з форматом YYYY/MM/DD HH:MM:SS (єдиний формат з часом що приймає API)"""
    from_time_str = params.get("from_time", "")
    to_time_str = params.get("to_time", "")

    try:
        day_from = datetime.strptime(from_time_str[:10].replace("/", "-"), "%Y-%m-%d")
        day_to = datetime.strptime(to_time_str[:10].replace("/", "-"), "%Y-%m-%d")
    except Exception:
        data = await didpbx_request("cdr_list", params)
        if not isinstance(data, dict):
            return []
        return data.get("CDR_LIST", [])

    all_calls = []
    seen_ids: set = set()
    current = datetime(day_from.year, day_from.month, day_from.day, 0, 0, 0)
    end = datetime(day_to.year, day_to.month, day_to.day, 23, 59, 59)

    chunk_num = 0
    while current <= end:
        chunk_end = min(current + timedelta(hours=chunk_hours) - timedelta(seconds=1), end)
        chunk_params = dict(params)
        chunk_params["from_time"] = _dt(current)    # YYYY/MM/DD HH:MM:SS
        chunk_params["to_time"] = _dt(chunk_end)    # YYYY/MM/DD HH:MM:SS

        cdr_from = None
        page_num = 0
        while True:
            paged_params = dict(chunk_params)
            if cdr_from is not None:
                paged_params["cdr_from"] = cdr_from

            data = await didpbx_request("cdr_list", paged_params)
            if not isinstance(data, dict):
                break

            page = data.get("CDR_LIST", [])
            new_count = 0
            for call in page:
                uid = call.get("CALL_ID") or str(call)
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    all_calls.append(call)
                    new_count += 1

            page_num += 1
            chunk_num += 1
            print(f"[CDR chunk {chunk_num} p{page_num}] {current.strftime('%m-%d %H:%M')}-{chunk_end.strftime('%H:%M')} got={len(page)} new={new_count} total={len(all_calls)}")

            cdr_next = data.get("CDR_NEXT")
            if not page or cdr_next is None:
                break
            cdr_from = cdr_next

        current += timedelta(hours=chunk_hours)

    return all_calls


async def search_calls(phone_number: str, days: int = 30) -> list:
    """Пошук дзвінків по номеру за останні N днів (фільтр на клієнті)"""
    now = datetime.now()
    to_time = _date(now)
    from_time = _date(now - timedelta(days=days - 1))

    all_calls = await _fetch_all_cdr({
        "from_time": from_time,
        "to_time": to_time,
    })

    needle = _norm_phone(phone_number)
    result = []
    for call in all_calls:
        called = _norm_phone(call.get("CALLED_ID") or "")
        caller = _norm_phone(call.get("CALLER_ID") or call.get("CNAM") or "")
        if needle and (needle in called or needle in caller):
            result.append(call)

    return result


_root_ext_id_cache: str | None = None


async def _get_root_ext_id() -> str:
    global _root_ext_id_cache
    if not _root_ext_id_cache:
        data = await didpbx_request("vb_list")
        _root_ext_id_cache = str(data.get("ROOT_EXT_ID", ""))
    return _root_ext_id_cache


def _account_id() -> str:
    """12317 з '12317/martin'"""
    return (DIDPBX_PHONE or "").split("/")[0]


async def download_recording(filename: str) -> bytes:
    """Скачати запис. FILE_NAME з msg_list (.g722) → URL додає .mp3"""
    root_ext_id = await _get_root_ext_id()
    if not filename.endswith(".mp3"):
        filename = filename + ".mp3"
    url = f"{DIDPBX_URL}/msg_download/{root_ext_id}/{_account_id()}/{filename}?media=mp3"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


async def _fetch_all_msg(params: dict) -> list:
    """Отримати всі записи з пагінацією через MSG_NEXT"""
    all_recs = []
    seen: set = set()
    msg_from = None

    for _ in range(50):
        page_params = dict(params)
        if msg_from is not None:
            page_params["msg_from"] = msg_from

        data = await didpbx_request("msg_list", page_params)
        if not isinstance(data, dict):
            break

        page = data.get("MSG_LIST", [])
        for rec in page:
            fid = rec.get("FILE_ID") or str(rec)
            if fid not in seen:
                seen.add(fid)
                all_recs.append(rec)

        msg_next = data.get("MSG_NEXT")
        if not page or msg_next is None:
            break
        msg_from = msg_next

    return all_recs


async def search_recordings(phone_number: str, days: int = 30) -> list:
    """Пошук записів по номеру телефону"""
    now = datetime.now()
    to_time = _date(now)
    from_time = _date(now - timedelta(days=days - 1))

    all_recs = await _fetch_all_msg({
        "from_time": from_time,
        "to_time": to_time,
    })

    needle = _norm_phone(phone_number)
    return [
        r for r in all_recs
        if needle and (
            needle in _norm_phone(r.get("CALLEDID") or "")
            or needle in _norm_phone(r.get("CALLERID") or "")
        )
    ]


def _parse_status(data_str: str) -> str:
    for part in (data_str or "").split(","):
        part = part.strip()
        if part.startswith("STATUS="):
            return part[7:].strip()
    return "OTHER"


async def get_stats(period: str = "today") -> dict:
    """Статистика дзвінків за период: today, yesterday, week, month"""
    now = datetime.now()

    if period == "today":
        from_dt = now
        to_dt = now
        chunk_hours = 1
    elif period == "yesterday":
        yesterday = now - timedelta(days=1)
        from_dt = yesterday
        to_dt = yesterday
        chunk_hours = 1
    elif period == "week":
        from_dt = now - timedelta(days=now.weekday())  # з понеділка
        to_dt = now
        chunk_hours = 2
    elif period == "month":
        from_dt = now.replace(day=1)  # з 1-го числа
        to_dt = now
        chunk_hours = 2
    else:
        from_dt = now
        to_dt = now
        chunk_hours = 1

    calls = await _fetch_all_cdr({
        "from_time": _date(from_dt),
        "to_time": _date(to_dt),
    }, chunk_hours=chunk_hours)

    total = len(calls)
    by_status: dict[str, int] = {}
    by_duration: dict[str, int] = {}  # total seconds per status

    for c in calls:
        st = _parse_status(c.get("DATA") or "")
        by_status[st] = by_status.get(st, 0) + 1
        by_duration[st] = by_duration.get(st, 0) + int(c.get("CDR_DURATION") or 0)

    answered = by_status.get("ANSWER", 0)
    ans_dur = by_duration.get("ANSWER", 0)
    avg_duration = round(ans_dur / answered) if answered > 0 else 0

    return {
        "total": total,
        "by_status": by_status,
        "by_duration": by_duration,
        "avg_duration": avg_duration,
        "period": period,
    }
