# Autonomous Workflow Launch — Design Spec

**Date:** 2026-07-12
**Status:** Draft (pending user review)
**Author:** Design session via brainstorming skill

## Context

`devflow-super` — это Python CLI на LangGraph для автоматизации разработки: fetch задачи из трекера → планирование → одобрение → реализация в изолированном worktree → self-review → параллельные чекеры → агрегация → reporter. Сейчас запуск **только ручной** (`devflow-super run-all` / `devflow run --task-id`). Никакого планировщика, CI, cron или сервиса нет.

**Цель:** запускать workflow без участия человека по расписанию, сохраняя при этом контроль через одобрение планов/публикаций с уровнем вовлечённости, выбираемым стратегией.

## Решённые решения (из brainstorming)

| Решение | Выбор |
|---|---|
| Где работает планировщик | Эта Windows-машина (Windows 10) |
| HITL одобрение | Интегрировать, но Telegram недоступен (заблокирован в РФ, VPN нельзя на рабочем ПК) |
| Push-канал уведомлений | ntfy (основной) + email (fallback) |
| Доступ к web-HITL | Только localhost (браузер на рабочем ПК) |
| Модель ожидания одобрения | Long-running supervisor (один процесс, InMemorySaver) |
| Forge для push/MR | Оба — GitHub и GitLab (общий интерфейс + 2 реализации) |
| EOD-триггер | По расписанию + кнопка в UI (оба) |
| Уровни вовлечённости HITL | 3 стратегии: `per_plan`, `full_detail`, `end_of_day` |
| Reporter | Настраивается конфигурацией + системным промптом + conventions-скиллами |
| Фронтенд | Vue 3 в отдельном модуле, JSON API, внешний вид — отдельная задача |

## Архитектурный компромисс по персистентности

Демон — long-running процесс, чекпойнтер остаётся `InMemorySaver` (как сейчас, `graph.py:47-48`). Это означает: при краше демона в `per_plan`/`full_detail` во время ожидания одобрения — interrupt-состояние **теряется**, задача начнётся заново на следующем тике планировщика.

Альтернатива (SqliteSaver + resume из БД после рестарта) отвергнута как преждевременная: резюм из SQLite после рестарта — нетривиальный контракт (`Command(resume=...)` на сохранённом `thread_id`), требующий сильной переработки `run_workflow_interactive` (`graph.py:256-320`). Практический ущерб от потери одной прерыванной задачи невелик (пере-прогон на следующем тике).

`end_of_day` режим не страдает от этой проблемы — накопленные за день результаты живут в SQLite (batch-store), который переживает любой краш.

## Архитектура

### Общая картина

```
┌──────────────────────────────────────────────────────────────────────┐
│  devflow-daemon  (Windows Service via nssm)                          │
│                                                                      │
│  ┌─────────────┐   ┌──────────────────────────────────────────────┐  │
│  │ APScheduler │──▶│  LangGraph runtime                           │  │
│  │ (cron jobs) │   │  orchestrator → fetcher → planner            │  │
│  └─────────────┘   │  → plan_approval → maker → self_review       │  │
│  │ EOD trigger  │   │  → checkers → aggregator                    │  │
│  │ (cron/button)│   │  → publish_approval (NEW) → reporter        │  │
│  └──────┬───────┘   └──────────────────────────────────────────────┘  │
│         │                                                            │
│         │   ┌────────────────────────────────────────────────────┐   │
│         │   │  Action executor (config-driven)                   │   │
│         │   │  publish_report | update_tracker | push | create_mr│   │
│         │   └────────────┬─────────────────────┬─────────────────┘   │
│         │                │                     │                     │
│         │   ┌────────────▼─────┐  ┌────────────▼──────────────────┐  │
│         │   │  Batch store     │  │  ForgeBackend (abstract)      │  │
│         │   │  (SQLite, EOD)   │  │  ├── GitHubBackend            │  │
│         │   └──────────────────┘  │  └── GitLabBackend            │  │
│         │                         └───────────────────────────────┘  │
│         │                                                           │
│  ┌──────▼───────────────────────────────────────────────────────────▼┐│
│  │  FastAPI (localhost:8787) — REST API + SSE                        ││
│  │  отдаёт собранный Vue 3 SPA из frontend/dist/                     ││
│  └───────────────────────────────────────────────────────────────────┘│
└───────────────┬───────────────────────────────────────┬────────────────┘
                ▼                                       ▼
     ┌────────────────────┐                  ┌─────────────────────┐
     │  Push: ntfy + email│                  │  ты за ПК в браузере │
     │  «план готов /     │                  │  localhost:8787      │
     │   день завершён»   │                  │  одобряешь           │
     └────────────────────┘                  └─────────────────────┘
```

