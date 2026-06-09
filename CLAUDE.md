# CLAUDE.md — Документация проекта

## Назначение

Веб-приложение для формирования бизнес-стратегий с подбором банковских продуктов. Предназначено для клиентских менеджеров банка: менеджер описывает ситуацию клиента → система валидирует ввод → LLM генерирует стратегию → менеджер выбирает шаги → к каждому шагу автоматически подбираются продукты банка → результат выгружается в PDF.

---

## Технологический стек

- **Backend**: Python 3.11, Flask, Flask-SQLAlchemy, SQLite
- **Frontend**: Bootstrap 5, Jinja2, `static/js/app.js`
- **LLM**: OpenAI API (модель `gpt-4o-mini`), клиент `openai`
- **PDF**: ReportLab (кириллический шрифт через TTFont)
- **Auth**: Bcrypt (werkzeug) + itsdangerous для URL-токенов

## Запуск

```bash
python main.py       # или
python app.py        # напрямую; запускает Flask на 0.0.0.0:5000
```

## Переменные окружения (`.env`)

| Переменная | Обязательна | Описание |
|---|---|---|
| `OPENAI_API_KEY` | Да | Ключ OpenAI |
| `SECRET_KEY` | Да | Flask session secret (fallback: `SESSION_SECRET`) |
| `APP_DATABASE_URL` | Нет | URL БД (default: `data/app.db` SQLite) |
| `PROMPT_A` | Нет | Переопределяет системный промпт Agent 1 |
| `PROMPT_B` | Нет | Переопределяет системный промпт Agent 2 |

---

## Архитектура: основной поток

```
/  →  /process  →  /situation_clarify?  →  /context_clarify  →  /industry_clarify?
     →  /clarify?  →  /process_after_clarify  →  /review  →  /continue
     →  /agent2  →  /agent2/finish  →  /result  →  /result_pdf
```

### 1. Ввод (`/`, `index.html`)

Форма принимает:
- **Сигналы** — выбор из `data/signals.tsv` (pre-defined триггеры, группируются по сегменту: `micro_small` / `medium_large`)
- **Описание ситуации** — свободный текст
- **Продукт** (опционально) — подсказка Agent 2

### 2. Валидация (`/process`)

Три последовательных проверки при наличии описания:

**а) Методологическая проверка** (`call_methodology_check` → LLM)  
Отсеивает нарушения:
- `product_request` — «нужен кредит», «хочу счёт»
- `bank_problem` — «клиент уходит из банка»
- `general_complaint` — «всё плохо»
- `abstract` — «улучшить бизнес»

При нарушении → `situation_check.ok = False` → редирект на `/situation_clarify`.

**б) Situation check** (`call_situation_check` в `situation_validation.py`)  
Трёхуровневая воронка:
1. `assess_strategy_readiness()` (rules-only) — если ОК, сразу пропускаем
2. `run_guards()` — если tier C (мусор/спам), блокируем
3. LLM-обогащение (`PROMPT_SITUATION_SLOT_FILL`) → повторный `assess_strategy_readiness()`

Тиры готовности (`strategy_readiness.py`):
- **A** — proceed (строим стратегию)
- **B** — уточнить (ambiguous problem или несколько проблем → `focus` / `slots` mode)
- **C** — reject (пусто, спам, слишком длинно)

Слоты: `problem` (блокирующий), `context` (выводимый), `goal` (выводимый).  
Архетипы (`PROBLEM_ARCHETYPES`) — 12 паттернов быстрого распознавания (отток клиентов, текучка кадров, кассовый разрыв и т.д.) с confidence=0.95.

**в) Industry check** (`run_context_checks_after_situation`)  
- Dependency test (LLM): `FINANCIAL` / `OPERATIONAL` → универсальная → отрасль не нужна  
- `REGULATORY` / `MARKET` / `STRATEGIC` → отраслезависимая → вопрос об отрасли (если не указана или размыта)

### 3. Уточнения

