# Dashboard Enhancements A — Priority Mapping, TASKS, Refresh, Cron Builder, Agent Models

**Date:** 2026-07-15
**Status:** Draft (pending approval)
**Subproject:** A из 2 (дополнения к dashboard workflow control)
**Base branch:** `feature/phase5-vue-dashboard` (после merge PR `feature/dashboard-workflow-control`)

## Context

После реализации P3 (Dashboard Workflow Control) пользователь запросил 5
дополнений. Декомпозиция: **подпроект A** (этот документ) покрывает 4 из них —
маппинг приоритетов, переименование файла, обновление списка, cron builder,
providers/models. **Подпроект B** (отдельная спека, позже) покроет LLM-очередь
выполнения + drag-and-drop редактирование.

Корневые проблемы, которые решает эта спека:
- Приоритеты Redmine не доходят до dashboard: `mcp/redmine.py` не извлекает
  поле `priority` → `task.metadata["priority"]` пустой → приоритет всегда
  дефолтный. В реальном TODO.md большинство задач вообще без `#rX` тега.
- Нет автообновления списка задач в UI.
- Cron-расписания вводятся raw-строкой — неудобно для нетехнических
  пользователей.
- Провайдер и модель агента недоступны для редактирования из UI (только
  `system_prompt`).

## Goals

1. **Маппинг приоритетов Redmine → r-level**: извлекать `priority.name` из
   Redmine API, маппить в `#r0`–`#r4`, `null` для задач без приоритета.
2. **Переименование `TODO.md` → `TASKS.md`**: файл становится списком
   спаршенных задач (а не очередью выполнения — очередь будет в подпроекте B).
3. **Обновление списка**: кнопка «Обновить» + SSE `tasks.updated` + поллинг.
4. **Графический cron builder**: заменяет raw cron input в Config-вкладке.
5. **Провайдеры и модели агентов**: dropdown провайдеров + текстовое поле
   модели в AgentsTab.

## Non-Goals

- **LLM-очередь выполнения** + drag-and-drop редактирование порядка (подпроект B,
  отдельная спека — требует нового persistent store + LLM-узла в graph).
- Полный UI/UX редизайн (отдельный подпроект).
- Token usage tracking.

## Section 1 — Priority Mapping + Rename TODO.md → TASKS.md

### Корневая причина

**Парсинг приоритета в `mcp/redmine.py` УЖЕ работает** (`_parse_issue:207`
извлекает `issue["priority"]["name"]`). Проблема в `todo.py`:
`priority_from_task()` возвращает дефолт `5` для пустого/неизвестного
приоритета (вместо `None`), и реальный TODO.md был сгенерирован/отредактирован
вручную без `#rX` тегов. Нужно: (а) убрать дефолт `5` → `None`, (б)
гарантировать что `generate_todo_from_source` пишет тег только для непустого
приоритета, (в) перегенерировать TASKS.md. Парсинг Redmine трогать НЕ нужно.

### Маппинг (подтверждён пользователем)

| Redmine priority | r-level | API priority field |
|---|---|---|
| Немедленный / Immediate | `#r0` | критичный |
| Срочный / Urgent | `#r1` | высокий |
| Нормальный / Normal | `#r3` | средний |
| Низкий / Low | `#r4` | низкий |
| (нет / не указан) | `null` | без `#rX` тега |

`_PRIORITY_MAP` в `todo.py` уже содержит правильные маппинги. Изменение: убрать
дефолт `5` → `priority_from_task()` возвращает `None` для неизвестного/пустого.

### Изменения

**`src/devflow/mcp/redmine.py`:** Без изменений — парсинг приоритета уже
работает (`_parse_issue:207`).

**`src/devflow/todo.py`:**
- `priority_from_task(task)`: возвращать `int | None` вместо `int`. Если
  `metadata.get("priority")` пустой или не в `_PRIORITY_MAP` → `None`.
- `generate_todo_from_source(tasks)`: если `priority is None` → строка БЕЗ
  `#rX` тега (как задачи без приоритета). Если есть → `#r{level}`.
- Сортировка: задачи с `None` приоритетом идут после задач с приоритетом
  (использовать `priority if priority is not None else 99` как sort key).

**Переименование `TODO.md` → `TASKS.md`:**
- `src/devflow/config.py:WorkflowConfig.todo_path` default: `"TODO.md"` →
  `"TASKS.md"`.
- `DEVFLOW_TODO_PATH` env override остаётся рабочим (он override, не зависит
  от default — `config.py:221`). CLI `--todo-path` тоже остаётся.
