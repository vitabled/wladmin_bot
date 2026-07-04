# Промпт — Phase 1 (MVP): ядро бота-администратора Telegram-групп

> Это ТЗ для AI-агента, который пишет код автономно. Реализуй ПОЛНОСТЬЮ описанный ниже объём,
> с тестами и рабочим `docker-compose up`. Не выходи за границы MVP (см. «Вне scope»).

## Роль и цель

Ты — senior backend-инженер. Построй **multi-tenant Telegram-бота для администрирования групп/супергрупп**
(аналог ChatKeeper) — один инстанс бота обслуживает произвольное число групп, у каждой свои независимые настройки.
Приоритеты в порядке важности: **надёжность → скорость → простота поддержки**.

## Технологический стек (обязательный)

- **Язык:** Python 3.12
- **Фреймворк бота:** aiogram 3 (webhook-режим, встроенный aiohttp-сервер)
- **БД:** PostgreSQL, доступ через SQLAlchemy 2.0 (async, `asyncpg`), миграции — Alembic
- **Кэш/состояние:** Redis — FSM-хранилище aiogram (`RedisStorage`), кэш настроек чатов, счётчики, pending-капча
- **i18n:** локализация ru/en, архитектура с лёгким добавлением языков; локаль на уровне чата (для групповых сообщений) и на уровне пользователя (для ЛС); дефолт — по `language_code` Telegram-клиента
- **Контейнеризация:** Docker + docker-compose (сервисы: `bot`, `postgres`, `redis`) с healthcheck'ами
- **Тесты:** pytest + pytest-asyncio; хендлеры — через мок-объекты aiogram; чистая логика — обычными юнит-тестами
- **Логирование:** структурный логгер (`structlog` или stdlib `logging` в JSON) с ротацией по размеру (10MB×5), уровни debug/info/warn/error, дефолт `info`, переключение через `LOG_LEVEL`. Секреты (токен, `Authorization`, cookie, пароли) — редактор в `[REDACTED]`, никогда в логах.

**ВАЖНО про версии:** не угадывай версии пакетов по памяти. Перед добавлением каждой зависимости получи
актуальную стабильную версию из PyPI (`pip index versions <pkg>`). Node/prettier для этого проекта не нужны.

## Архитектура

```
bot/
  __main__.py            # точка входа: webhook-сервер, graceful shutdown
  config.py              # pydantic-settings: чтение из env, валидация
  logging_conf.py        # настройка structured logging + редактор секретов
  db/
    models.py            # SQLAlchemy-модели
    session.py           # async engine + sessionmaker
    migrations/          # Alembic
  cache/
    redis.py             # клиент Redis + хелперы (кэш настроек, TTL-ключи)
  i18n/                  # .ftl (Fluent) либо .po (gettext) + middleware выбора локали
  middlewares/
    settings.py          # подгрузка настроек чата (из Redis, fallback в PostgreSQL) в data
    i18n.py              # выбор локали
    admin.py             # определение прав отправителя (кэш админов чата)
  filters/
    is_admin.py          # фильтр «отправитель — админ чата»
    chat_type.py         # групповые vs ЛС
  handlers/
    common.py            # /start, /help
    moderation.py        # /ban /unban /kick /mute /unmute /warn /unwarn /warns
    antispam.py          # проверка сообщений
    captcha.py           # обработка входа новичков и проверки
    welcome.py           # приветствие + удаление сервисных сообщений
    settings_cmd.py      # настройка чата командами
  services/
    moderation.py        # бизнес-логика наказаний (чистая, тестируемая)
    warns.py             # логика варнов и авто-наказания
    antispam.py          # логика детекции спама (чистая)
    captcha.py           # генерация/проверка капчи (чистая)
  utils/
    telegram.py          # безопасные обёртки над Bot API (ретраи на 429, обработка 400)
    text.py              # плейсхолдеры приветствий, парсинг длительностей (10m, 2h, 1d)
tests/
docker-compose.yml
docker-compose.override.yml.example   # локальная разработка
Dockerfile
.env.example
alembic.ini
Makefile
README.md
```

Групповые команды доступны только **админам чата** (и владельцу бота из env `OWNER_ID`).
Бот должен корректно вести себя, если он сам **не** админ или у него не хватает прав.

