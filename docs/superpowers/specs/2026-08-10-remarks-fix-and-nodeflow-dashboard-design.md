# Дизайн: исправление замечаний обзора + веб-панель в стиле NodeFlow

Дата: 2026-08-10
Статус: одобрено пользователем (брейншторм-флоу, варианты A + C + C)

## Объём

Пять независимых, но согласованных работ:

1. Перевод детекта новичков с `new_chat_members` на `ChatMemberUpdated`
2. Актуализация `README.md` под фазы 1–8
3. Честные сообщения об ошибках резолва `@username`
4. CI: `.github/workflows/ci.yml` (test / lint / docker-smoke)
5. Веб-панель: тёмная тема в стиле NodeFlow + новые страницы (полная панель)

---

## 1. Детект новичков через `ChatMemberUpdated`

### Проблема
`message.new_chat_members` — это сервисное сообщение. Оно не приходит, когда
пользователь вступает по инвайт-ссылке без сервисного сообщения, когда его
добавляет админ в некоторых конфигурациях, и вообще зависит от настроек чата.
`ChatMemberUpdated` — платформенное событие смены статуса, оно надёжнее.

### Компоненты

**`bot/services/chat_member_events.py`** (новый, чистая логика):

```python
def classify_transition(
    old_status: str | None, new_status: str
) -> Literal["joined", "left", "ignored"]
```

- `joined`: `old ∈ {None, "left", "kicked"}` → `new ∈ {"member", "restricted", "administrator"}`
- `left`: `old ∈ {"member", "restricted", "administrator"}` → `new ∈ {"left", "kicked"}`
- `ignored`: всё остальное (`member → administrator`, `restricted → member` и т.п.)

**`bot/handlers/captcha.py`**:
- Хендлер `on_new_members` (`F.new_chat_members`) удаляется.
- Добавляется `router.chat_member.register(on_chat_member, ...)` с фильтром
  `ChatMemberUpdatedFilter` (join-переход из `classify_transition`).
- Вся join-логика переезжает без изменений в `_handle_join(...)`:
  fed-ban проверка → newbie-mark → captcha (`_start_captcha`) или welcome.
- Ветка `new = "administrator"`: welcome без капчи/мьюта (бот не может
  ограничивать администраторов).
- Апдейты о ботах (`new_chat_member.user.is_bot`) — пропуск.

**`bot/handlers/welcome.py`**:
- Остаётся лёгкий хендлер `F.new_chat_members`, который **только** удаляет
  сервисное сообщение при `delete_service_messages` (у `ChatMemberUpdated`
  нет `message_id`). Никакой welcome/капча-логики — дублей не будет.
- `F.left_chat_member` (удаление сервисного «вышел») — без изменений.

**`bot/__main__.py`**:
- `dp.chat_member` добавляется в кортеж observers для четырёх outer-middleware
  (Database / Settings / I18n / Admin). Базовые хелперы
  `bot/middlewares/base.py` уже умеют извлекать chat/user из
  `ChatMemberUpdated` — поддержка была заложена.
- `allowed_updates` вычисляется через `resolve_used_update_types()` —
  `chat_member` подхватится автоматически, ручной правки webhook не требуется.

### Поток данных
Telegram `chat_member` update → middleware (session/settings/i18n/admin) →
`classify_transition` → `joined` → `_handle_join` → (fed-ban | newbie-mark |
captcha | welcome).

### Граничные случаи
- leave+rejoin в окне капчи — существующий nonce-механизм (`_captcha_timeout`)
  не трогаем, он продолжает работать.
- `kicked → member` (разбан + возврат) считается `joined` — капча пройдёт
  повторно, это корректно.
- Дубль `new_chat_members`-сообщения и `chat_member`-апдейта: welcome/капча
  идут только из `chat_member`; message-хендлер лишь чистит сервисное
  сообщение.

