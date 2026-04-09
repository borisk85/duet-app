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
# не устаревают через сутки (стейк+вино работает месяцами), это даёт
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

GLOBAL_BRANDS_REFERENCE = """ГЛОБАЛЬНАЯ БАЗА ХОДОВЫХ БРЕНДОВ ДОСТУПНЫХ ВЕЗДЕ В СНГ (используй активно, не зацикливайся на самых очевидных):

🍺 ПИВО:
— Лагер мировой (массово в СНГ): Heineken, Carlsberg, Stella Artois, Corona Extra, Becks, Tuborg, Holsten, Warsteiner, Bitburger, Krombacher, Pilsner Urquell, Budweiser Budvar
— Азиатский лагер (КЗ/РФ популярные): Asahi, Tsingtao, Sapporo, Kirin
— Немецкий пшеничный (популярны, Paulaner — ресторанная сеть в СНГ): Paulaner, Erdinger, Weihenstephaner
— Бельгийский: Hoegaarden, Leffe
— Стаут/портер: Guinness Draught, Guinness Extra Stout
— Сидр: Strongbow, Somersby

🥃 ВИСКИ:
— Шотландский blended: Johnnie Walker (Red/Black/Double Black/Gold/Blue), Chivas Regal (12/18), Ballantine's (Finest/12/17), Famous Grouse, J&B, Dewar's, William Lawson's, Teacher's, Bell's, White Horse, Cutty Sark
— Single Malt Scotch: Glenfiddich (12/15/18), Glenlivet (12/15/18), Macallan (12/15/18), Glenmorangie (Original/Lasanta/Quinta Ruban), Highland Park, Talisker, Laphroaig, Ardbeg, Bowmore, Cardhu, Aberlour, Dalwhinnie
— Ирландский: Jameson, Tullamore Dew, Bushmills, Powers, Redbreast, Connemara
— Американский bourbon/rye: Jack Daniel's, Jim Beam, Maker's Mark, Wild Turkey, Buffalo Trace, Woodford Reserve, Bulleit, Four Roses
— Японский: Suntory Toki/Hibiki/Yamazaki/Hakushu, Nikka From The Barrel/Coffey Grain

🥃 КОНЬЯК / БРЕНДИ:
— Французский коньяк: Hennessy (VS/VSOP/XO), Martell (VS/VSOP/XO/Cordon Bleu), Remy Martin (VSOP/1738/XO), Courvoisier (VS/VSOP/XO), Camus, Bisquit
— Армянский: Ararat (3/5/7/10/Akhtamar/Vaspurakan/Nairi), Noy
— Молдавский: Kvint, Bardar
— Испанский бренди: Cardenal Mendoza, Torres, Carlos I
— Греческий: Metaxa (5/7/12)

🍹 РОМ:
— Светлый/золотой массовый: Bacardi (Carta Blanca/Oro/Anejo), Havana Club (3/7/Especial), Captain Morgan (Spiced/White/Dark), Brugal, Mount Gay (Eclipse/XO)
— Премиум: Diplomatico (Reserva Exclusiva/Mantuano), Zacapa (23/XO), Plantation (3 Stars/Original Dark/XO), Appleton Estate (Signature/8/12), El Dorado (12/15)

🌵 ТЕКИЛА И МЕСКАЛЬ:
— Массовая: Jose Cuervo (Especial/Tradicional), Sauza (Silver/Gold/Hornitos), Olmeca (Blanco/Reposado/Anejo), Sierra Tequila
— Премиум 100% agave: Patron (Silver/Reposado/Anejo), Don Julio (Blanco/Reposado/Anejo/1942), Herradura, Espolon, Cazadores, Tres Generaciones, Casamigos
— Мескаль: Del Maguey, Montelobos, Ilegal

🌿 ДЖИН:
— Лондонский dry: Beefeater, Gordon's, Tanqueray (London Dry/Ten/Rangpur), Bombay Sapphire, Plymouth, Greenall's
— Современный premium: Hendrick's, Monkey 47, The Botanist, Roku, Sipsmith, Bulldog, Brockmans, Citadelle

🫗 ВОДКА:
— Премиум международная: Absolut (Original/Citron/Vanilla/Elyx), Smirnoff (Red/Black), Finlandia, Grey Goose, Belvedere, Ketel One, Tito's, Stolichnaya
— Восточно-европейская: Beluga (Noble/Gold Line/Transatlantic), Russian Standard (Original/Gold/Platinum), Khortytsa, Nemiroff, Soplica, Wyborowa, Zubrowka

🥂 ИГРИСТОЕ / ШАМПАНСКОЕ:
— Шампань: Moet & Chandon (Brut Imperial/Rose/Nectar), Veuve Clicquot (Yellow Label/Rose/Vintage), Dom Perignon, Lanson (Black Label/Rose), Mumm (Cordon Rouge), Taittinger, Bollinger, Pol Roger, Laurent-Perrier, Ruinart
— Просекко: Mionetto, Bisol, Bottega Gold, Cinzano Pro-Spritz, Carpene Malvolti
— Кава: Freixenet (Cordon Negro/Carta Nevada), Codorniu
— Ламбруско: Riccadonna, Cinzano, Chiarli, Cavicchioli

🍷 ВИНО (мировые регионы массово доступные):
— Италия: Chianti Classico, Barolo, Brunello di Montalcino, Amarone, Valpolicella, Montepulciano, Prosecco, Pinot Grigio, Soave, Frascati. Бренды: Antinori, Frescobaldi, Banfi, Ruffino, Gaja, Masi
— Франция: Bordeaux (Médoc/Saint-Émilion/Pomerol), Burgundy (Côte de Nuits/Beaune), Côtes du Rhône, Châteauneuf-du-Pape, Chablis, Sancerre, Beaujolais, Provence rosé. Бренды: Mouton Cadet, B&G, Louis Jadot, Joseph Drouhin, Georges Duboeuf
— Испания: Rioja (Marqués de Cáceres, Faustino, Campo Viejo, Marqués de Riscal), Ribera del Duero (Vega Sicilia, Pesquera, Protos), Priorat, Albariño, Cava
— Германия: Riesling (Dr Loosen, Selbach Oster, Mosel), Spätburgunder
— Португалия: Vinho Verde, Douro, Port wine (Taylor's, Graham's, Sandeman, Dow's)
— Чили/Аргентина: Concha y Toro, Santa Rita, Catena, Trapiche, Norton, Luigi Bosca (Malbec, Carmenere, Cabernet Sauvignon)
— Австралия/НЗ: Penfolds, Wolf Blass, Jacob's Creek, Yellow Tail, Cloudy Bay (Sauvignon Blanc), Oyster Bay
— ЮАР: KWV, Nederburg, Spier
— Грузия: Saperavi, Mukuzani, Kindzmarauli, Khvanchkara, Tsinandali, Rkatsiteli (Tbilvino, Teliani Valley, Kindzmarauli Marani, Telavi Wine Cellar, Askaneli)
— Армения: Areni, Voskehat (Armas, Karas, Trinity, ArmAs)
— Молдавия: Cricova, Milestii Mici, Purcari
— Крым/Кубань (для РФ): Massandra, Inkerman, Sevastopol Winery, Fanagoria, Kuban-Vino, Lefkadia, Abrau-Durso

ПРАВИЛО: при подборке используй РАЗНЫЕ бренды и стили. Не повторяй один и тот же бренд в трёх карточках одной подборки. Между запросами от пользователя — варьируй, не давай Krombacher на каждый второй запрос. У тебя сотни качественных альтернатив, используй ширину базы."""

