# Dashboard Enhancements B — LLM Execution Queue + Drag-and-Drop Reordering

**Date:** 2026-07-15
**Status:** Draft (pending approval)
**Subproject:** B из 2 (дополнения к dashboard workflow control)
**Depends on:** Подпроект A (TASKS.md с приоритетами), P3 (Dashboard Workflow Control)

## Context

TASKS.md (после подпроекта A) — это список спаршенных задач из Redmine с
приоритетами. Но **порядок выполнения** задач сейчас определяется простой
сортировкой `select_next_todo` (по r-level + line_no) — без учёта контекста
кода, зависимостей между задачами или сложности.

Пользователь хочет: LLM-агент оценивает оптимальный порядок выполнения задач
(на основе TASKS + контекста кода), формирует **очередь выполнения** —
отдельную от TASKS.md. Эта очередь видна человеку в dashboard и поддаётся
редактированию (drag-and-drop или кнопки). Очередь обновляется при старте
каждого цикла работы.

## Goals

1. **LLM-узел «prioritizer»** в workflow graph (перед orchestrator):
   анализирует TASKS + контекст репозитория → формирует упорядоченную очередь.
2. **Persistent store очереди** (SQLite в `.devflow/`): переживает рестарт,
   обновляется при каждом цикле работы.
3. **REST API** для чтения/перестановки очереди: `GET /api/queue`,
   `PATCH /api/queue/reorder`.
4. **UI в Run-вкладке**: визуализация очереди с d&d + кнопками ↑↓.
5. **Автоматическая переоценка** при старте каждого цикла (`run_task`/cron).

## Non-Goals

- Изменение TASKS.md (это список задач, очередь — отдельная сущность).
- Ручное назначение задач в очередь (только LLM-оценка + перестановка).
- Batch-операции над очередью.
- UI/UX редизайн (отдельный подпроект).
- Token usage tracking.

## Section 1 — LLM-узел «prioritizer» в graph

### Архитектура

Новый узел `prioritizer_node` вставляется в graph **перед orchestrator**:

```
START → prioritizer → orchestrator → task_fetcher → planner → ...
```

Prioritizer:
1. Читает TASKS.md (`parse_todo`) → список задач с приоритетами.
2. Собирает контекст репозитория (краткое дерево файлов, последние коммиты).
3. LLM-вызов (`call_structured`) → возвращает упорядоченный список task_id.
4. Записывает очередь в `QueueStore` (SQLite).
5. Передаёт управление orchestrator (который берёт первую задачу из очереди).

### LLM-промпт

Новый файл `config/agents/prioritizer.md`:
```yaml
---
name: prioritizer
provider: openai
model: GLM-5.2
temperature: 0.2
---
# Role
You are a task prioritization specialist for a software development workflow.

# Instructions
Given a list of tasks (with titles, priorities, and descriptions) and a
summary of the current repository state, determine the optimal execution order.

Consider:
- Task dependencies (does task A unblock task B?)
- Priority (r0 = critical, r5 = lowest)
- Estimated complexity (simpler tasks first to build momentum)
- Code areas touched (group related tasks to reduce context switching)

Output a JSON array of task IDs in recommended execution order (first = next).
```

### Схема вывода

Новая Pydantic-модель `PrioritizationResult`:
```python
class PrioritizedTask(BaseModel):
    task_id: str
    reason: str  # why this position

class PrioritizationResult(BaseModel):
    ordered_tasks: list[PrioritizedTask]
    notes: str = ""
```

### Интеграция в graph

В `graph.py`:
- Новая функция `prioritizer_node(state, app_cfg, queue_store)`.
- Изменение entry edge: `START → prioritizer` (вместо `START → orchestrator`).
- Prioritizer пишет в `QueueStore`, затем orchestrator читает оттуда первую
  задачу (а не из `select_next_todo`).
- **Fallback**: если prioritizer не смог (LLM-ошибка, пустой TASKS) →
  orchestrator использует `select_next_todo` (текущая логика).

### Триггер переоценки

Переоценка происходит автоматически при старте каждого `run_task` / cron
`task_run` — граф всегда начинается с prioritizer. Никакого отдельного
API-триггера не нужно (но `GET /api/queue` позволяет UI видеть результат).

## Section 2 — QueueStore (SQLite persistent storage)

### Схема

Новый модуль `src/devflow/batch/queue_store.py` (рядом с BatchStore):

