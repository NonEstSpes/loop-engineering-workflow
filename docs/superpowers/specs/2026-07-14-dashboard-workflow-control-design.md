# Dashboard Workflow Control — Design Spec

**Date:** 2026-07-14
**Status:** Draft (pending approval)
**Subproject:** P3 — Управление workflow из dashboard
**Branch:** `feature/phase5-vue-dashboard`

## Context

DevFlow dashboard (`frontend/`, Vue 3 SPA + FastAPI daemon на `:8787`) сейчас —
read-only монитор: здоровье, текущая задача, завершённые задачи, approvals, EOD
review. Запуск задач, конфигурация, приоритеты TODO, HITL-стратегия и промпты
агентов редактируются только через CLI / правку файлов / рестарт daemon.

Этот спека (P3) — первый из раунда «функциональность-первая». Он превращает
dashboard в **control center**: запуск задач по требованию, редактирование
конфигурации workflow, приоритетов TODO.md, переключение HITL-стратегии и
промптов агентов — без перезапуска daemon.

В раунд также войдут (позже, отдельными спеками): журнал событий (P2) и
`approval.waiting` в SSE. UI/UX редизайн (P1) и token usage tracking (P4) —
последующие раунды.

## Goals

1. **Запуск задач из dashboard** — `POST /api/tasks/run` с опциональным
   `task_id`; запуск в фоне, защита от двойного запуска.
2. **Редактор приоритетов TODO** — просмотр и inline-редактирование
   `#r0`–`#r5` приоритетов и статуса записей TODO.md, с немедленной
   персистентностью на диск.
3. **Управление конфигурацией workflow** — просмотр и правка in-memory
   (`hitl_strategy`, schedules, approval timeout/on_timeout); ручной save на
   диск; поля, требующие рестарт (`port`, `serve_frontend`, `frontend_dist`),
   доступны только для чтения с пометкой.
4. **Переключатель HITL** — выделенный эндпоинт `PUT /api/config/hitl` с
   валидацией и перерегистрацией EOD cron job при переключении в/из
   `end_of_day`.
5. **Редактор промптов агентов** — просмотр списка агентов, inline-правка
   `system_prompt` (in-memory мгновенно), ручной save на диск.

## Non-Goals

- UI/UX редизайн / дизайн-система / dark mode (отдельный подпроект P1).
- Журнал событий / Activity Log (P2, отдельная спека).
- `approval.waiting` в SSE (отдельная спека).
- Token usage tracking (P4, отдельная спека).
- Frontend-тесты (vitest/playwright) — в этом раунде фронт проверяется через
  `npm run typecheck` + `npm run build` + ручной smoke-чеклист.
- Аутентификация / авторизация (daemon localhost-only, как сейчас).
- Сохранение комментариев в `workflow.yaml` при Save (см. «Ограничения»).

## Architecture

### Текущее состояние

- `WorkflowRunner.run_task(task_id, repo_path, thread_id=None)` (`runner.py:65`)
  — синхронная точка входа для одной задачи. Граф перестраивается каждый run.
- `runner` и `app_cfg` захватываются в замыкании `create_app` (`web.py:79`),
  но **не** прикреплены к `app.state`. `scheduler` вообще не передаётся в
  `create_app` (локальная переменная в `run_daemon`, `__main__.py:101`).
- `task_run` lock в `DaemonLocks` (`locks.py`) — `asyncio.Lock`, **cross-loop
  unsafe**: scheduler-поток (APScheduler) и uvicorn event-loop разные.
  Эффективно не используется (только `.locked()` в health-чеке).
- `Config` (Pydantic v2, `config.py:120`) — in-memory mutable, загружается
  один раз через `load_config(config_dir)`. Поля мутабельны (CLI уже мутирует:
  `cli.py:145`).
- `hitl_strategy` читается **per-run** в `plan_approval_node` /
  `publish_approval_node` (`nodes/plan_approval.py:43`,
  `nodes/publish_approval.py:47`) — graph не нужно перестраивать.
  Исключение: `eod_review` cron job регистрируется на старте, только если
  strategy == `end_of_day` (`scheduler.py:96`).
- TODO.md перечитывается с диска каждый run (`todo.py:82`, `parse_todo`) —
  редактирование файла = мгновенный эффект.