Принцип: один постоянно живущий процесс-демон совмещает четыре роли: планировщик (APScheduler), рантайм LangGraph, web-сервер (FastAPI) и action executor. На `interrupt()` workflow не умирает — стоит в памяти, а демон отдаёт его план через API. Push лишь напоминает вернуться к ПК.

### Компоненты

1. **`devflow-daemon`** — единый процесс, становящийся Windows Service через `nssm`. Содержит scheduler, runtime, web-сервер, action executor.
2. **Approval Store** — in-process `dict[thread_id → Future[decision] + plan]`. Живёт, пока демон жив.
3. **ApprovalCallback-бридж** — реализация контракта `ApprovalCallback` (`graph.py:253`), блокирует на `Future` из Approval Store вместо Telegram long-polling. Возвращает тот же shape `{"approved","reason","requested_changes"}`.
4. **Batch Store** — SQLite (`{repo_path}/.devflow/batch_store.db`), переживает рестарт. Для `end_of_day` режима.
5. **ForgeBackend** — абстракция над forge (GitHub/GitLab), реализации push + create_mr.
6. **Notifier fan-out** — переиспользует `NotificationChannel` ABC; регистрируются `ntfy` и `email` через `register_notification_channel` (`factory.py:121`).
7. **Vue 3 SPA** — отдельный модуль `frontend/`, общается с бэкендом через JSON API.

### Режимы HITL и изменения графа

Центральная идея: **точка ворот (gate) перемещается** в зависимости от режима. Сегодня gate один — в `plan_approval` (`nodes/plan_approval.py:50`). Добавляется **второй gate** — `publish_approval` перед reporter'ом.

Новый конфигурационный ключ в `config/workflow.yaml`:
```yaml
hitl_strategy: per_plan   # per_plan | full_detail | end_of_day
```
+ env-оверрайд `DEVFLOW_HITL_STRATEGY` (по аналогии с `DEVFLOW_HUMAN_IN_THE_LOOP`, `config.py:168-177`).

| Режим | `plan_approval` interrupt | `publish_approval` interrupt | Reporter действия |
|---|---|---|---|
| `per_plan` | ✅ да (как сейчас) | ❌ нет | publish + update-tracker сразу |
| `full_detail` | ❌ нет (auto-approve) | ✅ да — показывает plan+diff+checker-отчёты | publish + update-tracker после approve |
| `end_of_day` | ❌ нет | ❌ нет per-task | **prepare-only** (отчёт готовится, не публикуется; задача → batch store) |

**Реализация в графе** (`graph.py:117-165` — текущая routing-логика `_after_plan_approval`):
- `plan_approval` узел уже проверяет `human_in_the_loop` и `auto_approve` (`plan_approval.py:40`). Расширяется: при `hitl_strategy in {full_detail, end_of_day}` → auto-approve plan, не interrupt.
- **Новый узел** `publish_approval` вставляется между checkers-aggregator и reporter. При `per_plan` — пропускается (edge сразу в reporter). При `full_detail` — делает `interrupt()` с payload `{plan, diff, checker_reports, self_review, branch}`. При `end_of_day` — пропускается, но подаёт сигнал reporter'у работать в prepare-only.