### Тесты
- Юнит-тесты `classify_transition`: все пары статусов (таблица переходов).
- `test_handlers_captcha.py` переписывается на события `ChatMemberUpdated`
  (мок `ChatMemberUpdated` вместо `Message.new_chat_members`).
- Новый тест: join без сервисного сообщения (только chat_member update) →
  капча запускается.
- Тест: `administrator`-join → welcome без капчи.
- Тест: service-message cleanup хендлер удаляет join-сообщение, но не шлёт
  welcome (нет дублей).

---

## 2. README.md

Переписать под фактическое состояние (источник истины — `CLAUDE.md`):

- Features: все 8 фаз (модерация, антифлуд/newbie, триггеры, статистика,
  расписание, inline-меню, веб-панель, федерации)
- Полный список команд, включая `/antiflood /newbie /addtrigger /stats /top
  /schedule /schedules /unschedule /menu /fcreate /fjoin /fleave /fban
  /funban /finfo`
- Секция Web Dashboard: Telegram Login, `/dev-login` для локальной разработки,
  новые страницы панели (см. §5)
- Развёртывание: docker compose (bot :8000, dashboard :8080, nginx 80/443,
  certbot), выпуск сертификата
- Тесты: 250+ (обновить счётчик по факту)
- Roadmap заменяется на «Phases 1–8 — done» + ссылка на новые возможности

Структура — по образцу CLAUDE.md, но компактнее (README — точка входа).

## 3. Сообщения об ошибках резолва @username

Ограничение Telegram Bot API (бот не резолвит произвольный username) не
устранимо — улучшаем только коммуникацию:

- `bot/i18n/ru.json` / `en.json`: текст ключа `error_target_not_found`
  заменяется на честный: «Пользователь не найден. Бот может найти по
  @username только тех, кого уже видел в чате. Ответьте на сообщение
  пользователя (reply) или укажите числовой ID.»
- Логика `bot/utils/targets.py` не меняется.
- Тест `test_targets.py`: обновить ожидание ключа/текста, если он
  фиксируется в тестах.

## 4. CI — `.github/workflows/ci.yml`

Три джобы:

- **test**: `ubuntu-latest`, Python 3.12, `pip install -r requirements-dev.txt`,
  `pytest tests/ -q`. Тесты не требуют живых PostgreSQL/Redis (моки).
- **lint**: `ruff check`, `black --check`, `mypy` (по `Makefile`, таргет lint).
- **docker-smoke**: `docker compose up -d` с CI-env (токен-заглушка
  `TELEGRAM_BOT_TOKEN=ci-dummy`, локальные DATABASE_URL/REDIS_URL;
  `set_webhook` нефатален по дизайну), ожидание health, `curl -f
  http://localhost:8000/health` и `curl -f http://localhost:8080/…` (health
  дашборда — если роута нет, добавить `/health` в `bot/web/app.py`),
  затем `docker compose down -v`. Env генерируется шагом workflow, секреты не
  нужны.

---

## 5. Веб-панель: тема NodeFlow + новые страницы

### Архитектура

Стек не меняется: FastAPI + серверный HTML. Рефакторинг `bot/web/`:

```
bot/web/
  __main__.py        # entrypoint (без изменений)
  app.py             # роуты (расширяется)
  auth.py            # Telegram Login (без изменений)
  csrf.py            # новый: выдача/проверка CSRF-токена сессии
  templating.py      # новый: Jinja2 env + render helper
  templates/         # новый: base.html + страницы
  static/theme.css   # новый: дизайн-токены (CSS-переменные) + компоненты
  charts.py          # новый: серверный SVG (линия/бары)
```

- Jinja2 добавляется в `requirements.txt` (autoescape включён).
- Статика монтируется через `app.mount("/static", StaticFiles(...))`.
- Графики — серверный SVG, без CDN/JS-зависимостей (тестируемо строками).

### Дизайн-система (тёмная тема)

База — токены kimi-design-skill (dark) + визуальный язык NodeFlow:

| Роль | Значение | Источник |
|------|----------|----------|
| Фон страницы | `#121212` | `color.background.primary` (dark) |
| Сайдбар / карточки | `#1f1f1f` | `color.background.secondary` (dark) |
| Приподнятые элементы | `#292929` | `color.background.tertiary` (dark) |
| Бордеры/разделители | `rgba(255,255,255,0.12)` | `color.separator.s1` (dark) |
| Текст основной | `rgba(255,255,255,0.84)` | `color.labels.primary` (dark) |
| Текст вторичный | `rgba(255,255,255,0.56)` | `color.labels.secondary` (dark) |
| Текст muted | `rgba(255,255,255,0.42)` | `color.labels.tertiary` (dark) |
| **Акцент (розовый)** | `#e0508a` | **token gap** — явное требование (NodeFlow); переменная `--accent`, hover `--accent-hover` (производная, +8% lightness) |
| Вкл / здорово | `#16c456` | `color.status.positiveGreen` |
| Опасное / выкл-важное | `#ff4756` | `color.status.danger` (dark) |

- Типографика: `typography.webUI.*` (заголовок страницы 20/30 600,
  тело 16/24, лейблы 14/20, мелочь 12/18 только для метаданных).
- Радиусы: карточки `radius.lg` (12), контролы 32px `radius.md` (10),
  мелкие `radius.sm` (8), тоггл-пилюля 999px.
- Ритм отступов: 32 / 24 / 16 / 12 / 8 / 4.
- Тени не используются декоративно; разделение — фонами и сепараторами
  (Surface over Stroke).
- Акцент применяется точечно: primary-кнопки, активный пункт сайдбара
  (розовая полоса слева + фон `fills.f2`), линия основного графика, ссылки-действия.
- Графики: один ряд данных → акцент 100%; нейтраль/сетка —
  `labels.quaternary` (dark `#424242`). Доп. серии — blue sequential по
  `components-web/chart-colors.md`.
- Анимации: только hover/active микро-интеракции (press `scale(0.97)`,
  100–160ms; тоггл slide 150–200ms ease-out), `prefers-reduced-motion`
  поддержан. Без entrance-анимаций — панель рабочая, частотность высокая.
- Toggle — по спеке `components-web/toggle.md`: lg 44×24, круглый thumb
  20×20, `role="switch"`, `aria-checked`.
- Кнопки — по `components-web/button.md`: primary/secondary/outline,
  размер 32 по умолчанию, 26 в плотных строках; danger только для
  деструктива (удаление триггера/поста/fed-ban).
- Шрифт: системный стек (`system-ui, -apple-system, "Segoe UI", Roboto,
  sans-serif`) — токен `PingFang SC` не покрывает кириллицу; зафиксирован
  как осознанная подмена (token gap, принцип Platform Fit).

### Layout

- Сайдбар 240px: лого/название бота, навигация (иконка + подпись):
  **Обзор**, затем по контексту чата — **Настройки, Активность, Триггеры,
  Расписание, Федерация, Журнал**; внизу — пользователь и «Выйти».
- Контент: max-width 1100px, отступы по ритму.
- Мобильная адаптация: сайдбар сворачивается в горизонтальную плашку
  (<768px) — минимально, без бургер-меню.

### Страницы и CRUD-циклы

1. `/` — Логин: центрированная карточка, Telegram-виджет, dev-login
   (как сейчас, стилизовано).
2. `/chats` — **Обзор**: сетка карточек чатов: название, сообщений/юзеров
   (`chat_activity_totals`), число включённых фич, чип статуса федерации.
   CTA «Управлять».
3. `/chats/{id}` → redirect на `/chats/{id}/settings`.
4. `/chats/{id}/settings` — группы тогглов по секциям: Модерация,
   Антиспам, Капча, Приветствие, Прочее (существующий `_TOGGLES` +
   группировка). POST `/toggle` — как сейчас + CSRF.