- Agent prompts (`config/agents/*.md`, frontmatter + body) загружаются один
  раз в `app_cfg.agents`. Ноды читают `agent_cfg.system_prompt` per-invocation
  (`planner.py:39,49,66`) — in-memory mutation = мгновенный эффект на next run.

### Изменения wiring

1. **`create_app` сигнатура** (`web.py:79`): добавить параметр
   `scheduler: DaemonScheduler | None = None`.
2. **`app.state`** прикрепляет зависимости в `create_app`:
   ```python
   app.state.runner = runner
   app.state.scheduler = scheduler
   app.state.cfg = app_cfg
   ```
3. **`run_daemon`** (`__main__.py`): обновить вызов `create_app(...)`, чтобы
   передать `scheduler`.
4. **`threading.Lock`** для взаимного исключения запуска задач:
   `app.state._run_lock = threading.Lock()`. **Не** `asyncio.Lock` (cross-loop
   unsafe). HTTP-обработчик запускает `runner.run_task` через
   `asyncio.to_thread` (worker thread), удерживая `threading.Lock`.
   Если lock уже занят → `409 Conflict`.
5. Существующий `set_current_task` callback (`web.py:282`) остаётся как
   индикатор занятости для health и UI.

### Поток данных

```
[Dashboard ControlsView]
   ↓ POST /api/tasks/run {task_id?}
[FastAPI handler] — acquire threading.Lock (409 if busy)
   ↓ asyncio.to_thread(runner.run_task, ...)
[Worker thread] — runner.run_task → build_graph → run
   ↓ task.started / task.finished / task.error events
[EventBus] → SSE /api/events
   ↓
[useSSE] → controlsStore обновляет статус
```

Config/HITL/TODO/agents — прямые REST-вызовы, без фоновой обработки (кроме
TODO, который пишет на диск синхронно).

## API Endpoints

### 2.1 Запуск задач

| Метод | Путь | Тело | Ответ | Поведение |
|---|---|---|---|---|
| `POST` | `/api/tasks/run` | `{task_id?: str, repo_path?: str}` | `202 {run_id, task_id?, status: "started"}` | Запускает `runner.run_task(task_id)` (если `task_id` задан) или `runner.run_all()` в фоне через `asyncio.to_thread`. `409` если уже занят (`_run_lock` занят). `run_id` = `uuid4`. |

- `repo_path` опционален, по умолчанию — `app_cfg` repo path (как в scheduler).
- Если `task_id` пустой/отсутствует → `run_all()` (next by priority через
  orchestrator).
- Обработчик сразу возвращает `202` (accepted), не дожидаясь завершения.

### 2.2 TODO management

| Метод | Путь | Ответ/Тело | Поведение |
|---|---|---|---|
| `GET` | `/api/todo` | `[{line_no, text, checkbox, priority, task_id?}]` | `parse_todo(Path(todo_path))` — читает TODO.md с диска. |
| `PATCH` | `/api/todo/{line_no}` | `{priority?: int 0-5, status?: "open"\|"in_progress"\|"done"}` | Изменяет приоритет/статус конкретной строки TODO.md. **Запись на диск сразу** (atomic: temp + `os.replace`). 200 + обновлённая запись. |

- **Persist-стратегия TODO = disk сразу.** Файл перечитывается каждый run,
  поэтому in-memory нет смысла.
- `priority` валидируется: 0–5, иначе `422`.
- `status` маппится на checkbox: `open` → `[ ]`, `in_progress` → `[~]`,
  `done` → `[x]`.
- `task_id` извлекается из текста (если есть), только для отображения.
- Несуществующий `line_no` → `404`.

### 2.3 Config management

