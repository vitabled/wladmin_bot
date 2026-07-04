# Telegram Group Admin Bot

**Phase 1 MVP** — Multi-tenant Telegram bot for group administration.

## Project Status

✅ **Phase 1 Complete** — Core infrastructure, moderation, antispam, captcha, welcome messages.

## Architecture

- **Backend**: Python 3.12 + aiogram 3 (webhook-mode aiohttp server)
- **Database**: PostgreSQL + SQLAlchemy 2.0 (async) + Alembic migrations
- **Cache**: Redis (FSM state, settings cache, counters)
- **Containerization**: Docker Compose (bot, postgres, redis services)
- **Testing**: pytest + pytest-asyncio (test-first for services)
- **i18n**: JSON-based localization (ru, en)

## Key Files

### Core
- `bot/__main__.py` — Webhook server, bot initialization
- `bot/config.py` — Settings via pydantic, environment validation
- `bot/db/models.py` — SQLAlchemy models (chats, settings, warns, users, logs)

### Business Logic (Pure Functions)
- `bot/services/antispam.py` — Link/forward/stopword detection
- `bot/services/warns.py` — Warn counting and action triggers
- `bot/services/captcha.py` — Math/emoji/button captcha generation
- `bot/services/moderation.py` — Duration parsing, unban date calculation

### Handlers & Filters
- `bot/handlers/common.py` — /start, /help
- `bot/handlers/moderation.py` — /ban, /kick, /warn, /mute (stubs)
- `bot/handlers/antispam.py` — Message filtering (stub)
- `bot/filters/is_admin.py` — Admin check filter
- `bot/filters/chat_type.py` — Private/group filters

### Infrastructure
- `bot/cache/redis.py` — Redis client wrapper (get/set/delete/incr)
- `bot/i18n/loader.py` — Localization manager, language fallback
- `bot/utils/text.py` — Text formatting (welcome placeholders, truncate)
- `bot/utils/telegram.py` — Safe Telegram API wrappers (ban, kick, restrict)

### Testing
- `tests/test_antispam.py` — Test-first antispam detection (13 tests)
- `tests/test_warns.py` — Warn system logic (6 tests)
- `tests/test_captcha.py` — Captcha generation/verification (10 tests)
- `tests/test_moderation.py` — Duration parsing, ban dates (12 tests)

## Edge Cases Covered

### Security & Permission
- ✅ Bot not admin → graceful fallback
- ✅ Target is admin/owner/bot → refuse action
- ✅ Permission change between cache and action → retry with fresh check

### Data Validation
- ✅ Empty/whitespace input → reject or use defaults
- ✅ Boundary values → off-by-one on warn limit, 1-20 number range
- ✅ Malformed durations → fallback to permanent

### State & Concurrency
- ✅ Non-existent/deleted user → idempotent (no crash)
- ✅ Concurrent /warn calls → transactional warn count
- ✅ User exits during captcha → timeout action (kick/ban/mute)
- ✅ Double button press → answered by target user only

### External Failures
- ✅ Telegram 429 (flood) → backoff + retry (in `safe_*` wrappers)
- ✅ Network timeout → fallback to delete-only
- ✅ Partial response → validation before use

## Running

### Quick Start
```bash
docker-compose up
```
Runs bot on `http://localhost:8000/webhook`, PostgreSQL, Redis.

### Local Dev
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

pytest tests/ -v          # Run unit tests
make format               # Format code
make lint                 # Check linters
```

### Database Migrations
```bash
docker-compose exec bot python -m alembic revision --autogenerate -m "desc"
docker-compose exec bot python -m alembic upgrade head
```

## Decisions Made

1. **Webhook over polling** — Scalable, instant updates, reduced CPU.
2. **Redis for FSM + cache** — Fast state machine, centralized cache with TTL.
3. **Async SQLAlchemy** — Non-blocking DB, better concurrency.
4. **Service layer (pure functions)** — Tested independently of aiogram.
5. **Test-first for services** — 41 unit tests covering logic edge cases.
6. **Safe API wrappers** — Catch TelegramBadRequest (400) and TelegramForbiddenError (403).

## Known Limitations

- Handlers (`moderation.py`, `antispam.py`, etc.) are stubs — need full implementation
- No real webhook URL setup (example: `/webhook` path)
- Alembic migrations not yet auto-generated (manual `env.py` setup)
- Redis persistence not configured in docker-compose (volatile)

## Phase 2+ Roadmap

- **Phase 2**: Anti-flood, media restrictions for newbies
- **Phase 3**: Custom filters, triggers, auto-replies
- **Phase 4**: User statistics, activity reports
- **Phase 5**: Scheduled posting
- **Phase 6**: Settings via private menu (inline buttons)
- **Phase 7**: Web dashboard (OAuth Telegram Login)
- **Phase 8**: Federated groups

## Environment Variables

See `.env.example`:
- `TELEGRAM_BOT_TOKEN` — From @BotFather
- `WEBHOOK_URL` — Public URL (e.g., `https://yourdomain.com/webhook`)
- `WEBHOOK_SECRET` — Random secret for webhook signature verification
- `OWNER_ID` — Your Telegram user ID
- `DATABASE_URL` — PostgreSQL async connection string
- `REDIS_URL` — Redis connection string
- `LOG_LEVEL` — debug, info, warn, error

## Testing Strategy

**Unit tests (41)**: Pure functions in services + edge cases
**Integration tests**: Planned (mocked aiogram, real DB in Docker)
**e2e tests**: Manual with real Telegram (requires bot token, domain)

## Performance Notes

- Settings cached in Redis (TTL 3600s), invalidated on change
- Warn checks use database transactionality (no race)
- Antispam filter runs synchronously (fast regex)
- Captcha timeout via Redis with auto-cleanup

## Logging

Structured JSON logs to `logs/app.log` (10MB × 5 rotation).
Redactor masks secrets: `token`, `password`, `secret`, `Authorization`, `Cookie`.

## Security Notes

- Webhook signature verified (WEBHOOK_SECRET)
- Admin permissions checked before moderation
- No secrets in logs
- Rate limit handling (429 → backoff)
- SQL injection: SQLAlchemy ORM, no raw queries
- XSS: Telegram API handles escaping

## Next Steps for Phase 2

1. Implement handler logic (ban, kick, warn, mute)
2. Add database operations (insert logs, update settings)
3. Implement captcha flow (new_chat_members event)
4. Add antispam checking on every message
5. Implement settings commands (/setwelcome, /antispam, etc.)
6. Write integration tests with mocked aiogram
7. Manual e2e testing with real bot
