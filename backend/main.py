import os
import json
import hashlib
import time
import asyncpg
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from anthropic import Anthropic, AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()

# ── Firebase Admin Init ──────────────────────────────────────────────────────
_firebase_app = None

def _get_firebase():
    global _firebase_app
    if _firebase_app is None:
        cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if cred_json:
            cred = credentials.Certificate(json.loads(cred_json))
        else:
            cred = credentials.ApplicationDefault()
        _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app

# ── Кеш в памяти (TTL 24 часа) ──────────────────────────────────────────────
_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 60 * 60 * 24

def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < CACHE_TTL:
        return entry[0]
    if entry:
        del _cache[key]
    return None

def _cache_set(key: str, value: dict) -> None:
    if len(_cache) >= 1000:
        oldest = min(_cache.items(), key=lambda x: x[1][1])
        del _cache[oldest[0]]
    _cache[key] = (value, time.time())

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Дуэт API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
async_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")
FREE_LIMIT = 10  # подборок для Free-пользователя

# ── База данных ───────────────────────────────────────────────────────────────
_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"), min_size=2, max_size=10)
        async with _pool.acquire() as conn:
            with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
                await conn.execute(f.read())
    return _pool

@app.on_event("startup")
async def startup():
    _get_firebase()
    await get_pool()

@app.on_event("shutdown")
async def shutdown():
    if _pool:
        await _pool.close()

# ── Firebase Auth helper ──────────────────────────────────────────────────────
async def verify_token(request: Request) -> dict:
    """Возвращает {'uid': ..., 'email': ...} или поднимает 401."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        decoded = firebase_auth.verify_id_token(token)
        return {"uid": decoded["uid"], "email": decoded.get("email", "")}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid auth token")

async def get_or_create_user(pool: asyncpg.Pool, uid: str, email: str) -> asyncpg.Record:
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE firebase_uid = $1", uid)
        if not user:
            user = await conn.fetchrow(
                "INSERT INTO users (firebase_uid, email) VALUES ($1, $2) RETURNING *",
                uid, email
            )
        return user

# ── Бюджет и промпт ──────────────────────────────────────────────────────────
BUDGET_MAP = {
    "budget":  "бюджетный сегмент: вино до $12, крепкий алкоголь до $20, пиво до $5. Рекомендуй доступные массовые бренды с хорошим соотношением цены и качества.",
    "medium":  "средний сегмент: вино $15-40, виски/коньяк $30-60, пиво $5-12. Рекомендуй качественные бренды среднего ценового диапазона.",
    "premium": "ПРЕМИУМ сегмент: вино от $50 (Bordeaux, Barolo, Napa Cabernet, Burgundy), виски от $80 (Single Malt 12+ лет, Scotch, Japanese), коньяк от $100 (XO, VSOP premium), шампанское от $70 (Vintage, Prestige Cuvée). Рекомендуй ТОЛЬКО элитные и культовые бренды — никаких массовых вин дешевле $50. Price_range ДОЛЖЕН быть соответствующим ($50-120 для вина, $80-200 для виски).",
}

REGION_AVAILABILITY = {
    "Казахстан": "Рекомендуй бренды широко доступные в Алматы и Астане. Избегай редких импортных брендов с ограниченной дистрибуцией в Казахстане.",
    "Россия":    "Рекомендуй бренды широко доступные в крупных городах РФ (Москва, СПб). Учитывай текущие ограничения импорта.",
    "Украина":   "Рекомендуй бренды доступные в Киеве и крупных городах Украины.",
    "Беларусь":  "Рекомендуй бренды доступные в Минске и крупных городах Беларуси.",
    "СНГ":       "Рекомендуй универсальные бренды присутствующие во всех странах СНГ.",
}

class PairRequest(BaseModel):
    dish: str
    mode: str = "food_to_alcohol"
    budget: str = "medium"
    region: str = "СНГ"

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

def _build_prompt(req: PairRequest) -> str:
    budget_desc = BUDGET_MAP[req.budget]
    availability = REGION_AVAILABILITY.get(req.region, REGION_AVAILABILITY["СНГ"])

    if req.mode == "food_to_alcohol":
        return f"""Ты эксперт-сомелье. Пользователь из региона {req.region}.
Блюдо: {req.dish}
Бюджет: {budget_desc}

Доступность: {availability}

Подбери ТОП-3 напитка разных типов (вино, виски, пиво и т.д.) если возможно.
Порядок: первым ставь напиток наиболее традиционный для данной кухни и блюда (к японской еде — саке/пиво, к стейку — красное вино, к морепродуктам — белое/игристое). Не сортируй по категории алкоголя.
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"тип","alcohol_type_emoji":"🍷","name":"название","brand":"конкретная марка доступная в {req.region}","reason":"2 предложения почему подходит к блюду","price_range":"$X-Y","serving_tip":"как подавать"}}]}}"""
    else:
        return f"""Ты эксперт-сомелье. Пользователь из региона {req.region}.
Напиток: {req.dish}
Бюджет на еду: {budget_desc}

