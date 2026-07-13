# Coach — Effectiveness Manager — Design Spec

**Date:** 2026-07-13
**Status:** Draft (pending user review)
**Author:** Design session via brainstorming skill
**Depends on:** `2026-07-12-autonomous-workflow-launch-design.md` (daemon, scheduler, ntfy/email)

## Context

`devflow-super` — Python CLI на LangGraph: `orchestrator → planner → plan_approval → maker → self_review → checker_a/b/c (parallel) → aggregate_checker → publish_approval → reporter`, с `research` как on-demand сервисом и `TODO.md` как очередью задач. Daemon (из spec автономного запуска) запускает workflow по cron с HITL-стратегиями.

**Проблема.** Сейчас система не знает, насколько эффективно работают её собственные субагенты. Нет метрик качества, нет способа понять «где болит», нет процесса улучшения промптов. Правки промптов делаются вслепую, на интуиции.

**Цель.** Ввести отдельную подсистему «coach» — agile coach + аналитик метрик, который:
1. Наблюдает за прогонами через metrics store (пассивно, не в графе).
2. Раз в период делает «ретро» — диагностику подсистем по множеству осей (Squad Health Check).
3. Генерирует Markdown-отчёт с гипотезами и предложенными правками промптов.
4. Не правит боевые промпты автоматически — человек решает.
5. Единственная автоматика — бандит-роутинг чекеров (opt-in, уровень A).

**Аналогия.** Это перенос зрелых управленческих практик (DORA-метрики, 360-review, agile retrospective, Squad Health Check) на команду агентов. Не «оценка людей/агентов», а «диагностика системы». Индустрия HR выучила за 30 лет, что single-metric рейтинги и continuous-tuning проваливаются; агентной системе эти ошибки повторять не обязательно.

## Принципы (из brainstorming-сессии)

1. **Coach не в графе, а в демоне** — отдельный ритм, не тормозит прогоны.
2. **Метрики — система из ≥3 осей**, ни одна не решает в одиночку (защита от Goodhart/gaming).
3. **Правки — дискретным ретро**, не continuous tuning (анти-prompt-drift).
4. **Оценка — три источника** (артефакты, downstream peer, LLM-judge), расхождение = диагноз.
5. **Выход — Squad Health Check**, не рейтинг агентов (диагностика подсистем, не персон).
6. **Решения по уровням риска**: автопилот (бандит) → рекомендация (правки промптов) → человек+eval (структурные).

## Решённые решения (из brainstorming)

| Решение | Выбор | Обоснование |
|---|---|---|
| Scope спеки | Full-scope, все 6 идей | Целостная картина, фазирование в отдельной секции |
| Metrics store | Отдельный SQLite `metrics_store.db` | Чистое разделение ответственности; не смешивать с `batch_store.db` |
| Единица наблюдения | Task run (с детализацией `node_events`) | Естественная единица — один todo-item = один run |
| Coach trigger | Cron в daemon + ручной запуск (CLI) | Ретро по расписанию + возможность внеочередного |
| Coach output | Markdown отчёт `docs/retro/YYYY-WWW.md` | Самый безопасный: ноль автоматических правок |
| Bandit scope | Выбор подмножества чекеров | Безопасный автопилот: чекеры не пишут код |
| Golden eval | Frozen реплей закрытых задач | Самый честный способ получить «известный правильный исход» |
| Coach agent | `config/agents/coach.md` (LLM) | Consistent с существующей архитектурой |
| Reopen signal | Да, опрашивать трекер (cron) | Самый поздний, но самый честный quality-сигнал |
| Архитектурный подход | Подход 1: минимальная инвазия | Coach снаружи графа, обёртки instrumentation, opt-in feature flag |

## Архитектурный компромисс: counterfactual в бандите

Бандит, выбирающий подмножество чекеров, сталкивается с проблемой: если выбрано `{a, b}`, мы *не знаем*, сказал бы `checker_c` REJECT. Чистый counterfactual на отдельных прогонах неполный.

**Решение:** forced exploration. ε=0.1 прогонов идёт с полным набором `{a, b, c}` независимо от beliefs бандита. Эти прогоны дают «ground truth» — все три вердикта — и на них beliefs обновляются полноценно для всех подмножеств. 90% прогонов экономят токены, 10% собирают правду. Это классический bandit-компромисс exploration/exploitation.

Альтернатива — всегда запускать все три и *post hoc* вычислять, какого было бы достаточно — отвергнута как уничтожающая саму цель экономии токенов.

## Архитектура

### Общая картина

```
                    ┌──────────────────────────────────────────────┐
   task_schedule ──▶│  LangGraph workflow (существующий)           │
                    │  orchestrator → planner → plan_approval      │
                    │  → maker → self_review → checkers            │
                    │  → aggregate → publish_approval → reporter   │
                    └──────────────┬───────────────────────────────┘
                                   │ instrumentation hooks
                                   │ (обёртки, не меняют контракт нод)
                                   ▼
                    ┌──────────────────────────────────────────────┐
                    │  metrics_store.db (SQLite)                   │
                    │  runs, node_events, checker_outcomes,        │
                    │  research_calls, bandit_beliefs              │
                    └──────┬───────────────────┬───────────────────┘
                           │                   │
             coach_cron ───┘                   └─── reopen_poller (cron)
                           │
                           ▼
                    ┌──────────────────────────────────────────────┐
                    │  Coach pipeline                              │
                    │  1. SQL-агрегация метрик за окно             │
                    │  2. Squad Health Check (детерминированный)   │
                    │  3. LLM-агент coach.md → гипотезы + диффы    │
                    │  4. Golden-eval gate (для правок уровня B)   │
                    └──────────────┬───────────────────────────────┘
                                   ▼
                    docs/retro/YYYY-WWW.md  (Markdown отчёт)
                                   │
                    ┌──────────────┴───────────────────────────────┐
                    ▼                       ▼                      ▼
              Уровень A               Уровень B              Уровень C
              автопилот               рекомендация           структура
              (бандит-роутинг         (дифф промпта в        (add/remove agent,
               чекеров, без            отчёте → человек       golden eval обязателен)
               человека)               копирует)              НЕТ в первой итерации
                                                           — отключено через конфиг
```

Принцип: coach — отдельный процесс в демоне (cron), читает metrics_store, генерирует отчёт. Граф workflow instrumentation'ится через обёртки, но контракт нод не меняется. Бандит — единственная точка, где автоматика влияет на выполнение задачи, и она opt-in.

