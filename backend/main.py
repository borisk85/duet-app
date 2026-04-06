import os
import json
import hashlib
import time
import asyncio
import psycopg2
import psycopg2.pool
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from anthropic import Anthropic, AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()

# ── Firebase Admin ────────────────────────────────────────────────────────────
_firebase_app = None

def _init_firebase():
    global _firebase_app
    if _firebase_app is not None:
        return
    cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if cred_json:
        cred = credentials.Certificate(json.loads(cred_json))
    else:
        cred = credentials.ApplicationDefault()
    _firebase_app = firebase_admin.initialize_app(cred)

# ── Кеш ──────────────────────────────────────────────────────────────────────
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
async_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")
FREE_LIMIT = 10

# ── БД (psycopg2 thread pool) ─────────────────────────────────────────────────
_pool: psycopg2.pool.ThreadedConnectionPool | None = None

def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(2, 10, os.getenv("DATABASE_URL"))
        _init_schema()
    return _pool

def _init_schema():
    pool = _pool
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            with open(schema_path) as f:
                cur.execute(f.read())
        conn.commit()
    finally:
        pool.putconn(conn)

@app.on_event("startup")
def startup():
    _init_firebase()
    get_pool()

@app.on_event("shutdown")
def shutdown():
    if _pool:
        _pool.closeall()

