import os
import json
import hashlib
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, field_validator
from anthropic import Anthropic, AsyncAnthropic
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

load_dotenv()

# ── Кеш в памяти (TTL 24 часа) ──────────────────────────────────────────────
_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 60 * 60 * 24  # 24 часа

def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < CACHE_TTL:
        return entry[0]
    if entry:
        del _cache[key]
    return None

def _cache_set(key: str, value: dict) -> None:
    # Не даём кешу разрастись — max 1000 записей
    if len(_cache) >= 1000:
        oldest = min(_cache.items(), key=lambda x: x[1][1])
        del _cache[oldest[0]]
    _cache[key] = (value, time.time())

# ── Rate limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Дуэт API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
async_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")

BUDGET_MAP = {
    "budget":  "бюджетный сегмент (минимальная стоимость для данного типа напитка)",
    "medium":  "средний ценовой сегмент",
    "premium": "премиум сегмент",
}

# ── Модели ───────────────────────────────────────────────────────────────────
class PairRequest(BaseModel):
    dish: str
    mode: str = "food_to_alcohol"
    budget: str = "medium"
    region: str = "СНГ"
    is_premium: bool = False  # платный пользователь — без жёсткого rate limit

    @field_validator("dish")
    @classmethod
    def validate_dish(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Укажите блюдо или напиток")
        if len(v) > 500:
            raise ValueError("Слишком длинное описание (макс. 500 символов)")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("food_to_alcohol", "alcohol_to_food"):
            raise ValueError("Неверный режим")
        return v

    @field_validator("budget")
    @classmethod
    def validate_budget(cls, v: str) -> str:
        if v not in ("budget", "medium", "premium"):
            raise ValueError("Неверный бюджет")
        return v

# ── Построение промпта ───────────────────────────────────────────────────────
def _build_prompt(req: PairRequest) -> str:
    budget_desc = BUDGET_MAP[req.budget]
    if req.mode == "food_to_alcohol":
        return f"""Ты эксперт-сомелье. Пользователь из региона {req.region}.
Блюдо: {req.dish}
Бюджет: {budget_desc}

Подбери ТОП-3 напитка. Каждый разного типа (вино, виски, пиво и т.д.) если возможно.
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"тип","alcohol_type_emoji":"🍷","name":"название","brand":"марка доступная в {req.region}","reason":"2 предложения почему подходит","price_range":"$X-Y","serving_tip":"как подавать"}}]}}"""
    else:
        return f"""Ты эксперт-сомелье. Пользователь из региона {req.region}.
Напиток: {req.dish}
Бюджет на еду: {budget_desc}

Подбери ТОП-3 блюда/закуски к этому напитку.
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"категория блюда","alcohol_type_emoji":"🍽️","name":"название блюда","brand":"вариант/где заказать","reason":"2 предложения почему подходит","price_range":"~$X","serving_tip":"как подавать"}}]}}"""

# ── Эндпоинты ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "cache_size": len(_cache)}

@app.post("/pair")
@limiter.limit("10/day")
def pair(request: Request, req: PairRequest):
    # Платные пользователи не ограничены rate limit'ом
    # (лимит выше всё равно применится, но для premium можно поднять отдельно)

    # Кеш-ключ: хеш от блюда + режим + бюджет + регион
    cache_key = hashlib.md5(
        f"{req.dish.lower()}|{req.mode}|{req.budget}|{req.region}".encode()
    ).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        return {**cached, "from_cache": True}

    prompt = _build_prompt(req)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )

        # Убираем markdown-обёртку если модель всё же добавила ```json```
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()

        data = json.loads(raw)
        result = {
            "dish": req.dish,
            "mode": req.mode,
            "budget": req.budget,
            "region": req.region,
            "results": data["results"][:3],
            "from_cache": False,
        }
        _cache_set(cache_key, result)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Ошибка обработки ответа AI")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pair/stream")
@limiter.limit("10/day")
async def pair_stream(request: Request, req: PairRequest):
    cache_key = hashlib.md5(
        f"{req.dish.lower()}|{req.mode}|{req.budget}|{req.region}".encode()
    ).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        async def from_cache():
            yield json.dumps(cached)
        return StreamingResponse(from_cache(), media_type="text/plain")

    prompt = _build_prompt(req)

    async def generate():
        accumulated = ""
        try:
            async with async_client.messages.stream(
                model=MODEL,
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    accumulated += text
                    yield text
        except Exception as e:
            yield json.dumps({"error": str(e)})
            return

        # Кешируем после завершения стрима
        try:
            raw = accumulated.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            result = {
                "dish": req.dish,
                "mode": req.mode,
                "budget": req.budget,
                "region": req.region,
                "results": data["results"][:3],
            }
            _cache_set(cache_key, result)
        except Exception:
            pass

    return StreamingResponse(generate(), media_type="text/plain")