### Компоненты

| Компонент | Файл | Ответственность |
|---|---|---|
| `MetricsStore` | `src/devflow/coach/store.py` | SQLite CRUD: runs, node_events, checker_outcomes, research_calls, bandit_beliefs |
| Instrumentation | `src/devflow/coach/instrument.py` | `trace_node()`, `trace_llm_call()`, `_infer_transition()` |
| `ReopenPoller` | `src/devflow/coach/reopen.py` | Cron: опрашивает трекер, обновляет `runs.tracker_reopened` ретроактивно |
| Health check | `src/devflow/coach/health.py` | Детерминированный расчёт 8 осей red/yellow/green из metrics_store |
| Coach runner | `src/devflow/coach/runner.py` | `run_coach(window)` — конвейер ретро: aggregate → health → LLM → отчёт |
| `coach.md` | `config/agents/coach.md` | System prompt coach-агента |
| `BanditRouter` | `src/devflow/coach/bandit.py` | Thompson sampling — выбор подмножества чекеров по task_class |
| `GoldenEvaluator` | `src/devflow/coach/golden.py` | Прогон кандидата-правки по замороженным задачам |
| Task classifier | `src/devflow/coach/classify.py` | `classify_task(task, rules)` → task_class (rule-based) |
| Coach CLI | `src/devflow/coach/cli.py` | `devflow-super coach --window 7d` |

### Что НЕ меняется

- **Существующие ноды** (`orchestrator`, `planner`, `maker`, `checkers`, `reporter`) — контракт не меняется. Instrumentation навешивается через обёртки в `graph.py` при сборке, не внутри нод.
- **Структура графа workflow** — рёбра те же. Единственное изменение: `checker_dispatcher` опционально спрашивает бандита, с fallback на `DEFAULT_CHECKERS`.
- **Daemon** (из spec автономного запуска) — coach добавляется как cron-правило + отдельный runner, не конкурирует с task-run по lock'ам (coach читает, не пишет в batch_store).

## Metrics store

### Файл

`{repo}/.devflow/metrics_store.db` — тот же каталог `.devflow/`, что и `batch_store.db` из daemon spec. Это разные файлы с разными ответственностями:
- `batch_store` — состояние EOD-публикаций (live, mutable per task lifecycle).
- `metrics_store` — append-mostly история для анализа (read-only в норме; reopen-апдейт — единственная mutation).

SQLite в WAL-mode для конкурентности (daemon пишет metrics, coach читает). Зависимость `sqlite3` — stdlib, новых пакетов не требуется.

### Schema

```sql
-- Главная единица наблюдения: один прогон задачи через граф
CREATE TABLE runs (
    run_id            TEXT PRIMARY KEY,        -- uuid4
    task_id           TEXT NOT NULL,           -- "251977" / "MOCK-1"
    task_title        TEXT,
    task_class        TEXT,                    -- класс задачи — для бандита и health check
    todo_line_no      INTEGER,                 -- строка в TODO.md
    started_at        TEXT NOT NULL,           -- ISO8601
    finished_at       TEXT,                    -- ISO8601, NULL пока идёт
    outcome           TEXT,                    -- done | escalate | error | interrupted
                                             -- (см. примечание про outcome ниже)
    final_verdict     TEXT,                    -- APPROVE | REJECT | CONDITIONAL | ESCALATE
    rework_count      INTEGER DEFAULT 0,       -- финальное значение (сколько rework-циклов было)
    tests_green       INTEGER,                 -- 0/1/NULL — см. примечание про tests_green ниже
    plan_requested_changes INTEGER DEFAULT 0,  -- 1 если plan_approval вернул непустой
                                             -- requested_changes; иначе 0
    -- ретроактивные:
    -- ретроактивные:
    tracker_reopened    INTEGER DEFAULT 0,     -- 1 если задача reopened в трекере после done
    reopened_checked_at TEXT                   -- когда последний раз опрашивали
);
CREATE INDEX idx_runs_started ON runs(started_at);
CREATE INDEX idx_runs_task_class ON runs(task_class);
CREATE INDEX idx_runs_outcome ON runs(outcome);

-- Детализация: каждый вызов ноды внутри run (включая rework-итерации)
CREATE TABLE node_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    node        TEXT NOT NULL,           -- "planner" | "maker" | "checker_a" | ...
    iteration   INTEGER DEFAULT 0,       -- 0 для первого прохода, 1+ для rework
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    tokens_in   INTEGER,                 -- из llm usage callback
    tokens_out  INTEGER,
    transition  TEXT,                    -- как нода завершилась:
                                         -- "ok" | "research_requested" | "error" |
                                         -- "rejected_by_next" | "approved" |
                                         -- "rejected" | "changes_requested" |
                                         -- "verdict:APPROVE" | "verdict:REJECT" | ...
    error_message TEXT
);
CREATE INDEX idx_node_run ON node_events(run_id);
CREATE INDEX idx_node_node ON node_events(node);

-- Вердикты каждого чекера по каждой задаче — для бандита
CREATE TABLE checker_outcomes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    checker    TEXT NOT NULL,           -- "checker_a" | "checker_b" | "checker_c"
    task_class TEXT NOT NULL,           -- денормализовано для быстрого запроса
    verdict    TEXT NOT NULL,           -- APPROVE | REJECT | CONDITIONAL
    issued_at  TEXT NOT NULL,
    invoked    INTEGER DEFAULT 1        -- был ли реально запущен (бандит мог дропнуть)
);
CREATE INDEX idx_co_class ON checker_outcomes(task_class, checker);

-- Research вызовы — для оценки ROI
CREATE TABLE research_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    caller_node  TEXT NOT NULL,         -- "planner" | "maker" | ...
    query        TEXT,
    sources_used TEXT,                  -- JSON array
    tokens_used  INTEGER,
    issued_at    TEXT NOT NULL
);

-- Beliefs бандита — Thompson sampling Alpha/Beta по (task_class, subset)
CREATE TABLE bandit_beliefs (
    task_class   TEXT NOT NULL,
    subset       TEXT NOT NULL,         -- "full" | "ab" | "ac" | "bc" | "a" | "b" | "c"
    alpha        REAL DEFAULT 1.0,      -- Beta prior: reward=1
    beta         REAL DEFAULT 1.0,      -- Beta prior: reward=0
    last_updated TEXT,
    PRIMARY KEY (task_class, subset)
);
```