# ── Firebase Auth ─────────────────────────────────────────────────────────────
def _verify_token_sync(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        decoded = firebase_auth.verify_id_token(token)
        return {"uid": decoded["uid"], "email": decoded.get("email", "")}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid auth token")

def _get_or_create_user(conn, uid: str, email: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE firebase_uid = %s", (uid,))
        row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        cur.execute(
            "INSERT INTO users (firebase_uid, email) VALUES (%s, %s) RETURNING *",
            (uid, email)
        )
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        conn.commit()
        return dict(zip(cols, row))

# ── Промпт ────────────────────────────────────────────────────────────────────
BUDGET_MAP = {
    "budget":  "бюджетный сегмент: вино до $12, крепкий алкоголь до $20, пиво до $5. Рекомендуй доступные массовые бренды с хорошим соотношением цены и качества.",
    "medium":  "средний сегмент: вино $15-40, виски/коньяк $30-60, пиво $5-12. Рекомендуй качественные бренды среднего ценового диапазона.",
    "premium": "ПРЕМИУМ сегмент: вино от $50 (Bordeaux, Barolo, Napa Cabernet, Burgundy), виски от $80 (Single Malt 12+ лет, Scotch, Japanese), коньяк от $100 (XO, VSOP premium), шампанское от $70 (Vintage, Prestige Cuvée). Рекомендуй ТОЛЬКО элитные и культовые бренды — никаких массовых вин дешевле $50. Price_range ДОЛЖЕН быть соответствующим ($50-120 для вина, $80-200 для виски).",
}

REGION_AVAILABILITY = {
    "Казахстан": "Рекомендуй бренды широко доступные в Алматы и Астане.",
    "Россия":    "Рекомендуй бренды широко доступные в крупных городах РФ. Учитывай текущие ограничения импорта.",
    "Украина":   "Рекомендуй бренды доступные в Киеве и крупных городах Украины.",
    "Беларусь":  "Рекомендуй бренды доступные в Минске и крупных городах Беларуси.",
    "СНГ":       "Рекомендуй универсальные бренды присутствующие во всех странах СНГ.",
}

DETAIL_LEVEL_MAP = {
    "simple":   "РЕЖИМ ПРОСТО: краткое объяснение для новичка. Reason — 1 короткое предложение простыми словами без терминов. Serving_tip — 1 короткая фраза (например 'охладить до 12°C'). Никакого специализированного жаргона.",
    "standard": "РЕЖИМ СТАНДАРТ: сбалансированное объяснение. Reason — 2 предложения почему сочетается. Serving_tip — практичный совет по подаче (температура, посуда, с чем подавать).",
    "expert":   "РЕЖИМ ЭКСПЕРТ: профессиональное описание для знатока. Reason — 2-3 предложения с указанием сорта винограда/региона/выдержки/вкусового профиля и обоснованием парности через танины/кислотность/умами/жирность. Serving_tip — точная температура подачи в °C, тип бокала/посуды, рекомендации по декантации/времени дыхания если применимо.",
}

class PairRequest(BaseModel):
    dish: str
    mode: str = "food_to_alcohol"
    budget: str = "medium"
    region: str = "СНГ"
    detail_level: str = "standard"

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

    @field_validator("detail_level")
    @classmethod
    def validate_detail_level(cls, v: str) -> str:
        if v not in ("simple", "standard", "expert"):
            raise ValueError("Неверный режим детализации")
        return v

def _build_prompt(req: PairRequest) -> str:
    budget_desc = BUDGET_MAP[req.budget]
    availability = REGION_AVAILABILITY.get(req.region, REGION_AVAILABILITY["СНГ"])
    detail_desc = DETAIL_LEVEL_MAP[req.detail_level]
    if req.mode == "food_to_alcohol":
        return f"""Ты эксперт-сомелье. Пользователь из региона {req.region}.
Блюдо: {req.dish}
Бюджет: {budget_desc}
Доступность: {availability}
{detail_desc}
Подбери ТОП-3 напитка разных типов. Первым ставь напиток наиболее традиционный для данной кухни.
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"тип","alcohol_type_emoji":"🍷","name":"название","brand":"конкретная марка доступная в {req.region}","reason":"объяснение в соответствии с режимом детализации","price_range":"$X-Y","serving_tip":"совет по подаче в соответствии с режимом детализации"}}]}}"""
    else:
        return f"""Ты эксперт-сомелье. Пользователь из региона {req.region}.
Напиток: {req.dish}
{detail_desc}
Подбери ТОП-3 блюда/закуски к этому напитку.
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"категория блюда","alcohol_type_emoji":"🍽️","name":"название блюда","brand":"вариант/где попробовать","reason":"объяснение в соответствии с режимом детализации","price_range":"~$X","serving_tip":"совет по подаче в соответствии с режимом детализации"}}]}}"""

# ── Эндпоинты ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "cache_size": len(_cache)}

@app.post("/pair/stream")
async def pair_stream(request: Request, req: PairRequest):
    user_info = _verify_token_sync(request)
    uid = user_info["uid"]

    pool = get_pool()
    conn = pool.getconn()
    try:
        user = _get_or_create_user(conn, uid, user_info["email"])
        if not user["is_premium"] and user["pairing_count"] >= FREE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Достигнут лимит {FREE_LIMIT} подборок. Перейдите на Premium."
            )
    finally:
        pool.putconn(conn)

    cache_key = hashlib.md5(
        f"{req.dish.lower()}|{req.mode}|{req.budget}|{req.region}|{req.detail_level}".encode()
    ).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        def _save_cached():
            c = pool.getconn()
            try:
                with c.cursor() as cur:
                    cur.execute("UPDATE users SET pairing_count = pairing_count + 1 WHERE firebase_uid = %s", (uid,))
                    cur.execute(
                        "INSERT INTO pairings (firebase_uid, dish, mode, budget, region, results) VALUES (%s,%s,%s,%s,%s,%s)",
                        (uid, req.dish, req.mode, req.budget, req.region, json.dumps(cached["results"]))
                    )
                c.commit()
            finally:
                pool.putconn(c)
        asyncio.get_event_loop().run_in_executor(None, _save_cached)

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
            result = {"dish": req.dish, "mode": req.mode, "budget": req.budget, "region": req.region, "results": results}
            _cache_set(cache_key, result)

            def _save():
                c = pool.getconn()
                try:
                    with c.cursor() as cur:
                        cur.execute("UPDATE users SET pairing_count = pairing_count + 1 WHERE firebase_uid = %s", (uid,))
                        cur.execute(
                            "INSERT INTO pairings (firebase_uid, dish, mode, budget, region, results) VALUES (%s,%s,%s,%s,%s,%s)",
                            (uid, req.dish, req.mode, req.budget, req.region, json.dumps(results))
                        )
                    c.commit()
                finally:
                    pool.putconn(c)
            asyncio.get_event_loop().run_in_executor(None, _save)
        except Exception:
            pass

    return StreamingResponse(generate(), media_type="text/plain")

@app.get("/history")
def get_history(request: Request):
    user_info = _verify_token_sync(request)
    pool = get_pool()
    conn = pool.getconn()
    try:
        user = _get_or_create_user(conn, user_info["uid"], user_info["email"])
        days = 30 if user["is_premium"] else 7
        with conn.cursor() as cur:
            cur.execute(
                """SELECT dish, mode, budget, region, results, created_at
                   FROM pairings WHERE firebase_uid = %s AND created_at > NOW() - INTERVAL '%s days'
                   ORDER BY created_at DESC LIMIT 50""",
                (user_info["uid"], days)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        # JSONB поле psycopg2 уже декодирует в Python list/dict — json.loads не нужен
        return [dict(zip(cols, r)) | {"results": r[4], "created_at": r[5].isoformat()} for r in rows]
    finally:
        pool.putconn(conn)

@app.get("/favorites")
def get_favorites(request: Request):
    user_info = _verify_token_sync(request)
    pool = get_pool()
    conn = pool.getconn()
    try:
        _get_or_create_user(conn, user_info["uid"], user_info["email"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, dish, mode, budget, region, results, created_at FROM favorites WHERE firebase_uid = %s ORDER BY created_at DESC",
                (user_info["uid"],)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) | {"results": r[5], "created_at": r[6].isoformat()} for r in rows]
    finally:
        pool.putconn(conn)

class FavoriteRequest(BaseModel):
    dish: str
    mode: str
    budget: str
    region: str
    results: list

@app.post("/favorites")
def add_favorite(request: Request, body: FavoriteRequest):
    user_info = _verify_token_sync(request)
    pool = get_pool()
    conn = pool.getconn()
    try:
        user = _get_or_create_user(conn, user_info["uid"], user_info["email"])
        if not user["is_premium"]:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM favorites WHERE firebase_uid = %s", (user_info["uid"],))
                count = cur.fetchone()[0]
            if count >= 10:
                raise HTTPException(status_code=429, detail="Лимит 10 избранных для Free. Перейдите на Premium.")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO favorites (firebase_uid, dish, mode, budget, region, results)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (firebase_uid, dish, budget) DO NOTHING RETURNING id""",
                (user_info["uid"], body.dish, body.mode, body.budget, body.region, json.dumps(body.results))
            )
            row = cur.fetchone()
        conn.commit()
        return {"saved": row is not None}
    finally:
        pool.putconn(conn)

@app.delete("/favorites/{favorite_id}")
def remove_favorite(favorite_id: int, request: Request):
    user_info = _verify_token_sync(request)
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM favorites WHERE id = %s AND firebase_uid = %s", (favorite_id, user_info["uid"]))
        conn.commit()
        return {"deleted": True}
    finally:
        pool.putconn(conn)

@app.get("/me")
def get_me(request: Request):
    user_info = _verify_token_sync(request)
    pool = get_pool()
    conn = pool.getconn()
    try:
        user = _get_or_create_user(conn, user_info["uid"], user_info["email"])
        return {
            "uid": user["firebase_uid"],
            "email": user["email"],
            "is_premium": user["is_premium"],
            "pairing_count": user["pairing_count"],
            "pairing_limit": None if user["is_premium"] else FREE_LIMIT,
            "pairings_left": None if user["is_premium"] else max(0, FREE_LIMIT - user["pairing_count"]),
        }
    finally:
        pool.putconn(conn)