```python
class QueueEntry(BaseModel):
    position: int          # 0-based order in the queue
    task_id: str           # Redmine task id
    task_title: str        # cached for UI display
    priority: int | None   # r-level from TASKS.md
    reason: str = ""       # LLM justification for this position
    updated_at: str        # ISO timestamp of last evaluation

class QueueStore:
    """SQLite-backed execution queue. Reordered by LLM + human edits."""

    def __init__(self, db_path: str): ...
    def get_queue(self) -> list[QueueEntry]: ...
    def set_queue(self, entries: list[QueueEntry]) -> None: ...
    def reorder(self, task_id: str, new_position: int) -> list[QueueEntry]: ...
    def move_up(self, task_id: str) -> list[QueueEntry]: ...
    def move_down(self, task_id: str) -> list[QueueEntry]: ...
    def next_task_id(self) -> str | None: ...
    def remove(self, task_id: str) -> None: ...
    def clear(self) -> None: ...
```

Таблица `execution_queue`:
```sql
CREATE TABLE IF NOT EXISTS execution_queue (
    position INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    task_title TEXT,
    priority INTEGER,
    reason TEXT DEFAULT '',
    updated_at TEXT
);
```

- `set_queue` полностью перезаписывает очередь (используется prioritizer).
- `reorder` / `move_up` / `move_down` — точечные операции для человеческих
  правок, атомарно пересчитывают `position` для всех записей.

### DB path

`.devflow/queue.db` (как `batch_store.db`). Конструируется в
`daemon/__main__.py` и передаётся в `create_app` + `WorkflowRunner` + graph.

## Section 3 — REST API

| Метод | Путь | Тело/Ответ | Поведение |
|---|---|---|---|
| `GET` | `/api/queue` | `[QueueEntry]` | Текущая очередь (ordered by position). |
| `PATCH` | `/api/queue/reorder` | `{task_id: str, new_position: int}` → `[QueueEntry]` | Переместить задачу на новую позицию (атомарный пересчёт). |
| `POST` | `/api/queue/move-up` | `{task_id: str}` → `[QueueEntry]` | Сдвинуть вверх на 1. |
| `POST` | `/api/queue/move-down` | `{task_id: str}` → `[QueueEntry]` | Сдвинуть вниз на 1. |

- `reorder` валидирует `new_position` (0..len-1), иначе `422`.
- Unknown `task_id` → `404`.
- Все операции возвращают обновлённую очередь целиком (для UI refresh).
- **Нет** POST для добавления задач (только LLM через prioritizer).

## Section 4 — Frontend (Run-вкладка расширение)

### QueuePanel (новый компонент)

В Run-вкладке, под формой запуска — новая секция **«Execution queue»**:

```
┌─ Execution queue ───────────────────────────┐
│ ↻ Обновить     Обновлено: 15:30             │
│                                             │
│ ⠿ 1. #251977 — Применить стиль [r0]   ↑ ↓  │
│ ⠿ 2. #239038 — При переходе... [r0]    ↑ ↓  │
│ ⠿ 3. #250460 — Голосовой поиск         ↑ ↓  │
│ ⠿ 4. #248835 — ИАФ. Фильтр даты         ↑ ↓ │
│                                             │
│ Перетащите ⠿ для изменения порядка          │
└─────────────────────────────────────────────┘
```

### Drag-and-Drop

Нативный HTML5 Drag and Drop API (без зависимостей):
- Каждая строка — `draggable="true"`.
- `dragstart` → сохраняет `task_id` источника.
- `dragover` → `e.preventDefault()` + визуальная подсветка target-строки.
- `drop` → `PATCH /api/queue/reorder {task_id, new_position}`.

### Кнопки ↑↓ (fallback)

На каждой строке:
- **↑** — `POST /api/queue/move-up` (disabled на первой строке).
- **↓** — `POST /api/queue/move-down` (disabled на последней строке).

### Store

Расширение `controlsStore` (или новый `queueStore`):
- `queue: ref<QueueEntry[]>`
- `fetchQueue()`, `reorder(task_id, new_position)`, `moveUp(task_id)`,
  `moveDown(task_id)`
- `lastUpdated: ref<Date | null>`

### SSE-интеграция

Новый event `queue.updated` в EventBus:
- Prioritizer публикует `queue.updated` после `queue_store.set_queue()`.
- `useSSE.ts` слушатель → `queueStore.fetchQueue()` (мгновенное обновление UI
  после LLM-переоценки).