### Семантика ключевых полей

**`runs.outcome`** — финальный результат *прогона целиком*, не отдельной итерации. Возможные значения:
- `done` — reporter завершил работу, задача отмечена выполненной (вне зависимости от того, сколько rework-циклов было внутри).
- `escalate` — `aggregate_checker` вернул `FinalVerdict.ESCALATE` (исчерпан `max_rework_iterations`).
- `error` — любая нода вернула `WorkflowError`, прогон упал.
- `interrupted` — прогон прерван (демон упал во время interrupt, или approval timeout → reject).

`rework` **не является** значением `outcome` — это переходный verdict внутри прогона. Сколько rework-циклов было, показывает `rework_count`. Один прогон с тремя rework-циклами, завершившийся успехом — это `outcome=done, rework_count=3`.

**`runs.tests_green`** — результат тестов из `maker`. Парсится из return-value `maker_node` (поле `test_output` в logs, статус выполнения `test_commands`). 0/1/NULL: NULL если тестов не было или статус не определён.

**`runs.plan_requested_changes`** — 1 если `plan_approval` resume value содержал непустой `requested_changes` (человек правил план), иначе 0. Это proxy-сигнал качества планов planner'а.

**`checker_outcomes.invoked`** — 1 если чекер реально запускался в этом прогоне, 0 если бандит его дропнул. Записи с `invoked=0` создаются только в режиме бандита — для всех дропнутых чекеров, чтобы в анализе видеть «для класса X чекер C не вызывался N раз». На прогонах без бандита все три чекера имеют `invoked=1`.

### task_class — классификация задач

Нужен для бандита (решение по классу) и для health check (агрегация не по всем задачам вперемешку).

- **На старте (rule-based, без LLM):** простые правила по `task.metadata` (Redmine project, tracker, tags). Например, Redmine project "frontend" → класс `frontend-vue`; тег "3D" → `frontend-threejs`. Правила задаются в `config/coach.yaml` → `task_class_rules`.
- **Позже (future work):** LLM-классификатор, если rule-based окажется недостаточно.
- Если класс не определён — `task_class = "default"`. Бандит и health check работают с `default` как с любым другим классом.

### Когда пишутся данные

| Событие | Что записывается |
|---|---|
| `orchestrator` ставит `[~]` | `INSERT INTO runs` (started_at, task_id, task_class) |
| Любая нода стартует | `INSERT INTO node_events` (started_at) |
| `call_structured` завершён | `UPDATE node_events` SET tokens_in/out (через on_usage callback) |
| Любая нода завершена | `UPDATE node_events` SET finished_at, duration_ms, transition |
| `aggregate_checker` | `INSERT INTO checker_outcomes` для каждого вызванного чекера (из `state["checker_reports"]`) |
| `checker_dispatcher` (в режиме бандита) | `INSERT INTO checker_outcomes` со `invoked=0` для дропнутых бандитом чекеров |
| `research` вызвался | `INSERT INTO research_calls` |
| `reporter` завершён | `UPDATE runs` SET finished_at, outcome, final_verdict, rework_count, tests_green |
| `reopen_poller` | `UPDATE runs` SET tracker_reopened (ретроактивно, по задачам старше N дней) |

**Ключевой принцип:** запись в metrics_store никогда не должна ронять прогон. Все insert/update в try/except, при ошибке — `logger.warning` и продолжение. Метрики — наблюдение, не критический путь.

## Instrumentation

Принцип: существующие ноды (`maker_node`, `run_checker_node`, и т.д.) **не меняются**. Instrumentation навешивается в двух точках — на сборке графа и на едином LLM-выходе.

### Hook 1: `trace_node()` — обёртка вокруг ноды

```python
# src/devflow/coach/instrument.py
def trace_node(node_name: str, store: MetricsStore):
    """Декоратор: оборачивает node-fn, пишет node_events в metrics_store."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(state, *args, **kwargs):
            run_id = state.get("__metrics_run_id")
            if run_id is None:
                return fn(state, *args, **kwargs)  # без run_id — без трассировки
            event_id = store.start_node_event(run_id, node_name)
            t0 = time.monotonic()
            try:
                result = fn(state, *args, **kwargs)
                transition = _infer_transition(node_name, state, result)
                store.finish_node_event(event_id, transition=transition,
                                        duration_ms=int((time.monotonic()-t0)*1000))
                return result
            except Exception as exc:
                store.finish_node_event(event_id, transition="error", error_message=str(exc),
                                        duration_ms=int((time.monotonic()-t0)*1000))
                raise
        return wrapper
    return decorator
```

Применяется в `graph.py` при сборке (единственная правка существующего файла):
```python
# Было:
graph.add_node("maker", partial(maker_node, app_cfg=cfg, ...))
# Стало:
maker_fn = maker_node
if metrics_store is not None:
    maker_fn = trace_node("maker", metrics_store)(partial(maker_node, app_cfg=cfg, ...))
graph.add_node("maker", maker_fn)
```

`run_id` проставляется обёрткой `orchestrator` при старте задачи (она же делает `INSERT INTO runs`). Передаётся через `state["__metrics_run_id"]` — префикс `__` означает «служебное, не для агентов».

### Hook 2: `trace_llm_call()` — обёртка вокруг `call_structured`

Единая точка для всех LLM-вызовов (`planner`, `maker`, `checker`, `reporter` — все через `call_structured`). Одна обёртка ловит токены для всех агентов.

```python
# src/devflow/utils/structured_llm.py (существующий файл — добавляется опц. callback)
def call_structured(llm, system_prompt, user_prompt, response_model,
                    *, on_usage: Callable[[Usage], None] | None = None):
    ...
    response = llm.invoke(messages, response_format=...)
    if on_usage and response.usage:
        on_usage(response.usage)   # tokens_in, tokens_out
    return parsed
```

`on_usage` прокидывается из `trace_node` обёртки через contextvar и пишет в текущий `node_event`. Даёт per-agent token breakdown без правок каждой ноды.

### `_infer_transition()` — что нода решила

Детерминированно выводится из return-value ноды (без LLM):