5. `/chats/{id}/activity` — stat-карточки (всего сообщений, активных
   юзеров, среднее/день за 30д) + SVG-график «сообщения по дням» (30 дней)
   + бар-чарт топ-10 (`top_active`).
6. `/chats/{id}/triggers` — таблица триггеров (pattern, match-type,
   response), форма добавления, удаление (danger-outline). crud готов
   (`add_trigger`, `remove_trigger`, `list_triggers`).
7. `/chats/{id}/schedule` — список постов (текст, next_run, интервал),
   форма создания (текст + datetime + опциональный повтор), отмена.
   crud готов (`add_scheduled_post`, `list_scheduled_posts`,
   `remove_scheduled_post`).
8. `/chats/{id}/federation` — если чат в федерации: название, владелец,
   число чатов, список fed-ban'ов + добавить/снять (только владелец
   федерации или OWNER_ID); если нет — empty state с подсказкой про
   бот-команды `/fcreate` `/fjoin`.
9. `/chats/{id}/logs` — таблица последних действий модерации
   (`recent_mod_logs`): время, действие, модератор, цель.

### Изменения данных (миграция Alembic)

Новая миграция (имя ревизии сгенерирует Alembic — `make revision m="dashboard daily activity"`):

```sql
CREATE TABLE activity_daily (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    day DATE NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id, day)
);
CREATE INDEX ix_activity_daily_chat_day ON activity_daily (chat_id, day);
```

- `bot/handlers/stats.py::record_activity` дополнительно инкрементит
  `activity_daily` (upsert за текущий UTC-день).
- Новый crud: `activity_by_day(session, chat_id, days=30) -> list[tuple[date, int]]`
  (агрегация по дням) — для графика; `top_active` уже есть.

### Безопасность

- `_can_manage` (owner или Redis-кэш админ-флага) проверяется на **каждой**
  новой странице и POST-роуте.
- fed-ban мутации: только владелец федерации (`federation.owner_id`) или
  OWNER_ID.
- **CSRF**: `bot/web/csrf.py` — токен в сессии, скрытое поле в формах,
  проверка на POST (сейчас `/toggle` без защиты — закрываем заодно).
- HTML: Jinja2 autoescape; пользовательский контент (trigger response,
  тексты постов) экранируется по умолчанию.
- Панель остаётся за nginx path-routing; новых портов не открываем.

### Язык панели

Русский (как NodeFlow). Все лейблы — словарь в `templating.py` (точка
расширения под en позже). Бот остаётся мультиязычным — речь только о веб-UI.

### Тестирование

Расширение `tests/test_web_app.py` (+ новые файлы при необходимости):

- auth-гейты: редирект неавторизованных со всех новых GET/POST.
- 403 на чужой чат для каждой страницы.
- CRUD триггеров/постов/fed-ban через TestClient (mock crud или sqlite).
- CSRF: POST без токена → 403; с токеном → 200/303.
- График: `activity_by_day` агрегация; SVG содержит данные (строковая
  проверка), пустые данные → empty state.
- fed-ban: не-владелец → 403.
- Сервисные юнит-тесты: `charts.py` (масштабирование, нули, один день).

---

## Порядок реализации (предложение)

1. §1 chat_member (поведение бота + тесты)
2. §3 тексты ошибок (маленькое, независимое)
3. §4 CI (независимое; дальше все PR проверяются)
4. §2 README (в конце, когда панель готова — чтобы описать её)
5. §5 панель (крупнейший блок; миграция БД → тема/шаблоны → страницы → тесты)

## Не входит в объём (YAGNI)

- Переключатель языка панели
- JS-фреймворки, SPA, CDN-зависимости
- Редактирование welcome-текста и стоп-слов через панель (остаются
  бот-команды; легко добавить позже тем же паттерном)
- Графики в реальном времени / автообновление
- e2e против реального Telegram (остаётся ручным; CI покрывает smoke)
