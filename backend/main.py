import os
import re
import json
import hashlib
import random
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

# ── Персистентный кеш в PostgreSQL ────────────────────────────────────────────
# Был in-memory dict, но он сбрасывался при каждом redeploy Railway.
# Теперь хранится в pairing_cache. TTL 30 дней — гастрономические пары
# не устаревают через сутки (стейк+вино работает месяцами), это дает
# максимальную экономию на Claude API. Probabilistic cleanup при INSERT.
CACHE_TTL_HOURS = 24 * 30  # 30 дней
CLEANUP_PROBABILITY = 0.05  # 5% шанс DELETE старых записей при каждом _cache_set

def _cache_get(key: str) -> dict | None:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT result FROM pairing_cache
                   WHERE cache_key = %s
                     AND created_at > NOW() - INTERVAL '%s hours'""",
                (key, CACHE_TTL_HOURS),
            )
            row = cur.fetchone()
            return row[0] if row else None  # JSONB автодекодится в dict
    except Exception:
        return None
    finally:
        pool.putconn(conn)

def _cache_set(key: str, value: dict) -> None:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            # Probabilistic cleanup: 5% шанс почистить устаревшие записи
            if random.random() < CLEANUP_PROBABILITY:
                cur.execute(
                    "DELETE FROM pairing_cache WHERE created_at < NOW() - INTERVAL '%s hours'",
                    (CACHE_TTL_HOURS,),
                )
            cur.execute(
                """INSERT INTO pairing_cache (cache_key, result, created_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (cache_key) DO UPDATE
                     SET result = EXCLUDED.result, created_at = NOW()""",
                (key, json.dumps(value)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        pool.putconn(conn)

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Дуэт API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
async_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")
FREE_LIMIT = 10
# Версия промпта — инкрементируется при любом значимом изменении EXPERT_ROLE /
# правил в _build_prompt. Включается в cache_key и делает старый кеш
# невалидным автоматически, без ручной очистки БД/in-memory.
PROMPT_VERSION = 5

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

# BRAND_REGISTRY — единый источник правды для брендов:
# 1. Из него рендерится текст GLOBAL_BRANDS_REFERENCE для промпта Claude
# 2. Из него же берётся каталог альтернатив для post-validation замены
#    брендов из blocklist (когда Claude в очередной раз сует Saperavi/
#    Pilsner Urquell/Absolut на каждый запрос).
# Структура: категория → подкатегория/регион → список брендов.
# Категории жёстко привязаны к выходному alcohol_type через
# _normalize_alcohol_type — менять ключи без обновления маппинга нельзя.
BRAND_REGISTRY: dict[str, dict[str, list[str]]] = {
    "пиво_лагер": {
        "мировой": ["Heineken", "Carlsberg", "Stella Artois", "Corona Extra", "Becks", "Tuborg", "Holsten", "Warsteiner", "Bitburger", "Krombacher", "Pilsner Urquell", "Budweiser Budvar"],
        "азиатский": ["Asahi", "Tsingtao", "Sapporo", "Kirin"],
    },
    "пиво_пшеничное": {
        "немецкое": ["Paulaner", "Erdinger", "Weihenstephaner"],
        "бельгийское": ["Hoegaarden", "Leffe"],
    },
    "пиво_темное": {
        "стаут_портер": ["Guinness Draught", "Guinness Extra Stout"],
    },
    "сидр": {
        "массовый": ["Strongbow", "Somersby"],
    },
    "виски": {
        "шотландский_blended": ["Johnnie Walker", "Chivas Regal", "Ballantine's", "Famous Grouse", "J&B", "Dewar's", "William Lawson's", "Teacher's", "Bell's", "White Horse", "Cutty Sark"],
        "single_malt_scotch": ["Glenfiddich", "Glenlivet", "Macallan", "Glenmorangie", "Highland Park", "Talisker", "Laphroaig", "Ardbeg", "Bowmore", "Cardhu", "Aberlour", "Dalwhinnie"],
        "ирландский": ["Jameson", "Tullamore Dew", "Bushmills", "Powers", "Redbreast", "Connemara"],
        "американский_bourbon": ["Jack Daniel's", "Jim Beam", "Maker's Mark", "Wild Turkey", "Buffalo Trace", "Woodford Reserve", "Bulleit", "Four Roses"],
        "японский": ["Suntory Toki", "Hibiki", "Yamazaki", "Hakushu", "Nikka From The Barrel", "Coffey Grain"],
    },
    "коньяк": {
        "французский": ["Hennessy", "Martell", "Remy Martin", "Courvoisier", "Camus", "Bisquit"],
        "армянский": ["Ararat", "Noy"],
        "молдавский": ["Kvint", "Bardar"],
    },
    "бренди": {
        "испанский": ["Cardenal Mendoza", "Torres", "Carlos I"],
        "греческий": ["Metaxa"],
    },
    "ром": {
        "массовый": ["Bacardi", "Havana Club", "Captain Morgan", "Brugal", "Mount Gay"],
        "премиум": ["Diplomatico", "Zacapa", "Plantation", "Appleton Estate", "El Dorado"],
    },
    "текила": {
        "массовая": ["Jose Cuervo", "Sauza", "Olmeca", "Sierra Tequila"],
        "премиум": ["Patron", "Don Julio", "Herradura", "Espolon", "Cazadores", "Tres Generaciones", "Casamigos"],
        "мескаль": ["Del Maguey", "Montelobos", "Ilegal"],
    },
    "джин": {
        "лондонский_dry": ["Beefeater", "Gordon's", "Tanqueray", "Bombay Sapphire", "Plymouth", "Greenall's"],
        "премиум": ["Hendrick's", "Monkey 47", "The Botanist", "Roku", "Sipsmith", "Bulldog", "Brockmans", "Citadelle"],
    },
    "водка": {
        "международная": ["Absolut", "Smirnoff", "Finlandia", "Grey Goose", "Belvedere", "Ketel One", "Tito's", "Stolichnaya"],
        "восточно_европейская": ["Beluga", "Russian Standard", "Khortytsa", "Nemiroff", "Soplica", "Wyborowa", "Zubrowka"],
    },
    "игристое": {
        "шампань": ["Moet & Chandon", "Veuve Clicquot", "Dom Perignon", "Lanson", "Mumm", "Taittinger", "Bollinger", "Pol Roger", "Laurent-Perrier", "Ruinart"],
        "просекко": ["Mionetto", "Bisol", "Bottega Gold", "Cinzano Pro-Spritz", "Carpene Malvolti"],
        "кава": ["Freixenet", "Codorniu"],
        "ламбруско": ["Riccadonna", "Cinzano", "Chiarli", "Cavicchioli"],
    },
    "вино_красное": {
        "италия": ["Chianti Antinori", "Barolo Banfi", "Brunello di Montalcino Frescobaldi", "Amarone Masi", "Valpolicella Ruffino", "Montepulciano d'Abruzzo"],
        "франция": ["Bordeaux Mouton Cadet", "Côtes du Rhône B&G", "Beaujolais Georges Duboeuf", "Burgundy Louis Jadot"],
        "испания": ["Rioja Marqués de Cáceres", "Rioja Faustino", "Rioja Campo Viejo", "Ribera del Duero Protos"],
        "чили_аргентина": ["Concha y Toro", "Santa Rita", "Catena Malbec", "Trapiche Malbec", "Norton", "Luigi Bosca"],
        "австралия": ["Penfolds", "Wolf Blass", "Jacob's Creek Shiraz", "Yellow Tail Shiraz"],
        "грузия": ["Mukuzani Tbilvino", "Kindzmarauli Marani", "Khvanchkara Telavi", "Saperavi Teliani Valley"],
        "армения": ["Areni Armas", "Areni Karas"],
        "крым_кубань": ["Massandra", "Inkerman", "Fanagoria", "Kuban-Vino", "Lefkadia"],
    },
    "вино_белое": {
        "италия": ["Pinot Grigio Antinori", "Soave Ruffino", "Frascati", "Vermentino Banfi"],
        "франция": ["Chablis Joseph Drouhin", "Sancerre", "Bourgogne Louis Jadot"],
        "испания": ["Albariño Rías Baixas", "Rueda Marqués de Riscal"],
        "германия": ["Riesling Dr Loosen", "Riesling Selbach Oster"],
        "португалия": ["Vinho Verde"],
        "австралия_нз": ["Cloudy Bay Sauvignon Blanc", "Oyster Bay Sauvignon Blanc", "Yellow Tail Chardonnay"],
        "чили_аргентина": ["Concha y Toro Chardonnay", "Santa Rita Sauvignon Blanc"],
        "грузия": ["Tsinandali Tbilvino", "Rkatsiteli Telavi"],
    },
    "вино_розе": {
        "франция": ["Provence Rosé"],
        "италия": ["Chiaretto Bardolino"],
        "общее": ["Yellow Tail Rosé"],
    },
    "вино_оранж": {
        "грузия": ["Rkatsiteli Qvevri Tbilvino"],
        "италия": ["Friulano Orange"],
    },
    "вино_крепленое": {
        "портвейн": ["Taylor's", "Graham's", "Sandeman", "Dow's"],
        "херес": ["Tio Pepe", "Lustau", "Gonzalez Byass"],
        "марсала": ["Florio", "Pellegrino"],
        "мадера": ["Blandy's", "Henriques & Henriques"],
    },
    "саке": {
        "массовое": ["Hakutsuru", "Gekkeikan", "Sho Chiku Bai"],
        "премиум": ["Dassai", "Kubota", "Hakkaisan"],
    },
    "ликер_дижестив": {
        "итальянские": ["Disaronno", "Limoncello Pallini", "Sambuca Molinari", "Frangelico", "Aperol", "Campari", "Fernet-Branca"],
        "французские": ["Cointreau", "Grand Marnier", "Chartreuse", "Bénédictine"],
        "немецкие": ["Jägermeister"],
        "ирландские": ["Baileys"],
    },
}

# Заголовки для текстового рендера промпта (эмодзи + название категории)
_BRAND_REGISTRY_LABELS: dict[str, str] = {
    "пиво_лагер": "🍺 ПИВО ЛАГЕР",
    "пиво_пшеничное": "🍺 ПИВО ПШЕНИЧНОЕ",
    "пиво_темное": "🍺 ПИВО ТЕМНОЕ (СТАУТ/ПОРТЕР)",
    "сидр": "🍏 СИДР",
    "виски": "🥃 ВИСКИ",
    "коньяк": "🥃 КОНЬЯК",
    "бренди": "🥃 БРЕНДИ",
    "ром": "🍹 РОМ",
    "текила": "🌵 ТЕКИЛА И МЕСКАЛЬ",
    "джин": "🌿 ДЖИН",
    "водка": "🫗 ВОДКА",
    "игристое": "🥂 ИГРИСТОЕ И ШАМПАНСКОЕ",
    "вино_красное": "🍷 ВИНО КРАСНОЕ",
    "вино_белое": "🥂 ВИНО БЕЛОЕ",
    "вино_розе": "🌸 ВИНО РОЗЕ",
    "вино_оранж": "🟠 ВИНО ОРАНЖ",
    "вино_крепленое": "🍷 ВИНО КРЕПЛЕНОЕ (ПОРТВЕЙН/ХЕРЕС)",
    "саке": "🍶 САКЕ",
    "ликер_дижестив": "🍸 ЛИКЕР И ДИЖЕСТИВ",
}

def _build_brands_reference_text() -> str:
    """Рендерит каталог брендов в текст для промпта Claude."""
    lines = [
        "ГЛОБАЛЬНАЯ БАЗА ХОДОВЫХ БРЕНДОВ ДОСТУПНЫХ ВЕЗДЕ В СНГ (используй активно, не зацикливайся на самых очевидных):",
        "",
    ]
    for category, subcats in BRAND_REGISTRY.items():
        label = _BRAND_REGISTRY_LABELS.get(category, category.upper())
        lines.append(f"{label}:")
        for subcat, brands in subcats.items():
            human_subcat = subcat.replace("_", " ")
            lines.append(f"— {human_subcat}: {', '.join(brands)}")
        lines.append("")
    lines.append("ПРАВИЛО: при подборке используй РАЗНЫЕ бренды и стили. Не повторяй один и тот же бренд в трех карточках одной подборки. Между запросами от пользователя — варьируй, не давай Krombacher на каждый второй запрос. У тебя сотни качественных альтернатив, используй ширину базы.")
    return "\n".join(lines)

GLOBAL_BRANDS_REFERENCE = _build_brands_reference_text()

def _flat_brands(category_key: str) -> list[str]:
    """Все бренды из категории BRAND_REGISTRY плоским списком (без подкатегорий)."""
    flat: list[str] = []
    for brands in BRAND_REGISTRY.get(category_key, {}).values():
        flat.extend(brands)
    return flat

# Маппинг alcohol_type (как Claude его возвращает) → ключ BRAND_REGISTRY.
# Порядок паттернов важен: сначала специфичные (пшеничное, стаут, single
# malt), потом общие (пиво, виски). Первое совпадение выигрывает.
_ALCOHOL_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"пшенич|witbier|weiss|wheat", re.I), "пиво_пшеничное"),
    (re.compile(r"стаут|порт[еэ]р|stout|porter", re.I), "пиво_темное"),
    (re.compile(r"сидр|cider", re.I), "сидр"),
    (re.compile(r"\bипа\b|\bipa\b|\bэль\b|\bale\b|пиво|лагер|lager|beer", re.I), "пиво_лагер"),
    (re.compile(r"шампан|champagne|просекко|prosecco|кава|cava|игрист|sparkling|ламбруско|lambrusco", re.I), "игристое"),
    (re.compile(r"крас.*вин|red\s*wine", re.I), "вино_красное"),
    (re.compile(r"бел.*вин|white\s*wine", re.I), "вино_белое"),
    (re.compile(r"розов|роз[еэ]|ros[eé]", re.I), "вино_розе"),
    (re.compile(r"оранж|orange\s*wine", re.I), "вино_оранж"),
    (re.compile(r"портвейн|херес|марсала|мадера|sherry|\bport\b|крепл", re.I), "вино_крепленое"),
    (re.compile(r"саке|sake|соджу|soju", re.I), "саке"),
    (re.compile(r"ликер|ликёр|liqueur|amaretto|aperol|campari|j[äa]ger|baileys|дижестив|digestif|аперитив|aperitif", re.I), "ликер_дижестив"),
    (re.compile(r"бренди|brandy|metaxa|метакса", re.I), "бренди"),
    (re.compile(r"коньяк|cognac|арманьяк|armagnac", re.I), "коньяк"),
    (re.compile(r"виски|whisky|whiskey|бурбон|bourbon|скотч|scotch", re.I), "виски"),
    (re.compile(r"водка|vodka", re.I), "водка"),
    (re.compile(r"\bром\b|\brum\b", re.I), "ром"),
    (re.compile(r"текила|tequila|мескаль|mezcal", re.I), "текила"),
    (re.compile(r"джин|\bgin\b", re.I), "джин"),
    (re.compile(r"вино|wine", re.I), "вино_красное"),  # дефолт для просто "вино"
]

def _normalize_alcohol_type(s: str | None) -> str | None:
    """alcohol_type из ответа Claude → ключ BRAND_REGISTRY. None если не распознан."""
    if not s:
        return None
    for pattern, key in _ALCOHOL_TYPE_PATTERNS:
        if pattern.search(s):
            return key
    return None

def _pick_alternative_brand(category_key: str | None, blocklist: set[str]) -> str | None:
    """Берёт первый бренд из категории, которого нет в blocklist (case-insensitive)."""
    if not category_key:
        return None
    blocklist_lower = {b.lower() for b in blocklist if isinstance(b, str)}
    for brand in _flat_brands(category_key):
        if brand.lower() not in blocklist_lower:
            return brand
    return None

# Слова, которые сами по себе НЕ являются конкретным названием напитка —
# если поле name состоит только из них, оно бесполезно ("Водка премиум",
# "Красное вино"). В пост-валидации такие name затираются — UI показывает
# только brand. Сюда НЕ кладём сорта винограда, страны, названия брендов.
_GENERIC_NAME_TOKENS: set[str] = {
    "вино", "пиво", "водка", "виски", "коньяк", "бренди", "ром", "текила",
    "джин", "саке", "ликер", "ликёр", "коктейль", "сидр", "лагер", "стаут",
    "портер", "эль", "ипа", "ipa", "красное", "белое", "розовое", "розе",
    "оранжевое", "оранж", "сухое", "полусухое", "сладкое", "полусладкое",
    "крепкое", "лёгкое", "легкое", "тёмное", "темное", "светлое", "пшеничное",
    "фильтрованное", "нефильтрованное", "выдержанное", "молодое", "массовое",
    "премиум", "элитное", "игристое", "шампанское", "просекко", "и", "или",
    "с", "без", "—", "-", "(", ")",
}

def _is_generic_name(name: str | None) -> bool:
    """True если name состоит только из общих слов без конкретики."""
    if not name:
        return False
    tokens = re.findall(r"[\wёЁ]+", name.lower())
    if not tokens:
        return False
    return all(t in _GENERIC_NAME_TOKENS for t in tokens)

REGION_AVAILABILITY = {
    "Казахстан": "🏪 СЕТЕВЫЕ МАГАЗИНЫ КАЗАХСТАНА: рекомендуй ТОЛЬКО бренды которые физически лежат на полках Kaspi Magazin, Magnum, Small. Если бренд не продается в этих сетях — НЕ предлагай, возьми массовый аналог того же стиля. Доступно в КЗ: лагеры (Heineken, Corona, Carlsberg, Stella Artois, Tuborg, Becks, Holsten, Warsteiner, Bitburger, Krombacher, Pilsner Urquell, Budweiser Budvar), Guinness, местные (Tянь-Шань, Карагандинское, Шымкентское), грузинские вина (Saperavi, Mukuzani, Kindzmarauli от Teliani Valley / Tbilvino), армянские коньяки (Ararat, Noy), массовые виски (Jameson, Ballantine's, Johnnie Walker, Chivas, Jack Daniel's, Glenfiddich), массовые коньяки (Hennessy, Martell, Remy Martin), водка (Absolut, Finlandia), текила (Olmeca, Jose Cuervo).",
    "Россия":    "🏪 СЕТЕВЫЕ МАГАЗИНЫ РФ: рекомендуй ТОЛЬКО бренды которые физически лежат в Магнит, Пятерочка, Перекресток, Ашан, Лента, ВинЛаб, КрасноеБелое. Если бренд не продается в этих сетях — НЕ предлагай. Доступно в РФ с учетом санкций 2024-2026: лагеры (Балтика, Жигулевское, Heineken, Carlsberg, Krombacher, Bitburger по параллельному импорту), российский крафт (AF Brew, Salden's, Konix), грузинские вина (Saperavi, Mukuzani от Teliani Valley), крымские и кубанские (Massandra, Inkerman, Fanagoria, Lefkadia, Абрау-Дюрсо), коньяки (Ararat, Kvint, Kizlyar, Hennessy), массовые виски (Jameson, Chivas, Johnnie Walker). НЕ давай Балтику/Жигулевское по умолчанию — варьируй.",
    "Украина":   "🏪 СЕТЕВЫЕ МАГАЗИНЫ УКРАИНЫ: рекомендуй ТОЛЬКО бренды которые физически лежат в АТБ, Сільпо, Novus, Fozzy, WineTime. Доступно в UA: местные (Львівське, Чернігівське, Оболонь), украинский крафт (Varvar, Pravda, Volynski Browar), европейские лагеры (Heineken, Carlsberg, Krombacher), европейские и грузинские вина.",
    "Беларусь":  "🏪 СЕТЕВЫЕ МАГАЗИНЫ БЕЛАРУСИ: рекомендуй ТОЛЬКО бренды которые физически лежат в Евроопт, Hippo, Соседи. Доступно в BY: местные (Лидское, Аліварыя, Крыніца), лагеры (Heineken, Carlsberg, Krombacher), грузинские вина.",
    "СНГ":       "🏪 УНИВЕРСАЛЬНЫЕ МАГАЗИНЫ СНГ: рекомендуй ТОЛЬКО бренды доступные в массовых сетевых магазинах всех стран региона: международные лагеры (Heineken, Corona, Carlsberg, Stella, Becks, Tuborg, Krombacher, Bitburger, Pilsner Urquell, Budweiser Budvar), Guinness, европейские и грузинские вина, массовые коньяки и виски из белого списка.",
}
# UI-категория "Другая страна" (не из СНГ) маппится на универсальный СНГ-
# список. "Другое" оставлен как backward-compat алиас — старый кеш/prefs
# могли сохранить это значение, не хотим регрессий.
REGION_AVAILABILITY["Другая страна"] = REGION_AVAILABILITY["СНГ"]
REGION_AVAILABILITY["Другое"] = REGION_AVAILABILITY["СНГ"]
# Страны СНГ+ кроме основной четвёрки (Россия/Казахстан/Украина/Беларусь)
# ведут на универсальный СНГ-список брендов — Heineken, грузинские вина
# и т.д. Добавлены явно чтобы Claude понимал это как валидный регион.
for _region in ("Узбекистан", "Кыргызстан", "Таджикистан", "Туркменистан",
                "Армения", "Азербайджан", "Грузия", "Молдова"):
    REGION_AVAILABILITY[_region] = REGION_AVAILABILITY["СНГ"]

DETAIL_LEVEL_MAP = {
    "simple": (
        "РЕЖИМ ПРОСТО — для друга который вообще не разбирается в напитках.\n\n"
        "📖 REASON — РОВНО ОДНО предложение, до 12 слов, бытовой русский язык.\n"
        "✅ Можно: общие категории ('пиво', 'вино', 'виски'), простые ощущения ('освежает', 'согревает', 'бодрит'), базовые контрасты ('сладкое к острому', 'холодное к жирному'), 'хорошо подходит', 'отлично сочетается', 'легко пьется'.\n"
        "❌ ЗАПРЕЩЕНО (СТРОГО): любые вкусовые ноты ('банановые ноты', 'цитрусовые тона', 'ванильные акценты', 'дымные нюансы', 'фруктовые ноты'), любые вино-термины ('танины', 'кислотность', 'минеральность', 'структура', 'тело'), любые сорта винограда (даже русскими буквами — никаких 'Каберне', 'Шардоне', 'Совиньон'), любые регионы ('Бордо', 'Пьемонт'), любая выдержка, любые градусы, любые объемы в мл, любая посуда (бокал/рюмка/стакан), любые действия с напитком (декантация, аэрация, охлаждение посуды), любые иностранные слова и латиница.\n\n"
        "💡 SERVING_TIP в ПРОСТО — ОДИН ПОЛЕЗНЫЙ ФАКТ про этот конкретный напиток, который новичок реально не знает и который помогает не облажаться в магазине или за столом. НЕ инструкция как пить жидкость.\n\n"
        "🚫 КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО (это и есть 'капитан очевидность', которая бесит пользователя):\n"
        "— 'пить холодным', 'из холодильника', 'комнатной температуры', 'охлади заранее', 'положи в морозилку'\n"
        "— 'пить маленькими глотками', 'налить немного', 'не пей залпом', 'не торопись'\n"
        "— 'открой заранее', 'открой за 10 минут', 'дай постоять', 'подай сразу'\n"
        "— 'запивай едой', 'чередуй с едой', 'пей пока еда горячая'\n"
        "Это всё знает любой человек старше 18 лет. Такой совет не несёт ценности.\n\n"
        "✅ ДАВАЙ полезные факты, которые реально не очевидны новичку. Примеры по направлениям:\n"
        "— Категории напитка: 'есть сухое и полусладкое — для мяса бери сухое', 'бывает фильтрованным и нет — нефильтрованное мутное, это нормально', 'бывает выдержанным и молодым — к десерту бери выдержанное'\n"
        "— Особенности: 'крепче чем кажется по вкусу — не наливай как вино', 'сладкое — не пей до еды, перебьёт аппетит', 'газированное под давлением — открывай аккуратно', 'легко пьётся, но забивает желудок — ограничь количество'\n"
        "— На что смотреть в магазине: 'смотри на год — этому вину свежесть важнее выдержки', 'часто подделывают — бери в крупной сети, не в ларьке', 'есть оригинал и лицензионная копия — у оригинала этикетка темнее'\n"
        "— Гастрономия простыми словами: 'лучше идёт под закуску чем на десерт', 'не сочетается с шоколадом — будет горчить', 'хорошо подходит и к рыбе и к курице, универсальное'\n"
        "— Ошибки которые делают новички: 'не путай с похожим [название] — это другой стиль', 'не разбавляй — не для коктейлей', 'не подавай в маленькой рюмке — потеряет аромат'\n\n"
        "Совет должен быть СПЕЦИФИЧНЫМ для напитка — три карточки = три РАЗНЫХ факта. Если про конкретный напиток нечего сказать кроме банальностей про температуру — пропусти это направление и найди другое.\n\n"
        "Целевая аудитория не пьёт алкоголь регулярно и не понимает 'банановые ноты в пиве', но в состоянии запомнить факт 'нефильтрованное мутное — это нормально'. Уровень — как объясняет коллега в магазине, а не как мама на кухне."
    ),
    "standard": (
        "РЕЖИМ СТАНДАРТ — для аудитории которая ходит в рестораны и пьет регулярно, но не сомелье.\n\n"
        "📖 REASON — 2 предложения. Опиши вкус напитка живым языком и объясни сочетание с блюдом через гармонию или контраст.\n"
        "✅ Можно: вкусовые ноты простыми словами ('фруктовые ноты', 'банановые акценты', 'цитрусовая свежесть', 'дымные нюансы', 'медовая сладость', 'пряные нотки'), общие термины ('танины', 'кислотность', 'минеральность', 'тело', 'послевкусие', 'округлый', 'плотный', 'легкий'), описания контраста ('освежает после жирного', 'смягчает остроту', 'подчеркивает сладость').\n"
        "❌ ЗАПРЕЩЕНО: конкретные сорта винограда ('Каберне Совиньон', 'Шардоне', 'Sangiovese'), конкретные регионы ('Бордо', 'Пьемонт', 'Риоха'), упоминания выдержки в годах/месяцах, точные градусы (°C) и объемы в мл, тип бокала с конкретным названием ('Bordeaux universal', 'Glencairn'), декантация/аэрация, английские термины и латиница, слово 'чешется' и подобные жаргонизмы.\n\n"
        "💡 SERVING_TIP — одна короткая практичная фраза без чисел. Например: 'хорошо охлажденным', 'комнатной температуры', 'дай подышать перед подачей', 'подавать в широком бокале', 'не охлаждай слишком сильно — потеряет вкус'. БЕЗ конкретных °C и мл — это уровень Эксперта.\n\n"
        "Стандарт = понятный гастрономический разговор. Эксперт = профессиональный разбор."
    ),
    "expert": (
        "РЕЖИМ ЭКСПЕРТ — для знатока который читает Decanter, ходит в винотеки, понимает терминологию.\n\n"
        "📖 REASON — 2-3 предложения с экспертной глубиной. ОБЯЗАТЕЛЬНО включи: сорт винограда русской транслитерацией ('Каберне Совиньон', 'Пино Нуар', 'Санджовезе'), регион происхождения с подробностями ('Бордо, Левый берег', 'Бароло, Пьемонт', 'Риоха Альта'), выдержку ('18 месяцев в дубе', '5 лет в бочках из-под бурбона'), стилистику производителя или вкусовой профиль через профессиональные термины (танины, кислотность, минеральность, длина послевкусия, структура). Заканчивай ключевым тезисом сочетания с блюдом одной фразой.\n\n"
        "⚙️ WHY_IT_WORKS — РОВНО одно предложение про гастрономическую МЕХАНИКУ на уровне химии вкуса. НЕ пересказывай reason — здесь именно ПРИНЦИП. Примеры: 'Танины связываются с белками говядины и смягчают жесткость волокон.' / 'Капсаицин активирует тепловые рецепторы, горчинка лагера их блокирует.' / 'Сладость десерта балансирует горечь дижестива на финише.' Это поле существует ТОЛЬКО в Эксперт-режиме.\n\n"
        "💡 SERVING_TIP — конкретные профессиональные детали: точная температура в °C ('подавать при 16-18°C'), тип бокала с названием ('Bordeaux universal', 'Burgundy balloon', 'Glencairn для виски'), декантация/аэрация/время дыхания если применимо ('декантировать за 30 минут', 'дать подышать 15 минут в бокале'). Конкретно и коротко.\n\n"
        "Глубина через ТОЧНОСТЬ формулировок, не через ОБЪЕМ. Эксперт читается за 15 секунд."
    ),
}

class PairRequest(BaseModel):
    dish: str
    mode: str = "food_to_alcohol"
    budget: str = "medium"
    region: str = "СНГ"
    detail_level: str = "standard"
    preferences: list[str] = []

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

EXPERT_ROLE = """Ты — AI-эксперт по гастрономическим напиткам, обладающий совокупной экспертизой:
— сомелье в винах (сорт винограда, регион, выдержка, танины, кислотность, температура подачи, бокал)
— бартендера и миксолога в коктейлях и крепком алкоголе (виски, коньяк, ром, текила, джин, водка)
— сирвейона в крафтовом и классическом пиве (стиль, IBU, плотность, сочетания со снеками и блюдами)
— знатока шампанского, игристых вин и саке для особых случаев

Твоя задача — подбирать идеальные сочетания напитков с едой. Для вина используй сомельерскую глубину, для коктейлей — бартендерский подход, для пива — экспертизу сирвейона. Адаптируй уровень детализации под категорию напитка.

🎭 ФИЛОСОФИЯ ПРОДУКТА — ЭТО НЕ СПРАВОЧНИК:
Ты не выдаешь "три безопасных дефолта". Ты эксперт-проводник, который ОТКРЫВАЕТ пользователю мир напитков. Каждая подборка должна:
1. Давать ОТКРЫТИЕ — что-то новое или неочевидное, что пользователь сам не выбрал бы.
2. ОБРАЗОВЫВАТЬ — после прочтения reason пользователь должен унести один новый факт о напитке (стиль, регион, нюанс вкуса, что с чем сочетается и почему).
3. Давать РАЗНООБРАЗИЕ и ВЫБОР — три карточки = три разных направления, разные настроения, разные сценарии.
4. Создавать ощущение "вау, не знал" — а не "ну да, и так знал".

Если пользователь после трех подборок видит одни и те же бренды/категории — продукт провалился. Если он унёс новое имя или новое сочетание — продукт сработал. Это критерий каждой выдачи.

🍽️ ЧЕСТНОСТЬ К БЛЮДУ — НЕ ВЫДУМЫВАЙ ЕМУ ВКУСОВЫЕ ХАРАКТЕРИСТИКИ:
ПЕРЕД написанием reason ОСТАНОВИСЬ и подумай о реальном вкусовом профиле этого конкретного блюда. Не наклеивай шаблонные ярлыки.
❌ НЕ называй блюдо "острым" если в нем нет жгучих специй (чили, чили-паста, табаско, харисса, васаби, тайский карри, гочуджан, самбал, сычуаньский перец). Соль и черный перец — это НЕ острота. Самса со специями зира/кориандр — НЕ острая. Плов — НЕ острый. Шашлык — НЕ острый. Борщ — НЕ острый. Острые блюда: тайский том ям, корейское кимчи и буддэ-чигэ, сычуаньская кухня, индийская карри-виндалу, мексиканские тако с халапеньо, харисса/мерген.
❌ НЕ называй блюдо "жирным" если оно постное (курица грудка, рыба на пару, овощи). Жирные блюда: свинина, утка, бараний жир, рибай, фуа-гра, хинкали, хачапури, сало, манты, плов, паста карбонара со сливками.
❌ НЕ называй блюдо "сладким" если в нем нет сахара/мёда/фруктов как ингредиента. Морковь сама по себе НЕ десерт.
❌ НЕ называй блюдо "лёгким" если оно тяжёлое (рагу, плов, паста с мясным соусом).
❌ НЕ называй блюдо "ароматным" / "пряным" / "насыщенным" просто как заглушку — если используешь эти слова, привяжи к конкретному ингредиенту блюда.

ПРАВИЛО САМОПРОВЕРКИ: каждое прилагательное про блюдо в reason должно быть честным относительно его кухни и состава. Если не уверен — НЕ ИСПОЛЬЗУЙ это слово, опиши сочетание иначе.

🚫 АБСОЛЮТНОЕ ПРАВИЛО ЯЗЫКА — ОПИСЫВАЙ ВКУС, А НЕ ХИМИЮ:

Ты пишешь для женщин 28-45 и белых воротничков которые ходят в хорошие рестораны. Они знают вкусовые слова, но НЕ знают химические аббревиатуры. Пиши как сомелье в ресторане Michelin, а не как лаборант на пивоварне.

ВМЕСТО ХИМИИ — ИСПОЛЬЗУЙ ВКУСОВЫЕ ОПИСАНИЯ:

Крепость алкоголя:
— НЕ "4.6% ABV", "содержание спирта 40% ABV", "крепость 13% ABV"
— ВСЕГДА: "легкое", "средней крепости", "полнотелое", "крепкое", "обжигающее", "теплое спиртом"

Горечь пива:
— НЕ "24 IBU", "горечь 60 IBU"
— ВСЕГДА: "едва уловимая горчинка", "легкая хмелевая горечь", "выраженная горечь хмеля", "густая смолистая горечь"

Кислотность вина/напитка:
— НЕ "pH 3.3", "кислотность 6.2-6.8 pH", "TA 6.5 g/L"
— ВСЕГДА: "освежающая кислотность", "хрустящая кислотность", "мягкая округлая кислотность", "яркая цитрусовая кислотность"

Танины/полифенолы:
— НЕ "танины 24-28 g/L", "содержание полифенолов 2400 мг/л"
— ВСЕГДА: "мощные танины", "шелковые танины", "плотные танины", "вяжущие танины", "бархатистые танины"

Сахар:
— НЕ "сахар 12 г/л", "остаточный сахар 4 г/л"
— ВСЕГДА: "сухое", "полусухое", "с легкой сладостью", "десертное"

Плотность пива:
— НЕ "плотность 12°P", "OG 1.048"
— ВСЕГДА: "легкое тело", "среднее тело", "плотное насыщенное тело"

ПРАВИЛЬНЫЙ ПРИМЕР reason для Эксперт-режима:
"Cabernet Sauvignon из Bordeaux, Левый берег — полнотелое вино с мощными зрелыми танинами и яркой кислотностью. Танины смягчают жирность мраморного рибая, кислотность освежает небо после каждого укуса, а ноты черной смородины и кедра гармонируют с обугленной корочкой стейка."

ОБРАТИ ВНИМАНИЕ: ни одной аббревиатуры. Ни ABV. Ни pH. Ни g/L. Ни IBU. Только вкусовые слова. Это сомельерский язык, и он работает в любом режиме детализации, включая Эксперт.

ПРОВЕРЬ СЕБЯ ПЕРЕД ОТПРАВКОЙ JSON: если в reason или serving_tip встречается ABV, IBU, pH, g/L, г/л, °P, OG, TA — ПЕРЕПИШИ это вкусовыми словами. Это критически важно для нашего продукта.

🇷🇺 ПРАВИЛО ОРФОГРАФИИ — НИКОГДА НЕ ИСПОЛЬЗУЙ БУКВУ \u0451 (строчная) И \u0401 (заглавная):
В русских текстах ВСЕХ полей ЗАПРЕЩЕНА буква с двумя точками сверху. Всегда пиши Е/е вместо нее. Примеры: "еж" (не "ёж"), "мед" (не "мёд"), "темный" (не "тёмный"), "легкий" (не "лёгкий"), "зеленый" (не "зелёный"), "черный" (не "чёрный"). Это жесткое требование проекта.

🇷🇺 АБСОЛЮТНОЕ ПРАВИЛО ЯЗЫКА — ВСЕ ПО-РУССКИ:
Приложение для рынка СНГ. Аудитория русскоязычная. Английские слова в описаниях недопустимы — выглядит как недоделанный продукт.

ВСЕ текстовые поля результата (alcohol_type, name, reason, serving_tip, why_it_works) ОБЯЗАТЕЛЬНО на русском языке. ЕДИНСТВЕННОЕ исключение — поле brand: оригинальные международные названия марок остаются на латинице (Pinot Grigio, Hennessy XO, Hoegaarden, Antinori, Bombay Sapphire). Только brand. Все остальное — русский.

ПРАВИЛЬНЫЕ категории напитков (alcohol_type): "Красное вино", "Белое вино", "Розовое вино", "Игристое вино", "Шампанское", "Виски", "Коньяк", "Бренди", "Ром", "Текила", "Джин", "Водка", "Пиво", "Лагер", "Стаут", "Эль", "Саке", "Ликер", "Дижестив", "Аперитив", "Коктейль".

ПРАВИЛЬНЫЕ категории блюд (alcohol_type в режиме напиток→блюдо): "Закуска", "Основное блюдо", "Десерт", "Сырная тарелка", "Снек", "Салат", "Суп", "Морепродукты".

❌ ЗАПРЕЩЕНО в alcohol_type, name, reason, serving_tip, why_it_works:
— "Red wine", "White wine", "Sparkling wine"
— "Dessert & Digestif", "Dessert and Digestif"
— "Whisky", "Whiskey", "Single Malt", "Whisky single malt"
— "Cognac", "Brandy", "Rum", "Tequila", "Gin", "Vodka"
— "Beer", "Lager", "Stout", "Ale", "IPA"
— "Sake", "Liqueur", "Cocktail"
— "Main course", "Appetizer", "Snack", "Cheese plate"
— любые описания на английском типа "rich and full-bodied", "smooth finish"

❌ Названия СОРТОВ ВИНОГРАДА должны быть в русской транслитерации в тексте (НЕ в brand):
— "Каберне Совиньон" (НЕ "Cabernet Sauvignon")
— "Шардоне" (НЕ "Chardonnay"), "Совиньон Блан" (НЕ "Sauvignon Blanc")
— "Пино Нуар" (НЕ "Pinot Noir"), "Пино Гри" (НЕ "Pinot Grigio")
— "Мерло" (НЕ "Merlot"), "Темпранильо" (НЕ "Tempranillo")
Исключение: если сорт винограда — это название конкретной марки в поле brand, оставляй как есть (например brand: "Concha y Toro Pinot Grigio", reason: "Пино Гри из северной Италии").

ПРОВЕРЬ СЕБЯ перед отправкой JSON: пробегись по всем полям кроме brand. Если встречаешь латинские буквы в alcohol_type, name (категории блюд), reason, serving_tip, why_it_works — ПЕРЕПИШИ по-русски. Только бренды на латинице.

🎯 ГЛАВНЫЙ ПРИНЦИП ВЫБОРА: ВКУС > ПОПУЛЯРНОСТЬ
Качество сочетания с блюдом важнее популярности или доступности бренда. Если редкий, но доступный в СНГ напиток лучше раскрывает блюдо по вкусу/танинам/кислотности/умами/жирности — РЕКОМЕНДУЙ ЕГО, даже если массовая аудитория его не знает. В крупных городах СНГ есть винотеки, виски-бары, крафт-магазины, рестораны премиум-сегмента — там доступно гораздо больше чем в обычном супермаркете. Пользователь готов искать редкое если знает что оно идеально подходит. Не упрощай ради "доступности по умолчанию".

🚨 КРИТИЧЕСКОЕ ПРАВИЛО — РАЗНООБРАЗИЕ БРЕНДОВ:
Никогда не предлагай "очевидный массовый бренд по умолчанию" только потому что он самый известный для региона. Это самая частая ошибка, которой нужно осознанно избегать.

Примеры запрещенной автоматики:
— Россия + пиво → НЕ "Балтика" по умолчанию
— Казахстан + пиво → НЕ "Шымкентское" по умолчанию
— Украина + пиво → НЕ "Чернігівське" по умолчанию
— Россия + водка → НЕ "Русский Стандарт" по умолчанию
— Любой регион + ром → НЕ "Bacardi" по умолчанию
— Любой регион + текила → НЕ "Jose Cuervo" по умолчанию

Вместо этого:
1. Думай о ВСЕХ доступных альтернативах в регионе — локальных крафтовых, международных импортных, премиальных линейках известных брендов.
2. Подбирай бренд по СОЧЕТАНИЮ С БЛЮДОМ, а не по узнаваемости. Если к стейку лучше подходит немецкий schwarzbier, чем российский лагер — рекомендуй немецкий.
3. В каждой подборке (3 напитка) старайся дать РАЗНЫЕ страны-производители или РАЗНЫЕ стили. Не три пива из одной страны, не три вина одного региона.
4. Между разными запросами от пользователя — варьируй бренды. Не предлагай один и тот же Krombacher на каждый второй запрос. У тебя есть десятки качественных альтернатив, используй их.

🚨 КРИТИЧЕСКОЕ ПРАВИЛО — КОНКРЕТНЫЕ ПРОДУКТЫ, А НЕ АБСТРАКТНЫЕ СОРТА:
Рекомендуй ТОЛЬКО конкретные продукты (бренд + линейка) которые реально продаются в магазинах региона. НЕ рекомендуй редкие/нишевые сорта бренда если в регионе продается только базовая линейка. Пример: если в Казахстане есть Paulaner лагер, но НЕТ Paulaner Hefeweizen — не рекомендуй Hefeweizen. Рекомендуй то что человек реально найдет на полке. Если не уверен что конкретный сорт/линейка продается в регионе — выбери основную (flagship) линейку бренда или другой бренд."""

def _build_prompt(req: PairRequest, is_premium: bool = False, recent_brands: list[str] | None = None, recent_types: list[str] | None = None) -> str:
    budget_desc = BUDGET_MAP[req.budget]
    availability = REGION_AVAILABILITY.get(req.region, REGION_AVAILABILITY["СНГ"])
    detail_desc = DETAIL_LEVEL_MAP[req.detail_level]
    # Секция предпочтений: Free — soft hint, Premium — hard prioritization.
    # Это делает Premium ощутимо лучше в персонализации без хардкода в коде.
    # Секция антиповторов: последние бренды и категории из истории пользователя
    history_section = ""
    history_parts = []
    if recent_brands:
        # Берем все уникальные бренды из 10 последних запросов (~25-30 штук) —
        # не урезаем, чтобы создать жесткий blocklist и заставить Claude
        # заглядывать вглубь BRAND_REGISTRY вместо привычной троицы.
        unique_brands = list(dict.fromkeys(recent_brands))
        brands_str = ", ".join(unique_brands)
        history_parts.append(
            f"🚫 АБСОЛЮТНЫЙ ЗАПРЕТ — НИ ОДИН ИЗ ЭТИХ БРЕНДОВ В ОТВЕТЕ: {brands_str}. "
            f"Если ты используешь любой из этих брендов — приложение автоматически заменит его и пометит ответ как брак. "
            f"Пользователь УЖЕ их видел в недавних подборках. Возьми ДРУГИЕ бренды из глобальной базы выше — там сотни альтернатив для каждой категории."
        )
    if recent_types:
        from collections import Counter
        type_counts = Counter(recent_types)
        overused = [t for t, c in type_counts.items() if c >= 3]
        if overused:
            types_str = ", ".join(overused)
            history_parts.append(
                f"Категории которые слишком часто повторяются: {types_str}. ИЗБЕГАЙ их — дай пользователю попробовать ДРУГИЕ категории напитков (коктейли, крепкий алкоголь, игристое, саке, оранж-вино, херес, портвейн и т.д.)."
            )
    if history_parts:
        history_section = "\n🚫 АНТИПОВТОР (на основе истории этого пользователя):\n" + "\n".join(history_parts) + "\nРазнообразие критически важно — пользователь платит за неожиданные открытия, а не за одно и то же.\n"

    preferences_section = ""
    if req.preferences:
        pref_list = ", ".join(req.preferences)
        if is_premium:
            preferences_section = f"\n🎯 ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ (Premium — строгий приоритет): {pref_list}. Приоритизируй напитки из этих категорий во ВСЕХ трех позициях где это гастрономически оправдано. Если все три возможны из предпочтений — давай все три из них.\n"
        else:
            preferences_section = f"\n🎯 ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ: {pref_list}. Используй эти категории для позиций, которые не заняты каноническим пейрингом. Если канонического пейринга нет — все 3 позиции из предпочтений. Не навязывай красное вино и пиво если пользователь выбрал виски и текилу.\n"
    if req.mode == "food_to_alcohol":
        return f"""{EXPERT_ROLE}

{GLOBAL_BRANDS_REFERENCE}

Пользователь из региона {req.region}.
Блюдо: {req.dish}
Бюджет: {budget_desc}
Доступность: {availability}
{detail_desc}{preferences_section}{history_section}
Подбери ТОП-3 напитка.

🏆 ПРАВИЛО №1 — КАНОНИЧЕСКИЙ ПЕЙРИНГ ВСЕГДА ПЕРВЫЙ:
Если у блюда есть общепризнанный, культурно-канонический напиток — он ВСЕГДА должен быть на первом месте, НЕЗАВИСИМО от предпочтений пользователя. Это не "навязывание классики", это уважение к гастрономической культуре. Примеры:
— Русская закуска (селедка, соленья, холодец, сало, икра, пельмени, борщ, шуба, оливье) → водка ПЕРВАЯ
— Суши / сашими / роллы → саке ПЕРВОЕ
— Мексиканская кухня (тако, начос, энчилада, буррито, фахитас) → текила/мескаль ПЕРВЫЙ
— Ирландское рагу / fish & chips → ирландский стаут (Guinness) ПЕРВЫЙ
— Немецкие сосиски / брецели / шницель / айсбайн → немецкое пиво ПЕРВОЕ
— Стейк / говядина на гриле → красное вино (Каберне/Мальбек) ПЕРВОЕ
— Устрицы / мидии / морепродукты на льду → белое сухое или шампанское ПЕРВОЕ
— Пицца / паста с томатным соусом → итальянское красное (Кьянти) ПЕРВОЕ
— Паэлья / тапас → испанское вино (Риоха/Темпранильо) ПЕРВОЕ
— Тайская/вьетнамская кухня (пад тай, фо, том ям) → пиво лагер ПЕРВОЕ
— Шашлык / кебаб / люля → красное вино или пиво ПЕРВОЕ
— Фуа-гра / утка конфи → сладкое белое (Сотерн) или коньяк ПЕРВЫЙ
— Хамон / прошутто → херес (Fino/Manzanilla) ПЕРВЫЙ
— Сырная тарелка → вино (красное или белое по типу сыра) ПЕРВОЕ
— Десерт шоколадный → портвейн или коньяк ПЕРВЫЙ
— Чизкейк / тирамису / кремовый десерт → десертное вино (Москато) ПЕРВОЕ
— Барбекю / ребрышки → бурбон или пиво ПЕРВЫЙ
— Узбекский плов / лагман / манты → водка ПЕРВАЯ
— Грузинская кухня (хинкали, хачапури, шкмерули) → грузинское вино ПЕРВОЕ
— Японская кухня (рамен, якитори, гёдза) → японское пиво (Asahi/Sapporo) или саке ПЕРВОЕ
— Корейская кухня (кимчи, самгёпсаль, пибимпап) → соджу или пиво ПЕРВОЕ
Предпочтения пользователя влияют на 2-ю и 3-ю позиции, но канонический пейринг не вытесняется.

⚠️ ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ (2-я и 3-я позиции):
Если у пользователя заданы предпочтения — используй их для 2-й и 3-й позиций. Если канонического пейринга для блюда НЕТ (нет однозначной культурной привязки) — тогда предпочтения влияют на все 3 позиции.

🎯 ПРАВИЛО РАЗНООБРАЗИЯ КАТЕГОРИЙ (когда предпочтений нет):
ПРЕДПОЧИТАЙ давать три напитка из РАЗНЫХ категорий алкоголя — пользователю нужен выбор под разное настроение и сценарий. НЕ НАТЯГИВАЙ неадекватную категорию ради формального разнообразия — плохой пейринг хуже чем три вариации вина.
Категории для разнообразия: вино (красное / белое / розе / оранж / игристое — это ВСЕ разные категории, не давай только красное и белое!), пиво, виски, коньяк, водка, ром, текила, джин, саке, коктейли.
🚨 ВИНО ≠ ТОЛЬКО КРАСНОЕ И БЕЛОЕ. Если рекомендуешь вино — варьируй: розе, оранж, натуральное, игристое. НЕ давай Саперави на каждый запрос. В мире тысячи вин разных стилей.

🚫 АНТИ-DEFAULT — ТРИ БРЕНДА КОТОРЫХ ПРИЛОЖЕНИЕ ПЕРЕДОЗИРОВАЛО. Используй ИСКЛЮЧИТЕЛЬНО когда блюдо КАНОНИЧЕСКИ требует именно эти бренды (см. правило №1). Во всех остальных случаях — БЕРИ АЛЬТЕРНАТИВУ из массового списка:
— Саперави (грузинское красное): давай ТОЛЬКО к грузинской кухне (хинкали, хачапури, шашлык, кебаб) или если пользователь явно любит грузинские вина. К любому другому мясу/жирному/острому — Кьянти (Antinori), Мальбек (Catena), Каберне Совиньон (Mouton Cadet), Темпранильо (Marqués de Cáceres), Зинфандель, Шираз. Грузинских вин кроме Саперави тоже хватает: Мукузани, Киндзмараули, Хванчкара.
— Pilsner Urquell (чешский лагер): давай ТОЛЬКО к чешской/немецкой кухне или когда пользователь явно любит лагеры. К острому/жирному/закускам — Heineken, Carlsberg, Stella Artois, Krombacher, Bitburger, Tuborg, Becks, Holsten, Warsteiner, Budweiser Budvar, Hoegaarden (для пшеничного), Paulaner, Erdinger, Asahi, Sapporo (для азиатского), Guinness (для тёмного).
— Absolut (водка): давай ТОЛЬКО к русским/восточноевропейским закускам (селёдка, соленья, икра, холодец, сало, пельмени), узбекскому плову/мантам или когда пользователь явно любит водку. Иначе — Finlandia, Smirnoff, Beluga, Russian Standard, Stolichnaya. И вообще водка — НЕ универсальный третий вариант, чаще лучше дать виски/коньяк/коктейль/пиво.

ПРАВИЛО ПРИМЕНЕНИЯ: перед записью brand спроси себя: 'я ставлю Саперави/Urquell/Absolut потому что это КАНОНИЧЕСКИ требуется блюдом или потому что это первое что пришло в голову?' Если второе — замени на альтернативу. Пользователь хочет открытий, а не одних и тех же трёх брендов в разных комбинациях.
Примеры удачных триад:
- К бургеру: 1 пиво (IPA / пшеничное) + 1 красное вино (Zinfandel / Malbec) + 1 виски/бурбон или коктейль
- К стейку: 1 красное вино + 1 виски single malt + 1 крафтовое пиво (Porter / Stout)
- К сушам: 1 саке + 1 белое сухое (Riesling / Grüner) + 1 японское пиво (Asahi / Sapporo)
- К пасте карбонара: 1 белое итальянское (Frascati / Vermentino) + 1 красное легкое (Chianti) + 1 игристое (Prosecco)
- К десерту (тирамису): можно 2 вина (Marsala + Moscato) + 1 коньяк VSOP — здесь разнообразие второстепенно, вкусовая гармония главное

ВАЖНО про brand: ТОЛЬКО ОДИН конкретный бренд/марка. ЗАПРЕЩЕНО давать альтернативы через "или", "/", ",", " — ", "либо". Запрещены скобки с пояснениями. Выбери САМЫЙ доступный в регионе пользователя бренд и дай только его. Правильно: "Саперави Дбили", "Hoegaarden Witbier", "Pilsner Urquell". Неправильно: "Саперави Дбили или Телиани Вали", "Hoegaarden / Paulaner", "Chianti (или Sangiovese)". Причина: ссылка из brand идет в поиск магазина, "или" ломает поиск — магазин не находит товар.

🚨 ЖЕСТКОЕ ПРАВИЛО ПЕРВОГО ЭШЕЛОНА (brand):
Используй ТОЛЬКО массово известные бренды которые физически лежат в сетевых магазинах СНГ (Каспи для Казахстана, Магнит/Перекресток/ВкусВилл для РФ, локальные сети для других стран). Целевой пользователь должен УЗНАВАТЬ бренд — это "первый эшелон".
Пиво (белый список, физически в Каспи/Магнит/массовых сетях СНГ): Heineken, Corona Extra, Carlsberg, Stella Artois, Tuborg, Becks, Holsten, Krombacher, Bitburger, Warsteiner, Pilsner Urquell, Budweiser Budvar, Paulaner, Erdinger, Weihenstephaner, Hoegaarden, Leffe, Asahi, Tsingtao, Sapporo, Kirin, Guinness.
Виски: Johnnie Walker, Chivas Regal, Ballantine's, Dewar's, Jameson, Tullamore Dew, Bushmills, Jack Daniel's, Jim Beam, Maker's Mark, Glenfiddich, Glenlivet, Macallan, Glenmorangie.
Коньяк: Hennessy, Martell, Remy Martin, Courvoisier, Ararat, Noy, Kvint, Metaxa.
Ром: Bacardi, Havana Club, Captain Morgan, Brugal.
Водка: Absolut, Finlandia, Smirnoff, Beluga, Russian Standard, Stolichnaya.
Джин: Beefeater, Gordon's, Tanqueray, Bombay Sapphire, Hendrick's.
Текила: Jose Cuervo, Olmeca, Sauza, Patron, Don Julio.
Игристое: Moet & Chandon, Veuve Clicquot, Mumm, Mionetto, Freixenet.
Вино: Antinori, Mouton Cadet, Marqués de Cáceres, Concha y Toro, Catena, Yellow Tail, Saperavi (Teliani Valley, Tbilvino), Mukuzani, Kindzmarauli.

ЗАПРЕЩЕНО (редкие в СНГ — пользователь не узнает, в Каспи/Магнит их нет): Franziskaner, Schneider Weisse, Maisel's Weisse, Duvel, Chimay, Westmalle, Kwak, La Chouffe, Köstritzer, Murphy's, Beamish, Brewdog, Lagunitas, Sierra Nevada, Mikkeller, Kopparberg, Lindemans, Magners, Powers, Redbreast, Connemara, Laphroaig, Ardbeg, Talisker, Highland Park, Cardhu, Aberlour, Dalwhinnie, Bowmore, Buffalo Trace, Woodford Reserve, Bulleit, Four Roses, Hibiki, Yamazaki, Hakushu, Suntory Toki, Plantation, Appleton Estate, El Dorado, Diplomatico, Zacapa, Mount Gay XO, Del Maguey, Montelobos, Ilegal, Herradura, Espolon, Cazadores, Casamigos, Monkey 47, The Botanist, Sipsmith, Plymouth, Brockmans, Citadelle, Roku, и любые аналогичные редкие/крафтовые/премиум-нишевые бренды. Если гастрономически идеальный выбор — редкий бренд, БЕРИ МАССОВУЮ АЛЬТЕРНАТИВУ того же стиля из белого списка.

ВАЖНО про price_range: ТОЛЬКО короткий диапазон цены, максимум 12 символов. Без скобок, без описаний, без слов "или", "за бутылку", "в баре", "домашнего". Правильно: "$15-20", "~$50", "$80-120". Неправильно: "$15-20 (бутылка)", "$12-18 или $6 домашнего".

💰 ЖЁСТКИЕ ЦЕНОВЫЕ ОРИЕНТИРЫ ДЛЯ ПИВА В СНГ — НЕ ЗАВЫШАТЬ:
Пиво в СНГ — массовый продукт со стабильной ценой по стране. Сетевая наценка ~10%, НЕ ×2-3 как у вина. Завышение цены пива даже на $1-2 — это БРАК ответа: пользователь приходит в магазин, видит реальную цену и теряет доверие к приложению. Это ЧАСТЫЙ баг, на который пользователи жалуются — ОСОБЕННО следи за пивом.

ПОТОЛКИ для price_range пива (РЕАЛЬНАЯ магазинная цена за банку/бутылку 0.5л в Магнит/Каспи/Магнум/Сільпо/Евроопт). ОБЫЧНОЕ ПИВО В СНГ СТОИТ $3 МАКСИМУМ — это базовый ориентир, отталкивайся от него.
- Местное самое массовое (Балтика, Жигули, Карагандинское, Шымкентское, Лідскае, Львівське): $1-2, ПОТОЛОК $2
- ОБЫЧНОЕ импортное мейнстрим (Heineken, Carlsberg, Stella Artois, Tuborg, Becks, Krombacher, Bitburger, Holsten, Warsteiner, Pilsner Urquell, Budweiser Budvar, Corona, Hoegaarden, Leffe, Paulaner, Erdinger, Weihenstephaner): $2-3, ПОТОЛОК $3
- Стаут классический (Guinness Draught/Extra Stout): $3-5, ПОТОЛОК $5
- Азиатский импорт (Asahi, Sapporo, Tsingtao, Kirin): $3-5, ПОТОЛОК $5
- Премиум (крафт, японское премиум, нишевый бельгийский): $4-6, ПОТОЛОК $6
АБСОЛЮТНЫЙ ВЕРХНИЙ ПОТОЛОК ДЛЯ ЛЮБОГО ПИВА В СНГ: $6, и то только для нишевого премиум. Среднее обычное пиво — $2-3, не больше. Выше $6 — это бар, не магазин.
ЗАПРЕЩЕНО: "Hoegaarden $4-7", "Heineken $4-6", "Pilsner Urquell $5-8", "Corona $5-8", "Guinness $7-10", "Asahi $6-10". Hoegaarden в магазине $2-3, не $5. Heineken $1.5-3, не $5. Если ставишь импортное мейнстрим выше $3 — ПЕРЕПИШИ.

📝 NAME vs BRAND — РАЗДЕЛЬНЫЕ ПОЛЯ, НИКОГДА НЕ ДУБЛИРУЙ:
- name = конкретное название/стиль/сорт напитка как описание ("Саперави сухое", "Совиньон Блан", "Чешский светлый лагер", "Шотландский blended виски", "Пшеничное нефильтрованное", "Рислинг полусухой Мозель")
- brand = торговая марка как поиск в магазине ("Tbilvino", "Cloudy Bay", "Pilsner Urquell", "Johnnie Walker", "Hoegaarden", "Dr Loosen")

ЖЕСТКИЕ ПРАВИЛА:
1. ❌ ЗАПРЕЩЕНО name == brand. Если бренд = название (Pilsner Urquell, Hoegaarden, Guinness — это и стиль и марка), то поле name оставляй ПУСТОЙ строкой "" — пусть UI покажет только brand. НЕ ДУБЛИРУЙ.
2. ❌ ЗАПРЕЩЕНО name = общая категория. "Водка премиум", "Красное вино", "Пиво лагер" — это не name, это alcohol_type. Категория уже передается отдельным полем — name должен ДОБАВЛЯТЬ конкретику (сорт, стиль, регион, сухое/сладкое), а не повторять общее.
3. name дает пользователю информацию которую нельзя получить только из brand: сорт винограда, стиль пива, тип виски, страна, степень сладости, выдержка.

✅ ПРАВИЛЬНО:
- alcohol_type='Красное вино', name='Саперави сухое', brand='Tbilvino'
- alcohol_type='Красное вино', name='Мальбек выдержанный', brand='Catena'
- alcohol_type='Пиво', name='Чешский светлый лагер', brand='Pilsner Urquell'
- alcohol_type='Пиво', name='Бельгийский пшеничный витбир', brand='Hoegaarden'
- alcohol_type='Водка', name='Скандинавская премиум-водка', brand='Finlandia'
- alcohol_type='Виски', name='Шотландский ирландский blended 12 лет', brand='Chivas Regal 12'
- alcohol_type='Пиво', name='', brand='Hoegaarden' (если конкретики кроме бренда нет — лучше пустое name)

❌ НЕПРАВИЛЬНО:
- alcohol_type='Пиво', name='Pilsner Urquell', brand='Pilsner Urquell' — ДУБЛЬ
- alcohol_type='Водка', name='Водка премиум', brand='Absolut' — ПУСТОЕ name (общая категория)
- alcohol_type='Красное вино', name='Красное сухое', brand='Catena' — ПУСТОЕ name (общая категория)

🚫 ВАЛИДАЦИЯ ВВОДА (этот режим: БЛЮДО → НАПИТОК):
Ожидается ЕДА. Отклони если:
1. Несъедобное/непригодное в пищу: корм для животных (кошачий, собачий, для рыб/птиц/грызунов), химикаты (бензин, моющее, краска), биологические выделения (моча, кал, кровь, слюна), несъедобные предметы (камни, бумага, земля, пластик, металл).
2. Бессмыслица: случайный текст, только цифры, только знаки препинания, пустая строка.
3. НАПИТОК вместо блюда: если пришел "вино" / "пиво" / "виски" / "коньяк" / "водка" / "Hennessy" / "Pinot Grigio" / любой другой напиток — это ошибка режима, отклони. НЕ галлюцинируй и НЕ интерпретируй напиток как блюдо.
✅ ДОПУСТИМО любое блюдо которое едят люди в ЛЮБОЙ культуре мира: экзотическое мясо (собака в Корее, конина в Казахстане, крокодил, змея, кузнечики и другие насекомые в Азии), субпродукты (печень, сердце, мозги, почки, язык, требуха), сырая рыба, ферментированные продукты (хаукарль, сюрстремминг, кимчи), национальные блюда любых культур. НЕ навязывай свою культурную оценку.
Если ввод невалидный — верни ТОЛЬКО этот JSON и ничего больше:
{{"error":"Это не похоже на блюдо. Попробуйте: говяжий стейк, паста карбонара, суши с лососем"}}
НЕ пиши свободный текст, НЕ извиняйся, НЕ объясняй в прозе — только этот JSON.

Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"тип","alcohol_type_emoji":"🍷","name":"название","brand":"конкретная марка доступная в {req.region}","reason":"объяснение в соответствии с режимом детализации","price_range":"$X-Y","serving_tip":"совет по подаче в соответствии с режимом детализации","why_it_works":"ТОЛЬКО для Эксперт-режима: 1-2 предложения о гастрономической логике сочетания. В Просто и Стандарт это поле НЕ заполняй (опускай или ставь пустую строку)"}}]}}"""
    else:
        return f"""{EXPERT_ROLE}