| Нода | transition значения | Откуда берётся |
|---|---|---|
| `planner` | `ok` / `research_requested` | `Command(goto="research")` vs plain dict |
| `plan_approval` | `approved` / `rejected` / `changes_requested` | resume value shape |
| `maker` | `ok` / `research_requested` / `error` | Command vs dict, наличие `error` |
| `self_review` | `ok` / `rejected` | edge routing (→ checkers vs → reporter) |
| `run_checker` | `verdict:APPROVE` / `verdict:REJECT` / ... | из `CheckerReport.verdict` |
| `aggregate_checker` | `rework` / `done` / `escalate` | из `FinalVerdict` |
| `reporter` | `done` / `error` / `prepare_only` | из `ReporterResponse` + hitl_strategy |

Эти переходы — **бесплатный downstream-acceptance сигнал**. `transition="rejected"` у self_review — peer review maker'а. `transition="rework"` у aggregate_checker — peer review с другой стороны.

### Неинвазивность

Полный список правок существующего кода для instrumentation:
1. `src/devflow/utils/structured_llm.py` — добавить опц. параметр `on_usage` (~5 строк).
2. `src/devflow/graph.py` — обернуть ноды через `trace_node()` при сборке, если `metrics_store` сконфигурирован.
3. `src/devflow/state.py` — добавить `__metrics_run_id` в `WorkflowState` (опц. поле).

**Никакая нода (`nodes/*.py`) не правится.** Instrumentation не должен становиться точкой отказа.

### Отказоустойчивость

- Все методы `MetricsStore` — в try/except, при ошибке БД логируют warning и продолжают.
- Instrumentation можно **полностью выключить** конфигом (`coach.enabled: false`) — граф собирается без обёрток, система работает как сейчас.

## Squad Health Check

Концептуальное ядро всей затеи — сдвиг с «рейтинга агентов» на «диагностику подсистем». Coach не выдаёт «maker: 7/10», он выдаёт health-check по осям. **Bad-менеджмент оценивает людей; good-менеджмент оценивает систему.**

### Восемь осей

Каждая считается **детерминированно** (SQL + правило red/yellow/green), без LLM. Пороги задаются в `config/coach.yaml` и калибруются со временем.

| Ось | Подсистема | Что меряет | Красный | Жёлтый | Зелёный | SQL-источник |
|---|---|---|---|---|---|---|
| `routing` | orchestrator | баланс приоритетов | только r0, игнорирует r3-r5 | небольшой перекос | сбалансировано | distribution по task_class и todo_line_no |
| `planning` | planner | качество планов | человек правит план в >50% | 20-50% | <20% | `node_events` planner + `runs.plan_requested_changes` |
| `implementation` | maker | качество реализации | rework rate >40% | 20-40% | <20% | доля runs с `rework_count > 0` / total runs |
| `self_review_boundary` | self_review ↔ checkers | согласованность peer-review | self_review пропускает то, что ловят checkers (gap >30%) | gap 10-30% | gap <10% | сравнение transition self_review vs aggregate verdict |
| `checker_coverage` | checkers | ценность каждого чекера | чекер не добавляет уникальных REJECT (>80% дубликатов) | moderate | добавляет уникальные сигналы | `checker_outcomes` — доля REJECT, не совпадающих с другими |
| `research_roi` | research | окупаются ли research-вызовы | research зовётся, но outcome не меняется | частично | вызовы коррелируют с green outcome | `research_calls` + сравнение outcome с/без research |
| `reporter_trust` | reporter | соответствие «done» реальности | tracker reopen после done >20% | 5-20% | <5% | `runs.tracker_reopened` после outcome=done |
| `throughput` | весь pipeline | стоимость | tokens/task растёт / cycle time растёт | plateau | снижается или стабильно | `node_events` sum(tokens) per run + cycle time |

### Почему оси, а не «оценка каждого агента»

В осях **нет «оценки maker'а как личности»**. Есть:
- `implementation` — качество кода, которое maker производит, *в контексте плана* (план плохой → maker выглядит плохо)
- `self_review_boundary` — про *границу* между двумя агентами
- `checker_coverage` — про *ценность* чекера в системе

Если `implementation` красный, coach не скажет «maker плохой». Он скажет: «implementation-ось красная; X% случаев это плохой план (planning-ось тоже красная → корень в planner), Y% случаев это maker не справился с хорошим планом (planning зелёная → корень в maker)». **Расхождение между осями = диагноз.**

### Диагностические паттерны (расхождения = сигнал)

| Что красное | Что зелёное | Диагноз | Куда копает coach |
|---|---|---|---|
| `implementation` | `planning` | maker плохо выполняет хорошие планы | промпт maker, tools, max_tokens |
| `implementation` + `planning` | — | planner выдаёт планы, которые maker не может выполнить | промпт planner, согласованность plan↔maker |
| `self_review_boundary` | `implementation` | self_review пропускает баги, которые ловят checkers | промпт self_review (критерии мягче, чем у checkers) |
| `checker_coverage` (для C) | другие | чекер C не добавляет ценности | кандидат на дроп бандитом / удаление (уровень C) |
| `research_roi` | — | research зовётся впустую | отключить research для класса, или улучшить промпт |
| `reporter_trust` | `implementation` | maker хорошо, но reporter «закрывает» то, что reopen'ится | промпт reporter, или критерии aggregate слишком мягкие |
| `throughput` растёт | остальные стабильны | дрейф стоимости без причины | prompt drift у одного из агентов |

### Пороги — калибруемые

`config/coach.yaml`:
```yaml
health_check:
  thresholds:
    implementation:
      rework_rate_red: 0.40
      rework_rate_yellow: 0.20
    planning:
      plan_changes_red: 0.50
      plan_changes_yellow: 0.20
    self_review_boundary:
      gap_red: 0.30
      gap_yellow: 0.10
    reporter_trust:
      reopen_red: 0.20
      reopen_yellow: 0.05
    checker_coverage:
      unique_reject_yellow: 0.25   # ниже — кандидат на деприоритизацию
    # ... итд для каждой оси
  min_window_size: 20    # меньше 20 runs → "insufficient data", не оцениваем
```

`min_window_size` критичен: на малой выборке любая метрика — шум. Health check явно говорит «недостаточно данных» вместо ложного red/green.

## Coach pipeline и отчёт

### Конвейер (запускается по cron)

