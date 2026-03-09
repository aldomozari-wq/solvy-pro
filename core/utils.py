import asyncio
import openai

from core.config import OPENAI_KEY


async def transcribe_voice(file_path: str) -> tuple[str, str]:
    """Повертає (text, language_code). language — ISO 639-1, наприклад 'uk', 'ru', 'en'."""
    loop = asyncio.get_event_loop()
    client = openai.OpenAI(api_key=OPENAI_KEY)

    def _run():
        with open(file_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
            )
        return result.text, getattr(result, "language", "unknown")

    return await loop.run_in_executor(None, _run)