Resume-контракт для `publish_approval` — тот же `ApprovalCallback` shape (`{"approved","reason","requested_changes"}`), что и для plan. Это позволяет переиспользовать `run_workflow_interactive` почти без изменений — просто будет два interrupt'а подряд в одном прогоне, и existing loop в `graph.py:302-318` их обработает (он читает `interrupts[0]` в цикле).

### Рефакторинг reporter'а

Сейчас reporter (`reporter.py:21-104`) делает всё сразу и захардкоженно: генерирует PR-описание, публикует отчёт, обновляет трекер, пишет в TODO — без разбора, что разрешено. Разделяется на два слоя.

**`prepare_report`** — LLM-вызов (существующий `call_structured` → `ReporterResponse`, `reporter.py:62`). Расширяется `ReporterResponse` (`schemas.py`) полем `commit_message: str` — теперь LLM генерирует и текст коммита по конвенциям. Системный промпт reporter'а (`config/agents/reporter.md`) ссылается на conventions-скиллы. Эта часть всегда работает, независимо от стратегии — артефакты нужны даже в `end_of_day` (они лягут в batch-store).

**`execute_actions`** — детерминированный, без LLM. Берёт `actions_enabled` (множество, вычисляемое из `hitl_strategy` + конфига) и запускает только разрешённое. Каждое действие — независимый вызов с try/except (как сейчас `_publish_to_channels` в `reporter.py:198-211` — падение одного не роняет остальные).

| Действие | Что делает | `per_plan` | `full_detail` | `end_of_day` (per-task) | `end_of_day` (batch) |
|---|---|---|---|---|---|
| `publish_report` | `_publish_to_channels` (существ.) | ✅ | ✅ (после approve) | ❌ | ✅ (все сразу) |
| `update_tracker` | `source.update_task_status` (существ.) | ✅ | ✅ | ❌ | ✅ |
| `push` | `forge.push(branch)` — НОВОЕ | по конфигу | по конфигу | ❌ | ✅ |
| `create_mr` | `forge.create_mr(...)` — НОВОЕ | по конфигу | по конфигу | ❌ | ✅ |
| `record_todo` | `mark_done` (существ.) | ✅ | ✅ | ✅ (локально) | ✅ |

В `end_of_day` per-task reporter работает в prepare-only: генерирует артефакты, складывает их в batch-store, пишет `mark_done` локально, но не публикует и не пушит. Сама публикация — отдельный batch-flow.

Конфиг действий в `config/workflow.yaml`:
```yaml
reporter:
  default_actions: [publish_report, update_tracker, record_todo]
  # push и create_mr не входят в default_actions;
  # для per_plan/full_detail их можно добавить явно:
  # default_actions: [publish_report, update_tracker, record_todo, push, create_mr]
  # для end_of_day они включаются автоматически на batch-publish этапе
forge:
  backend: auto        # auto | github | gitlab (auto = по git remote origin)
```

### Conventions-скиллы

Отдельные markdown-документы, которые reporter подмешивает в системный промпт:
```
config/conventions/
  ├── mr.md            # формат MR: заголовок, шаблон описания, чеклист
  ├── commit.md        # conventional commits / корп. формат сообщения
  └── report.md        # формат корпоративного отчёта
```

Эти файлы — не код, а инструкции для LLM. Reporter'ный агент (`config/agents/reporter.md`) получает в system-prompt ссылку: «Следуй конвенциям из `config/conventions/mr.md` при формировании `pr_title` и `pr_description`». Контент файлов загружается в промпт рантайм (как уже загружаются агентские `.md` через `python-frontmatter`). Редактирование markdown меняет поведение reporter'а без правки Python.

### ForgeBackend — абстракция

Новый модуль `src/devflow/forge/`:

```python
# src/devflow/forge/base.py
class ForgeBackend(ABC):
    name: str
    @abstractmethod
    def push(self, branch: str, *, force: bool = False) -> str:
        """Push branch to remote. Returns remote ref / URL."""
    @abstractmethod
    def create_mr(self, *, branch: str, target: str,
                  title: str, description: str) -> str:
        """Create MR/PR. Returns MR URL."""
    @abstractmethod
    def healthcheck(self) -> bool: ...
    def close(self) -> None: ...
```