| Метод | Путь | Тело | Поведение |
|---|---|---|---|
| `GET` | `/api/config` | — | Текущая in-memory конфигурация: `hitl_strategy`, `daemon` (schedule, timeout, on_timeout, port, serve_frontend, frontend_dist), `forge`, `todo_path`, `task_source`. |
| `PATCH` | `/api/config` | частичный объект (`hitl_strategy?`, `daemon.task_schedule?`, `daemon.eod_schedule?`, `daemon.approval_timeout_hours?`, `daemon.approval_on_timeout?`) | **In-memory mutation** мгновенно. Для `task_schedule`/`eod_schedule` → дополнительно `scheduler.reschedule(...)`. Поля `port`/`serve_frontend`/`frontend_dist` в PATCH → `422` "restart only". |
| `POST` | `/api/config/save` | — | Пишет текущую in-memory конфигурацию в `config/workflow.yaml` (atomic: temp + `os.replace`). 200 `{path}`. |
| `GET` | `/api/config/diff` | — | Diff между in-memory config и файлом на диске: `{changed: [{field, in_memory, on_disk}], clean: bool}`. |

- `PATCH` валидирует cron-выражения через `CronTrigger.from_crontab` в
  try/except → `422` при невалидном.
- `PATCH hitl_strategy` валидирует против `HitlStrategy.ALL`.

### 2.4 HITL strategy (shortcut)

| Метод | Путь | Тело | Поведение |
|---|---|---|---|
| `PUT` | `/api/config/hitl` | `{strategy: "per_plan"\|"full_detail"\|"end_of_day"}` | Валидация против `HitlStrategy.ALL`, in-memory mutation `app_cfg.workflow.hitl_strategy`. При переключении **на** `end_of_day` → регистрирует EOD cron job; **с** `end_of_day` → удаляет EOD job (`scheduler.remove_job("eod_review")` или эквивалент). 200 `{strategy}`. |

- Дублирует `PATCH /api/config` для `hitl_strategy`, но с явной семантикой и
  обработкой EOD job. **Frontend использует именно этот shortcut** для
  HITL radio-group в Config tab (Sec 3.3), т.к. он инкапсулирует логику
  перерегистрации EOD job. `PATCH /api/config` остаётся для остальных полей.

### 2.5 Agent prompts

| Метод | Путь | Тело | Поведение |
|---|---|---|---|
| `GET` | `/api/agents` | — | Список агентов: `[{name, provider, model, temperature, has_prompt}]`. |
| `GET` | `/api/agents/{name}` | — | Полный `AgentConfig`: frontmatter metadata + `system_prompt`. `404` если unknown. |
| `PUT` | `/api/agents/{name}/prompt` | `{system_prompt: str}` | In-memory mutation `app_cfg.agents[name].system_prompt` мгновенно. 200. |
| `POST` | `/api/agents/{name}/save` | — | Пишет `config/agents/{name}.md` на диск: frontmatter + body (через `python-frontmatter`). Atomic. 200 `{path}`. |

### Безопасность

- Все write-эндпоинты логируются (`logger.info`): что изменено.
- Atomic writes: temp файл в той же директории + `os.replace` (atomic на POSIX
  и Windows для same-filesystem).
- YAML пишется через `yaml.dump(default_flow_style=False, allow_unicode=True,
  sort_keys=False)`.
- Agent `.md` пишется через `frontmatter.dump` (библиотека `python-frontmatter`,
  уже в зависимостях).

## Frontend

### 3.1 Навигация и роутинг

- Новый пункт **«Controls»** в `App.vue` nav (после «EOD Review»).
- Роут `/controls` → `ControlsView` (lazy-load через dynamic import).
- `ControlsView` — контейнер с табами: **Run / TODO / Config / Agents**.

### 3.2 Pinia stores (composition style)

- **`useControlsStore`** (`stores/controls.ts`): `currentRun`, `isRunning`,
  `runHistory` (последние 5 за сессию, in-memory list), `runTask(task_id?)`.
- **`useConfigStore`** (`stores/config.ts`): `config`, `diskDiff`,
  `unsavedChanges`, `fetchConfig()`, `patchConfig(partial)`, `saveToDisk()`,
  `setHitlStrategy(strategy)`, `fetchDiff()`.
- **`useTodoStore`** (`stores/todo.ts`): `items`, `fetchTodo()`,
  `updateLine(line_no, {priority, status})`.
- **`useAgentsStore`** (`stores/agents.ts`): `agents`, `current`,
  `fetchAgents()`, `fetchAgent(name)`, `updatePrompt(name, text)`,
  `saveToDisk(name)`.

### 3.3 Компоненты (по табам)

**Run tab** (`components/controls/RunTab.vue`):
- Поле ввода `task_id` (опциональное, placeholder «Оставьте пустым — запустить
  next by priority»).
