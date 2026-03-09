import os
import psycopg2


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                role TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                niche TEXT,
                goal TEXT,
                mode TEXT DEFAULT 'business'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                user_id BIGINT PRIMARY KEY,
                summary TEXT,
                messages_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id BIGINT PRIMARY KEY,
                reason TEXT,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY,
                credits INTEGER DEFAULT 0,
                plan TEXT DEFAULT 'free',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS photo_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                model TEXT,
                prompt TEXT,
                result_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id SERIAL PRIMARY KEY,
                caller_id TEXT,
                called_id TEXT,
                duration INTEGER DEFAULT 0,
                disposition TEXT,
                call_date TIMESTAMP,
                uniqueid TEXT UNIQUE,
                recording_file TEXT,
                raw_data TEXT,
                source TEXT DEFAULT 'unknown',
                call_type TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'unknown'")
        cur.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS call_type TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def get_history(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content FROM messages WHERE user_id = %s ORDER BY created_at DESC LIMIT 50",
            (user_id,)
        )
        messages = [{"role": row[0], "content": row[1]} for row in cur.fetchall()]
        return list(reversed(messages))
    finally:
        conn.close()


def save_message(user_id, role, content):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
        conn.commit()
    finally:
        conn.close()


def get_user(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def save_user(user_id, name, niche, goal, mode="business"):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO users (user_id, name, niche, goal, mode) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET name=%s, niche=%s, goal=%s, mode=%s""",
            (user_id, name, niche, goal, mode, name, niche, goal, mode)
        )
        conn.commit()
    finally:
        conn.close()


def delete_user(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM memory WHERE user_id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()


def count_messages(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def update_mode(user_id, mode):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET mode = %s WHERE user_id = %s", (mode, user_id))
        conn.commit()
    finally:
        conn.close()


def is_blocked(user_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM blocked_users WHERE user_id = %s", (user_id,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def block_user(user_id: int, reason: str = ""):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO blocked_users (user_id, reason)
               VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET reason = %s""",
            (user_id, reason, reason)
        )
        conn.commit()
    finally:
        conn.close()


def unblock_user(user_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM blocked_users WHERE user_id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_credits(user_id: int) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT credits FROM subscriptions WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def use_credit(user_id: int) -> bool:
    """Deduct one credit. Returns True if successful, False if no credits."""
    return use_credits(user_id, 1)


def use_credits(user_id: int, amount: int) -> bool:
    """Deduct N credits atomically. Returns True if successful, False if insufficient."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE subscriptions SET credits = credits - %s "
            "WHERE user_id = %s AND credits >= %s RETURNING credits",
            (amount, user_id, amount)
        )
        result = cur.fetchone()
        conn.commit()
        return result is not None
    finally:
        conn.close()


def add_credits(user_id: int, amount: int, plan: str = "paid"):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO subscriptions (user_id, credits, plan)
               VALUES (%s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE
               SET credits = subscriptions.credits + %s, plan = %s""",
            (user_id, amount, plan, amount, plan)
        )
        conn.commit()
    finally:
        conn.close()


