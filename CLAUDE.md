# Telegram Group Admin Bot

**Phase 1 MVP** — Multi-tenant Telegram bot for group administration.

## Project Status

✅ **Phase 1 Complete** — Core infrastructure, moderation, antispam, captcha, welcome messages.
✅ **Phase 2 Complete** — Anti-flood (per-window counter) + media restrictions for newbies; ☰ command menu (`set_my_commands`).

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
- `bot/services/antiflood.py` — Flood predicate + newbie restricted-media check (Phase 2)
- `bot/services/warns.py` — Warn counting and action triggers
- `bot/services/captcha.py` — Math/emoji/button captcha generation
- `bot/services/moderation.py` — Duration parsing, unban date calculation

### Handlers & Filters
- `bot/handlers/common.py` — /start, /help (localized)
- `bot/handlers/moderation.py` — /ban /unban /kick /mute /unmute /warn /unwarn /warns
- `bot/handlers/settings_cmd.py` — /settings, /welcome, /captcha, /antispam, /addstop …
- `bot/handlers/captcha.py` — New-member captcha (restrict → challenge → verify/timeout)
- `bot/handlers/welcome.py` — Welcome messages + service-message cleanup
- `bot/handlers/antispam.py` — Per-message link/forward/stopword filtering (also invokes Phase 2 guards)
- `bot/handlers/antiflood.py` — Anti-flood + newbie-media guards, called from the per-message handler (Phase 2)
- `bot/commands.py` — Registers the ☰ command menu (`set_my_commands`) on startup
- `bot/handlers/actions.py` — Reusable moderation actions (ban/mute/kick/warn)
- `bot/filters/is_admin.py` — Admin/owner check filter
- `bot/filters/chat_type.py` — Private/group filters

### Middlewares (outer, per update)
- `bot/middlewares/database.py` — One committed session per update
- `bot/middlewares/settings.py` — Auto-register chat, load settings (Redis→DB)
- `bot/middlewares/i18n.py` — Locale resolution + `_` translator injection
- `bot/middlewares/admin.py` — is_admin/is_owner detection (Redis-cached)

### Infrastructure
- `bot/db/crud.py` — Async data-access layer (repository functions)
- `bot/logging_conf.py` — Structured JSON logging + secret redaction + rotation
- `bot/cache/redis.py` — Redis client + settings/stopwords/captcha helpers
- `bot/i18n/loader.py` — Localization manager, language fallback
- `bot/utils/text.py` — HTML-safe welcome/mention rendering, duration format
- `bot/utils/telegram.py` — Safe API wrappers (429 backoff, 400/403 handling)
- `bot/utils/targets.py` — Resolve moderation target (reply/mention/id/@username)
- `bot/utils/tasks.py` — Background task registry (captcha timeout, delayed delete)

### Testing (143 tests)
- `tests/test_antispam.py` — Antispam detection (service)
- `tests/test_antiflood.py` — Anti-flood / newbie-media predicates (service)
- `tests/test_handlers_antiflood.py` — Flood/newbie guards + /antiflood /newbie commands
- `tests/test_commands.py` — ☰ command-menu registration
- `tests/test_warns.py` — Warn system logic (service)
- `tests/test_captcha.py` — Captcha generation/verification (service)
- `tests/test_moderation.py` — Duration parsing, ban dates (service)
- `tests/test_targets.py` — Target resolution
- `tests/test_actions.py` — Warn cascade + ban/mute/kick actions
- `tests/test_handlers_*.py` — Handlers via mocked aiogram (moderation, antispam, captcha, settings, welcome)

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
pip install -r requirements-dev.txt   # runtime + test/lint tooling

pytest tests/ -v          # Run tests (114)
make format               # Format code (ruff + black)
make lint                 # ruff + black --check + mypy (services)
```

### Database Migrations
Applied automatically on bot startup (`alembic upgrade head`). Manually:
```bash
make migrate                                    # upgrade head in the container
make revision m="add field"                     # autogenerate a new migration
```

## Decisions Made

1. **Webhook over polling** — Scalable, instant updates, reduced CPU.
2. **Redis for FSM + cache** — Fast state machine, centralized cache with TTL.
3. **Async SQLAlchemy** — Non-blocking DB, better concurrency.
4. **Service layer (pure functions)** — Tested independently of aiogram.
5. **Test-first for services** — services unit-tested; handlers via mocked aiogram (114 total).
6. **Safe API wrappers** — retry 429 (`TelegramRetryAfter`) with backoff; catch 400/403.
7. **Migrations auto-applied on startup** — `alembic upgrade head` runs in a worker thread before the webhook is served (schema failure is fatal; webhook-set failure is not).

## Known Limitations

- New-member detection uses `message.new_chat_members` (not `chat_member` updates); works when the bot is admin.
- `@username` targeting resolves via the cached `users` table, falling back to `bot.get_chat` (only users the bot has seen or public entities).
- e2e against real Telegram not run in CI (no test bot/domain); verified via `docker compose up` (services healthy, migrations applied, `/health` ok).

## Phase 2+ Roadmap

- ✅ **Phase 2**: Anti-flood, media restrictions for newbies — **done**
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

**Unit tests**: Pure service functions + edge cases (antispam, warns, captcha, moderation, targets).
**Handler tests**: aiogram handlers with mocked Message/CallbackQuery/Bot/session (moderation, antispam, captcha, settings, welcome, actions).
**e2e**: `docker compose up` — services healthy, migrations applied, `/health` responds. Real-Telegram e2e is manual (needs bot token + domain).

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
