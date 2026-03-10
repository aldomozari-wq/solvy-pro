import os
import time
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
OPENAI_KEY    = os.getenv("OPENAI_KEY")
os.environ.setdefault("FAL_KEY", os.getenv("FAL_KEY", ""))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
COPERATO_BASE_URL = os.getenv("COPERATO_BASE_URL", "").rstrip("/")
CROCO_API_KEY = os.getenv("CROCO_API_KEY", "")

# ── Admin ──────────────────────────────────────────────────────────────────────
def _parse_ids(env_key: str) -> set[int]:
    return {int(x) for x in os.getenv(env_key, "").split(",") if x.strip()}

ADMIN_IDS: set[int] = _parse_ids("ADMIN_IDS") | _parse_ids("ADMIN_IDS_K")

# ── Rate limiting ─────────────────────────────────────────────────────────────
_rate_limit: dict[int, list[float]] = defaultdict(list)

def check_rate_limit(user_id: int, max_per_minute: int = 10) -> bool:
    now = time.time()
    timestamps = _rate_limit[user_id]
    _rate_limit[user_id] = [t for t in timestamps if now - t < 60]
    if len(_rate_limit[user_id]) >= max_per_minute:
        return False
    _rate_limit[user_id].append(now)
    return True

# ── Content safety ────────────────────────────────────────────────────────────
BANNED_KEYWORDS = ("nude", "naked", "nsfw", "porn", "explicit", "gore", "violence", "weapon")

def is_safe_prompt(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return not any(kw in prompt_lower for kw in BANNED_KEYWORDS)

FAL_MODELS = {
    # Легке редагування — зберігає оригінал
    "xai":          {"id": "fal-ai/xai/edit",                     "needs_image": True,  "multi": False, "input_key": "image_urls"},
    # Покращення якості та деталізації, підтримує кілька фото
    "nana":         {"id": "fal-ai/nano-banana-2/edit",           "needs_image": True,  "multi": True,  "input_key": "image_urls"},
    # Творча переробка, стиль, атмосфера
    "seedream_edit":{"id": "fal-ai/flux-2-pro/edit",             "needs_image": True,  "multi": False, "input_key": "image_urls"},
    # GPT Image — редагування з кількома референсами
    "gpt_edit":     {"id": "fal-ai/gpt-image-1.5/edit",          "needs_image": True,  "multi": True,  "input_key": "image_urls"},
    # Flux 2 Pro — редагування
    "flux_edit":    {"id": "fal-ai/flux-2-pro/edit",             "needs_image": True,  "multi": False, "input_key": "image_urls"},
    # Upscale якості
    "upscale":      {"id": "fal-ai/topaz/upscale/image",         "needs_image": True,  "multi": False, "input_key": "image_url"},
    # Видалення фону
    "remove_bg":    {"id": "fal-ai/bria/background/remove",      "needs_image": True,  "multi": False, "input_key": "image_url"},
    # Генерація з нуля (без вхідного фото)
    "seedream_gen": {"id": "fal-ai/flux-2-pro",                  "needs_image": False, "multi": False, "input_key": None},
}