## Модель данных (PostgreSQL, multi-tenant)

- **chats**: `chat_id (PK, BIGINT)`, `title`, `type`, `language (default 'ru')`, `added_at`, `is_active`
- **chat_settings** (1:1 к chats): все настройки фич — например:
  - приветствие: `welcome_enabled bool`, `welcome_text text (nullable)`, `delete_service_messages bool`, `delete_welcome_after int (сек, nullable)`
  - капча: `captcha_enabled bool`, `captcha_type enum(button,math,emoji)`, `captcha_timeout int (сек)`, `captcha_fail_action enum(kick,ban,mute)`
  - варны: `warn_limit int (default 3)`, `warn_action enum(mute,kick,ban)`, `warn_action_duration int (сек, nullable — null=навсегда)`
  - антиспам: `filter_links bool`, `filter_forwards bool`, `filter_stopwords bool`, `antispam_action enum(delete,warn,mute,ban)`, `antispam_exempt_admins bool (default true)`
- **stopwords**: `id`, `chat_id (FK)`, `word`, `created_at` — список стоп-слов чата
- **warns**: `id`, `chat_id`, `user_id`, `admin_id`, `reason (nullable)`, `created_at`, `is_active bool` — история варнов
- **mod_log** (audit): `id`, `chat_id`, `actor_id`, `target_id`, `action`, `reason`, `duration`, `created_at`
- **users** (минимально): `user_id (PK)`, `first_name`, `username`, `last_seen`

Настройки чата кэшируются в Redis (ключ `chat_settings:{chat_id}`, TTL + инвалидация при изменении).
Pending-капча живёт только в Redis (ключ `captcha:{chat_id}:{user_id}` с TTL = таймаут).

## Функциональные требования

### 1. Инфраструктура и запуск
- `docker-compose up` поднимает бота (webhook), PostgreSQL, Redis; Alembic-миграции применяются на старте (или отдельной командой `make migrate`).
- Webhook: конфигурируемый `WEBHOOK_URL` + `WEBHOOK_SECRET`; входящие апдейты проверяются по secret-token заголовку.
- `.env.example` со всеми переменными; `config.py` валидирует их через pydantic-settings, падает с понятной ошибкой при отсутствии обязательных.
- Graceful shutdown: снятие webhook / закрытие пулов БД и Redis.

### 2. Общие команды
- `/start`, `/help` — работают в ЛС и в группе, локализованы; в группе `/help` кратко перечисляет команды модерации (только админам).

### 3. Модерация командами
Команды (цель задаётся reply на сообщение, либо `@username`, либо числовым id):
- `/ban [длительность] [причина]` — бан (kick+ban); длительность опциональна (`30m`,`2h`,`1d`) → временный бан
- `/unban`
- `/kick` — удалить без блокировки (ban+unban)
- `/mute [длительность] [причина]`, `/unmute`
- `/warn [причина]` — выдать предупреждение; при достижении `warn_limit` — авто-наказание (`warn_action`)
- `/unwarn` — снять последнее активное предупреждение; `/warns` — показать активные варны цели
Все действия пишутся в `mod_log`. Ответ бота — локализованное подтверждение с указанием цели, действия, причины.

### 4. Антиспам
На каждое НЕслужебное сообщение в группе (учитывать и `edited_message`):
- **Ссылки** (`filter_links`): url/text_link-entity, `t.me/…`, @-упоминания каналов — по настройке
- **Пересланные** (`filter_forwards`): `forward_origin` присутствует
- **Стоп-слова** (`filter_stopwords`): совпадение по словам, регистронезависимо, с учётом простых обходов (пробелы/пунктуация между буквами — базовая нормализация)
- Админы освобождены, если `antispam_exempt_admins` (по умолчанию да)
- Действие по `antispam_action`: удалить сообщение и/или warn/mute/ban. Всё логируется.

### 5. Капча
- На вход нового участника (`chat_member`/`message.new_chat_members`) при `captcha_enabled`: сразу **ограничить** пользователя (запрет отправки сообщений), отправить капчу выбранного `captcha_type`:
  - `button` — «Я не бот» inline-кнопка
  - `math` — простой пример, варианты ответов кнопками
  - `emoji` — выбрать нужный эмодзи из набора
