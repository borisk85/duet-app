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

GLOBAL_BRANDS_REFERENCE = """ГЛОБАЛЬНАЯ БАЗА ХОДОВЫХ БРЕНДОВ ДОСТУПНЫХ ВЕЗДЕ В СНГ (используй активно, не зацикливайся на самых очевидных):

🍺 ПИВО:
— Лагер мировой: Corona Extra, Heineken, Carlsberg, Stella Artois, Becks, Tuborg, Holsten, Warsteiner, Bitburger, Krombacher, Pilsner Urquell, Budweiser Budvar, Tsingtao, Asahi
— Немецкий пшеничный/крафт: Paulaner, Erdinger, Franziskaner, Schneider Weisse, Maisel's Weisse, Weihenstephaner
— Бельгийский: Hoegaarden, Leffe (Blonde/Brune/Ruby), Duvel, Chimay (Red/Blue/White), Westmalle, Kwak, La Chouffe
— Стаут/портер: Guinness Draught, Guinness Extra Stout, Murphy's, Beamish, Köstritzer Schwarzbier
— IPA / крафт международный: Brewdog (Punk IPA, Hazy Jane, Elvis Juice), Lagunitas, Sierra Nevada, Mikkeller
— Сидр и фруктовое: Strongbow, Somersby, Magners, Kopparberg, Lindemans (Kriek, Framboise)

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
    "Казахстан": "Бренды доступные в Алматы и Астане. Помимо локальных (Шымкентское, Карагандинское, Тянь-Шань) широко представлены: международные пива (Corona Extra, Heineken, Carlsberg, Krombacher, Paulaner, Bitburger, Erdinger, Hoegaarden, Leffe, Pilsner Urquell, Budweiser Budvar), крафтовые казахстанские пивоварни (Tsarka, Brewster, Mezhdu Strok), европейские вина, грузинские вина (Saperavi, Mukuzani, Kindzmarauli), армянские коньяки (Ararat, Noy), импортные виски и текила. НЕ зацикливайся на самых очевидных местных брендах.",
    "Россия":    "Бренды доступные в крупных городах РФ с учётом ограничений импорта 2024-2026. Помимо очевидных (Балтика, Жигулёвское) широко представлены: российские крафтовые пивоварни (AF Brew, Salden's, Konix, Stamm, Brewlok, Zagovor), доступные импортные пива (Krombacher, Paulaner, Hoegaarden, Pilsner Urquell, Bitburger через параллельный импорт), грузинские вина (Saperavi, Mukuzani), крымские и кубанские вина (Massandra, Inkerman, Fanagoria, Lefkadia), армянские и российские коньяки (Ararat, Kvint, Kizlyar), азиатские крепкие напитки. НЕ предлагай Балтику и Жигулёвское по умолчанию — давай разнообразие.",
    "Украина":   "Бренды доступные в Киеве и крупных городах Украины. Помимо локальных (Львівське, Чернігівське, Оболонь) широко представлены: украинские крафтовые пивоварни (Varvar, Pravda, Volynski Browar), европейские пива (Heineken, Carlsberg, Krombacher, Paulaner), европейские вина, грузинские вина, украинские игристые. Давай разнообразие, не зацикливайся на массовых брендах.",
    "Беларусь":  "Бренды доступные в Минске и крупных городах Беларуси. Помимо локальных (Лидское, Аліварыя, Крыніца) широко представлены: международные пива (Heineken, Carlsberg, Krombacher), европейские и грузинские вина, российские крафтовые пивоварни. Не зацикливайся на самых очевидных местных брендах.",
    "СНГ":       "Универсальные бренды присутствующие во всех странах СНГ: международные пива (Corona Extra, Heineken, Carlsberg, Krombacher, Paulaner, Hoegaarden, Pilsner Urquell), европейские и грузинские вина, армянские коньяки, импортные виски и крепкий алкоголь. Давай разнообразие, не зацикливайся на одной стране-производителе.",
}

DETAIL_LEVEL_MAP = {
    "simple":   "РЕЖИМ ПРОСТО: для новичка который не разбирается. Reason — РОВНО одно короткое предложение почему подходит, простыми бытовыми словами. Serving_tip — РОВНО одна короткая фраза с практичным советом (например 'Подавать охлаждённым' или 'Открыть за 15 минут до подачи'). Никаких терминов вообще — ни 'танины', ни 'кислотность', ни 'минералы', ни сорта винограда. Только бытовой язык. Цель — чтобы было понятно человеку который вообще не пьёт алкоголь регулярно.",
    "standard": "РЕЖИМ СТАНДАРТ: сбалансированное гастрономическое объяснение для аудитории которая ходит в рестораны. Reason — 2-3 предложения с вкусовыми характеристиками на ЖИВОМ человеческом языке. Используй слова: 'танины', 'кислотность', 'минеральность', 'шёлковистая текстура', 'лёгкая горчинка', 'фруктовые ноты', 'округлый вкус'. Можно упомянуть сорт винограда РУССКИМИ названиями ('Каберне Совиньон', 'Шардоне', 'Темпранильо'). Объясняй сочетание через вкусовой контраст или гармонию ('кислотность освежает после жирного мяса', 'танины смягчают остроту'). Serving_tip — практичный совет: температура подачи в °C, тип посуды, с чем подавать, как открывать.",
    "expert":   "РЕЖИМ ЭКСПЕРТ: профессиональное описание для знатока который читает Decanter и ходит в винотеки. Reason — 2-3 предложения с глубокой информацией: сорт винограда (можно использовать оригинальные международные названия 'Cabernet Sauvignon', 'Pinot Noir', 'Sangiovese'), регион происхождения с подробностями ('Bordeaux, Левый берег', 'Barolo, Пьемонт', 'Rioja Alta'), выдержка ('18 месяцев в дубе', '5 лет в бочках из-под бурбона'), стилистика производителя. Обосновывай парность через вкусовой профиль — танины, кислотность, минеральность, фруктовость, дубовые ноты, длина послевкусия. Serving_tip — точная температура подачи в °C, тип бокала ('Bordeaux universal', 'Burgundy balloon', 'Glencairn для виски'), декантация/аэрация/время дыхания если применимо.",
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

def _build_prompt(req: PairRequest) -> str:
    budget_desc = BUDGET_MAP[req.budget]
    availability = REGION_AVAILABILITY.get(req.region, REGION_AVAILABILITY["СНГ"])
    detail_desc = DETAIL_LEVEL_MAP[req.detail_level]
    if req.mode == "food_to_alcohol":
        return f"""{EXPERT_ROLE}

