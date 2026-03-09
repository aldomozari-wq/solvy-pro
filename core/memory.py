import asyncio
import anthropic

from core.config import ANTHROPIC_KEY
from core.database import get_conn


def get_memory(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT summary, messages_count FROM memory WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return row if row else (None, 0)
    finally:
        conn.close()


def save_memory(user_id, summary, messages_count):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO memory (user_id, summary, messages_count, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET summary = %s, messages_count = %s, updated_at = NOW()
        """, (user_id, summary, messages_count, summary, messages_count))
        conn.commit()
    finally:
        conn.close()


async def create_memory_summary(history: list, existing_summary: str = None) -> str:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-50:]])
    existing = f"Existing summary: {existing_summary}\n\n" if existing_summary else ""

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": f"""{existing}Analyze this conversation and create a concise memory summary about the user.
Include: name, occupation, goals, interests, personality traits, important facts mentioned, communication style.
Write in Russian, max 300 words. Be specific and personal.

Conversation:
{history_text}"""}]
    ))
    return response.content[0].text.strip()


async def update_memory_background(user_id: int, history: list, existing_summary: str):
    try:
        new_summary = await create_memory_summary(history, existing_summary)
        save_memory(user_id, new_summary, len(history))
    except Exception as e:
        print(f"Memory update error: {e}")