Две реализации + factory (по образцу `notifications/factory.py` и `mcp/factory.py`):

| Backend | Файл | Transport | Auth | API |
|---|---|---|---|---|
| `GitHubBackend` | `forge/github.py` | `httpx` (+ опц. `gh` CLI) | `GITHUB_TOKEN` | REST `/repos/{owner}/{repo}/pulls` |
| `GitLabBackend` | `forge/gitlab.py` | `httpx` | `GITLAB_TOKEN` | REST `/projects/{id}/merge_requests` |

`forge/factory.py:build_forge(workflow_cfg)` — выбирает по конфигу `forge.backend`, при `auto` парсит `git remote get-url origin` (через `GitPython`, уже в зависимостях) и определяет github/gitlab по хосту. Учитывает корпоративный прокси через `HTTP(S)_PROXY` (уже в `.env`).

Push реализуется через `GitPython` `repo.remotes.origin.push(refspec)` — встроенная возможность, обёрнутая в try/except. MR — через REST API соответствующего forge.

**Idempotency forge-операций:** `create_mr` проверяет, не существует ли уже MR для этой ветки (GitHub: `GET /pulls?head=branch`, GitLab: `GET /merge_requests?source_branch=`). Если есть — возвращает существующий URL. Это делает EOD-publish безопасным к повторам.

### EOD batch-flow и batch-store

В режиме `end_of_day` задачи накапливаются весь день, публикация происходит одним пакетом.

**BatchEntry** (Pydantic-модель записи):
```
task_id, task_title, branch_name, commit_hash,
diff, plan_summary, plan_steps,
checker_reports[], self_review,
reporter_artifacts (pr_title, pr_description, corporate_report, commit_message),
status: pending_review | approved | rejected | published,
created_at, published_at?
```

**Хранилище — SQLite** (зависимость `langgraph-checkpoint-sqlite` уже в `pyproject.toml`). Файл: `{repo_path}/.devflow/batch_store.db` (каталог `.devflow/` добавить в `.gitignore`). SQLite выбран, а не in-memory: демон может перезапуститься — накопленные за день задачи не должны пропасть.

**Поток в течение дня (end_of_day mode):**
```
APScheduler триггер (09:00 будни)
  → run-all в режиме end_of_day
  → для каждой задачи:
      граф прогоняется БЕЗ interrupt'ов
      (plan_approval → auto-approve, publish_approval → skip)
      → reporter prepare-only:
        • LLM генерирует артефакты
        • commit уже сделан в maker (локально, как сейчас)
        • record_todo ✅ (локальная отметка)
        • publish_report ❌, update_tracker ❌, push ❌, create_mr ❌
      → BatchEntry(status=pending_review) → SQLite
      → граф → END, следующая задача
```

Ключевое: граф завершается штатно для каждой задачи. В отличие от `per_plan`/`full_detail`, здесь нет висящего `interrupt()` — LangGraph отработал до конца, состояние не нужно держать в памяти. Batch-store — единственное, что связывает задачи в пакет.

**Batch-review — итог дня.** Триггерится двумя способами:
- **EOD cron** — отдельное правило в APScheduler (например 18:00 будней),
- **Кнопка «Завершить день»** в UI (`POST /api/eod/finalize`).

Страница batch-review показывает список всех `pending_review` записей с чекбоксами; «Подробнее» раскрывает: план, diff, checker-отчёты, self-review, подготовленные MR-описание и commit-сообщение. Чекбокс позволяет исключить отдельные задачи из публикации.

**Batch-publish:** `POST /api/eod/publish` с списком task_id. Для каждой выбранной задачи (последовательно):
```
forge.push(branch) → remote ref
forge.create_mr(branch, target, title, description) → MR URL
_publish_to_channels(report) → ntfy + email
source.update_task_status → resolved
mark_done (обновить статус в TODO.md)
BatchEntry.status = published, published_at = now
```