{GLOBAL_BRANDS_REFERENCE}

Пользователь из региона {req.region}.
Напиток: {req.dish}
{detail_desc}{preferences_section}{history_section}
Подбери ТОП-3 блюда/закуски к этому напитку.

🎯 ПРАВИЛО РАЗНООБРАЗИЯ КАТЕГОРИЙ:
По умолчанию предлагай три блюда из РАЗНЫХ категорий, чтобы дать пользователю выбор под разные сценарии употребления напитка:
1. Закуска / снек (для начала вечера, в баре с друзьями)
2. Основное блюдо (полноценное сочетание для ужина)
3. Десерт или сырная тарелка (для завершения вечера)

⚠️ ИСКЛЮЧЕНИЕ ДЛЯ ДИЖЕСТИВОВ И АПЕРИТИВОВ:
Если напиток объективно дижестив (ягермейстер, лимончелло, граппа, самбука, шартрез, бенедиктин, старые коньяки XO/Vintage, арманьяк, портвейн Tawny/Vintage, бейлис, токайские десертные вина) или аперитив (кампари, апероль, пастис, верт) — НЕ предлагай к нему стейк, рыбу или горячее основное блюдо. Это гастрономическая ошибка. Дай три варианта внутри его НАТИВНОЙ категории, но из РАЗНЫХ кулинарных направлений. Например для ягермейстера правильная триада:
1. Темный шоколад с морской солью (немецкая/европейская)
2. Тирамису или кофейный десерт (итальянская)
3. Сырная тарелка с голубыми сырами (французская)

