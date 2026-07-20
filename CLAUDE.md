@C:\Users\bkoma\.claude\BASE_RULES.md

# Duet — AI Drink Pairing App

## Product overview

Duet is a production mobile app (iOS + Android) that recommends drinks to pair with any dish using Claude AI. User describes a dish, picks an answer mode, and receives a personalized pairing streamed in real time. Built for CIS markets. Subscription-based (free tier: 10 requests, Premium: unlimited + Expert mode).

App name: **Дуэт** (Russian UI). Do not call it "Duet AI" or "Duet".

## Repository layout

```
/
├── lib/                        # Flutter app (Dart) — mobile frontend
│   ├── main.dart               # Entrypoint: Firebase init, routing, orientation lock
│   ├── screens/                # UI screens (home, result, history, favorites, profile, paywall, auth, onboarding)
│   ├── services/               # api_service.dart, auth_service.dart, storage_service.dart
│   ├── models/                 # PairingResult and related data models
│   └── widgets/                # Reusable UI components
├── backend/                    # Python backend
│   ├── main.py                 # Entrypoint: all FastAPI routes + Claude streaming logic
│   ├── requirements.txt
│   ├── schema.sql              # PostgreSQL schema
│   └── .env.example            # Template for required env vars
├── assets/                     # App icons, fonts, images
├── android/                    # Android config (Gradle, signing)
├── ios/                        # iOS config
└── docs/                       # Marketing and business docs
```

## Backend entrypoint — `backend/main.py`

FastAPI app. Start locally:
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

Deployed on **Railway** (auto-deploys on `git push origin main`).

### Routes

| Method | Path | Description |
|---|---|---|
| `POST` | `/pair/stream` | Main endpoint — streaming Claude pairing response |
| `GET` | `/history` | User's pairing history (last 30 days) |
| `DELETE` | `/history` | Clear user history |
| `GET` | `/favorites` | User's saved pairings |
| `POST` | `/favorites` | Save a pairing |
| `DELETE` | `/favorites/{id}` | Remove a saved pairing |
| `GET` | `/me` | Current user info + premium status + free count |
| `GET` | `/health` | Healthcheck |

### Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `MODEL` | Claude model ID (default: `claude-haiku-4-5-20251001`) |
| `DATABASE_URL` | PostgreSQL connection string |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase Admin SDK credentials (full JSON string) |

### Key internals

- **Streaming:** `AsyncAnthropic.messages.stream` → `StreamingResponse(text/plain)` → `Stream<String>` in Flutter
- **PostgreSQL cache:** 30-day TTL, probabilistic cleanup (5% per insert), auto-invalidates via `PROMPT_VERSION` integer in cache key
- **Detail levels:** `simple` / `standard` / `expert` — expert is Premium-only, enforced server-side at the top of `pair_stream`
- **Pairing directions:** `food_to_alcohol` (default) and `alcohol_to_food`
- **Auth:** every protected route calls `_verify_token_sync(request)` which validates a Firebase ID token via Firebase Admin SDK

## Mobile app entrypoint — `lib/main.dart`

Flutter app: Firebase init, locks orientation to portrait, sets up `go_router` routing.

Key services:
- `lib/services/api_service.dart` — `pairStream()` opens an HTTP streaming request to `/pair/stream`, yields `String` chunks
- `lib/services/auth_service.dart` — `signInWithGoogle()` and `signInAnonymously()` via Firebase Auth; passes ID token in `Authorization: Bearer` header
- `lib/services/storage_service.dart` — local persistence (SharedPreferences)

## Tech stack

| Layer | Tech |
|---|---|
| Mobile | Flutter 3.x, Dart |
| Backend | Python 3.11, FastAPI |
| Hosting | Railway (backend), Codemagic (iOS builds) |
| AI | Claude API — streaming via Anthropic SDK |
| Auth | Firebase Authentication (Google + anonymous) |
| Payments | RevenueCat (subscription paywall) |
| Database | PostgreSQL (Railway) |

## Design tokens

| Role | Hex |
|---|---|
| Background | `#0D0D0D` |
| Cards | `#1A1A1A` |
| Accent gold (large) | `#C9A84C` |
| Gold (small text, AA) | `#D4B563` |
| Text primary | `#FFFFFF` |
| Text secondary | `rgba(255,255,255,0.4)` |

## UI conventions

- Bottom Navigation Bar (no drawer/hamburger)
- Skeleton screens instead of spinners
- Minimum tap target 48px
- 8px grid (8, 16, 24, 32...)
- Haptic feedback on key actions
- Russian UI language — address user as вы/ваш

## Error handling

| Situation | Response |
|---|---|
| 5xx from backend | "Сервис временно недоступен. Попробуйте через минуту." + Retry |
| Timeout >30s | "Долго думаем... Попробуйте ещё раз" |
| 429 rate limit | "Достигнут дневной лимит. Обновите до Premium для безлимита." |
| No network | "Нет подключения к сети" |
| Empty input | Inline error below field (not SnackBar) |

All SnackBars: `Colors.red.shade800`, floating, rounded.

## Public texts and marketing

All approved public-facing texts are in `docs/MARKETING.md`. Do not write app store copy, taglines, or descriptions from scratch — check that file first.

## Working notes

Working queue and session memory live in `STATE.md` (see BASE_RULES §3, §11).

Exception to BASE_RULES §1: `docs/BACKLOG.md` is a product document — v2+ ideas, not a working queue. It stays as is and is not merged into `STATE.md`.