- Кнопка **«Run task»** / **«Run next»** (зависит от заполненности).
- Статус: `isRunning` → «Running: {task_id}» + кнопка disabled; иначе «Idle».
- История запусков за сессию (последние 5, `runHistory`).
- Ошибка `409` → «Already running: {task}».

**TODO tab** (`components/controls/TodoTab.vue`):
- Таблица записей: checkbox-статус, текст, приоритет (`#r0`–`#r5`), task_id.
- Inline-редактирование приоритета: dropdown 0–5 → `PATCH /api/todo/{line_no}`
  (debounce 500ms).
- Статус-toggle: `[ ]` → `[~]` → `[x]` кликом.
- Цветовое выделение: `#r0` — красный, `#r1` — оранжевый, остальные — серый.

**Config tab** (`components/controls/ConfigTab.vue`):
- Форма:
  - HITL strategy — radio-group (3 варианта) → `PUT /api/config/hitl` сразу.
  - Task schedule — text input + подсказка cron.
  - EOD schedule — text input.
  - Approval timeout hours — number input.
  - Approval on timeout — select (defer/reject).
  - `port`, `serve_frontend`, `frontend_dist` — **read-only** с пометкой
    «restart only».
- Кнопка **«Save to disk»** → `POST /api/config/save` (показывает diff через
  `/api/config/diff` перед сохранением).
- Индикатор «Unsaved changes» (in-memory ≠ disk).

**Agents tab** (`components/controls/AgentsTab.vue`):
- Список агентов слева (selectable): name, provider, model.
- Справа — редактор выбранного агента:
  - Frontmatter (read-only): provider, model, temperature.
  - `system_prompt` — `<textarea>` (monospace, resizable) с авто-сохранением
    in-memory (debounce 1s) → `PUT /api/agents/{name}/prompt`.
  - Кнопка **«Save to disk»** → `POST /api/agents/{name}/save`.
  - Индикатор «Modified» (in-memory ≠ disk).

### 3.4 API client

Новые функции в `frontend/src/api/client.ts` + типы в `api/types.ts`:
`TodoItem`, `TodoUpdate`, `ConfigResponse`, `ConfigPatch`, `ConfigDiff`,
`HitlStrategy`, `AgentSummary`, `AgentDetail`, `AgentPromptUpdate`,
`RunRequest`, `RunResponse`.

### 3.5 SSE-интеграция

`composables/useSSE.ts` расширяется: на `task.started`/`task.finished`/
`task.error` → `controlsStore` обновляет статус (`isRunning`, `currentRun`).
На `task.finished` — добавление записи в `runHistory`.

## Testing

### Backend (pytest)

Новые файлы в `tests/unit/daemon/`:

- **`test_web_controls.py`** — endpoint-тесты:
  - `POST /api/tasks/run`: 202 + `run_id`; 409 если занят; async-выполнение
    (mock `runner.run_task` через `threading.Event` для синхронизации в тесте).
  - `GET /api/todo`: парсинг реального TODO.md (tmp_path).
  - `PATCH /api/todo/{line_no}`: изменение приоритета → запись → перечитывание.
  - `GET /api/config`: все поля присутствуют.
  - `PATCH /api/config`: in-memory mutation; 422 на restart-only поля;
    `scheduler.reschedule` вызывается для schedule-полей.
  - `PUT /api/config/hitl`: валидация; EOD job регистрация/удаление (mock
    scheduler).
  - `POST /api/config/save`: atomic write; YAML корректен; перечитывание =
    in-memory.
  - `GET /api/config/diff`: показывает изменённые поля.
  - `/api/agents`: список; детали; 404 unknown; prompt update (in-memory);
    save (disk).
  - Использует `mock_config` fixture (`tests/conftest.py`) + tmp TODO.md +
    mock runner/scheduler.
- **`test_scheduler_reschedule.py`** — `DaemonScheduler.reschedule(...)`:
  - `reschedule_job` вызывается с новым cron trigger.
  - Несуществующий job → graceful (no-op + warning log).

### Frontend