### Polling

Поллинг каждые 30с (как TasksTab) — fallback если SSE не сработал.

## Section 5 — Data flow

```
[Cron / run_task]
  ↓
graph: START → prioritizer_node
  ↓
  reads TASKS.md + repo context
  ↓
  LLM call → ordered_tasks
  ↓
  queue_store.set_queue(entries)
  ↓
  event_bus.publish("queue.updated")
  ↓ (SSE)
[Dashboard Run tab] → queueStore.fetchQueue() → UI обновляется
  ↓
orchestrator_node
  ↓
  queue_store.next_task_id() → task_id
  ↓
  task_fetcher → planner → ...
```

Человек может в любой момент переставить очередь (d&d / ↑↓) →
`queue_store.reorder` → `event_bus.publish("queue.updated")` →
оркестратор возьмёт обновлённую первую задачу.

## Section 6 — Testing

### Backend (pytest)

- `tests/unit/batch/test_queue_store.py`:
  - `set_queue` перезаписывает, `get_queue` возвращает по порядку.
  - `reorder` атомарно пересчитывает позиции.
  - `move_up` / `move_down` граничные случаи (первая/последняя).
  - `next_task_id` возвращает первую или `None`.
- `tests/unit/nodes/test_prioritizer.py`:
  - Mock LLM → возвращает ordered_tasks → записывает в QueueStore.
  - Fallback: LLM-ошибка → orchestrator использует select_next_todo.
- `tests/unit/daemon/test_web_queue.py`:
  - `GET /api/queue`, `PATCH /api/queue/reorder`, move-up/down, 404/422.
  - SSE `queue.updated` публикуется после set_queue.

### Frontend

- `npm run typecheck` + `npm run build`.
- Ручной smoke-чеклист: очередь рендерится, d&d работает, ↑↓ работают.

## Section 7 — Ограничения и риски

- **LLM-стоимость**: prioritizer делает LLM-вызов при каждом цикле. Если задач
  много (>20), промпт растёт. **Решение**: ограничить контекст (топ-N задач
  по приоритету + краткое дерево файлов, не полный diff).
- **Согласованность очереди и TASKS**: если TASKS.md изменился (новые задачи
  из Redmine), очередь может устареть. **Решение**: prioritizer всегда
  пересоздаёт очередь из текущего TASKS.md при каждом цикле — человеческие
  правки переписываются. **Компромисс**: документировать, что ручные правки
  живут только до следующего цикла (или добавить флаг «lock manual order»).
- **Конкурентность**: d&d-перестановка во время работы prioritizer — race
  condition. **Решение**: `threading.Lock` в QueueStore (как в BatchStore).
- **Graph изменение**: вставка узла перед orchestrator меняет entry edge —
  нужно обновить тесты graph (`test_graph.py`).

## File inventory

**Backend — new:**
- `src/devflow/batch/queue_store.py` — QueueStore (SQLite)
- `src/devflow/nodes/prioritizer.py` — LLM-узел
- `config/agents/prioritizer.md` — промпт
- `tests/unit/batch/test_queue_store.py`
- `tests/unit/nodes/test_prioritizer.py`
- `tests/unit/daemon/test_web_queue.py`

**Backend — modified:**
- `src/devflow/graph.py` — entry edge + prioritizer_node в graph
- `src/devflow/daemon/__main__.py` — QueueStore construction + wiring
- `src/devflow/daemon/web.py` — `/api/queue/*` endpoints
- `src/devflow/daemon/runner.py` — pass queue_store to graph

**Frontend — new:**
- `frontend/src/components/controls/QueuePanel.vue`
- `frontend/src/stores/queue.ts`

**Frontend — modified:**
- `frontend/src/components/controls/RunTab.vue` — integrate QueuePanel
- `frontend/src/api/client.ts` — queue API functions
- `frontend/src/api/types.ts` — QueueEntry type
- `frontend/src/composables/useSSE.ts` — `queue.updated` listener
- `frontend/src/style.css` — queue panel + d&d styles

## Open questions

1. **Lock manual order**: стоит ли добавить флаг «зафиксировать ручной порядок»,
   чтобы prioritizer не переписывал человеческие правки при следующем цикле?
   (Рекомендация: добавить в первой итерации простой флаг на QueueStore;
   если set — prioritizer пропускается.)