- `/situation_clarify` — уточнение ситуации (режим `focus` для выбора главной проблемы из нескольких, или `slots` для заполнения пропущенных слотов)
- `/context_clarify` — уточнение сегмента компании (`company_size`) и отрасли
- `/industry_clarify` — углублённый вопрос по отрасли (если Industry check вернул `needs_answer`)
- `/clarify` — дополнительные вопросы (до 5) сгенерированные LLM через `PROMPT_CLARIFY`

### 4. Agent 1 (`/review`)

Промпт `PROMPT_A`: бизнес-консультант, 1 стратегия, 3 типа механики (снижение потерь / восстановление операций / изменение модели дохода). Название не содержит продукт, начинается с существительного, ≤5 слов.

Действия пользователя: принять / отклонить / редактировать / добавить своё. Можно запросить «ещё» (`/more`).

`call_openai_check_str` сравнивает сгенерированную стратегию с `strategies.xlsx` и проставляет `implemented = "Реализована"`.

### 5. Agent 2 (`/agent2`)

Промпт `PROMPT_B`: 5 шагов к выбранной стратегии. Каждый шаг — глагол + конкретное действие, срок ≤1 месяц, с критериями (числовые, на основе открытых/банковских данных) и полем `product`.

Генерация: `generate_agent2_items` → `ensure_agent2_product_usage_local` (постпроцессинг, если продукт указан явно) → `annotate_steps_with_products` (автоподбор).

### 6. Подбор продуктов

Детерминированный (без LLM). Pipeline:

```
extract_step_intent(step)          # домены + действия из текста шага
    ↓
score_product_for_step(step, product)
    ├── score_product_by_metadata()    # product_metadata.json: capabilities-скор
    └── legacy_product_score()         # токен-матчинг + forbidden-правила
    ↓
rank_products_for_step()           # top-N по score ≥ MIN_PRODUCT_MATCH_SCORE
```

**capability_rules.py**: 45+ regex-паттернов текст→capabilities (напр. `кредит|овердрафт` → `["business_financing"]`).

**FORBIDDEN_PRODUCT_CAPS_WHEN_STEP_HAS**: если шаг про `treasury`, продукты с `employee_hiring` / `advertising` / `restaurant_automation` и др. запрещены.

**product_metadata.json** (`scripts/build_product_metadata.py`): пред-вычисленные capabilities для каждого продукта из `data/products.txt`.

---

## Файловая структура

```
app.py                     # Основной файл: модели, маршруты, промпты, вся логика
main.py                    # Точка входа (вызывает create_app())
capability_rules.py        # Regex-правила text → capabilities + FORBIDDEN-таблица
product_metadata.py        # Скоринг продуктов: score_product_by_metadata()
situation_validation.py    # Фасад: call_situation_check(), вспомогательные утилиты
strategy_readiness.py      # Rules-движок: assess_strategy_readiness(), run_guards(),
                           # архетипы, тиры A/B/C, sanitize, infer_*
load_products.py           # Утилита загрузки Products.xlsx → products.txt

data/
  products.txt             # CSV (;) каталог банковских продуктов
  signals.tsv              # Каталог сигналов с сегментами
  product_metadata.json    # Пред-вычисленные capabilities продуктов
  app.db                   # SQLite база данных
  strategies.xlsx          # Эталонные стратегии для проверки дублей

scripts/
  build_product_metadata.py  # Пересчёт product_metadata.json из products.txt

templates/
  base.html                # Bootstrap layout, flash-сообщения
  index.html               # Форма ввода (сигналы + описание)
  situation_clarify.html   # Уточнение ситуации (focus/slots)
  context_clarify.html     # Уточнение сегмента/отрасли
  industry_clarify.html    # Углублённый вопрос про отрасль
  clarify.html             # Дополнительные вопросы (LLM-generated)
  review.html              # Agent 1: карточки стратегий
  agent2.html              # Agent 2: карточки шагов
  result.html              # Финальный результат
  history.html             # История запросов пользователя
  login.html / register.html

tests/
  test_situation_validation.py
  test_product_matching.py
  test_strategy_readiness.py
```

---

## База данных