Подбери ТОП-3 блюда/закуски к этому напитку.
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"категория блюда","alcohol_type_emoji":"🍽️","name":"название блюда","brand":"вариант/где попробовать","reason":"2 предложения почему подходит","price_range":"~$X","serving_tip":"как подавать"}}]}}"""

# ── Эндпоинты ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok", "cache_size": len(_cache)}

@app.post("/pair/stream")
async def pair_stream(request: Request, req: PairRequest):
    user_info = await verify_token(request)
    uid = user_info["uid"]
    pool = await get_pool()
    user = await get_or_create_user(pool, uid, user_info["email"])

    # Проверка лимита для Free-пользователей
    if not user["is_premium"] and user["pairing_count"] >= FREE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Достигнут лимит {FREE_LIMIT} подборок. Перейдите на Premium для безлимитного доступа."
        )

    cache_key = hashlib.md5(
        f"{req.dish.lower()}|{req.mode}|{req.budget}|{req.region}".encode()
    ).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        # Даже для кешированного ответа — сохраняем в историю и увеличиваем счётчик
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET pairing_count = pairing_count + 1 WHERE firebase_uid = $1",
                uid
            )
            await conn.execute(
                """INSERT INTO pairings (firebase_uid, dish, mode, budget, region, results)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                uid, req.dish, req.mode, req.budget, req.region,
                json.dumps(cached["results"])
            )

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

        try:
            raw = accumulated.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            results = data["results"][:3]
            result = {
                "dish": req.dish,
                "mode": req.mode,
                "budget": req.budget,
                "region": req.region,
                "results": results,
            }
            _cache_set(cache_key, result)

            # Сохраняем в историю + увеличиваем счётчик
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET pairing_count = pairing_count + 1 WHERE firebase_uid = $1",
                    uid
                )
                await conn.execute(
                    """INSERT INTO pairings (firebase_uid, dish, mode, budget, region, results)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    uid, req.dish, req.mode, req.budget, req.region,
                    json.dumps(results)
                )
        except Exception:
            pass

    return StreamingResponse(generate(), media_type="text/plain")

# ── История ───────────────────────────────────────────────────────────────────

@app.get("/history")
async def get_history(request: Request, limit: int = 50):
    user_info = await verify_token(request)
    pool = await get_pool()
    user = await get_or_create_user(pool, user_info["uid"], user_info["email"])

    days = 30 if user["is_premium"] else 7
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT dish, mode, budget, region, results, created_at
               FROM pairings
               WHERE firebase_uid = $1
                 AND created_at > NOW() - INTERVAL '1 day' * $2
               ORDER BY created_at DESC
               LIMIT $3""",
            user_info["uid"], days, limit
        )

    return [
        {
            "dish": r["dish"],
            "mode": r["mode"],
            "budget": r["budget"],
            "region": r["region"],
            "results": json.loads(r["results"]),
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]

# ── Избранное ─────────────────────────────────────────────────────────────────

@app.get("/favorites")
async def get_favorites(request: Request):
    user_info = await verify_token(request)
    pool = await get_pool()
    await get_or_create_user(pool, user_info["uid"], user_info["email"])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, dish, mode, budget, region, results, created_at
               FROM favorites
               WHERE firebase_uid = $1
               ORDER BY created_at DESC""",
            user_info["uid"]
        )
    return [
        {
            "id": r["id"],
            "dish": r["dish"],
            "mode": r["mode"],
            "budget": r["budget"],
            "region": r["region"],
            "results": json.loads(r["results"]),
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]

class FavoriteRequest(BaseModel):
    dish: str
    mode: str
    budget: str
    region: str
    results: list

@app.post("/favorites")
async def add_favorite(request: Request, body: FavoriteRequest):
    user_info = await verify_token(request)
    pool = await get_pool()
    user = await get_or_create_user(pool, user_info["uid"], user_info["email"])

    # Лимит 10 для Free
    if not user["is_premium"]:
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM favorites WHERE firebase_uid = $1",
                user_info["uid"]
            )
        if count >= 10:
            raise HTTPException(
                status_code=429,
                detail="Лимит 10 избранных для Free. Перейдите на Premium."
            )

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO favorites (firebase_uid, dish, mode, budget, region, results)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (firebase_uid, dish, budget) DO NOTHING
                   RETURNING id""",
                user_info["uid"], body.dish, body.mode, body.budget, body.region,
                json.dumps(body.results)
            )
            return {"saved": row is not None}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.delete("/favorites/{favorite_id}")
async def remove_favorite(favorite_id: int, request: Request):
    user_info = await verify_token(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM favorites WHERE id = $1 AND firebase_uid = $2",
            favorite_id, user_info["uid"]
        )
    deleted = result.split()[-1] != "0"
    return {"deleted": deleted}

# ── Профиль / Использование ───────────────────────────────────────────────────

@app.get("/me")
async def get_me(request: Request):
    user_info = await verify_token(request)
    pool = await get_pool()
    user = await get_or_create_user(pool, user_info["uid"], user_info["email"])
    return {
        "uid": user["firebase_uid"],
        "email": user["email"],
        "is_premium": user["is_premium"],
        "pairing_count": user["pairing_count"],
        "pairing_limit": None if user["is_premium"] else FREE_LIMIT,
        "pairings_left": None if user["is_premium"] else max(0, FREE_LIMIT - user["pairing_count"]),
    }
