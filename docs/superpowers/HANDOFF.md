# Handoff: Autonomous Workflow Launch — Session Transfer

**Дата:** 2026-07-13
**Проект:** `loop-engineering-workflow` (devflow-super)
**Цель:** Перенос разработки в новую сессию из-за забитого контекста.

---

## TL;DR для новой сессии

Прочти этот файл целиком — он содержит всё состояние проекта. Затем:
1. Смержи PR Phase 4 (ветка `feature/phase4-eod-batch`), если ещё не смержён
2. Напиши план Phase 5 (Vue 3 SPA dashboard) по аналогии с предыдущими
3. Выполни через `superpowers:subagent-driven-development`

---

## Что это за проект

`devflow-super` — Python CLI на LangGraph для автоматизации разработки: fetch задачи → планирование → одобрение → реализация в worktree → checkers → reporter. **Цель надстройки:** запускать workflow автономно (Windows Service + cron), с тремя уровнями вовлечённости человека (HITL стратегии), push/MR в GitHub/GitLab, batch-режим «итог дня», и Vue 3 дашборд.

## Архитектурные документы

| Документ | Путь | Назначение |
|---|---|---|
| **Design spec** | `docs/superpowers/specs/2026-07-12-autonomous-workflow-launch-design.md` | Full scope дизайн (5 фаз) |
| **Phase 1 plan** | `docs/superpowers/plans/2026-07-12-phase1-daemon-scheduler-api.md` | Daemon + scheduler + API skeleton |
| **Phase 2 plan** | `docs/superpowers/plans/2026-07-12-phase2-hitl-strategies-approvals.md` | HITL стратегии + approval bridge |
| **Phase 3 plan** | `docs/superpowers/plans/2026-07-13-phase3-forge-reporter-refactor.md` | ForgeBackend + reporter refactor |
| **Phase 4 plan** | `docs/superpowers/plans/2026-07-13-phase4-eod-batch.md` | EOD batch-flow + batch-store |
| **SDD progress ledgers** | `.superpowers/sdd/progress*.md` | История выполненных задач |

## Git состояние

- **PRs:** #3 (Phase 1, merged), #4 (Phase 2, merged), #5 (Phase 3, merged), Phase 4 **pending merge** (ветка `feature/phase4-eod-batch`), Phase 5 **pending merge** (ветка `feature/phase5-vue-dashboard`)
- **Текущая ветка:** `feature/phase5-vue-dashboard` (Phase 5, ждёт merge)
- **Main:** содержит Phases 1-3 (Phases 4-5 ещё не смержены)

## Что реализовано (Phases 1-5)

### Phase 1: Daemon skeleton (PR #3, merged)
- `DaemonConfig` + `HitlStrategy` (config.py)
- `EventBus` — in-process pub/sub
- `DaemonLocks` — asyncio locks
- `cleanup_orphan_worktrees` — startup sweep
- `WorkflowRunner` — adapter вокруг `run_workflow`
- FastAPI `/api/health` + `/api/state` на localhost:8787
- `DaemonScheduler` — APScheduler cron jobs
- `python -m devflow.daemon` entry point
- nssm service install scripts

### Phase 2: HITL стратегии (PR #4, merged)
- `publish_approval` node — второй gate (interrupt в full_detail)
- `plan_approval` strategy — auto-approve в full_detail/end_of_day
- `ApprovalStore` — thread-safe registry (register/wait/resolve)
- `ApprovalBridge` — interrupt → store → push (ntfy/email) → wait(timeout)
- ntfy + email `NotificationChannel` implementations
- `WorkflowRunner` interactive mode (`run_workflow_interactive` с bridge)
- FastAPI `/api/approvals` GET + POST

**3 стратегии:**
| Режим | plan gate | publish gate | Interrupts |
|---|---|---|---|
| `per_plan` | human | auto-approve | 1 per task |
| `full_detail` | auto-approve | human (diff+checkers) | 1 per task |
| `end_of_day` | auto-approve | auto-approve | 0 (batch в Phase 4) |