Публикация последовательная, не параллельная — push и MR для одной задачи должны идти по порядку, а forge API может иметь rate-limits. Каждая задача — independent try/except: падение одной не роняет остальные.

Отклонённые / исключённые задачи остаются в batch-store со статусом `pending_review` — попадут в следующий EOD.

**Жизненный цикл записи в batch-store:**
```
pending_review  ──approve──▶  published   (batch-publish выполнен)
      │
      ├──reject──▶  rejected  (задача отложена/отклонена, остаётся в store)
      │
      └──(следующий EOD)──▶  снова pending_review
                              (если не опубликовано и не отвергнуто)
```

Старые `published` записи можно периодически чистить (retention: 7 дней) — ручная чистка через UI кнопку «Очистить архив».

### Фронтенд-модуль и API

**Разделение на модули:**
```
loop-engineering-workflow/
├── src/devflow/              # Python backend (существующий + daemon)
│   └── daemon/
│       ├── web.py            # FastAPI: REST API + SSE + раздача статики
│       ├── api/              # роуты (REST endpoints)
│       ├── runner.py         # graph.stream() → event bus
│       └── events.py         # in-process pub/sub
│
├── frontend/                 # НОВЫЙ отдельный модуль — Vue 3 SPA
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── main.ts
│   │   ├── App.vue
│   │   ├── router/           # Vue Router
│   │   ├── stores/           # Pinia
│   │   ├── api/              # typed API client
│   │   ├── composables/      # useSSE(), useTask(), useApproval()
│   │   ├── views/            # страницы (пустые каркасы, UI — отдельная задача)
│   │   └── components/
│   └── dist/                 # prod-билд (генерируется, gitignored)
└── ...
```

`frontend/` — отдельный npm-пакет со своим `package.json`, независим от Python. Бэкенд ничего не знает о Vue — только контракт JSON.

**Технологии фронтенда:**

| Слой | Выбор |
|---|---|
| Framework | Vue 3 (Composition API, `<script setup>`) |
| Сборка | Vite |
| Типы | TypeScript |
| Routing | Vue Router |
| State | Pinia |
| HTTP | ofetch / нативный `fetch` |
| SSE | EventSource (нативный) |

UI-библиотека (компоненты, стили) — не выбирается сейчас, это отдельная задача про внешний вид. На первом этапе — голые каркасы страниц, функциональные, но без дизайна.

**Контракт: REST API + SSE.** Бэкенд — чистый JSON-API (FastAPI), без серверного рендеринга HTML.

```
REST API (JSON)
─────────────────────────────────────────────────────
GET    /api/state                 статус демона, стратегия, scheduler
GET    /api/tasks/current         активная задача + live-прогресс
GET    /api/tasks/queue           очередь ожидающих задач
GET    /api/tasks/done            завершённые сегодня (из batch-store)
GET    /api/tasks/{id}            полные детали: план, diff, отчёты, лог
GET    /api/tasks/{id}/log        полный лог (отдельно, для ленивого озора)
GET    /api/tasks/{id}/diff       diff (отдельно, может быть большим)
GET    /api/approvals             pending одобрения (plan / publish)
POST   /api/approvals/{thread_id} {decision, reason, requested_changes?}
GET    /api/eod                   batch-review: pending записи
POST   /api/eod/finalize          запустить batch-review досрочно
POST   /api/eod/publish           {task_ids: [...]} → опубликовать пакет
GET    /api/health                healthcheck

SSE
─────────────────────────────────────────────────────
GET    /api/events                text/event-stream: live-обновления
                                 events: task.started, node.completed,
                                 approval.waiting, task.finished, eod.ready
```

Типы ответов описываются Pydantic-моделями на бэкенде (FastAPI автогенерирует OpenAPI-схему). Фронтенд генерирует TS-типы из этой схемы (`openapi-typescript`) — контракт синхронизирован, ручной дубликат устранён.

**Два режима работы фронтенда:**