- `.gitignore`: `/TODO.md` → `/TASKS.md`.
- **Фолбэк при загрузке** (в `orchestrator_node`, где вызывается `ensure_todo`,
  или в `load_config` после разрешения пути): если `TASKS.md` не существует, а
  `TODO.md` существует → скопировать `TODO.md` → `TASKS.md` (один раз,
  залогировать INFO). Не удалять старый `TODO.md` (пользователь может его
  держать как backup). Фолбэк срабатывает только один раз за lifecycle daemon.
- Все текстовые ссылки в коде/документации: `README.md`, `docs/`, docstrings,
  где упоминается `TODO.md` → `TASKS.md`.

### Перегенерация

После фикса daemon при следующем `list-tasks` / cron-запуске
`run_all`→`generate_todo_from_source` перегенерирует TASKS.md с правильными
приоритетами из Redmine. Существующие `#rX` теги, проставленные вручную,
перезапишутся при перегенерации (это ожидаемо — source of truth = Redmine).

### Testing

- `tests/unit/mcp/test_redmine.py`: тест что `task.metadata["priority"]`
  извлекается из мока Redmine API ответа.
- `tests/unit/test_todo.py`: `priority_from_task` возвращает `None` для пустого
  и неизвестного; `generate_todo_from_source` генерирует строки без тега для
  `None`-приоритета.
- `tests/unit/test_config.py`: default `todo_path` = `"TASKS.md"`; фолбэк
  копирования `TODO.md` → `TASKS.md`.

## Section 2 — Обновление списка задач (refresh + SSE + поллинг)

### Решение

Кнопка «Обновить» + SSE-событие `tasks.updated` + поллинг fallback.

### Frontend (TasksTab — переименование TodoTab)

- Кнопка **«↻ Обновить»** в шапке вкладки → `todoStore.fetch()`.
- **Авто-поллинг каждые 30с** через `usePolling` (30с вместо 5с — список
  меняется редко).
- **Индикатор последнего обновления**: «Обновлено: HH:MM:SS» серым мелким
  шрифтом под кнопкой (отслеживается в store).
- Состояние `loading`: «Загрузка…» во время fetch.

### SSE-интеграция

- Новый event `tasks.updated` в EventBus.
- Backend (`runner.py` или место, где вызывается `generate_todo_from_source` +
  `write_todo`): после перегенерации TASKS.md →
  `event_bus.publish("tasks.updated", {"path": str(todo_path), "count": N})`.
- `composables/useSSE.ts`: слушатель `tasks.updated` → `todoStore.fetch()`
  (мгновенное обновление).

### API

Без изменений. `GET /api/todo` остаётся (internal naming; фронтенд-компонент
переименуется в TasksTab, но endpoint `/api/todo` не трогается чтобы избежать
breaking change в API-клиенте).

### Testing

- `tests/unit/daemon/test_web_controls.py` или новый: эмуляция SSE
  `tasks.updated` обновляет store (через тестовую подписку на EventBus).
- Backend: тест что после `write_todo` публикуется `tasks.updated`.

## Section 3 — Графический cron builder

### Решение

Новый компонент `CronBuilder.vue` заменяет raw `<input>` для `task_schedule` и
`eod_schedule` в ConfigTab. Чисто frontend, backend без изменений.

### Компонент `CronBuilder.vue`

Структура:
- **Повторение** (radio-group): Ежедневно / По будням (пн–пт) / По выходным
  (сб–вс) / Конкретные дни недели (checkboxes пн–вс) / Каждые N (часов|минут).
- **Время**: time-picker `[HH:MM]` + кнопка «+ добавить время» для нескольких
  значений (массив `["09:00", "15:00"]`).
- **Preview**: человекочитаемая строка — «По будням в 09:00 и 15:00».
- **Raw**: read-only cron-строка `0 9,15 * * 1-5`.
- **Справка** (`<details>`): формат cron (5 полей: min hour day month weekday),
  спецсимволы (`* , - /`), alias (`@daily`, `@hourly`, `@weekly`).

### Логика

- Builder генерирует cron из полей → отображает в «Raw» (read-only preview).
- При загрузке config: парсит существующий cron обратно в поля builder'а.
- **Парсинг cron → fields** (детерминированный для common cases):
  - `*/N * * * *` → «Каждые N минут» (или часов если N в позиции hours).
  - `M H * * *` → «Ежедневно в H:M».
  - `M H1,H2,... * * 1-5` → «По будням в H1:M, H2:M».
  - `M H * * 6,0` → «По выходным в H:M».
  - **Fallback**: если cron не распарсился builder'ом → raw `<input>` +
    предупреждение «Сложное расписание — правьте вручную».
- При ручном вводе raw (fallback) → `PATCH /api/config` как сейчас.

### Backend