- `npm run typecheck` (vue-tsc) — типы корректны.
- `npm run build` — сборка успешна.
- Ручной smoke-чеклист (в plans/implementation):
  1. `/controls` → 4 таба рендерятся.
  2. Run: запустить задачу → статус «Running» → по завершении в истории.
  3. TODO: изменить приоритет → `TODO.md` изменился.
  4. Config: изменить HITL → проверить `/api/state` → Save → проверить
     `workflow.yaml`.
  5. Agents: изменить промпт → Save → проверить `config/agents/{name}.md`.

## Error Handling

| Сценарий | Код | Тело |
|---|---|---|
| Запуск при занятом daemon | `409` | `{detail: "Task already running: {task_id}"}` |
| Невалидный `hitl_strategy` | `422` | FastAPI validation error |
| Невалидный cron | `422` | `{detail: "Invalid cron expression: ..."}` |
| Restart-only поле в PATCH | `422` | `{detail: "Field '{field}' requires daemon restart"}` |
| Unknown agent name | `404` | `{detail: "Unknown agent: {name}"}` |
| Несуществующий `line_no` | `404` | `{detail: "TODO line {line_no} not found"}` |
| Ошибка записи на диск | `500` | `{detail: "Failed to persist: {error}"}` (log ERROR) |

Все write-операции в try/except, ошибки через `logger.exception`.

## Ограничения и риски

- **Конкурентность config:** одновременное редактирование из нескольких
  вкладок не блокируется. In-memory mutation атомарна на уровне GIL.
  Документируется как «один оператор».
- **Scheduler cross-loop:** `reschedule_job` — thread-safe APScheduler API,
  безопасно из uvicorn loop.
- **YAML-комментарии:** `Save config` перезаписывает `workflow.yaml` через
  `yaml.dump` — **комментарии теряются**. Принято как ограничение этого раунда
  (альтернатива `ruamel.yaml` round-trip отложена). В UI — предупреждение
  перед Save.
- **Agent prompt persistence:** `Save agent` перезаписывает `.md` через
  `frontmatter.dump` — сохраняет frontmatter + body, но не гарантирует
  сохранение форматирования body точно (нормализация).
- **Запуск задач синхронный внутри потока:** `runner.run_task` блокирует
  worker thread до завершения. Длинные задачи удерживают `_run_lock`. Это
  приемлемо — daemon обрабатывает одну задачу за раз (соответствует
  `max_instances=1` scheduler).

## File inventory

**Backend (новые/изменённые):**
- `src/devflow/daemon/web.py` — изменить (new endpoints, app.state wiring)
- `src/devflow/daemon/__main__.py` — изменить (pass scheduler to create_app)
- `src/devflow/daemon/scheduler.py` — изменить (add `reschedule()`,
  `remove_eod_job()`)
- `src/devflow/daemon/todo_api.py` — **новый** (TODO read/write helpers для
  web-эндпоинтов: форматирование записей в JSON, atomic rewrite приоритета/
  статуса строки). Существующий `src/devflow/todo.py` (парсер) расширяется
  минимально — только если нужен helper для точечного обновления строки без
  полной перезаписи (иначе `todo_api.py` использует `parse_todo` + перезапись
  файла). Финальное решение — на этапе plan.
- `tests/unit/daemon/test_web_controls.py` — **новый**
- `tests/unit/daemon/test_scheduler_reschedule.py` — **новый**

**Frontend (новые):**
- `frontend/src/views/ControlsView.vue`
- `frontend/src/components/controls/RunTab.vue`
- `frontend/src/components/controls/TodoTab.vue`
- `frontend/src/components/controls/ConfigTab.vue`
- `frontend/src/components/controls/AgentsTab.vue`
- `frontend/src/stores/controls.ts`
- `frontend/src/stores/config.ts`
- `frontend/src/stores/todo.ts`
- `frontend/src/stores/agents.ts`

**Frontend (изменяемые):**
- `frontend/src/App.vue` — добавить nav-пункт «Controls»
- `frontend/src/router/index.ts` — добавить роут `/controls`
- `frontend/src/api/client.ts` — добавить функции-обёртки
- `frontend/src/api/types.ts` — добавить типы
- `frontend/src/composables/useSSE.ts` — расширить для controlsStore

## Open questions

Нет открытых вопросов — все решения согласованы с пользователем в ходе
brainstorming.