- **Development** — Vite dev-server на `:5173`, проксирует `/api/*` на FastAPI `:8787`. HMR. Оба процесса живые отдельно.
- **Production** — `npm run build` собирает `frontend/dist/`. FastAPI отдаёт `dist/` как статику по корню, а `/api/*` обрабатывает как API. Один процесс, один порт (`:8787`).

**SSE-мост:** существующий цикл `run_workflow_interactive` (`graph.py:256-320`) адаптируется — `graph.invoke()` заменяется на `graph.stream()`, который отдаёт чанки после каждой ноды. Эти чанки идут в in-process EventBus (`daemon/events.py`, pub/sub без Redis) → `GET /api/events` (SSE) → Vue composable `useSSE()` → Pinia store → реактивный ререндер. Логика резюма (`interrupts[0]` → `ApprovalCallback` → `Command(resume=...)`) сохраняется — `graph.stream(Command(resume=...), config)` работает аналогично `invoke`, просто стримит чанки вместо одного финального state.

### Отказоустойчивость и lifecycle демона

**Двухуровневая стратегия персистентности:**

| Уровень | Что | Когда |
|---|---|---|
| Норма: InMemorySaver | Процесс жив, interrupt стоит в памяти | `per_plan` / `full_detail` |
| Падение: accept loss + recover | Демон упал → interrupt-задача потеряна, но worktree/branch остались → при старте cleanup-sweep находит осиротевшие worktrees и убирает их | Всегда при старте демона |
| EOD: SQLite (batch-store) | Накопленные за день задачи в SQLite переживают любой краш | `end_of_day` режим |

**Таймаут одобрения.** Процесс стоит на `interrupt()` с пределом:
```yaml
approval:
  timeout: 8h        # per_plan / full_detail
  on_timeout: defer  # defer (вернуть в очередь) | reject (пометить rejected)
```
`defer` — задача помечается в логе `"deferred: approval timeout"`, попадёт в следующий прогон. `reject` — задача завершается со статусом `FinalVerdict.ESCALATE`. Для `end_of_day` таймаут не нужен — там нет per-task interrupt.

**Конкурентность.** Два планировщика в одном демоне: task-scheduler и eod-scheduler. Плюс ручные кнопки в UI.
```python
class DaemonLocks:
    task_run: asyncio.Lock      # активный прогон задач (одновременно один)
    eod_review: asyncio.Lock    # активный EOD-review (одновременно один)
```
Правила:
- APScheduler `max_instances=1, coalesce=True` на каждом job.
- EOD не стартует, пока идёт task-run: берёт `task_run` lock, ждёт. Если task-run на interrupt'е — EOD сначала шлёт push «день завершён, но есть неодобренная задача #4321».
- Task-run не стартует во время EOD-publish: EOD-publish блокирует `task_run` до завершения пуша всех веток.
- Кнопки UI берут те же lock'и.

**Очистка осиротевших worktrees.** При любом нештатном завершении остаются worktree-директория (`{repo}-worktree-{uuid}`) и локальная ветка (`devflow/{task_id}/{uuid}`). `GitWorktreeManager.cleanup()` (`git_worktree.py:114-131`) вызывается только при нормальном выходе. Startup sweep при старте демона:
1. Найти все `{repo}-worktree-*` директории (sibling к repo_path).
2. Для каждой: проверить, есть ли незавершённый worktree (`git worktree list`).
3. Если worktree висит без живого процесса: `git worktree remove --force`, `git branch -D devflow/{task_id}/{uuid}`, залогировать.
4. НЕ трогать worktrees, привязанные к pending batch-store записям (в `end_of_day` коммит уже сделан, ветка нужна для будущего push).

**Graceful shutdown.** nssm при остановке сервиса шлёт stop-сигнал:
1. `APScheduler.shutdown(wait=False)` — новые job'ы не стартуют.
2. Текущий `graph.stream()` — дать доработать текущую ноду (не рвать посередине LLM-вызова / git-операции), таймаут grace: 30s.
3. Если на interrupt(): залогировать "interrupted task {id} lost, will re-run next tick", закрыть future с cancel.
4. `uvicorn.shutdown()`.
5. Закрыть connections: forge, notifications, task_source.
6. Exit.