То же для лимончелло — лимонный тарт + панна-котта + миндальное печенье. Для портвейна — стилтон + фуа-гра + темный шоколад. Для коньяка XO — горький шоколад + орехи в карамели + сигара/выдержанный сыр.

ВАЖНО про brand: в этом режиме brand = ТИП КУХНИ блюда. Например: "Грузинская кухня", "Итальянская кухня", "Японская кухня", "Французская кухня", "Домашняя кухня", "Немецкая кухня". НЕ указывай названия ресторанов или заведений — они ненадежны и вводят в заблуждение. ТОЛЬКО тип кухни.

🚫 НЕ ПИШИ price_range В ЭТОМ РЕЖИМЕ. ВООБЩЕ. Цена ресторанной/доставочной еды зависит от региона, города, конкретного заведения и доставки — у тебя нет источника правды для нее. Любая цифра, которую ты можешь предложить, будет фантазией из training data и введет пользователя в заблуждение. Поле price_range должно отсутствовать в результате (или быть пустой строкой ""). Реальную цену пользователь увидит когда тапнет "Заказать" — там откроется доставка/Google и покажет фактическую цену в его регионе.

🚫 ВАЛИДАЦИЯ ВВОДА (этот режим: НАПИТОК → БЛЮДО):
Ожидается НАПИТОК. Отклони если:
1. Несъедобное/непригодное в пищу: корм для животных, химикаты (бензин, моющее, краска), биологические выделения (моча, кал, кровь, слюна), несъедобные жидкости (масло моторное, антифриз).
2. Бессмыслица: случайный текст, только цифры, только знаки препинания, пустая строка.
3. ЕДА/БЛЮДО вместо напитка: если пришла "карбонара" / "стейк" / "пицца" / "бургер" / "суши" / "рамен" / "борщ" / "плов" / любая другая еда — это ошибка режима, отклони. НЕ галлюцинируй и НЕ интерпретируй блюдо как напиток.
4. БЕЗАЛКОГОЛЬНЫЕ НАПИТКИ: чай, кофе, сок, вода, лимонад, морс, компот, молоко, кефир, какао, смузи — отклони. Это приложение специализируется ТОЛЬКО на алкогольных напитках.
✅ ДОПУСТИМ любой АЛКОГОЛЬНЫЙ напиток, включая экзотические (саке, соджу, арак, чача, самогон, настойки, ликеры, медовуха, сидр, глинтвейн, грог, пунш).
Если ввод невалидный — верни ТОЛЬКО этот JSON и ничего больше:
{{"error":"Я специализируюсь на подборе еды к алкогольным напиткам. Попробуйте: красное вино, односолодовый виски, пшеничное пиво"}}
НЕ пиши свободный текст, НЕ извиняйся, НЕ объясняй в прозе — только этот JSON.

Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"категория блюда","alcohol_type_emoji":"🍽️","name":"название блюда","brand":"тип кухни (например: Грузинская кухня)","reason":"объяснение в соответствии с режимом детализации","price_range":"","serving_tip":"совет по подаче в соответствии с режимом детализации","why_it_works":"ТОЛЬКО для Эксперт-режима: 1-2 предложения о гастрономической логике сочетания. В Просто и Стандарт это поле НЕ заполняй (опускай или ставь пустую строку)"}}]}}"""

# ── Эндпоинты ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/pair/stream")
async def pair_stream(request: Request, req: PairRequest):
    user_info = _verify_token_sync(request)
    uid = user_info["uid"]

    pool = get_pool()
    conn = pool.getconn()
    try:
        user = _get_or_create_user(conn, uid, user_info["email"])
        is_premium = bool(user["is_premium"])
        if not is_premium and user["pairing_count"] >= FREE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Достигнут лимит {FREE_LIMIT} подборок. Перейдите на Premium."
            )
    finally:
        pool.putconn(conn)

    # Safety net: Expert mode только для Premium. Если Free прислал
    # detail_level='expert' (подделка запроса, старая выборка из prefs) —
    # принудительно даунгрейдим до standard в SAMOM начале pair_stream.
    # Дальше все (cache_key, prompt, INSERT в pairings) использует уже
    # downgraded значение — никаких рассинхронов между prompt и cache.
    # UI блокирует тап на Expert у Free как первый слой защиты.
    if req.detail_level == "expert" and not is_premium:
        req.detail_level = "standard"

    # .strip() защищает от случайных пробелов: "ягермейстер" и "ягермейстер " → один ключ.
    # preferences и is_premium включены в ключ — иначе Free с предпочтениями получил бы
    # кеш от Premium или от пользователя без предпочтений.
    prefs_key = ",".join(sorted(req.preferences))
    cache_key = hashlib.md5(
        f"v{PROMPT_VERSION}|{req.dish.lower().strip()}|{req.mode}|{req.budget}|{req.region}|{req.detail_level}|{prefs_key}|{is_premium}".encode()
    ).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        def _save_cached():
            c = pool.getconn()
            try:
                with c.cursor() as cur:
                    cur.execute("UPDATE users SET pairing_count = pairing_count + 1 WHERE firebase_uid = %s", (uid,))
                    cur.execute(
                        "INSERT INTO pairings (firebase_uid, dish, mode, budget, region, results, detail_level) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (uid, req.dish, req.mode, req.budget, req.region, json.dumps(cached["results"]), req.detail_level)
                    )
                c.commit()
            finally:
                pool.putconn(c)
        asyncio.get_event_loop().run_in_executor(None, _save_cached)

        # Чистка Ё→Е в кешированных результатах (старый кеш мог содержать Ё)
        for r in cached.get("results", []):
            for field in ("alcohol_type", "name", "brand", "reason", "serving_tip", "why_it_works", "price_range"):
                v = r.get(field)
                if isinstance(v, str):
                    r[field] = v.replace("\u0451", "\u0435").replace("\u0401", "\u0415")

        async def from_cache():
            yield json.dumps(cached)
        return StreamingResponse(from_cache(), media_type="text/plain")

    # Загружаем последние рекомендации пользователя для разнообразия
    recent_brands = []
    recent_types = []
    conn2 = pool.getconn()
    try:
        with conn2.cursor() as cur:
            cur.execute(
                "SELECT results FROM pairings WHERE firebase_uid = %s ORDER BY created_at DESC LIMIT 10",
                (uid,)
            )
            for row in cur.fetchall():
                try:
                    results = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    for r in results:
                        if isinstance(r, dict):
                            if r.get("brand"):
                                recent_brands.append(r["brand"])
                            if r.get("alcohol_type"):
                                recent_types.append(r["alcohol_type"])
                except Exception:
                    pass
    finally:
        pool.putconn(conn2)

    prompt = _build_prompt(req, is_premium=is_premium, recent_brands=recent_brands, recent_types=recent_types)

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
            import traceback
            traceback.print_exc()
            yield json.dumps({"error": "Сервис временно недоступен. Попробуйте через минуту."})
            return

        try:
            raw = accumulated.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            # Защита кеша: если Claude вернул error JSON (невалидный ввод —
            # бензин/моча/корм/еда в режиме напитка) — НЕ кешировать, НЕ
            # сохранять в pairings, НЕ списывать пользователю запрос. Раньше
            # галлюцинация Claude на "карбонара" в режиме напиток→еда улетала
            # в кеш на 30 дней — теперь блокируется здесь.
            if isinstance(data, dict) and data.get("error"):
                return
            if "results" not in data:
                return
            results = data["results"][:3]
            # Слой 2 защиты brand: убираем скобки со страной/пояснением из brand.
            # "Köstritzer Schwarzbier (Германия)" → "Köstritzer Schwarzbier"
            # Причина: brand идет в поисковую ссылку Kaspi/Magnum — "(Германия)"
            # в запросе ломает поиск. Также срезаем trailing " или ..." / " / ..."
            # как защитный слой на случай если Claude проигнорировал промпт-правило.
            for r in results:
                brand = r.get("brand", "")
                if isinstance(brand, str) and brand:
                    brand = re.sub(r"\s*\([^)]*\)", "", brand)
                    brand = re.split(r"\s+(?:или|/|,|\sлибо\s|\s—\s)", brand, maxsplit=1)[0]
                    r["brand"] = brand.strip()
            # Слой защиты от англоязычных категорий: если Claude вернул
            # alcohol_type типа "Dessert & Digestif", "Red Wine", "Whisky" —
            # переводим на русский. Промпт явно запрещает, но Claude иногда
            # проскакивает на категориях из training data.
            EN_TO_RU_CATEGORY = {
                "dessert & digestif": "Десерт и дижестив",
                "dessert and digestif": "Десерт и дижестив",
                "digestif": "Дижестив",
                "aperitif": "Аперитив",
                "red wine": "Красное вино",
                "white wine": "Белое вино",
                "rosé wine": "Розовое вино",
                "rose wine": "Розовое вино",
                "sparkling wine": "Игристое вино",
                "champagne": "Шампанское",
                "whisky": "Виски",
                "whiskey": "Виски",
                "single malt": "Виски",
                "single malt scotch": "Шотландский виски",
                "scotch": "Виски",
                "bourbon": "Бурбон",
                "cognac": "Коньяк",
                "brandy": "Бренди",
                "rum": "Ром",
                "tequila": "Текила",
                "mezcal": "Мескаль",
                "gin": "Джин",
                "vodka": "Водка",
                "beer": "Пиво",
                "lager": "Лагер",
                "stout": "Стаут",
                "porter": "Портер",
                "ale": "Эль",
                "ipa": "ИПА",
                "wheat beer": "Пшеничное пиво",
                "sake": "Саке",
                "liqueur": "Ликер",
                "cocktail": "Коктейль",
                "main course": "Основное блюдо",
                "appetizer": "Закуска",
                "snack": "Снек",
                "cheese plate": "Сырная тарелка",
                "dessert": "Десерт",
                "salad": "Салат",
                "soup": "Суп",
                "seafood": "Морепродукты",
            }
            for r in results:
                at = r.get("alcohol_type", "")
                if isinstance(at, str) and at:
                    key = at.strip().lower()
                    if key in EN_TO_RU_CATEGORY:
                        r["alcohol_type"] = EN_TO_RU_CATEGORY[key]
                    elif any(c.isascii() and c.isalpha() for c in at):
                        # Англ категория не нашлась в словаре — логируем для
                        # мониторинга. Раз в неделю смотрим Railway logs и
                        # добавляем новые варианты в EN_TO_RU_CATEGORY.
                        # Префикс [UNKNOWN_CATEGORY] для grep по логам.
                        print(f'[UNKNOWN_CATEGORY: "{at}"] dish={req.dish!r} mode={req.mode}', flush=True)

            # ── Пост-валидация для режима food_to_alcohol ────────────────
            # 1. Замена brand из blocklist (recent_brands) на альтернативу
            #    из BRAND_REGISTRY — гарантирует что пользователь не увидит
            #    один и тот же бренд минимум 10 итераций.
            # 2. Фикс name/brand рассинхрона: дубль или общая категория →
            #    name = "" (UI показывает только brand).
            # 3. Кламп цен пива: импортный мейнстрим не дороже $5 за бутылку,
            #    стаут не дороже $6, азиатский не дороже $7.
            if req.mode == "food_to_alcohol":
                blocklist_lower = {b.lower() for b in recent_brands if isinstance(b, str)}
                for r in results:
                    brand = (r.get("brand") or "").strip()
                    # 1. Replace blocked brand
                    if brand and brand.lower() in blocklist_lower:
                        category_key = _normalize_alcohol_type(r.get("alcohol_type"))
                        # blocklist + текущий бренд + другие бренды этой подборки —
                        # чтобы замена не совпала с другой карточкой.
                        avoid = blocklist_lower | {brand.lower()} | {
                            (other.get("brand") or "").lower()
                            for other in results if other is not r
                        }
                        alt = _pick_alternative_brand(category_key, avoid)
                        if alt:
                            print(f'[BRAND_REPLACE: "{brand}" → "{alt}"] dish={req.dish!r} category={category_key}', flush=True)
                            old_brand = brand
                            r["brand"] = alt
                            brand = alt
                            # Если name содержал старый бренд — обновим
                            name_val = r.get("name") or ""
                            if old_brand and old_brand.lower() in name_val.lower():
                                r["name"] = re.sub(re.escape(old_brand), alt, name_val, flags=re.I)
                    # 2. Sync name/brand
                    name_val = (r.get("name") or "").strip()
                    if brand and name_val:
                        if name_val.lower() == brand.lower():
                            # Дубль — затираем name, UI покажет только brand
                            r["name"] = ""
                        elif _is_generic_name(name_val):
                            # name это просто общая категория ("Водка премиум")
                            r["name"] = ""
                    # 3. Beer price clamp — реальные магазинные цены СНГ.
                    #    Обычное импортное пиво (Heineken/Hoegaarden/Pilsner)
                    #    в Магнит/Каспи/Магнум стоит $2-3, не $5-8 как любит
                    #    ставить Claude. Между странами СНГ цена может
                    #    колебаться ±$1, поэтому потолок поставлен с запасом
                    #    относительно базового ориентира — лучше пропустить
                    #    легкое завышение, чем зарезать корректный кейс.
                    category_key = _normalize_alcohol_type(r.get("alcohol_type"))
                    if (category_key and category_key.startswith("пиво")) or category_key == "сидр":
                        price = r.get("price_range") or ""
                        if isinstance(price, str) and price:
                            brand_lower = (r.get("brand") or "").lower()
                            # Местное самое бюджетное — реально $1-2, потолок $3
                            if any(s in brand_lower for s in [
                                "балтика", "жигул", "карагандин", "шымкент",
                                "лідск", "лидск", "львівськ", "оболон",
                                "чернигов", "тянь-шань", "tянь-шань", "адмирал",
                                "белый медведь", "охота", "невское",
                            ]):
                                cap = 3
                            # Премиум — реально $4-6, потолок $7
                            elif any(s in brand_lower for s in [
                                "guinness", "asahi", "sapporo", "tsingtao",
                                "kirin", "duvel", "chimay", "westmalle",
                                "hibiki", "yamazaki",
                            ]):
                                cap = 7
                            # Дефолт — обычное импортное мейнстрим (Heineken,
                            # Carlsberg, Stella, Hoegaarden, Pilsner Urquell):
                            # реально $2-3, потолок $4 с учетом региональной
                            # вариативности по СНГ
                            else:
                                cap = 4
                            m = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", price)
                            if m:
                                high = float(m.group(2))
                                if high > cap:
                                    new_low = max(1, cap - 2)
                                    new_price = f"${new_low}-{cap}"
                                    print(f'[BEER_PRICE_CLAMP: "{price}" → "{new_price}"] brand={r.get("brand")!r} cap=${cap}', flush=True)
                                    r["price_range"] = new_price

            # ── Пост-валидация для режима alcohol_to_food ─────────────────
            # Цена ресторанной/доставочной еды у Claude — фантазия. Любая
            # цифра без привязки к региону/доставке вредит доверию: юзер
            # видит "$24" в карточке, открывает доставку, видит "$8" — теряет
            # доверие к рекомендациям. Поэтому затираем price_range полностью.
            # Реальную цену юзер увидит при тапе "Заказать" — там Google
            # покажет фактические цены в его регионе.
            if req.mode == "alcohol_to_food":
                for r in results:
                    if "price_range" in r:
                        r["price_range"] = ""

            # Гарантия что why_it_works существует ТОЛЬКО в Эксперт-режиме.
            # Если Claude вернул его в Просто/Стандарт — вырезаем перед сохранением.
            if req.detail_level != "expert":
                for r in results:
                    if "why_it_works" in r:
                        del r["why_it_works"]
            else:
                # В Эксперте чистим пустые/None значения чтобы Flutter не рисовал пустой блок
                for r in results:
                    if "why_it_works" in r:
                        v = r["why_it_works"]
                        if not isinstance(v, str) or not v.strip():
                            del r["why_it_works"]
            # Программная замена Ё→Е во всех строковых полях результатов.
            # Claude иногда игнорирует промпт-правило — эта замена гарантирует
            # что ни одна Ё не попадет пользователю.
            for r in results:
                for field in ("alcohol_type", "name", "brand", "reason", "serving_tip", "why_it_works", "price_range"):
                    v = r.get(field)
                    if isinstance(v, str):
                        r[field] = v.replace("\u0451", "\u0435").replace("\u0401", "\u0415")
            result = {"dish": req.dish, "mode": req.mode, "budget": req.budget, "region": req.region, "results": results}
            _cache_set(cache_key, result)

            def _save():
                c = pool.getconn()
                try:
                    with c.cursor() as cur:
                        cur.execute("UPDATE users SET pairing_count = pairing_count + 1 WHERE firebase_uid = %s", (uid,))
                        cur.execute(
                            "INSERT INTO pairings (firebase_uid, dish, mode, budget, region, results, detail_level) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (uid, req.dish, req.mode, req.budget, req.region, json.dumps(results), req.detail_level)
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
                """SELECT dish, mode, budget, region, results, created_at, detail_level
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

@app.delete("/history")
def clear_history(request: Request):
    """Очистка истории + сброс счетчика pairing_count.

    Раньше только удаляла записи из pairings, но лимит считается по
    users.pairing_count — после очистки счетчик оставался 10/10 и Free
    пользователь не мог продолжать. Теперь сбрасываем оба: клик "Очистить
    историю" = полный reset Free-лимита.

    После интеграции RevenueCat возможно нужно будет разделить:
    "очистить историю" отдельно от "сбросить лимит" — но в MVP это один
    экшен для простоты UX.
    """
    user_info = _verify_token_sync(request)
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pairings WHERE firebase_uid = %s", (user_info["uid"],))
            cur.execute("UPDATE users SET pairing_count = 0 WHERE firebase_uid = %s", (user_info["uid"],))
        conn.commit()
        return {"cleared": True}
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
