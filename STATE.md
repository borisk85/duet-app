# STATE — Дуэт (pairing-app)
Обновлено: 2026-07-20

## ПРАВИЛА ЭТОГО ФАЙЛА
- Единственное место рабочих заметок и памяти между сессиями.
- Агент читает этот файл в начале каждой сессии и обновляет после каждого этапа.
- В работе всегда ровно одна задача.

## В РАБОТЕ (ровно 1)
- [ ] RevenueCat — реальная интеграция вместо заглушки paywall (ждем approval Internal Testing в Google Play)

## ОЧЕРЕДЬ (по порядку)
1. Бренд в карточке избранного обрезается (favorites_screen.dart:345-350) — две строки вместо одной с ellipsis. Решено делать до релиза
2. Google Play — довести стор-листинг до публикации (тексты из docs/MARKETING.md, иконка 512px готова)

## СДЕЛАНО (с датой, новое сверху)
- [2026-07-20] main запушен в origin (ac824d8..32e9b1e)
- [2026-07-20] Миграция на BASE_RULES + STATE.md; хвост закоммичен (формат result_screen, market research)
- [2026-07] README и проектная документация для портфолио
- [2026-07] Скриншоты Play Store + bump 1.0.1+3 для AAB
- [2026-07] Промпт: сегмент вместо бюджета, разнообразие вина, антиповтор брендов и категорий, канонические пары
- [2026-07] Firebase Performance: автометрики HTTP + ручные трейсы на ключевых экранах
- [2026-07] Перф: compute()-изолят для JSON, singleton HTTP-клиента, убрано двойное парсирование в getHistory()
- [2026-07] Безопасность: сырые исключения больше не уходят в ответы API

## КОНТЕКСТ ПРОЕКТА (кратко, только факты)
- Стек: Flutter 3.x / Dart SDK ^3.11.4 (firebase_core, firebase_auth, firebase_performance, google_sign_in, http, shared_preferences, share_plus) + Python FastAPI 0.115 (anthropic, psycopg2, firebase-admin, slowapi)
- Версия: 1.0.1+3
- Деплой: бэкенд на Railway (авто на git push origin main), iOS-сборки Codemagic, БД PostgreSQL на Railway
- Ключевые решения: стриминг Claude через text/plain; кеш в PostgreSQL 30 дней с инвалидацией по PROMPT_VERSION; expert-режим только Premium, проверка на бэкенде; free-лимит 10 запросов
- Оплата: RevenueCat — пока заглушка
- Публичные тексты — только из docs/MARKETING.md

## СЛЕДУЮЩИЙ ШАГ ПОСЛЕ ПЕРЕРЫВА
- Взять задачу 1 из очереди: бренд в карточке избранного. Boris сказал «вернемся позже» — план правки UI показать до кода (BASE_RULES §9).
- Параллельно проверить статус Internal Testing в Google Play Console; если approved — разблокируется RevenueCat.