Что НЕ делаем при shutdown: не пытаемся сохранить interrupt-состояние (accept loss). batch-store уже в SQLite, он в безопасности.

**Health-мониторинг:**
```
GET /api/health
{
  "status": "healthy" | "degraded" | "unhealthy",
  "scheduler": "running" | "stopped",
  "current_task": "4321" | null,
  "current_node": "maker" | null,
  "uptime_seconds": 3600,
  "last_run_ago_seconds": 120,
  "pending_approvals": 1,
  "batch_store_pending": 3,
  "errors_last_24h": 0
}
```
nssm может мониторить этот endpoint и рестартовать сервис при `unhealthy`. Deadlock-детекция: watcher-корутина раз в 5 минут проверяет, не стоит ли `current_node` дольше `node_timeout` (по умолчанию 10 минут). Если да — логирует, шлёт push через ntfy/email.

**Логирование и аудит:**

| Куда | Что | Зачем |
|---|---|---|
| `logs/daemon.log` | ротируемый лог демона (stdout nssm → файл) | диагностика, nssm читает при падении |
| `logs/workflow/{date}/{task_id}.log` | per-task лог: весь `state["logs"]` + таймстампы | разбор полётов по конкретной задаче |
| `logs/forge_audit.jsonl` | каждая forge-операция: `{ts, action, task_id, branch, result, url}` | аудит публикации (push/MR — необратимые внешние действия) |
| batch-store SQLite | status-переходы задач | отчётность |

Forge-аудит — отдельный JSONL-файл, потому что push и MR — необратимые внешние действия. Нужна точная хронология: что запушилось, когда, под каким токеном, какой MR создан.

**Сводка по resilience-сценариям:**

| Сценарий | Что происходит | Восстановление |
|---|---|---|
| Ребут ПК | Демон убит, InMemory-состояние потеряно | nssm автозапуск → startup sweep чистит worktrees → scheduler продолжит по расписанию |
| Краш Python | То же + возможно осиротевший worktree | nssm рестарт → startup sweep → лог краша в `daemon.log` |
| Завис LLM-вызов | Нода висит | Deadlock-детектор через `node_timeout` → push-уведомление → ручное вмешательство или рестарт |
| Не одобрил вовремя | Interrupt стоит до таймаута | `approval.timeout` → `defer` или `reject` |
| EOD во время task-run | Конфликт | EOD ждёт `task_run` lock, push предупреждает о неодобренной задаче |
| Crash во время EOD-publish | Часть MR создана, часть нет | batch-store помечает `published` только после успеха; при рестарте повтор `publish` для `pending_review` (idempotent на уровне forge) |
| Forge API недоступен | Push/MR упал | try/except per-task, остальные идут; задача остаётся `pending_review`, попадёт в следующий EOD |

## Что меняется в существующем коде