REGION_AVAILABILITY = {
    "Казахстан": "🏪 СЕТЕВЫЕ МАГАЗИНЫ КАЗАХСТАНА: рекомендуй ТОЛЬКО бренды которые физически лежат на полках Kaspi Magazin, Magnum, Small. Если бренд не продаётся в этих сетях — НЕ предлагай, возьми массовый аналог того же стиля. Доступно в КЗ: лагеры (Heineken, Corona, Carlsberg, Stella Artois, Tuborg, Becks, Holsten, Warsteiner, Bitburger, Krombacher, Pilsner Urquell, Budweiser Budvar), Guinness, местные (Tянь-Шань, Карагандинское, Шымкентское), грузинские вина (Saperavi, Mukuzani, Kindzmarauli от Teliani Valley / Tbilvino), армянские коньяки (Ararat, Noy), массовые виски (Jameson, Ballantine's, Johnnie Walker, Chivas, Jack Daniel's, Glenfiddich), массовые коньяки (Hennessy, Martell, Remy Martin), водка (Absolut, Finlandia), текила (Olmeca, Jose Cuervo).",
    "Россия":    "🏪 СЕТЕВЫЕ МАГАЗИНЫ РФ: рекомендуй ТОЛЬКО бренды которые физически лежат в Магнит, Пятёрочка, Перекрёсток, Ашан, Лента, ВинЛаб, КрасноеБелое. Если бренд не продаётся в этих сетях — НЕ предлагай. Доступно в РФ с учётом санкций 2024-2026: лагеры (Балтика, Жигулёвское, Heineken, Carlsberg, Krombacher, Bitburger по параллельному импорту), российский крафт (AF Brew, Salden's, Konix), грузинские вина (Saperavi, Mukuzani от Teliani Valley), крымские и кубанские (Massandra, Inkerman, Fanagoria, Lefkadia, Абрау-Дюрсо), коньяки (Ararat, Kvint, Kizlyar, Hennessy), массовые виски (Jameson, Chivas, Johnnie Walker). НЕ давай Балтику/Жигулёвское по умолчанию — варьируй.",
    "Украина":   "🏪 СЕТЕВЫЕ МАГАЗИНЫ УКРАИНЫ: рекомендуй ТОЛЬКО бренды которые физически лежат в АТБ, Сільпо, Novus, Fozzy, WineTime. Доступно в UA: местные (Львівське, Чернігівське, Оболонь), украинский крафт (Varvar, Pravda, Volynski Browar), европейские лагеры (Heineken, Carlsberg, Krombacher), европейские и грузинские вина.",
    "Беларусь":  "🏪 СЕТЕВЫЕ МАГАЗИНЫ БЕЛАРУСИ: рекомендуй ТОЛЬКО бренды которые физически лежат в Евроопт, Hippo, Соседи. Доступно в BY: местные (Лидское, Аліварыя, Крыніца), лагеры (Heineken, Carlsberg, Krombacher), грузинские вина.",
    "СНГ":       "🏪 УНИВЕРСАЛЬНЫЕ МАГАЗИНЫ СНГ: рекомендуй ТОЛЬКО бренды доступные в массовых сетевых магазинах всех стран региона: международные лагеры (Heineken, Corona, Carlsberg, Stella, Becks, Tuborg, Krombacher, Bitburger, Pilsner Urquell, Budweiser Budvar), Guinness, европейские и грузинские вина, массовые коньяки и виски из белого списка.",
}

