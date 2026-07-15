# Dashboard P2 — Event Log + Approval SSE

**Date:** 2026-07-15
**Status:** Draft (pending approval)
**Subproject:** P2 из серии dashboard improvements
**Depends on:** P3 (Dashboard Workflow Control) — toast/useSSE patterns

## Context

EventBus сейчас только fan-out событий в реальном времени через SSE — без
истории. Approvals — poll-only (4с), т.к. `ApprovalStore.register/resolve`
не публикуют в EventBus (задокументированный gap в HANDOFF.md). Пользователь
не видит мгновенных уведомлений о новых approvals и не может посмотреть что
происходило в системе.

Этот подпроект (P2) добавляет persistent журнал событий (Activity Log) и
мгновенные SSE-уведомления о approvals через toast-систему.

## Goals

1. **EventStore** (SQLite) — persistent история последних 1000 событий,
   подписанная на EventBus.
2. **Approval SSE** — `approval.waiting` / `approval.resolved` публикуются в
   EventBus при register/resolve в ApprovalStore.
3. **`GET /api/events/history`** — REST endpoint для чтения истории с фильтром.
4. **ActivityView** — новый view с лентой событий, фильтром по типу, live SSE.
5. **Toast-система** — `useToast` composable + глобальный toast-container для
   approval/task уведомлений.

## Non-Goals

- UI/UX редизайн (отдельный подпроект P1).
- Token usage tracking.
- Конфигурационные события в журнале (только task/approval/queue).
- Сложный поиск/фильтрация по журналу (только фильтр по типу).

## Section 1 — EventStore (SQLite persistent history)

### Модуль `src/devflow/batch/event_store.py`

Новый модуль рядом с BatchStore/QueueStore.

**EventLogEntry** модель:
```python
class EventLogEntry(BaseModel):
    id: int
    timestamp: str  # ISO 8601 UTC
    event_type: str  # task.started, approval.waiting, queue.updated, etc.
    data: dict[str, Any]  # event payload
```

**EventStore** класс:
- DB path: `.devflow/events.db`
- Таблица `event_log`:
  ```sql
  CREATE TABLE IF NOT EXISTS event_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp TEXT NOT NULL,
      event_type TEXT NOT NULL,
      data TEXT NOT NULL  -- JSON
  )
  ```
- `add(event_type: str, data: dict) -> None` — insert + авто-очистка старых
  (если count > 1000, удалить избыточные по id).
- `get_recent(limit: int = 100, event_type: str | None = None) -> list[EventLogEntry]`
  — последние N событий, опционально отфильтрованные по типу. Сортировка:
  новые сверху (ORDER BY id DESC).
- `threading.Lock` для thread safety (как BatchStore/QueueStore).
- `sqlite3.connect(check_same_thread=False)` — cross-thread access.

### Интеграция с EventBus

EventStore **не подписывается** на EventBus напрямую через `subscribe` (это
async). Вместо этого — новый sync-метод в EventBus или wrapper в daemon:

**Подход (simplest):** в `daemon/__main__.py` после создания EventStore,
создаём фоновую asyncio-задачу, которая подписывается на GLOBAL_TOPIC и
пишет каждое событие в EventStore через `asyncio.to_thread(store.add, ...)`.