| Файл / модуль | Изменение |
|---|---|
| `config/workflow.yaml` | новые ключи: `hitl_strategy`, `reporter.default_actions`, `forge.backend`, `approval.timeout`, `approval.on_timeout`, cron-расписания |
| `src/devflow/config.py` | `WorkflowConfig` += `hitl_strategy`, `reporter`, `forge`, `approval`; env-оверрайд `DEVFLOW_HITL_STRATEGY` |
| `src/devflow/nodes/plan_approval.py` | проверка `hitl_strategy` для auto-approve решения |
| `src/devflow/nodes/reporter.py` | разделить на `prepare_report` + `execute_actions`; убрать `_placeholder_pr_url`; actions gating; prepare-only mode для EOD |
| `src/devflow/nodes/publish_approval.py` | **новый файл** — второй interrupt-узел |
| `src/devflow/graph.py` | регистрация `publish_approval` узла; обновление routing-логики (`_after_aggregate_checker` / новый router) |
| `src/devflow/schemas.py` | `ReporterResponse` += `commit_message: str` |
| `config/agents/reporter.md` | system-prompt ссылается на conventions-скиллы |
| `config/conventions/*.md` | **новые файлы**: `mr.md`, `commit.md`, `report.md` (не код, инструкции LLM) |
| `src/devflow/forge/` | **новый модуль**: `base.py`, `github.py`, `gitlab.py`, `factory.py` |
| `src/devflow/notifications/ntfy.py` | **новый файл** — реализация `NotificationChannel` для ntfy |
| `src/devflow/notifications/email_channel.py` | **новый файл** — реализация `NotificationChannel` для SMTP |
| `src/devflow/batch/` | **новый модуль**: `store.py` (SQLite CRUD), `models.py` (BatchEntry) |
| `src/devflow/daemon/` | **новый модуль**: `__init__.py` (entry point), `web.py` (FastAPI), `api/*.py` (роутеры), `runner.py` (graph.stream → events), `events.py` (EventBus), `scheduler.py` (APScheduler config), `locks.py` (DaemonLocks), `sweep.py` (startup worktree cleanup) |
| `src/devflow/daemon/api/models.py` | Pydantic-модели для OpenAPI-генерации |
| `frontend/` | **новый отдельный модуль** — Vue 3 SPA |
| `pyproject.toml` | += `fastapi`, `uvicorn`, `apscheduler`, `python-multipart`; dev-зависимости для тестов |
| `frontend/package.json` | `vue`, `vue-router`, `pinia`, `ofetch`; dev: `vite`, `@vitejs/plugin-vue`, `typescript`, `openapi-typescript`, `vue-tsc` |
| `.env.example` | `GITHUB_TOKEN` / `GITLAB_TOKEN` задокументировать как live; `NTFY_*`, `SMTP_*` |
| `.gitignore` | += `.devflow/`, `frontend/dist/`, `frontend/node_modules/`, `logs/` |
| `nssm` config | `devflow-daemon` service: `python -m devflow.daemon`, autorestart, health check на `/api/health` |
| `scripts/install-service.bat` | **новый файл** — установка nssm-сервиса |
| `scripts/run-daemon-dev.bat` | **новый файл** — запуск демона в foreground для отладки |

## Future work (явно за рамками этого дизайна)

- **Фазирование реализации** — данный дизайн описывает full scope. Реализация может разбиваться на фазы: (1) daemon + scheduler + API скелет, (2) HITL стратегии + approval bridge, (3) forge-интеграция, (4) EOD batch-flow, (5) Vue-дашборд. Фазирование определяется на этапе writing-plans.
- **SqliteSaver для interrupt-резюма после рестарта** — если потеря прерыванной задачи при краше окажется недопустимой. Требует переработки `run_workflow_interactive`.
- **UI-дизайн и компонентная библиотека** — отдельная задача (явно отложена пользователем).
- **Retention-политика для batch-store** — автоматическая чистка старых `published` записей.
- **Локальный `telegram-bot-api` сервер** — если когда-нибудь понадобится Telegram как канал (через MTProxy для MTProto). Сейчас считается недоступным.
- **Корпоративный мессенджер (Mattermost/Rocket.Chat)** как альтернативный HITL-канал с интерактивными кнопками — абстракция интерактивного канала заложена, реализация отложена.

## Открытые вопросы для ревью

1. **Значение по умолчанию `hitl_strategy`** — предлагаю `per_plan` (ближе к текущему поведению с `human_in_the_loop: true`). Подтвердить?
2. **Расписание по умолчанию** — task-run: будни 09:00 и 15:00 (`0 9,15 * * 1-5`). EOD: будни 18:00 (`0 18 * * 1-5`). Подтвердить или предложить своё?
3. **`approval.on_timeout` по умолчанию** — `defer` или `reject`? Предлагаю `defer` (безопаснее — задача не теряется).
4. **Порт дашборда** — `8787`. Подтвердить или предложить другой?
5. **Язык conventions-скиллов** — пользователь явно укажет язык в промпте reporter'а. Конвенции писать на русском или английском? (Пользователь сказал «в случае чего я явно в правилах и промпте reporter укажу на каком языке отвечать» — оставлено на его усмотрение.)