```
1. SQL-агрегация метрик за окно
   ├─ sum/count по runs, node_events, checker_outcomes
   ├─ per task_class breakdown
   └─ comparison с предыдущим окном (Δ WoW)

2. Squad Health Check (детерминированный, секция выше)
   └─ output: dict[axis → {status, value, delta}]

3. Coach LLM (config/agents/coach.md)
   ├─ input: агрегаты + health check + сырые аномалии
   ├─ output: CoachReport schema (см. ниже)
   │   • observations (факты, не интерпретация)
   │   • hypotheses (1-3, ранжированные, с осью-корнем)
   │   • proposed_diffs (для уровня B: диффы к config/agents/*.md)
   │   • anti_list (что НЕ трогать — тренажёр сдержанности)
   │   • bandit_suggestions (для уровня A: кого дропнуть)
   └─ constraints (жёсткие ограничения, см. ниже)

4. Golden-eval gate (опционально, для предложенных диффов)
   ├─ если дифф промпта уровня B → прогон по golden set
   ├─ если regressed → отметить «failed eval», не включать в рекомендации
   └─ см. секцию Golden eval

5. Сборка Markdown отчёта → docs/retro/YYYY-WWW.md

6. Push-уведомление (переиспользует ntfy/email из daemon spec)
   └─ «Ретро за W28 готово: 2 красных оси, 1 предложенная правка»
```

### `config/agents/coach.md` — контрактурирование

Coach — LLM-агент в существующем формате. Получает агрегаты + health check, выдаёт структурированный `CoachReport`. Жёсткий контракт (rubric, не свободный текст) — защита от галлюцинаций и дрейфа.

```python
# src/devflow/schemas.py — новые модели
class Hypothesis(BaseModel):
    root_axis: str                 # какая ось health check
    statement: str                 # "self_review критерии мягче, чем у checkers"
    evidence: str                  # ссылка на метрики
    confidence: str                # "high" | "medium" | "low"
    severity: str                  # "high" | "medium" | "low"

class PromptDiff(BaseModel):
    target_file: str               # "config/agents/maker.md"
    rationale: str                 # почему
    diff: str                      # unified diff как текст
    eval_status: str               # "passed" | "failed" | "skipped" | "unevaluated"

class BanditSuggestion(BaseModel):
    target_checker: str            # "checker_c"
    task_class: str                # "frontend-vue"
    action: str                    # "deprioritize" | "drop_for_class"
    rationale: str

class CoachReport(BaseModel):
    window: str                    # "2026-W28"
    observations: list[str]        # факты, не интерпретация
    hypotheses: list[Hypothesis]   # 1-3, ранжированные
    proposed_diffs: list[PromptDiff]        # для уровня B
    bandit_suggestions: list[BanditSuggestion]  # для уровня A
    anti_list: list[str]           # что НЕ трогать и почему (минимум 2)
    summary: str                   # 1-2 предложения для push-уведомления
```

### Жёсткие ограничения на coach LLM (constraints)

Зашиваются в system prompt `coach.md`:

1. **Максимум 3 гипотезы** за ретро. Если хочешь предложить больше — выбери топ-3 по severity × confidence.
2. **Максимум 1 предложенный дифф промпта** за ретро (уровень B). Правка промптов — тяжёлое оружие, не дробовик.
3. **Каждая гипотеза обязана ссылаться на конкретную ось** health check и конкретные числа. Без evidence — отбрасывается.
4. **Anti-list обязателен** (минимум 2 записи). Coach, который не может назвать, что оставить — плохой coach.
5. **Coach НЕ правит свой собственный промпт** (`config/agents/coach.md`). Self-reference запрещён.
6. **Coach НЕ предлагает уровень C** (add/remove agent) в первой итерации. Только уровень B и уровень A.

### Формат отчёта `docs/retro/YYYY-WWW.md`

```markdown
# Retro 2026-W28
**Период:** 2026-07-06 — 2026-07-13
**Runs в окне:** 47 (done: 31, rework: 12, escalate: 2, error: 2)
**Сгенерировано:** coach v0.1 @ 2026-07-13T22:00:00

## Health Check
| Ось | Статус | Значение | Δ к прошлой неделе |
|---|---|---|---|
| routing | 🟢 | r0:r3 = 3:1 (healthy mix) | — |
| planning | 🟡 | plan-changes 28% (threshold 20%) | ↑ from 18% |
| implementation | 🔴 | rework rate 44% (threshold 40%) | ↑ from 31% |
| self_review_boundary | 🔴 | gap 34% (threshold 30%) | ↑ from 22% |
| checker_coverage (a) | 🟢 | unique REJECTs 62% | — |
| checker_coverage (b) | 🟢 | unique REJECTs 58% | — |
| checker_coverage (c) | 🟡 | unique REJECTs 18% | ↓ from 27% |
| research_roi | 🟢 | green-with-research 71% vs without 54% | — |
| reporter_trust | 🟢 | reopen 3% (threshold 5%) | — |
| throughput | 🟡 | tokens/task +12% WoW | ↑ |

## Observations
- rework rate вырос с 31% до 44% за неделю, концентрация в frontend-vue классе
- self_review_boundary gap расширился: self_review пропускает баги,
  которые ловят checkers, в 34% случаев (было 22%)
- checker_c unique REJECTs упали с 27% до 18% — кандидат на деприоритизацию

## Hypotheses (ranked)
1. **[HIGH]** root_axis: self_review_boundary
   self_review критерии стали мягче, чем у checkers.
   Evidence: из 16 rework-случаев, 11 прошли self_review без замечаний.
   → см. proposed_diff ниже
2. **[MEDIUM]** root_axis: implementation
   ...

## Proposed prompt diff (level B — human review required)
**Target:** `config/agents/self_review.md`
**Rationale:** синхронизировать критерии с checkers
```diff
@@ -12,7 +12,7 @@
- Проверяй соответствие плану и очевидные опечатки.
+ Проверяй соответствие плану, edge-cases в логике, и
+ согласованность с критериями checker_a/b/c (см. их промпты).
```
**Eval status:** passed (golden set: 9/10 outcome совпали, baseline 8/10)

## Bandit suggestions (level A)
- checker_c: deprioritize for class "frontend-vue"
  (unique REJECT contribution 18% < threshold 25%)

## Anti-list (не трогать)
- planner — planning-ось зелёная, промпт работает
- research — research_roi зелёная, вызовы окупаются
- reporter — reporter_trust зелёная, reopen 3%

## window metrics (raw)
[детальные таблицы — для разбора]
```