DETAIL_LEVEL_MAP = {
    "simple":   "РЕЖИМ ПРОСТО: это совет другу который НИЧЕГО не знает об алкоголе. Никакой посуды, никаких температур, никаких действий требующих специального оборудования или знаний (никаких бокалов, декантеров, термометров, градусов, аэрации, охлаждения посуды). Только бытовой язык. Reason — РОВНО одно предложение до 10 слов на бытовом языке. НИКАКИХ терминов ('танины', 'кислотность', 'минеральность', сорта винограда и т.д.).\n\n💡 SERVING_TIP в ПРОСТО — одна фраза практичного совета СПЕЦИФИЧНОГО для ЭТОГО напитка и ЭТОГО блюда. НЕ повторяй одно и то же на всех трёх карточках (не три раза 'пить холодным'). Варьируй направления совета:\n— Момент подачи: 'открыть пока готовится блюдо', 'подать сразу когда еда на столе', 'дать постоять 10 минут перед подачей'\n— Количество/крепость: 'налить немного, это крепкое', 'пить маленькими глотками', 'хватит одной рюмки к обеду'\n— Сочетание: 'хорошо с первым глотком пока горячее', 'запивать кусочек сыра', 'чередовать с едой'\n— Покупка: 'ищи в винном отделе не в пивном', 'продаётся в крафтовой секции', 'есть в любом супермаркете'\n— Температура бытовым языком: 'пить холодным из холодильника', 'комнатной температуры', 'со льдом если жарко'\nТри карточки = три РАЗНЫХ направления совета. Если не уверен можно ли слово — его НЕЛЬЗЯ.",
    "standard": "РЕЖИМ СТАНДАРТ: сбалансированное гастрономическое объяснение для аудитории которая ходит в рестораны. Reason — 2-3 предложения с вкусовыми характеристиками на ЖИВОМ человеческом языке. Используй слова: 'танины', 'кислотность', 'минеральность', 'шёлковистая текстура', 'лёгкая горчинка', 'фруктовые ноты', 'округлый вкус'. Можно упомянуть сорт винограда РУССКИМИ названиями ('Каберне Совиньон', 'Шардоне', 'Темпранильо'). Объясняй сочетание через вкусовой контраст или гармонию ('кислотность освежает после жирного мяса', 'танины смягчают остроту'). Serving_tip — практичный совет: температура подачи в °C, тип посуды, с чем подавать, как открывать.",
    "expert":   "РЕЖИМ ЭКСПЕРТ: профессиональное описание для знатока. Глубина через ТОЧНОСТЬ формулировок, не через ОБЪЁМ текста. Эксперт читается за 15 секунд, не за минуту.\n\n📖 REASON — МАКСИМУМ 2 предложения. Только: (1) вкусовой профиль напитка с экспертными деталями (сорт винограда оригинальным названием 'Cabernet Sauvignon'/'Pinot Noir', регион 'Bordeaux Левый берег'/'Barolo Пьемонт', выдержка '18 месяцев в дубе', танины/кислотность/минеральность), (2) ключевое сочетание с блюдом одной фразой. ВСЁ. Никакого пересказа гастрономической механики (это в why_it_works), никаких списков нот, никакой региональной истории.\n\n⚙️ WHY_IT_WORKS — РОВНО одно предложение с гастрономической МЕХАНИКОЙ на уровне химии вкуса. НЕ пересказывай reason — здесь именно ПРИНЦИП. Примеры: 'Танины связываются с белками говядины и смягчают жёсткость волокон.' / 'Капсаицин активирует тепловые рецепторы, горчинка лагера их блокирует.' / 'Сладость десерта балансирует горечь дижестива на финише.'\n\n💡 SERVING_TIP — точная температура в °C, тип бокала ('Bordeaux universal', 'Glencairn'), декантация/аэрация/время дыхания если применимо. Конкретно и коротко.\n\nИтого ТРИ КОРОТКИХ блока, а не три абзаца. Reason и why_it_works НЕ должны дублировать друг друга — reason про напиток, why_it_works про химию сочетания. Это поле существует ТОЛЬКО в Эксперт-режиме.",
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

🚫 АБСОЛЮТНОЕ ПРАВИЛО ЯЗЫКА — ОПИСЫВАЙ ВКУС, А НЕ ХИМИЮ:

Ты пишешь для женщин 28-45 и белых воротничков которые ходят в хорошие рестораны. Они знают вкусовые слова, но НЕ знают химические аббревиатуры. Пиши как сомелье в ресторане Michelin, а не как лаборант на пивоварне.

ВМЕСТО ХИМИИ — ИСПОЛЬЗУЙ ВКУСОВЫЕ ОПИСАНИЯ:

Крепость алкоголя:
— НЕ "4.6% ABV", "содержание спирта 40% ABV", "крепость 13% ABV"
— ВСЕГДА: "лёгкое", "средней крепости", "полнотелое", "крепкое", "обжигающее", "тёплое спиртом"

Горечь пива:
— НЕ "24 IBU", "горечь 60 IBU"
— ВСЕГДА: "едва уловимая горчинка", "лёгкая хмелевая горечь", "выраженная горечь хмеля", "густая смолистая горечь"

Кислотность вина/напитка:
— НЕ "pH 3.3", "кислотность 6.2-6.8 pH", "TA 6.5 g/L"
— ВСЕГДА: "освежающая кислотность", "хрустящая кислотность", "мягкая округлая кислотность", "яркая цитрусовая кислотность"

Танины/полифенолы:
— НЕ "танины 24-28 g/L", "содержание полифенолов 2400 мг/л"
— ВСЕГДА: "мощные танины", "шёлковые танины", "плотные танины", "вяжущие танины", "бархатистые танины"

Сахар:
— НЕ "сахар 12 г/л", "остаточный сахар 4 г/л"
— ВСЕГДА: "сухое", "полусухое", "с лёгкой сладостью", "десертное"

Плотность пива:
— НЕ "плотность 12°P", "OG 1.048"
— ВСЕГДА: "лёгкое тело", "среднее тело", "плотное насыщенное тело"

ПРАВИЛЬНЫЙ ПРИМЕР reason для Эксперт-режима:
"Cabernet Sauvignon из Bordeaux, Левый берег — полнотелое вино с мощными зрелыми танинами и яркой кислотностью. Танины смягчают жирность мраморного рибая, кислотность освежает нёбо после каждого укуса, а ноты чёрной смородины и кедра гармонируют с обугленной корочкой стейка."

ОБРАТИ ВНИМАНИЕ: ни одной аббревиатуры. Ни ABV. Ни pH. Ни g/L. Ни IBU. Только вкусовые слова. Это сомельерский язык, и он работает в любом режиме детализации, включая Эксперт.

ПРОВЕРЬ СЕБЯ ПЕРЕД ОТПРАВКОЙ JSON: если в reason или serving_tip встречается ABV, IBU, pH, g/L, г/л, °P, OG, TA — ПЕРЕПИШИ это вкусовыми словами. Это критически важно для нашего продукта.

🎯 ГЛАВНЫЙ ПРИНЦИП ВЫБОРА: ВКУС > ПОПУЛЯРНОСТЬ
Качество сочетания с блюдом важнее популярности или доступности бренда. Если редкий, но доступный в СНГ напиток лучше раскрывает блюдо по вкусу/танинам/кислотности/умами/жирности — РЕКОМЕНДУЙ ЕГО, даже если массовая аудитория его не знает. В крупных городах СНГ есть винотеки, виски-бары, крафт-магазины, рестораны премиум-сегмента — там доступно гораздо больше чем в обычном супермаркете. Пользователь готов искать редкое если знает что оно идеально подходит. Не упрощай ради "доступности по умолчанию".

🚨 КРИТИЧЕСКОЕ ПРАВИЛО — РАЗНООБРАЗИЕ БРЕНДОВ:
Никогда не предлагай "очевидный массовый бренд по умолчанию" только потому что он самый известный для региона. Это самая частая ошибка, которой нужно осознанно избегать.

Примеры запрещённой автоматики:
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
4. Между разными запросами от пользователя — варьируй бренды. Не предлагай один и тот же Krombacher на каждый второй запрос. У тебя есть десятки качественных альтернатив, используй их."""

def _build_prompt(req: PairRequest, is_premium: bool = False) -> str:
    budget_desc = BUDGET_MAP[req.budget]
    availability = REGION_AVAILABILITY.get(req.region, REGION_AVAILABILITY["СНГ"])
    detail_desc = DETAIL_LEVEL_MAP[req.detail_level]
    # Секция предпочтений: Free — soft hint, Premium — hard prioritization.
    # Это делает Premium ощутимо лучше в персонализации без хардкода в коде.
    preferences_section = ""
    if req.preferences:
        pref_list = ", ".join(req.preferences)
        if is_premium:
            preferences_section = f"\n🎯 ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ (Premium — строгий приоритет): {pref_list}. Приоритизируй напитки из этих категорий во ВСЕХ трёх позициях где это гастрономически оправдано. Если все три возможны из предпочтений — давай все три из них.\n"
        else:
            preferences_section = f"\n🎯 ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ (Free — мягкая подсказка): {pref_list}. Постарайся включить хотя бы ОДИН напиток из этих категорий в тройку, но НЕ в ущерб гастрономическому качеству подбора.\n"
    if req.mode == "food_to_alcohol":
        return f"""{EXPERT_ROLE}

{GLOBAL_BRANDS_REFERENCE}

Пользователь из региона {req.region}.
Блюдо: {req.dish}
Бюджет: {budget_desc}
Доступность: {availability}
{detail_desc}{preferences_section}
Подбери ТОП-3 напитка. Первым ставь напиток наиболее традиционный для данной кухни.

🎯 ПРАВИЛО РАЗНООБРАЗИЯ КАТЕГОРИЙ (обязательно):
Три напитка ДОЛЖНЫ быть из РАЗНЫХ категорий алкоголя. ЗАПРЕЩЕНО давать три напитка одного типа (нельзя 3 пива, нельзя 3 красных вина, нельзя 3 виски). Пользователю нужен выбор под разное настроение и сценарий.
Категории для разнообразия: вино (красное / белое / игристое — считаются разными категориями), пиво, виски, коньяк, водка, ром, текила, джин, саке, коктейли.
Правильные триады:
- К бургеру: 1 пиво (IPA / пшеничное) + 1 красное вино (Zinfandel / Malbec) + 1 виски/бурбон или коктейль
- К стейку: 1 красное вино + 1 виски single malt + 1 крафтовое пиво (Porter / Stout)
- К сушам: 1 саке + 1 белое сухое (Riesling / Grüner) + 1 японское пиво (Asahi / Sapporo)
- К пасте карбонара: 1 белое итальянское (Frascati / Vermentino) + 1 красное лёгкое (Chianti) + 1 игристое (Prosecco)

ВАЖНО про brand: ТОЛЬКО ОДИН конкретный бренд/марка. ЗАПРЕЩЕНО давать альтернативы через "или", "/", ",", " — ", "либо". Запрещены скобки с пояснениями. Выбери САМЫЙ доступный в регионе пользователя бренд и дай только его. Правильно: "Саперави Дбили", "Hoegaarden Witbier", "Pilsner Urquell". Неправильно: "Саперави Дбили или Телиани Вали", "Hoegaarden / Paulaner", "Chianti (или Sangiovese)". Причина: ссылка из brand идёт в поиск магазина, "или" ломает поиск — магазин не находит товар.

🚨 ЖЁСТКОЕ ПРАВИЛО ПЕРВОГО ЭШЕЛОНА (brand):
Используй ТОЛЬКО массово известные бренды которые физически лежат в сетевых магазинах СНГ (Каспи для Казахстана, Магнит/Перекрёсток/ВкусВилл для РФ, локальные сети для других стран). Целевой пользователь должен УЗНАВАТЬ бренд — это "первый эшелон".
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

💰 ОРИЕНТИРЫ ЦЕН ДЛЯ ПИВА В СНГ (цены стабильны по стране — фабричное пиво имеет сетевую наценку ~10%, не ×2-3 как вино):
- Бюджетно $1-3: Балтика, Жигули, Карагандинское, Шымкентское
- Средний $3-7: Heineken, Guinness, Corona, Estrella, Hoegaarden, Paulaner
- Премиум $7-15: крафтовое, японское (Sapporo, Asahi), бельгийское (Chimay, Duvel)
НЕ завышай цены на фабричное пиво. Hoegaarden в магазине ≈ $3, не $5. Выше $15 за пиво — только коллекционные позиции.

🚫 НЕВАЛИДНЫЙ ЗАПРОС:
Отклоняй ТОЛЬКО запросы про корм для животных (кошачий корм, собачий корм, корм для рыб/птиц/грызунов), химикаты, явно несъедобные предметы (камни, бумага, земля, пластик), бессмысленный случайный текст, только цифры, только знаки препинания, пустая строка.
✅ ДОПУСТИМО любое блюдо которое едят люди в ЛЮБОЙ культуре мира: экзотическое мясо (собака в Корее, конина в Казахстане, крокодил, змея, кузнечики и другие насекомые в Азии), субпродукты (печень, сердце, мозги, почки, язык, требуха), сырая рыба, ферментированные продукты (хаукарль, сюрстрёмминг, кимчи), национальные блюда любых культур. Это гастрономические традиции, а не повод отказывать. НЕ навязывай свою культурную оценку.
Если запрос действительно невалидный — верни ТОЛЬКО этот JSON и ничего больше:
{{"error":"Введите название блюда или закуски"}}
НЕ пиши свободный текст, НЕ извиняйся, НЕ объясняй в прозе — только этот JSON.

Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"тип","alcohol_type_emoji":"🍷","name":"название","brand":"конкретная марка доступная в {req.region}","reason":"объяснение в соответствии с режимом детализации","price_range":"$X-Y","serving_tip":"совет по подаче в соответствии с режимом детализации","why_it_works":"ТОЛЬКО для Эксперт-режима: 1-2 предложения о гастрономической логике сочетания. В Просто и Стандарт это поле НЕ заполняй (опускай или ставь пустую строку)"}}]}}"""
    else:
        return f"""{EXPERT_ROLE}