### Phase 3: ForgeBackend + reporter refactor (PR #5, merged)
- `ForgeBackend` ABC — `push(branch)`, `create_mr(branch, target, title, desc) -> MRInfo`
- `GitHubBackend` — push via GitPython, PR via GitHub REST API (idempotent)
- `GitLabBackend` — push via GitPython, MR via GitLab REST API v4 (idempotent)
- `build_forge_backend` factory — registry + auto-detect + register hook
- Reporter refactor: `prepare_report` (LLM) + `execute_actions` (config-driven)
- `ForgeConfig` — provider, target_branch, actions list
- Conventions-skill files (mr.md, commit.md)
- Push error flags check (PushInfo.ERROR)
- 274/274 тестов, ruff + mypy clean

### Phase 4: EOD batch-flow + batch-store (this branch, feature/phase4-eod-batch)
- `BatchEntry` Pydantic model + `BatchStatus` lifecycle (pending_review/approved/rejected/published)
- `BatchStore` SQLite CRUD — add, get_pending, get_by_task, list_all, update_status, count_pending (JSON-in-column design)
- `BatchPublisher` — sequential idempotent publish (forge.push + create_mr + _publish_to_channels + update_tracker)
- `EodHandler` — list_pending, publish_selected(task_ids), finalize (emits eod.ready event)
- Reporter `prepare_only` mode — generates artifacts + record_todo, defers publish/push/MR
- `WorkflowRunner` stores BatchEntry after each end_of_day per-task run
- `DaemonScheduler._run_eod_wrapper` wired to EodHandler (finalize + publish_all)
- `/api/eod`, `/api/eod/finalize`, `/api/eod/publish`, `/api/eod/entries/{id}` routes
- `HealthResponse.batch_store_pending` populated
- Soft lock coordination (cross-loop limitation documented)

#### Known limitations (Phase 4)

- **`prepare_only` not wired through the graph.** The reporter's `prepare_only`
  param (Task 3) exists and is fully implemented — when True, only `record_todo`
  runs locally and publish/push/MR are deferred. However, `graph.py` builds
  `partial(reporter_node, app_cfg=app_cfg)` WITHOUT setting `prepare_only`, so
  in `end_of_day` mode the per-task EOD runs currently execute the DEFAULT
  `forge.actions` (`publish_report`, `update_tracker`, `record_todo`) during the
  day. This means `publish_report` and `update_tracker` run BOTH per-task during
  the day AND again at the EOD batch-publish stage (double behavior) — an
  accepted trade-off, since both are idempotent best-effort operations. Wiring
  `prepare_only` through the graph (so per-task EOD runs are truly local-only)
  was explicitly deferred per Task 6's decision — it is a larger change and is
  tracked as future work.
- **Cross-loop hard mutual exclusion (eod_review ↔ task_run).** No asyncio
  `Lock` is shared across the APScheduler thread and uvicorn's thread pool
  (cannot be done safely). `max_instances=1` per APScheduler job id is the only
  hard guard; if task and EOD schedules are set close together or a task run
  overruns past `eod_schedule`, overlap is possible.

### Phase 5: Vue 3 SPA Dashboard (this branch, feature/phase5-vue-dashboard)
- Backend: `/api/events` SSE endpoint (sse-starlette, global '*' topic)
- Backend: `/api/tasks/*` routes (current/queue/done/{id})
- Backend: CORS middleware (dev server origins), StaticFiles SPA serving + fallback
- Backend: `on_task_change` callback wires runner → app.state.set_current_task
- Frontend: Vue 3 + Vite + TypeScript (strict) + Pinia + Vue Router scaffold
- Frontend: typed API client (hand-written types matching FastAPI models)
- Frontend: useSSE composable (live event stream → store refresh) + usePolling
- Frontend: 4 views — Dashboard, Approvals, EOD Review, Task Detail (+ 404)
- `DaemonConfig` += serve_frontend, frontend_dist, cors_origins
- Minimal functional CSS (no design system — UI polish is future work)