### Триггеры запуска

| Триггер | Как | Окно |
|---|---|---|
| Cron | `coach_schedule: "0 22 * * 5"` в daemon | 7 дней (с прошлого ретро) |
| CLI | `devflow-super coach --window 7d` | параметр CLI |
| Ad-hoc после серии падений | watchdog в daemon: 3+ escalation за час → внеочередной coach | последние 24ч |

## Reopen poller

`ReopenPoller` — отдельный cron job в демоне. Раз в день опрашивает трекер по задачам, помеченных `outcome=done` в metrics_store, и обновляет `runs.tracker_reopened` ретроактивно.

```python
# src/devflow/coach/reopen.py
class ReopenPoller:
    def __init__(self, store: MetricsStore, task_source: TaskSource, days_back: int = 14):
        ...

    def poll(self) -> int:
        """Опросить трекер по задачам, done за последние days_back дней.
        Returns: количество найденных reopen'ов."""
        recent_done = self.store.fetch_runs(outcome="done",
                                            since=now - timedelta(days=self.days_back))
        reopened_count = 0
        for run in recent_done:
            if self._is_reopened(run.task_id):
                self.store.mark_reopened(run.run_id)
                reopened_count += 1
            self.store.mark_reopen_checked(run.run_id)
        return reopened_count
```

`days_back` — компромисс: слишком мало → пропустим поздние reopen'ы; слишком много → растёт число API-вызовов к трекеру. 14 дней по умолчанию: большинство reopen'ов приходит в первые 1-2 недели, дальше это уже не сигнал качества первоначального решения, а новые требования.

Защита от шума API: poller пишет `reopened_checked_at`, не опрашивает одну задачу дважды без необходимости. Если трекер недоступен — try/except, retry на следующем тике.

## Bandit-router (уровень A — автопилот)

Единственный компонент, действующий **без человека**. Спроектирован консервативно: чекеры только проверяют, не пишут код, fallback всегда есть.

### Что решает

На каждый прогон задачи, **перед** запуском чекеров, бандит решает: для данного `task_class` — какое подмножество из `{checker_a, checker_b, checker_c}` запускать. Цель: экономия токенов без потери coverage.

Варианты: `full` (все три), `subset` (два из трёх), `single` (один — только при высокой уверенности).

### Где встраивается

Единственная правка существующей логики диспетчера. Сейчас (`nodes/checker.py:26`):
```python
def checker_dispatcher(state: WorkflowState) -> list[Send] | str:
    ...
    return [Send("run_checker", {...}) for name in DEFAULT_CHECKERS]
```

Становится:
```python
def checker_dispatcher(state: WorkflowState, *, bandit: BanditRouter | None = None) -> list[Send] | str:
    ...
    if bandit is None:
        checkers = DEFAULT_CHECKERS
    else:
        task_class = state.get("__task_class", "default")
        checkers = bandit.select(task_class)
    return [Send("run_checker", {..., "checker_agent": name}) for name in checkers]
```

`bandit=None` — система работает как раньше. Бандит opt-in через `coach.bandit_enabled: true`.

### Алгоритм: contextual multi-armed bandit (Thompson sampling)

Контекст — `task_class`. «Руки» — подмножества чекеров. Награда — «ловит ли комбинация все REJECT, которые поймала бы полная тройка».

**Reward signal** (counterfactual из historical прогонов с full):
```
reward(S, run) = 1 если
    ∃ checker c ∈ S: c.verdict == REJECT
    ИЛИ
    все чекеры в полной тройке были APPROVE (значит ничего не упустили)
              иначе 0 (упустили REJECT, который поймал бы чекер ∉ S)
```

Подмножество хорошо если оно либо ловит REJECT, либо REJECT действительно не было. Плохо — если пропустило REJECT, который поймал бы полный набор.

Для каждой пары `(task_class, subset)` храним Beta-распределение в `bandit_beliefs`. При выборе: для каждого candidate-subset семплируем из Beta, выбираем subset с максимальным sample. Thompson sampling естественно балансирует exploration/exploitation.

### Кандидаты-подмножества

```python
CANDIDATE_SUBSETS = {
    "full":  ["checker_a", "checker_b", "checker_c"],   # безопасный baseline
    "ab":    ["checker_a", "checker_b"],
    "ac":    ["checker_a", "checker_c"],
    "bc":    ["checker_b", "checker_c"],
    "a":     ["checker_a"],
    "b":     ["checker_b"],
    "c":     ["checker_c"],
}
```

Конфиг `bandit.candidate_subsets: [full, ab, ac, bc]` по умолчанию — только безопасные (2+). Single-варианты выключены по умолчанию.

### Безопасность

1. **Coverage floor.** Минимум 2 чекера всегда (`bandit.min_checkers: 2`). Single-кандидаты участвуют в обучении, но не выбираются в бою — если Thompson их выбрал, fallback на `"ab"`.
2. **Forced exploration (ε-greedy floor).** С вероятностью `ε = 0.1` запускается полный набор (`full`) независимо от beliefs. Гарантирует continued counterfactual данные.
3. **Fallback на холодную старте.** Если для `(task_class, subset)` меньше `min_observations = 5` точек — выбираем `full`. Не принимаем решений на пустой статистике.

### Counterfactual через forced exploration

Если бандит выбрал `{a, b}`, мы *не знаем*, сказал бы `checker_c` REJECT. Forced exploration решает: ε=0.1 прогонов идёт с `full`, и эти прогоны дают ground truth (все три вердикта). На них beliefs обновляются полноценно для всех подмножеств. 90% прогонов экономят токены, 10% собирают правду.

### Связь с health check

Coach видит beliefs в отчёте и может «советовать» (но не принуждать):
- Если `checker_c` стабильно низкий reward для всех классов → coach пишет в `bandit_suggestions`: «drop checker_c for class X».
- Бандит это и так сделает через Thompson, но coach даёт человеку понимание *почему*.

Это связка уровней A и B: бандит делает, coach объясняет.

### Конфиг

```yaml
bandit:
  enabled: false              # opt-in, по умолчанию off
  algorithm: thompson
  epsilon: 0.1                # forced exploration rate
  min_checkers: 2             # coverage floor
  min_observations: 5         # холодный старт → full
  candidate_subsets: [full, ab, ac, bc]
```

## Golden eval (для правок уровня B)