{GLOBAL_BRANDS_REFERENCE}

Пользователь из региона {req.region}.
Напиток: {req.dish}
{detail_desc}{preferences_section}
Подбери ТОП-3 блюда/закуски к этому напитку.

🎯 ПРАВИЛО РАЗНООБРАЗИЯ КАТЕГОРИЙ:
По умолчанию предлагай три блюда из РАЗНЫХ категорий, чтобы дать пользователю выбор под разные сценарии употребления напитка:
1. Закуска / снек (для начала вечера, в баре с друзьями)
2. Основное блюдо (полноценное сочетание для ужина)
3. Десерт или сырная тарелка (для завершения вечера)

⚠️ ИСКЛЮЧЕНИЕ ДЛЯ ДИЖЕСТИВОВ И АПЕРИТИВОВ:
Если напиток объективно дижестив (ягермейстер, лимончелло, граппа, самбука, шартрез, бенедиктин, старые коньяки XO/Vintage, арманьяк, портвейн Tawny/Vintage, бейлис, токайские десертные вина) или аперитив (кампари, апероль, пастис, верт) — НЕ предлагай к нему стейк, рыбу или горячее основное блюдо. Это гастрономическая ошибка. Дай три варианта внутри его НАТИВНОЙ категории, но из РАЗНЫХ кулинарных направлений. Например для ягермейстера правильная триада:
1. Тёмный шоколад с морской солью (немецкая/европейская)
2. Тирамису или кофейный десерт (итальянская)
3. Сырная тарелка с голубыми сырами (французская)