## Что осталось

Ничего — все 5 фаз реализованы. Дальнейшая работа — UI-дизайн/полировка
дашборда (сейчас функциональные каркасы без дизайн-системы), GC для
`ApprovalStore`, retention policy для `published` BatchEntry, и другие
технические долги из списка ниже.

## Workflow разработки (как работали)

1. **Brainstorming** (`superpowers:brainstorming`) → design spec
2. **Writing-plans** (`superpowers:writing-plans`) → TDD план на каждую фазу
3. **Subagent-driven-development** (`superpowers:subagent-driven-development`):
   - На каждую задачу: generate brief → dispatch implementer (haiku/sonnet по сложности) → generate review-package → dispatch reviewer → fix если нужно
   - Model selection: haiku для механических задач, sonnet для интеграционных, opus для финального whole-branch review
   - Progress ledger в `.superpowers/sdd/progress.md`
4. **Final whole-branch review** (opus/sonnet) → fix subagent со всеми findings
5. **PR** через GitHub API (gh CLI недоступен, используем `git credential fill` + `urllib`)

## Known issues / Tech debt (для учёта в будущих фазах)

- **ApprovalStore resolved entries accumulate** — нет GC sweep (filtered from get_pending, но растёт в памяти). Future work.
- **push() в Phase 3 теперь проверяет PushInfo.ERROR** — исправлено в fix commit.
- **Store keyed by task_id, not graph thread_id** — низкий риск из-за `max_instances=1`.
- **Forge built unconditionally** в reporter — создаётся даже когда push/create_mr не в actions (cosmetic).
- **provider="auto" path** не unit-тестирован (зависит от git remote).
- **Registry mutation leaks across tests** — `register_*` хуки мутируют module-level dicts без teardown.
- **Cross-loop hard mutual exclusion (eod_review ↔ task_run)** — Phase 4 обеспечивает soft coordination; APScheduler `max_instances=1` даёт hard guard в пределах scheduler, но full cross-loop exclusion documented as known limitation.
- **Retention policy for old `published` BatchEntry** — spec line 249, "Future work".
- **Sweep EOD-awareness** (не удалять ветки, на которые ссылаются pending entries) — spec line 365; tech debt.
- **Forge audit JSONL log** (`logs/forge_audit.jsonl`, spec line 400) — future work.
- **`/api/eod` routes don't acquire `eod_review` lock** — plan-level, deferred to final review.

## Команды для новой сессии

```bash
# Синхронизировать main после merge PR Phase 4
git checkout main && git pull origin main

# Создать ветку Phase 5
git checkout -b feature/phase5-vue-dashboard

# Прочитать спеку для Phase 5 секций
# docs/superpowers/specs/2026-07-12-autonomous-workflow-launch-design.md
# (секция "Vue 3 SPA dashboard")

# Начать writing-plans skill
```

## Технологии

- **Backend:** Python ≥3.11, LangGraph, FastAPI, APScheduler, GitPython, httpx, Pydantic
- **Forge:** GitHub REST API, GitLab REST API v4
- **Notifications:** console, ntfy.sh, SMTP email (Telegram недоступен в РФ)
- **Frontend (Phase 5):** Vue 3, Vite, TypeScript, Pinia, Vue Router
- **Testing:** pytest, pytest-asyncio, ruff, mypy
- **Service:** nssm (Windows Service)

## Контакт с пользователем

Пользователь (Egor Bobilev, `NonEstSpes`) работает с проектом автономно, часто уходит спать / заниматься другими делами. Ожидает:
- Автономное выполнение без постоянных вопросов
- Чёткие PR-описания на русском/английском
- Honest reporting (если тесты падают — говорить прямо)
- Phase 5 (Vue) — это отдельная задача про UI дизайн, сейчас не приоритет