{GLOBAL_BRANDS_REFERENCE}

Пользователь из региона {req.region}.
Блюдо: {req.dish}
Бюджет: {budget_desc}
Доступность: {availability}
{detail_desc}
Подбери ТОП-3 напитка разных типов. Первым ставь напиток наиболее традиционный для данной кухни.
ВАЖНО про price_range: ТОЛЬКО короткий диапазон цены, максимум 12 символов. Без скобок, без описаний, без слов "или", "за бутылку", "в баре", "домашнего". Правильно: "$15-20", "~$50", "$80-120". Неправильно: "$15-20 (бутылка)", "$12-18 или $6 домашнего".
Верни ТОЛЬКО валидный JSON без markdown:
{{"results":[{{"alcohol_type":"тип","alcohol_type_emoji":"🍷","name":"название","brand":"конкретная марка доступная в {req.region}","reason":"объяснение в соответствии с режимом детализации","price_range":"$X-Y","serving_tip":"совет по подаче в соответствии с режимом детализации"}}]}}"""
    else:
        return f"""{EXPERT_ROLE}

{GLOBAL_BRANDS_REFERENCE}

Пользователь из региона {req.region}.
Напиток: {req.dish}
{detail_desc}
Подбери ТОП-3 блюда/закуски к этому напитку.
ВАЖНО про price_range: ТОЛЬКО короткая цифровая оценка, максимум 12 символов. Без скобок и описаний. Правильно: "~$10", "$5-15". Неправильно: "~$10 (порция)", "$5-15 в ресторане".
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