Альтернативно (проще для синхронных publisher'ов): расширить `EventBus.publish`
опциональным `event_store` callback. Но это засоряет EventBus. Принято:
фоновая задача-подписчик в daemon.

### Wiring в `daemon/__main__.py`

```python
from devflow.batch.event_store import EventStore
event_store = EventStore(str(Path(repo_path) / ".devflow" / "events.db"))

# Background task: subscribe to EventBus → write to EventStore.
async def _log_events():
    queue = await event_bus.subscribe(GLOBAL_TOPIC)
    while True:
        msg = await queue.get()
        event_type = msg.get("event", "unknown")
        asyncio.to_thread(event_store.add, event_type, msg)  # non-blocking
```

EventStore передаётся в `create_app` для `/api/events/history`.

### Testing

`tests/unit/batch/test_event_store.py`:
- `add` insert + `get_recent` round-trip.
- Авто-очистка при превышении 1000.
- Фильтр по `event_type`.
- Сортировка новые-сверху.

## Section 2 — Approval SSE

### Изменения ApprovalStore

`src/devflow/daemon/approval_store.py`:

- `__init__` принимает optional `event_bus: EventBus | None = None`.
- `register(thread_id, payload)`: после записи в `_pending`:
  ```python
  if self._event_bus is not None:
      _publish_sync(self._event_bus, "*", {
          "event": "approval.waiting",
          "thread_id": thread_id,
          **payload,
      })
  ```
- `resolve(thread_id, decision)`: после отметки resolved:
  ```python
  if self._event_bus is not None:
      _publish_sync(self._event_bus, "*", {
          "event": "approval.resolved",
          "thread_id": thread_id,
          "approved": decision.get("approved"),
          "reason": decision.get("reason", ""),
      })
  ```

`_publish_sync` — helper (как runner._publish): вызывает `asyncio.run(bus.publish(...))`,
при RuntimeError (внутри loop) fallback на thread.

### Wiring в `daemon/__main__.py`

```python
approval_store = ApprovalStore(event_bus=event_bus)
```

### Frontend SSE listeners

`composables/useSSE.ts`:
```typescript
source.addEventListener('approval.waiting', () => {
  toast.show('🔔 Новый approval ожидает', 'warning')
  void approvals.fetch()
})
source.addEventListener('approval.resolved', (ev) => {
  const data = JSON.parse(ev.data)
  toast.show(
    data.approved ? '✅ Approval approved' : '❌ Approval rejected',
    data.approved ? 'success' : 'error'
  )
  void approvals.fetch()
})
```

Это убирает poll-only задержку. Polling остаётся как fallback (ApprovalsView 4с).

### Testing

`tests/unit/daemon/test_approval_store.py` (расширить):
- `register` с `event_bus` публикует `approval.waiting` (через тестовую подписку).
- `resolve` публикует `approval.resolved`.
- `event_bus=None` → публикация пропускается (нет ошибки).

## Section 3 — REST API + ActivityView + Toast

### `GET /api/events/history`

| Параметр | Default | Описание |
|---|---|---|
| `limit` | 100 | Макс. число событий (1..1000) |
| `event_type` | None | Фильтр по типу (напр. `task.started`); prefix match (`task` → все task.*) |

Response: `list[EventLogEntry]` (новые сверху).

Реализация в `web.py`:
```python
@app.get("/api/events/history")
async def event_history(limit: int = 100, event_type: str | None = None):
    es = app.state.event_store
    if es is None:
        return []
    return [e.model_dump() for e in es.get_recent(limit=limit, event_type=event_type)]
```

### Frontend — ActivityView

Новый роут `/activity` → `ActivityView.vue` (lazy-load).

Навигация: новый пункт «Activity» в `App.vue` (после «Controls»).

Layout:
```
┌─ Activity Log ────────────────────────────────┐
│ Filter: [all ▾]             ↻ Обновить        │
│                              Обновлено: 15:30  │
├───────────────────────────────────────────────┤
│ 15:30:22  task.started    #251977 — Стиль     │
│ 15:28:10  queue.updated   LLM: 12 задач       │
│ 15:25:03  approval.waiting  plan #239038      │
│ 15:24:55  task.finished   #239038 — ✅        │
└───────────────────────────────────────────────┘
```

- Лента событий в обратном хронологическом порядке (новые сверху).
- Фильтр по типу: `<select>` (all / task.* / approval.* / queue.*).
- Кнопка «↻ Обновить» + индикатор «Обновлено: HH:MM:SS».
- Live-обновление через SSE: новые события добавляются вверху списка.
- Polling 30с (fallback).
- Цветовое кодирование: task — синий, approval — оранжевый, queue — зелёный, error — красный.

**Stores:**
- `useActivityStore` (`stores/activity.ts`): `events: ref<EventLogEntry[]>`,
  `filter: ref<string>`, `fetch()`, `addEvent(entry)` (для live SSE prepend).

### Frontend — Toast-система

**`composables/useToast.ts`:**
```typescript
interface ToastItem {
  id: number
  message: string
  type: 'info' | 'success' | 'warning' | 'error'
}

export function useToast() {
  const toasts = ref<ToastItem[]>([])

  function show(message: string, type: ToastItem['type'] = 'info') {
    const id = Date.now()
    toasts.value.push({ id, message, type })
    setTimeout(() => dismiss(id), 5000)  // auto-dismiss 5s
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  return { toasts, show, dismiss }
}
```

**`App.vue`** — глобальный toast-container:
```vue
<div class="toast-container">
  <div v-for="t in toasts" :key="t.id" :class="['toast', t.type]" @click="dismiss(t.id)">
    {{ t.message }}
  </div>
</div>
```

Position: fixed top-right, z-index 9999. Types → colors: info=#0366d6, success=#28a745, warning=#d97706, error=#cb2431.

Используется: `approval.waiting` (warning), `approval.resolved` (success/error),
`task.error` (error), опционально `task.started/finished` (info).

### API client + types

`api/client.ts`: `getEventHistory(limit?, eventType?)`.
`api/types.ts`: `EventLogEntry`.

## File inventory

**Backend — new:**
- `src/devflow/batch/event_store.py` — EventStore (SQLite)
- `tests/unit/batch/test_event_store.py`

**Backend — modified:**
- `src/devflow/daemon/approval_store.py` — event_bus param + publish on register/resolve
- `src/devflow/daemon/__main__.py` — EventStore construction + EventBus subscriber task + wiring
- `src/devflow/daemon/web.py` — `GET /api/events/history` + event_store param
- `tests/unit/daemon/test_approval_store.py` — SSE publish tests

**Frontend — new:**
- `frontend/src/views/ActivityView.vue`
- `frontend/src/stores/activity.ts`
- `frontend/src/composables/useToast.ts`

**Frontend — modified:**
- `frontend/src/App.vue` — nav «Activity» + toast-container
- `frontend/src/router/index.ts` — `/activity` route
- `frontend/src/composables/useSSE.ts` — approval.waiting/resolved listeners
- `frontend/src/api/client.ts` — getEventHistory
- `frontend/src/api/types.ts` — EventLogEntry
- `frontend/src/style.css` — activity log + toast styles

## Open questions

Нет — все решения согласованы.