```
User
  └─ UserInput (session_token UUID)
       ├─ SituationCheck          (ok, score, slots, clarify_payload, tier)
       ├─ IndustryCheck           (problem_nature, is_universal_problem, question/answer)
       ├─ Clarification           (questions/answers JSON)
       ├─ Agent1Response[]        (status: pending/accepted/rejected)
       │    └─ Agent1Edit         (original vs edited)
       └─ Agent1Selected          (final accepted strategy)
            ├─ Agent2Response[]   (status: pending/accepted/rejected, product)
            └─ Agent2Final        (pdf_locked → навигация назад заблокирована)
```

---

## Маршруты

| Метод | URL | Описание |
|---|---|---|
| GET/POST | `/register` | Регистрация |
| GET/POST | `/login` | Вход (username или email) |
| GET | `/logout` | Выход |
| GET | `/` | Главная: форма ввода |
| GET | `/history` | История запросов |
| POST | `/process` | Создание UserInput + валидация |
| GET | `/situation_clarify/<id>` | Показ уточняющего экрана |
| POST | `/situation_clarify/<id>` | Обработка ответа, повторная проверка |
| GET/POST | `/context_clarify/<id>` | Сегмент + отрасль |
| GET/POST | `/industry_clarify/<id>` | Углублённый вопрос про отрасль |
| GET/POST | `/clarify/<id>` | Доп. вопросы LLM |
| GET | `/process_after_clarify/<id>` | Запуск Agent 1 |
| GET | `/review/<id>` | Agent 1: просмотр стратегий |
| POST | `/more/<id>` | Ещё раунд Agent 1 |
| POST | `/item1/accept/<id>` | Принять стратегию |
| POST | `/item1/reject/<id>` | Отклонить |
| POST | `/item1/save/<id>` | Сохранить редактирование |
| POST | `/item1/custom/<id>` | Добавить свой вариант |
| POST | `/continue/<id>` | Перейти к Agent 2 |
| GET | `/agent2/<id>` | Agent 2: просмотр шагов |
| POST | `/agent2/more/<id>` | Ещё раунд Agent 2 |
| POST | `/item2/accept|reject|save|custom` | Действия с шагами |
| POST | `/agent2/finish/<id>` | Зафиксировать результат |
| GET | `/result/<id>` | Финальный результат |
| GET | `/result_pdf/<id>` | Скачать PDF (блокирует навигацию назад) |

---

## Ключевые дизайн-решения

**LLM — обогащение, не gate.** `strategy_readiness.py` принимает решение по rules. LLM-ответ (`call_openai_raw`) передаётся в `assess_strategy_readiness()` как `enrichment` — дополнение, которое система может переопределить. `missing_blockers` из LLM всегда `[]`; система сама вычисляет, нужно ли уточнение.

**Архетипы — быстрый путь.** 12 паттернов в `PROBLEM_ARCHETYPES` дают confidence=0.95 без LLM-вызова. LLM запускается только если архетип не сработал и rules-based проверка вернула `ok=False`.

**Подбор продуктов — детерминированный.** Нет LLM-вызова. Скоринг комбинирует structured metadata score и legacy token score. `FORBIDDEN_PRODUCT_CAPS_WHEN_STEP_HAS` исключает семантически несовместимые продукты (напр., программа лояльности к клиентам не подходит к шагу про размещение средств).

**pdf_locked** — после скачивания PDF флаг `Agent2Final.pdf_locked = True`, навигация назад в Agent 2 недоступна.

**`_auth` URL-токен** — itsdangerous-подписанный user_id передаётся в URL для работы внутри iframe (Replit preview, SameSite=None cookies).

**Legacy admin-аккаунты заблокированы** при старте: `password_hash = NULL` для учёток `admin` без email.

---

## Обновление продуктового каталога

```bash
# 1. Положить новый файл в data/Products.xlsx
# 2. Конвертировать в products.txt
python load_products.py

# 3. Пересчитать метаданные capabilities
python scripts/build_product_metadata.py
# → обновит data/product_metadata.json
```

Кэш загружается по mtime файла, поэтому перезапуск приложения не нужен.
