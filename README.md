# Telegram Group Admin Bot

Multi-tenant Telegram bot for group administration with moderation, antispam, captcha, and customizable settings.

## Features

- **Moderation**: `/ban`, `/kick`, `/mute`, `/warn` with durations
- **Warn System**: Automatic actions when warn limit reached
- **Captcha**: Button, math, or emoji captcha for new members
- **Antispam**: Filter links, forwards, stopwords
- **Welcome Messages**: Customizable with placeholders
- **i18n**: Russian (ru) and English (en) localization
- **Multi-tenant**: One bot instance handles multiple groups independently

## Tech Stack

- Python 3.12
- aiogram 3 (webhook mode)
- PostgreSQL + SQLAlchemy 2.0
- Redis (FSM storage, cache)
- Docker Compose
- pytest for testing

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12 (for local development)
- Telegram Bot Token

### Environment Setup

1. Copy `.env.example` to `.env` and fill in values:
   ```bash
   cp .env.example .env
   ```

2. Required variables:
   - `TELEGRAM_BOT_TOKEN`: Your bot token from @BotFather
   - `WEBHOOK_URL`: Public URL for webhook (e.g., `https://yourdomain.com/webhook`)
   - `WEBHOOK_SECRET`: Random secret string
   - `OWNER_ID`: Your Telegram user ID

### Run with Docker

```bash
make up
```

This starts:
- Bot (webhook server on port 8000)
- PostgreSQL (port 5432)
- Redis (port 6379)

Migrations run automatically on startup.

### Local Development

```bash
# Create venv
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies (runtime + test/lint tooling)
pip install -r requirements-dev.txt

# For running the app locally (outside Docker), point at your services:
# export DATABASE_URL=postgresql+asyncpg://admin:password@localhost:5432/telegram_bot
# export REDIS_URL=redis://localhost:6379/0

# Run tests
pytest tests/ -v

# Format code
make format

# Run linters
make lint
```

Database migrations are applied automatically on bot startup (Alembic
`upgrade head`); run them manually with `make migrate`.

## Project Structure

```
bot/
  __main__.py           # Webhook server, middleware/router wiring, lifecycle
  config.py             # Settings (pydantic-settings) + logging bootstrap
  logging_conf.py       # Structured JSON logging + secret redaction + rotation
  constants.py          # Enums/limits/defaults for settings
  db/
    models.py           # SQLAlchemy models (timezone-aware, indexed)
    session.py          # Async engine + sessionmaker
    crud.py             # Async data-access layer (repository functions)
    migrations/         # Alembic (async env.py)
  cache/
    redis.py            # Redis client + settings/stopwords/captcha helpers
  i18n/
    loader.py           # Localization manager (lang fallback)
    ru.json, en.json    # Translations
  filters/
    is_admin.py         # Admin/owner check filter
    chat_type.py        # Private vs group filters
  middlewares/
    base.py             # chat/user extraction helpers
    database.py         # One committed session per update
    settings.py         # Auto-register chat, load settings (cache -> DB)
    i18n.py             # Locale resolution + `_` translator injection
    admin.py            # is_admin/is_owner detection (Redis-cached)
  handlers/
    common.py           # /start, /help
    moderation.py       # /ban /unban /kick /mute /unmute /warn /unwarn /warns
    settings_cmd.py     # /settings, /welcome, /captcha, /antispam, /addstop ...
    captcha.py          # New-member captcha: restrict -> challenge -> verify
    welcome.py          # Welcome messages + service-message cleanup
    antispam.py         # Per-message link/forward/stopword filtering
    actions.py          # Reusable moderation actions (ban/mute/kick/warn)
  services/             # Pure business logic (unit-tested)
    antispam.py         # Spam detection
    warns.py            # Warn counting/limit logic
    captcha.py          # Captcha generation/verification
    moderation.py       # Duration parsing, unban-date math
  utils/
    text.py             # HTML-safe welcome/mention rendering, duration format
    telegram.py         # Safe API wrappers (429 backoff, 400/403 handling)
    targets.py          # Resolve moderation target (reply/mention/id/@username)
    tasks.py            # Background task registry (captcha timeout, delayed delete)
tests/
  test_*.py             # Unit + handler tests (pytest)
```

## Commands

### Moderation (admins only)

- `/ban [duration] [reason]` - Ban user (e.g., `/ban 30m spam`)
- `/unban` - Unban user
- `/kick` - Remove without ban
- `/mute [duration] [reason]` - Mute user
- `/unmute` - Unmute user
- `/warn [reason]` - Warn user
- `/unwarn` - Remove last warning
- `/warns` - Show warnings for user
- `/settings` - Show chat settings

### Admin Commands

- `/setwelcome <text>` - Set welcome message
- `/welcome on|off` - Enable/disable welcome
- `/captcha on|off` - Enable/disable captcha
- `/setcaptcha button|math|emoji` - Set captcha type
- `/antispam links|forwards|stopwords on|off` - Toggle filters
- `/addstop <word>` - Add stopword
- `/delstop <word>` - Remove stopword

## Configuration

Chat settings are managed per-group via commands or stored in `chat_settings` table:

```python
# Moderation
warn_limit: int = 3
warn_action: str = "mute"  # mute | kick | ban
warn_action_duration: Optional[int] = None

# Captcha
captcha_enabled: bool = False
captcha_type: str = "button"  # button | math | emoji
captcha_timeout: int = 300  # seconds
captcha_fail_action: str = "kick"  # kick | ban | mute

# Antispam
filter_links: bool = False
filter_forwards: bool = False
filter_stopwords: bool = False
antispam_action: str = "delete"  # delete | warn | mute | ban
antispam_exempt_admins: bool = True

# Welcome
welcome_enabled: bool = True
welcome_text: Optional[str] = None
delete_service_messages: bool = True
delete_welcome_after: Optional[int] = None  # seconds
```

## Localization

Add a new language by creating `bot/i18n/{lang}.json`:

```json
{
  "cmd_start": "Welcome!",
  "cmd_help": "Help text",
  ...
}
```

Language is selected per-chat (stored in `chats.language`) and per-user (from Telegram client).

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_antispam.py -v

# With coverage
pytest tests/ --cov=bot --cov-report=html
```

Services have test-first unit tests for pure logic. Handlers tested with mocked aiogram objects.

## Edge Cases Covered

- ✅ Bot not admin / missing permissions
- ✅ Target is admin/owner/bot
- ✅ Empty/whitespace input
- ✅ Non-existent/already banned users
- ✅ Warn limit boundary conditions
- ✅ Concurrent operations (warn race conditions)
- ✅ Anonymous admins / auto-forwards
- ✅ Telegram 429 (flood) retries
- ✅ Deleted resources (deleted/empty states)
- ✅ Permission changes between cache and action

## Logs

Structured JSON logs in `logs/app.log` with rotation (10MB × 5 files).

Configuration via `LOG_LEVEL` env variable: `debug`, `info` (default), `warn`, `error`.

## Database Migrations

Migrations use Alembic:

```bash
# Create migration
alembic revision --autogenerate -m "Add new field"

# Apply migrations (runs on docker-compose up)
alembic upgrade head

# Downgrade
alembic downgrade -1
```

## Roadmap

**Phase 1 (MVP)**: Core moderation, antispam, captcha, welcome
**Phase 2**: Rate limiting, media restrictions
**Phase 3**: Custom filters & triggers
**Phase 4**: User statistics & reports
**Phase 5**: Scheduled posting
**Phase 6**: Private menu settings
**Phase 7**: Web dashboard
**Phase 8**: Federated chats

## License

MIT

## Support

For issues or questions, open an issue in the repository.