Фундамент безопасности для правок промптов. **Без golden set автооптимизация — gambling.** Frozen реплей закрытых задач.

### Структура

```
config/golden/
├── tasks.yaml                 # список замороженных задач
└── expectations/
    ├── 251977.yaml            # одна задача — один файл ожиданий
    ├── 250586.yaml
    └── ...
```

`tasks.yaml`:
```yaml
# Замороженные задачи для golden eval.
# Выбраны из закрытых Redmine-тикетов с известным хорошим исходом.
# НЕ используются в боевом workflow — только для eval правок промптов.
golden_tasks:
  - id: "251977"
    task_ref: "#251977"
    title: "Применить стиль в Обозревателе"
    class: "frontend-vue"
    expectation_file: "expectations/251977.yaml"
    frozen_at: "2026-07-10"
```

`expectations/251977.yaml` — **semantic outcome, не точный diff**:
```yaml
task_id: "251977"
# Замороженный ожидаемый исход (semantic, не побайтовый).
outcome:
  must_touch_files:            # ДОЛЖНЫ быть затронуты (множество, не точный контент)
    - "src/components/StyleExplorer.vue"
  must_not_touch_files:        # грубая ошибка если задеты
    - "package.json"
    - "src/main.ts"
  tests_must_pass: true
  semantic_checks:             # оценивает LLM-judge по рубрике
    - "Изменение относится к применению стиля, а не к чему-то другому"
    - "Не ломает существующую логику StyleExplorer"
    - "Не вводит очевидных регрессий (hardcoded values, no-op changes)"
baseline:                      # текущий промпт на этой задаче
  recorded_at: "2026-07-10"
  outcome_match: true
  tokens_used: 18432
```

### Baseline

При первой сборке golden set — каждая задача прогоняется **текущими** промптами, записывается baseline. Задачи с `baseline.outcome_match: false` исключаются (текущий промпт их не решает — нельзя мерить регрессию). В golden попадают только задачи, которые система *уже* решает.

### Прогон eval для правки промпта

Когда coach предлагает `PromptDiff`:
```
golden_eval(proposed_diff):
  1. Применить дифф во временный sandbox (copy конфига)
  2. Для каждой задачи в golden set:
     a. Прогнать граф с sandbox-конфигом
        (maker → tests → judge; без полной публикации)
     b. Проверить must_touch_files / must_not_touch_files
     c. Проверить tests_must_pass
     d. LLM-judge прогоняет semantic_checks (pass/fail по рубрике)
  3. Агрегат:
     - pass_rate = (# задач с outcome_match) / (# golden tasks)
     - mean_tokens = среднее по прогонам
  4. Вердикт:
     - "passed" если pass_rate >= baseline_pass_rate - tolerance (0.1)
       И mean_tokens <= baseline_tokens * 1.2 (не сильно дороже)
     - "failed" иначе
  5. Откатить sandbox. Не трогать боевой конфиг.
```

**Правка промпта никогда не пишется в боевой `config/agents/*.md` автоматически.** Sandbox-прогон → вердикт → запись в отчёт → человек решает.

### LLM-judge для semantic_checks

Judge ≠ модель, которая работает как coach, и ≠ модель, которая работает как maker. Отдельная роль с рубрикой (`config/agents/judge.md`): строгий ревьюер, оценивает по рубрике, без снисходительности, если не уверен — fail.

Каждое `semantic_check` из expectations — критерий. Judge отвечает `{check, verdict: pass|fail, reason}`. Если *любой* check fail → `outcome_match: false` для этой задачи.

Защита от gaming: обязательный check в semantic_checks: `"Изменение не является no-op или hardcoded-pass ради зелёных тестов"`.

### Стоимость

Golden eval = N прогонов графа на каждую предложенную правку. При N=10 и ~15k токенов на прогон = ~150k токенов на одну правку. Это **дорого**, и это правильно:
- Coach предлагает максимум 1 правку за ретро → eval запускается нечасто.
- Прямой сдерживающий фактор против prompt drift: правка «дешёвая, давайте попробуем» перестаёт быть дешёвой.
- Можно кэшировать: если coach предложил правку, которая по diff-хэшу совпадает с ранее eval'енной — переиспользовать результат.

### Что golden eval НЕ делает в первой итерации

- **Не применяется к уровню C** (add/remove agent). `coach.allow_structural_changes: false`. Структурные изменения требуют отдельной процедуры (см. future work).
- **Не эволюционирует автоматически.** Добавление новых golden-задач — ручной процесс. Distribution shift — известный риск, mitigation в future work: ротация golden set.

### Конфиг

```yaml
golden:
  enabled: true
  tasks_file: "config/golden/tasks.yaml"
  tolerance_pass_rate: 0.1    # допускаем падение на 10% от baseline
  token_budget_multiplier: 1.2  # правка не должна быть дороже baseline × 1.2
  judge_agent: "judge"        # config/agents/judge.md
  cache_results: true         # кэшировать по хэшу диффа
```

## Что меняется в существующем коде

### Новые файлы

| Файл | Назначение |
|---|---|
| `src/devflow/coach/__init__.py` | package |
| `src/devflow/coach/store.py` | `MetricsStore`: SQLite CRUD |
| `src/devflow/coach/instrument.py` | `trace_node()`, `trace_llm_call()`, `_infer_transition()` |
| `src/devflow/coach/health.py` | детерминированный расчёт 8 осей |
| `src/devflow/coach/runner.py` | `run_coach(window)` — конвейер ретро |
| `src/devflow/coach/reopen.py` | `ReopenPoller` — cron опрос трекера |
| `src/devflow/coach/bandit.py` | `BanditRouter` — Thompson sampling |
| `src/devflow/coach/golden.py` | `GoldenEvaluator` — прогон правок по замороженным задачам |
| `src/devflow/coach/classify.py` | `classify_task()` → task_class (rule-based) |
| `src/devflow/coach/cli.py` | `devflow-super coach` команда |
| `config/agents/coach.md` | system prompt coach-агента |
| `config/agents/judge.md` | system prompt LLM-judge |
| `config/coach.yaml` | конфигурация всей coach-подсистемы |
| `config/golden/tasks.yaml` | каталог golden-задач |
| `config/golden/expectations/*.yaml` | ожидаемые исходы по задачам |

### Правки существующих файлов