То же для лимончелло — лимонный тарт + панна-котта + миндальное печенье. Для портвейна — стилтон + фуа-гра + тёмный шоколад. Для коньяка XO — горький шоколад + орехи в карамели + сигара/выдержанный сыр.

ВАЖНО про brand: ТОЛЬКО ОДИН конкретный вариант блюда или место. ЗАПРЕЩЕНО давать альтернативы через "или", "/", ",", " — ", "либо". Запрещены скобки с пояснениями. Правильно: "Бельгийский шоколад Leonidas", "Ресторан La Piazza". Неправильно: "Тирамису или панна-котта", "Шоколад / пралине", "Стейк (или рибай)". Причина: brand идёт в поисковую ссылку магазина — "или" ломает поиск.

ВАЖНО про price_range: ТОЛЬКО короткая цифровая оценка, максимум 12 символов. Без скобок и описаний. Правильно: "~$10", "$5-15". Неправильно: "~$10 (порция)", "$5-15 в ресторане".

🚫 НЕВАЛИДНЫЙ ЗАПРОС:
Отклоняй ТОЛЬКО запросы про несъедобное/непитьевое (химикаты, бытовая химия, несъедобные предметы, бессмысленный случайный текст, только цифры, пустая строка). Любой напиток который пьют люди в ЛЮБОЙ культуре мира — допустим, включая экзотические (саке, соджу, арак, буза, кумыс, чача, самогон, настойки).
Если запрос невалидный — верни ТОЛЬКО этот JSON и ничего больше:
{{"error":"Введите название напитка"}}
НЕ пиши свободный текст, НЕ извиняйся, НЕ объясняй в прозе — только этот JSON.

Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"категория блюда","alcohol_type_emoji":"🍽️","name":"название блюда","brand":"вариант/где попробовать","reason":"объяснение в соответствии с режимом детализации","price_range":"~$X","serving_tip":"совет по подаче в соответствии с режимом детализации","why_it_works":"ТОЛЬКО для Эксперт-режима: 1-2 предложения о гастрономической логике сочетания. В Просто и Стандарт это поле НЕ заполняй (опускай или ставь пустую строку)"}}]}}"""

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

    # .strip() защищает от случайных пробелов: "ягермейстер" и "ягермейстер " → один ключ.
    # preferences и is_premium включены в ключ — иначе Free с предпочтениями получил бы
    # кеш от Premium или от пользователя без предпочтений.
    prefs_key = ",".join(sorted(req.preferences))
    cache_key = hashlib.md5(
        f"{req.dish.lower().strip()}|{req.mode}|{req.budget}|{req.region}|{req.detail_level}|{prefs_key}|{is_premium}".encode()
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

        async def from_cache():
            yield json.dumps(cached)
        return StreamingResponse(from_cache(), media_type="text/plain")

    prompt = _build_prompt(req, is_premium=is_premium)

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
            # Слой 2 защиты brand: убираем скобки со страной/пояснением из brand.
            # "Köstritzer Schwarzbier (Германия)" → "Köstritzer Schwarzbier"
            # Причина: brand идёт в поисковую ссылку Kaspi/Magnum — "(Германия)"
            # в запросе ломает поиск. Также срезаем trailing " или ..." / " / ..."
            # как защитный слой на случай если Claude проигнорировал промпт-правило.
            for r in results:
                brand = r.get("brand", "")
                if isinstance(brand, str) and brand:
                    brand = re.sub(r"\s*\([^)]*\)", "", brand)
                    brand = re.split(r"\s+(?:или|/|,|\sлибо\s|\s—\s)", brand, maxsplit=1)[0]
                    r["brand"] = brand.strip()
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
    """Очистка истории + сброс счётчика pairing_count.

    Раньше только удаляла записи из pairings, но лимит считается по
    users.pairing_count — после очистки счётчик оставался 10/10 и Free
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