Без изменений. Cron валидируется через `CronTrigger.from_crontab` при PATCH
(`scheduler.reschedule`), как сейчас.

### Testing

- Frontend: `npm run typecheck` + `npm run build` (cron builder — чистый
  frontend без отдельного тест-раннера в этом раунде).
- Backend: существующие тесты `test_web_controls.py` покрывают PATCH config
  с cron — без изменений.

## Section 4 — Провайдеры и модели в конфиге агентов

### Решение

Dropdown провайдеров (из providers.yaml) + текстовое поле модели. Расширяет
AgentsTab.

### Backend

**`GET /api/providers`** (новый endpoint):
- Возвращает список провайдеров из `app_cfg.providers`:
  ```json
  [{"name": "openai", "type": "openai_compatible"},
   {"name": "kimi", "type": "openai_compatible"},
   {"name": "ollama", "type": "ollama"}, ...]
  ```
- Реализация: итерация по `app_cfg.providers.items()`.

**`PUT /api/agents/{name}`** (расширить существующий `update_agent_prompt`):
- Текущий `PUT /api/agents/{name}/prompt` принимает только `system_prompt`.
- Новый `PUT /api/agents/{name}` принимает полный набор полей:
  ```json
  {
    "system_prompt": "...",
    "provider": "kimi",
    "model": "GLM-5.2",
    "temperature": 0.3
  }
  ```
- Все поля optional. In-memory mutation мгновенно:
  `app_cfg.agents[name].provider/model/temperature`.
- **Note**: `/api/agents/{name}/prompt` остаётся для обратной совместимости
  (делегирует на новый endpoint или дублирует логику).

### Frontend (AgentsTab расширение)

- Frontmatter-секция (была read-only) становится редактируемой:
  - **Provider**: `<select>` dropdown — список из `GET /api/providers`
    (загружается один раз при mount вкладки, кэшируется в `agentsStore`).
  - **Model**: `<input type="text">` — свободный ввод.
  - **Temperature**: `<input type="number" step="0.1" min="0" max="2">`.
- Debounce (1с) на каждое поле → `PUT /api/agents/{name}`.
- «Modified» индикатор + «Save to disk» — как сейчас для prompt.
- `save_agent` уже пишет frontmatter через `frontmatter.dump` — расширить
  `post.metadata` (provider, model, temperature уже там).

### API client + types

- `getProviders()` → `GET /api/providers`.
- `updateAgent(name, {system_prompt?, provider?, model?, temperature?})` →
  `PUT /api/agents/{name}`.
- Новые типы: `ProviderSummary`, расширение `AgentUpdate`.

### Testing

- `tests/unit/daemon/test_web_controls.py`:
  - `GET /api/providers` возвращает список.
  - `PUT /api/agents/{name}` мутирует provider/model/temperature in-memory.
  - `save_agent` пишет новые поля в `.md` frontmatter.

## File inventory

**Backend — modified:**
- `src/devflow/todo.py` — `priority_from_task` → `None`, generate без тега
- `src/devflow/config.py` — `todo_path` default → `TASKS.md`, фолбэк копирования
- `src/devflow/daemon/web.py` — `GET /api/providers`, расширить `PUT /api/agents/{name}`
- `src/devflow/daemon/runner.py` (или orchestrator) — publish `tasks.updated`
- `README.md`, `docs/` — ссылки TODO.md → TASKS.md
- `.gitignore` — `/TODO.md` → `/TASKS.md`

**Backend — new tests:**
- `tests/unit/test_todo.py` — расширить (None priority, generate без тега)
- `tests/unit/test_config.py` — расширить (TASKS.md default + фолбэк)
- `tests/unit/daemon/test_web_controls.py` — расширить (providers, agent fields)

**Frontend — new:**
- `frontend/src/components/controls/CronBuilder.vue` — cron builder
- `frontend/src/components/controls/TasksTab.vue` — переименование TodoTab + refresh/SSE

**Frontend — modified:**
- `frontend/src/components/controls/ConfigTab.vue` — использовать CronBuilder
- `frontend/src/components/controls/AgentsTab.vue` — редактируемые provider/model/temperature
- `frontend/src/composables/useSSE.ts` — слушатель `tasks.updated`
- `frontend/src/api/client.ts` — `getProviders`, `updateAgent`
- `frontend/src/api/types.ts` — `ProviderSummary`, расширение типов
- `frontend/src/stores/agents.ts` — providers list
- `frontend/src/stores/todo.ts` — `lastUpdated` timestamp
- `frontend/src/views/ControlsView.vue` — переименование таба TODO → TASKS

## Open questions

Нет — все решения согласованы в ходе brainstorming.