def remove_credits(user_id: int, amount: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE subscriptions SET credits = GREATEST(0, credits - %s) WHERE user_id = %s",
            (amount, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def set_credits(user_id: int, amount: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO subscriptions (user_id, credits, plan)
               VALUES (%s, %s, 'paid')
               ON CONFLICT (user_id) DO UPDATE SET credits = %s""",
            (user_id, amount, amount)
        )
        conn.commit()
    finally:
        conn.close()


def save_photo_history(user_id: int, model: str, prompt: str, result_url: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO photo_history (user_id, model, prompt, result_url) VALUES (%s, %s, %s, %s)",
            (user_id, model, prompt, result_url)
        )
        conn.commit()
    finally:
        conn.close()


# ── Calls (DIDPBX webhook) ─────────────────────────────────────────────────

def save_call(data: dict, source: str = "unknown"):
    """Зберегти дзвінок з webhook-даних (Coperato або DIDPBX)."""
    import json as _json
    from datetime import datetime

    caller = data.get("callerid") or data.get("caller_id") or data.get("src") or ""
    called = data.get("destination") or data.get("called_id") or data.get("dst") or data.get("exten") or ""
    duration = int(data.get("duration") or data.get("billsec") or 0)
    call_type = (data.get("type") or "").lower()
    uniqueid = data.get("uniqueid") or data.get("guid") or data.get("id") or data.get("callid") or ""
    recording = data.get("recordingFile") or data.get("recording") or data.get("filename") or data.get("record") or ""

    # Disposition
    status = (data.get("status") or data.get("disposition") or "").upper()
    if source == "coperato":
        disposition = "answered" if duration > 0 else "missed"
    else:
        disposition = status

    # Date — Coperato: "08-07-2020 08:26:05", DIDPBX: "2020-07-08 08:26:05"
    raw_date = data.get("date") or data.get("calldate") or data.get("start") or ""
    call_date = None
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            call_date = datetime.strptime(raw_date, fmt)
            break
        except (ValueError, TypeError):
            continue
    if call_date is None:
        call_date = datetime.now()

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO calls
                   (caller_id, called_id, duration, disposition, call_date, uniqueid,
                    recording_file, raw_data, source, call_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (uniqueid) DO UPDATE SET
                   disposition    = EXCLUDED.disposition,
                   duration       = EXCLUDED.duration,
                   recording_file = COALESCE(NULLIF(EXCLUDED.recording_file, ''), calls.recording_file),
                   raw_data       = EXCLUDED.raw_data""",
            (caller, called, duration, disposition, call_date,
             uniqueid or None, recording, _json.dumps(data), source, call_type)
        )
        conn.commit()
    finally:
        conn.close()


def get_calls_by_phone(phone: str, days: int = 30) -> list:
    """Знайти всі дзвінки де phone є caller або called за останні N днів."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT caller_id, called_id, duration, disposition, call_date, recording_file, raw_data
               FROM calls
               WHERE (caller_id LIKE %s OR called_id LIKE %s)
                 AND call_date >= NOW() - INTERVAL '%s days'
               ORDER BY call_date DESC""",
            (f"%{phone}%", f"%{phone}%", days)
        )
        cols = ["caller_id", "called_id", "duration", "disposition", "call_date", "recording_file", "raw_data"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_coperato_stats(period: str = "today") -> dict:
    """Статистика Coperato дзвінків з локальної БД."""
    from datetime import datetime, timedelta

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

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT call_type, disposition, COUNT(*), COALESCE(SUM(duration), 0)
               FROM calls
               WHERE source = 'coperato'
                 AND call_date >= %s AND call_date <= %s
               GROUP BY call_type, disposition""",
            (from_dt, to_dt)
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    total = 0
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_duration: dict[str, int] = {}

    for call_type, disposition, count, dur_sum in rows:
        ct = (call_type or "unknown").lower()
        d = (disposition or "unknown").lower()
        total += count
        by_type[ct] = by_type.get(ct, 0) + count
        by_status[d] = by_status.get(d, 0) + count
        by_duration[d] = by_duration.get(d, 0) + int(dur_sum)

    answered = by_status.get("answered", 0)
    ans_dur = by_duration.get("answered", 0)
    avg_duration = round(ans_dur / answered) if answered > 0 else 0

    return {
        "total": total,
        "by_type": by_type,
        "by_status": by_status,
        "by_duration": by_duration,
        "avg_duration": avg_duration,
        "period": period,
    }


def search_coperato_recordings(phone: str, days: int = 30) -> list:
    """Знайти записи Coperato по номеру телефону."""
    digits = "".join(c for c in phone if c.isdigit())
    needle = digits[-9:] if len(digits) >= 9 else digits

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT caller_id, called_id, duration, call_date, recording_file, call_type
               FROM calls
               WHERE source = 'coperato'
                 AND recording_file IS NOT NULL AND recording_file != ''
                 AND call_date >= NOW() - INTERVAL '%s days'
                 AND (caller_id LIKE %s OR called_id LIKE %s)
               ORDER BY call_date DESC
               LIMIT 20""",
            (days, f"%{needle}%", f"%{needle}%")
        )
        cols = ["caller_id", "called_id", "duration", "call_date", "recording_file", "call_type"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def debug_coperato_db() -> dict:
    """Діагностика Coperato записів в БД."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM calls WHERE source = 'coperato'")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM calls WHERE source = 'coperato' AND recording_file IS NOT NULL AND recording_file != ''")
        with_rec = cur.fetchone()[0]
        cur.execute(
            """SELECT caller_id, called_id, call_date, recording_file
               FROM calls WHERE source = 'coperato'
               ORDER BY call_date DESC LIMIT 5"""
        )
        recent = [
            {"caller_id": r[0], "called_id": r[1], "call_date": str(r[2]), "recording_file": r[3]}
            for r in cur.fetchall()
        ]
        return {"total": total, "with_recording": with_rec, "recent": recent}
    finally:
        conn.close()


def get_call_stats(days: int = 1) -> dict:
    """Статистика дзвінків за останні N днів."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN duration > 0 THEN 1 ELSE 0 END),
                      AVG(CASE WHEN duration > 0 THEN duration ELSE NULL END)
               FROM calls
               WHERE call_date >= NOW() - INTERVAL '%s days'""",
            (days,)
        )
        row = cur.fetchone()
        total = row[0] or 0
        answered = row[1] or 0
        avg_dur = round(float(row[2])) if row[2] else 0
        return {
            "total": total,
            "answered": answered,
            "missed": total - answered,
            "pickup_rate": round(answered / total * 100) if total > 0 else 0,
            "avg_duration": avg_dur,
            "period_days": days,
        }
    finally:
        conn.close()
