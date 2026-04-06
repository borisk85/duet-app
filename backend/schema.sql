-- Пользователи (создаются при первом запросе, uid из Firebase)
CREATE TABLE IF NOT EXISTS users (
    firebase_uid  TEXT PRIMARY KEY,
    email         TEXT,
    is_premium    BOOLEAN   DEFAULT FALSE,
    pairing_count INTEGER   DEFAULT 0,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- История подборок
CREATE TABLE IF NOT EXISTS pairings (
    id           SERIAL PRIMARY KEY,
    firebase_uid TEXT      NOT NULL REFERENCES users(firebase_uid) ON DELETE CASCADE,
    dish         TEXT      NOT NULL,
    mode         TEXT      NOT NULL,
    budget       TEXT      NOT NULL,
    region       TEXT      NOT NULL,
    results      JSONB     NOT NULL,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- Избранное (уникальность: один пользователь не может сохранить одно блюдо+бюджет дважды)
CREATE TABLE IF NOT EXISTS favorites (
    id           SERIAL PRIMARY KEY,
    firebase_uid TEXT      NOT NULL REFERENCES users(firebase_uid) ON DELETE CASCADE,
    dish         TEXT      NOT NULL,
    mode         TEXT      NOT NULL,
    budget       TEXT      NOT NULL,
    region       TEXT      NOT NULL,
    results      JSONB     NOT NULL,
    created_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE (firebase_uid, dish, budget)
);

CREATE INDEX IF NOT EXISTS idx_pairings_uid_date ON pairings (firebase_uid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_favorites_uid      ON favorites (firebase_uid);

-- Персистентный кеш подборок (переживает redeploy Railway, не сбрасывается между запусками контейнера)
-- Ключ — md5 от dish + mode + budget + region + detail_level
-- TTL 24 часа, чистится вероятностно при INSERT (5% шанс)
CREATE TABLE IF NOT EXISTS pairing_cache (
    cache_key  TEXT      PRIMARY KEY,
    result     JSONB     NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pairing_cache_created ON pairing_cache (created_at);