- Верна → снять ограничения, удалить сообщение капчи, запустить приветствие.
- Таймаут (`captcha_timeout`) без прохождения → `captcha_fail_action` (kick/ban/mute) + удалить сообщение капчи.
- Кнопки капчи реагируют только на целевого пользователя; чужие нажатия — alert «не для вас».

### 6. Приветствия
- После прохождения капчи (или сразу при входе, если капча выключена) при `welcome_enabled` — отправить `welcome_text` с плейсхолдерами: `{first_name}`, `{mention}`, `{username}`, `{chat_title}`, `{members_count}`. Дефолтный текст, если не задан.
- `delete_welcome_after` — авто-удаление приветствия через N секунд (если задано).
- `delete_service_messages` — удалять системные сообщения о входе/выходе.

### 7. Настройка чата командами (только админы)
Например: `/settings` (показать текущие), `/welcome on|off`, `/setwelcome <текст>`, `/captcha on|off`,
`/setcaptcha button|math|emoji`, `/setwarnlimit <N>`, `/setwarnaction mute|kick|ban [длительность]`,
`/antispam links|forwards|stopwords on|off`, `/addstop <слово>`, `/delstop <слово>`, `/stopwords`.
Изменение → запись в PostgreSQL + инвалидация кэша Redis.

## Edge-cases — обязательно покрыть тестами

- **Бот не админ / не хватает прав** (`can_restrict_members` и т.д.) → понятное сообщение, без падения
- **Цель — админ/владелец чата или сам бот** → отказ, обработка Telegram 400
- **Команда без цели** (нет reply и аргумента) → подсказка по использованию
- **Несуществующий/уже забаненный/уже размьюченный** пользователь → идемпотентная реакция
- **Снятие варна/бана, которого нет** (deleted/empty resource) → корректное сообщение
- **Пустой ввод / whitespace-only** для `/setwelcome`, `/addstop`
- **Malformed input**: `/setwarnlimit abc`, отрицательные/огромные числа, некорректная длительность
- **Boundary**: `warn_limit=1`, очень длинный текст приветствия (лимит Telegram), off-by-one на достижении лимита варнов
- **Concurrency/races**: двойной `/warn` одновременно (транзакция на подсчёт), пользователь вышел во время капчи, повторное нажатие кнопки капчи
- **Anonymous admins / `sender_chat`** и **auto-forward из привязанного канала** — не модерировать как спам
- **External failures**: Telegram 429 (flood) → ретрай с backoff; 5xx; частичный ответ; сетевой таймаут
- **Permission edge**: у отправителя отозвали админку между кэшем и действием
- **Настроек чата ещё нет** → дефолты, чат авто-регистрируется при добавлении бота

## Верификация (обязательна перед «готово»)

1. `docker-compose up` — все сервисы healthy, миграции применены, бот отвечает на webhook.
2. Юнит-тесты чистой логики (services/*): антиспам-детекция, парсинг длительностей, генерация/проверка капчи, логика достижения warn-лимита — **test-first** для этих модулей.
3. Тесты хендлеров через мок aiogram: happy-path + перечисленные edge-cases.
4. Регресс-тесты на каждый исправленный баг.
5. Линтеры/форматтеры (ruff + black/prettier-эквивалент) зелёные; типизация (mypy) на services.
6. README: запуск, переменные env, список команд, как добавить язык.

Если end-to-end против реального Telegram недоступен в окружении — так и напиши честно
(«e2e против Telegram не гонял: нет тест-бота/домена; проверь вручную: …») и приложи максимум:
поднятый compose, зелёные тесты, лог применённых миграций.

## Вне scope (НЕ делать в этой фазе)
Антифлуд, запрет медиа/стикеров для новичков, ночной режим, фильтры/триггеры/заметки, статистика/отчёты,
расписание постинга, ЛС inline-меню настройки, веб-панель, федерации. Всё это — последующие фазы;
не закладывай их реализацию, но не мешай архитектуре их добавить (модель настроек — расширяемая).

## Deliverables
Рабочий репозиторий: код по структуре выше, `docker-compose.yml` + `Dockerfile` + `.env.example`,
Alembic-миграции, тесты, `Makefile` (`up`, `migrate`, `test`, `lint`), `README.md`, локали ru+en.