| Файл | Изменение |
|---|---|
| `src/devflow/utils/structured_llm.py` | `call_structured` += опц. параметр `on_usage` (~5 строк) |
| `src/devflow/state.py` | `WorkflowState` += `__metrics_run_id: str \| None` (опц.) |
| `src/devflow/graph.py` | сборка графа оборачивает ноды через `trace_node()`, если `metrics_store` сконфигурирован; `checker_dispatcher` += опц. `bandit` параметр |
| `src/devflow/nodes/checker.py` | `checker_dispatcher` += опц. `bandit` параметр (fallback на `DEFAULT_CHECKERS`); в режиме бандита += запись `checker_outcomes` со `invoked=0` для дропнутых; `aggregate_checker_node` += запись `checker_outcomes` для вызванных (опц.) |
| `src/devflow/config.py` | `Config` += `coach: CoachConfig` (читает `config/coach.yaml`, опц.) |
| `src/devflow/schemas.py` | += `Hypothesis`, `PromptDiff`, `BanditSuggestion`, `CoachReport` |
| `src/devflow/cli.py` | += `coach` subcommand: `devflow-super coach --window 7d` |
| `src/devflow/daemon/scheduler.py` | += `coach_schedule` cron job; += `reopen_poller` cron job |
| `config/workflow.yaml` | без изменений (coach-конфиг отдельно в `config/coach.yaml`) |
| `.gitignore` | += `.devflow/metrics_store.db` |
| `pyproject.toml` | без новых зависимостей (sqlite3 — stdlib) |

### Принцип правок

Все правки существующих файлов — **опциональные**, через feature flag `coach.enabled`. Если `coach.enabled: false`:
- `graph.py` собирается как сейчас, без обёрток.
- `checker_dispatcher` использует `DEFAULT_CHECKERS`.
- `aggregate_checker` не пишет в metrics_store.
- Coach-подсистема полностью спит.

**Coach можно выключить одним флагом, система работает как раньше.** Нулевой регресс-риск внедрения.

## Фазирование реализации

Full scope описан выше. Реализация разбивается на фазы (каждая независимо деплойбельна и даёт ценность сама по себе):

| Фаза | Что | Риск | Ценность |
|---|---|---|---|
| **1. Foundation** | metrics_store + instrumentation (trace_node, trace_llm_call) + reopen_poller. Ноль LLM, ноль автоматизации. | ~0 (feature flag) | данные для всего остального |
| **2. Visibility** | health check (детерминированный) + Markdown-отчёт без LLM (dashboard). `devflow-super coach` показывает health check из метрик. | ~0 | немедленная visibility, «где болит» |
| **3. Coach LLM** | `config/agents/coach.md` + `run_coach()` pipeline. LLM генерирует гипотезы и diffs. Без golden eval — diffs помечаются «unevaluated». | низкий (только отчёт) | первая интеллектуальная ценность |
| **4. Golden eval** | `GoldenEvaluator` + `config/agents/judge.md` + каталог задач. Diff'ы прогоняются через eval перед попаданием в отчёт. | низкий (sandbox) | правки получают вердикт passed/failed |
| **5. Bandit** | `BanditRouter` + Thompson sampling. Уровень A автопилота. Opt-in. | средний (первая автоматика) | экономия токенов на чекерах |
| **6. Уровень C (future)** | add/remove agent через golden eval. | высокий | future work, не первая итерация |

Точное фазирование и порядок — определяются на этапе writing-plans.

## Future work (явно за рамками этого дизайна)

- **Continuous golden set эволюция** — автоматическое пополнение golden-задачами из новых закрытых тикетов. Снимает distribution shift, но требует критериев «что считать эталоном».
- **Уровень C — структурные изменения** — add/remove agent через golden eval. Самый рискованный, требует золотого набора N≥20 и multi-run оценки (3+ прогона на задачу).
- **A/B testing вариантов промптов** — параллельный прогон двух кандидатов на одних задачах (то, что невозможно в HR, но возможно у агентов). Дорожка из BOAD-стиля.
- **DSPy-style оптимизация промптов** — вместо coach-генерации диффа, MIPROv2-оптимизатор для автоматического поиска инструкций. Требует DSPy-зависимости и переформулировки агентов в DSPy-сигнатуры.
- **Auto-apply passed diffs** — если правка прошла golden eval с большим отрывом, автоматически создавать git-ветку с PR (уровень между B и C). Требует forge-интеграции из daemon spec.
- **Coach для coach'а** — мета-метрики: стало ли лучше *после* правок coach'а. ROI coach'а как такового. Самореферентная петля.
- **Multi-task-class LLM-классификатор** — заменить rule-based `classify_task()` на LLM.
- **Корреляция rework-типов** — семантическая кластеризация *причин* rework (по checker_reports), не только частоты. Позволяет coach'у предлагать точечные правки вместо общих.
- **Фронтенд-страница ретро** — страница в daemon dashboard (из spec автономного запуска) для просмотра ретро и разбора предложенных диффов.

## Открытые вопросы для ревью

1. **`coach.enabled` по умолчанию** — предлагаю `false` (opt-in). Система работает как сейчас, coach подключается когда метрики и golden set подготовлены. Подтвердить?

2. **Размер окна по умолчанию** — 7 дней для cron-ретро, 20 runs минимум (`min_window_size`). Для объёма Redmine-задач это реалистично? Или нужно меньше/больше?

3. **Бандит `enabled` по умолчанию** — `false`. Включается только после того, как собрано достаточно `checker_outcomes` (минимум ~50 прогонов с full тройкой для холодного старта). Подтвердить порог?

4. **Размер golden set** — 10–15 задач. Это ~150k токенов на одну правку промпта. Приемлемо, или начать с меньшего (5–7)?

5. **Coach-промпт — язык.** По аналогии с reporter: язык указывается явно в `coach.md`. Отчёты `docs/retro/*.md` — на каком языке по умолчанию? (Предлагаю русский, как у TODO.md.)

6. **Расположение отчётов** — `docs/retro/` рядом с `docs/superpowers/`. Подтвердить или предпочитаешь `docs/coach/retro/`?

7. **Депенденси на daemon spec** — coach переиспользует ntfy/email каналы уведомлений и APScheduler из spec автономного запуска. Эти компоненты должны быть реализованы до Фазы 3+ (coach LLM с push-уведомлениями). Фазы 1–2 (metrics + health check dashboard) не зависят от daemon и могут работать сразу. Подтвердить порядок?
