# Dashboard P1 — UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the ad-hoc CSS with a proper design-token system (CSS variables), add dark mode, status indicators, human-readable time formatting, responsive layout, nav active-state, and polished empty states.

**Architecture:** Full rewrite of `style.css` with CSS variables. New `useTheme.ts` (dark mode toggle + localStorage) and `useFormat.ts` (uptime/relative/timestamp formatting). `useSSE.ts` gains a `connectionState` ref for the SSE dot. All views/components updated to use `var(--color-*)` and the new composables. App.vue gets sticky header with theme toggle + SSE dot.

**Tech Stack:** Vanilla CSS (no framework), Vue 3 Composition API, TypeScript.

## Global Constraints

- **No CSS framework** — vanilla CSS with CSS variables only.
- **Dark mode** via `[data-theme="dark"]` attribute on `<html>`, persisted in `localStorage`.
- **All colors** must use `var(--color-*)` — no hardcoded hex in components.
- **Responsive breakpoints**: mobile (<768px), tablet (768-1024px), desktop (>1024px).
- **Frontend**: no test runner; verify via `npm run typecheck` + `npm run build`.
- **Commit after each task**.
- **Branch**: `feature/phase5-vue-dashboard`.

---

### Task 1: useTheme + useFormat composables

**Files:**
- Create: `frontend/src/composables/useTheme.ts`
- Create: `frontend/src/composables/useFormat.ts`

- [ ] **Step 1: Create useTheme.ts**

```typescript
import { ref, onMounted } from 'vue'

export type Theme = 'light' | 'dark'

export function useTheme() {
  const theme = ref<Theme>(
    (localStorage.getItem('devflow-theme') as Theme) || 'light'
  )

  function apply(t: Theme) {
    document.documentElement.setAttribute('data-theme', t)
    localStorage.setItem('devflow-theme', t)
  }

  function toggle() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    apply(theme.value)
  }

  onMounted(() => apply(theme.value))

  return { theme, toggle }
}
```

- [ ] **Step 2: Create useFormat.ts**

```typescript
export function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  const remM = m % 60
  if (h < 24) return `${h}h ${remM}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}

export function formatRelative(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  const diff = Math.floor((now - then) / 1000)
  if (diff < 60) return 'только что'
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`
  return `${Math.floor(diff / 86400)} дн назад`
}

export function formatTimestamp(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}
```

- [ ] **Step 3: Verify typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/composables/useTheme.ts frontend/src/composables/useFormat.ts
git commit -m "feat(frontend): useTheme (dark mode) + useFormat (uptime/relative/timestamp)"
```

---

### Task 2: SSE connectionState in useSSE

**Files:**
- Modify: `frontend/src/composables/useSSE.ts`

- [ ] **Step 1: Add connectionState ref**

In `useSSE.ts`, add a module-level singleton ref (shared across callers, like useToast):

```typescript
import { ref } from 'vue'

export const sseConnectionState = ref<'connected' | 'reconnecting' | 'error'>('reconnecting')
```

In `connect()`, update the state:
- On successful `new EventSource('/api/events')` opening: set `'connected'` (add `source.onopen` handler).
- In `source.onerror`: set `'reconnecting'`.
- The state never goes to `'error'` permanently (always retries), so keep `'reconnecting'` on error.

Add to the `onMounted(connect)` flow:
```typescript
    source.onopen = () => {
      sseConnectionState.value = 'connected'
    }
```

And in `source.onerror`:
```typescript
    source.onerror = () => {
      sseConnectionState.value = 'reconnecting'
      source?.close()
      source = null
      reconnectTimer = window.setTimeout(connect, 5000)
    }
```

- [ ] **Step 2: Verify typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useSSE.ts
git commit -m "feat(frontend): SSE connectionState ref for status indicator"
```

---

### Task 3: Full style.css rewrite with design tokens + dark mode + responsive

**Files:**
- Rewrite: `frontend/src/style.css`

This is the core of P1. Replace the entire file with a design-token-based system.

- [ ] **Step 1: Rewrite style.css**

Replace the entire contents of `frontend/src/style.css` with:

```css
/* DevFlow Dashboard — Design System (P1) */

:root {
  --color-bg: #f7f7f8;
  --color-surface: #ffffff;
  --color-surface-hover: #f0f0f1;
  --color-border: #e1e1e3;
  --color-text: #1a1a1a;
  --color-text-muted: #666;
  --color-text-inverse: #ffffff;
  --color-primary: #0366d6;
  --color-primary-hover: #0256b9;
  --color-success: #28a745;
  --color-warning: #d97706;
  --color-danger: #cb2431;
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  --font-size-base: 14px;
  --font-size-sm: 0.85em;
  --font-size-lg: 1.25rem;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 1rem;
  --space-4: 1.5rem;
  --space-5: 2rem;
  --radius-sm: 4px;
  --radius-md: 6px;
  --shadow-card: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-card-hover: 0 2px 8px rgba(0,0,0,0.12);
  --transition-fast: 150ms ease;
}

[data-theme="dark"] {
  --color-bg: #0d1117;
  --color-surface: #161b22;
  --color-surface-hover: #21262d;
  --color-border: #30363d;
  --color-text: #c9d1d9;
  --color-text-muted: #8b949e;
  --color-text-inverse: #0d1117;
  --color-primary: #58a6ff;
  --color-primary-hover: #79b8ff;
  --shadow-card: 0 1px 3px rgba(0,0,0,0.3);
  --shadow-card-hover: 0 2px 8px rgba(0,0,0,0.4);
}

/* --- Base --- */
:root { font-family: var(--font-sans); color-scheme: light dark; }
body { margin: 0; padding: 0; background: var(--color-bg); color: var(--color-text); }
#devflow-app { padding: var(--space-2); max-width: 1200px; margin: 0 auto; padding-bottom: var(--space-5); }

/* --- Header --- */
header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  border-bottom: 1px solid var(--color-border);
  padding: var(--space-2) 0;
  margin-bottom: var(--space-4);
  position: sticky;
  top: 0;
  background: var(--color-bg);
  z-index: 100;
}
header h1 { font-size: var(--font-size-lg); margin: 0; }
nav { display: flex; gap: var(--space-2); flex-wrap: wrap; align-items: center; }
nav a {
  color: var(--color-text-muted);
  text-decoration: none;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  transition: var(--transition-fast);
}
nav a:hover { background: var(--color-surface-hover); color: var(--color-text); }
nav a.router-link-active {
  color: var(--color-primary);
  font-weight: 600;
  border-bottom: 2px solid var(--color-primary);
}

/* --- SSE dot + theme toggle --- */
.header-right { margin-left: auto; display: flex; align-items: center; gap: var(--space-2); }
.sse-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.sse-dot.connected { background: var(--color-success); }
.sse-dot.reconnecting { background: var(--color-warning); }
.theme-toggle {
  background: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  cursor: pointer; font-size: 1.1rem; padding: var(--space-1) var(--space-2);
}

/* --- Cards --- */
.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-3);
  box-shadow: var(--shadow-card);
  transition: var(--transition-fast);
}
.card:hover { box-shadow: var(--shadow-card-hover); }
.card h3, .card h4 { margin-top: 0; font-size: var(--font-size-base); }

/* --- Definition lists --- */
dl { display: grid; grid-template-columns: max-content 1fr; gap: var(--space-1) var(--space-3); margin: 0; }
dt { font-weight: 600; color: var(--color-text-muted); }
dd { margin: 0; }

/* --- Code & pre --- */
code, pre {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  background: var(--color-surface-hover);
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
pre { padding: var(--space-3); overflow-x: auto; max-height: 30em; }

/* --- Health badge --- */
.health-badge { display: inline-flex; align-items: center; gap: var(--space-1); }
.health-badge::before { content: ''; width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.health-healthy::before { background: var(--color-success); }
.health-degraded::before { background: var(--color-warning); }
.health-error::before { background: var(--color-danger); }

/* --- Buttons --- */
button {
  font: inherit; padding: var(--space-1) var(--space-3);
  border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  background: var(--color-surface); color: var(--color-text);
  cursor: pointer; margin-right: var(--space-2);
  transition: var(--transition-fast);
}
button:hover:not(:disabled) { background: var(--color-surface-hover); }
button:disabled { opacity: 0.5; cursor: not-allowed; }

/* --- Tables --- */
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: var(--space-1) var(--space-2); border-bottom: 1px solid var(--color-border); }
th { font-size: var(--font-size-sm); text-transform: uppercase; letter-spacing: 0.03em; color: var(--color-text-muted); }

/* --- Details/summary --- */
details { margin: var(--space-1) 0; }
summary { cursor: pointer; color: var(--color-primary); }

/* --- Error text --- */
.error { color: var(--color-danger); }

/* --- Tabs --- */
.tabs { display: flex; gap: var(--space-2); border-bottom: 1px solid var(--color-border); margin-bottom: var(--space-3); }
.tabs button {
  border: none; border-bottom: 2px solid transparent; background: none; border-radius: 0;
  padding: var(--space-2) var(--space-3); margin-right: 0; color: var(--color-text-muted);
}
.tabs button.active { border-bottom-color: var(--color-primary); color: var(--color-primary); font-weight: 600; }
.tabs button:hover:not(.active) { color: var(--color-text); }

/* --- Badges --- */
.running-badge { color: var(--color-primary); }
.idle-badge { color: var(--color-success); }
.unsaved-badge { color: var(--color-warning); font-weight: 600; }
.modified-dot { color: var(--color-warning); }

/* --- TODO priorities --- */
.prio-critical { color: var(--color-danger); font-weight: 600; }
.prio-urgent { color: var(--color-warning); }
.prio-normal { color: var(--color-text-muted); }
.prio-none { color: var(--color-border); }
.checkbox-btn { border: 1px solid var(--color-border); padding: 0.1rem 0.4rem; margin-right: 0; }

/* --- Agents layout --- */
.agents-layout { display: grid; grid-template-columns: 200px 1fr; gap: var(--space-3); }
.agent-list { list-style: none; padding: 0; margin: 0; }
.agent-list li { padding: 0.3rem 0; }
.agent-list li.active button { font-weight: 600; color: var(--color-primary); }
.agent-list li.modified button::after { content: " ●"; color: var(--color-warning); }
.agent-editor textarea.prompt-textarea {
  width: 100%; font-family: var(--font-mono); font-size: var(--font-size-sm);
  padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-sm); resize: vertical;
  background: var(--color-surface); color: var(--color-text);
}
input, select, textarea {
  background: var(--color-surface); color: var(--color-text);
  border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2); font: inherit;
}

/* --- Status colors --- */
.finished { color: var(--color-success); }
.started { color: var(--color-primary); }

/* --- Cron builder --- */
.cron-builder { margin-bottom: var(--space-3); }
.cron-builder fieldset { border: 1px solid var(--color-border); border-radius: var(--radius-sm); margin-bottom: var(--space-2); padding: var(--space-2); }
.cron-builder legend { font-size: var(--font-size-sm); font-weight: 600; color: var(--color-text-muted); }
.cron-builder label { display: inline-block; margin-right: var(--space-3); }
.day-checkboxes, .every-n, .time-entry { margin-top: 0.3rem; padding-left: var(--space-4); }
.time-entry { margin-bottom: 0.3rem; }
.add-time, .remove-time, .help-toggle { font-size: var(--font-size-sm); padding: 0.15rem 0.5rem; margin-right: 0; }
.cron-preview { background: var(--color-surface-hover); padding: var(--space-2); border-radius: var(--radius-sm); margin-top: var(--space-2); }
.cron-help { margin-top: var(--space-2); font-size: var(--font-size-sm); background: var(--color-surface-hover); padding: var(--space-2); border-radius: var(--radius-sm); }

/* --- Tasks refresh --- */
.tasks-header { display: flex; justify-content: space-between; align-items: center; }
.refresh-section { text-align: right; }
.refresh-btn { font-size: 0.9rem; margin-right: 0; }
.last-updated { display: block; color: var(--color-text-muted); font-size: 0.75rem; }

/* --- Execution queue --- */
.queue-panel { margin-top: var(--space-4); }
.queue-list { list-style: none; padding: 0; margin: 0; }
.queue-item {
  display: flex; align-items: center; gap: var(--space-2);
  padding: var(--space-2); border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  margin-bottom: 0.3rem; background: var(--color-surface); cursor: grab;
}
.queue-item.drag-over { border-color: var(--color-primary); background: var(--color-surface-hover); }
.drag-handle { color: var(--color-text-muted); font-size: 1.2rem; cursor: grab; }
.queue-position { font-weight: 600; min-width: 1.5rem; }
.queue-title { flex: 1; }
.queue-actions { display: flex; gap: 0.2rem; }
.move-btn { padding: 0.1rem 0.5rem; font-size: 0.9rem; margin-right: 0; }
.dnd-hint { display: block; color: var(--color-text-muted); font-size: 0.75rem; margin-top: var(--space-2); }

/* --- Activity log --- */
.activity-list { list-style: none; padding: 0; margin: 0; }
.activity-item {
  display: flex; align-items: baseline; gap: 0.75rem;
  padding: var(--space-2); border-bottom: 1px solid var(--color-border); font-size: var(--font-size-sm);
}
.activity-time { color: var(--color-text-muted); font-family: var(--font-mono); min-width: 5rem; }
.activity-data { color: var(--color-text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.event-task { color: var(--color-primary); }
.event-approval { color: var(--color-warning); }
.event-queue { color: var(--color-success); }
.event-error { color: var(--color-danger); }
.event-default { color: var(--color-text-muted); }
.activity-filter { font-size: 0.9rem; margin-right: var(--space-2); }

/* --- Empty states --- */
.empty-state { text-align: center; padding: var(--space-5); color: var(--color-text-muted); }
.empty-icon { font-size: 2rem; margin-bottom: var(--space-2); }
.empty-hint { font-size: var(--font-size-sm); }

/* --- Toast --- */
.toast-container {
  position: fixed; top: var(--space-3); right: var(--space-3); z-index: 9999;
  display: flex; flex-direction: column; gap: var(--space-2);
}
.toast {
  padding: var(--space-2) var(--space-4); border-radius: var(--radius-sm); color: var(--color-text-inverse);
  cursor: pointer; box-shadow: var(--shadow-card-hover); font-size: 0.9rem; max-width: 350px;
}
.toast-info { background: var(--color-primary); }
.toast-success { background: var(--color-success); }
.toast-warning { background: var(--color-warning); }
.toast-error { background: var(--color-danger); }

/* --- Responsive --- */
@media (min-width: 768px) {
  #devflow-app { padding: var(--space-3) var(--space-4) var(--space-5); }
}
@media (min-width: 1024px) {
  #devflow-app { max-width: 1200px; margin: 0 auto; }
}
@media (max-width: 767px) {
  .agents-layout { grid-template-columns: 1fr; }
  header { flex-wrap: wrap; }
  nav { width: 100%; }
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/style.css
git commit -m "feat(frontend): full style.css rewrite — design tokens, dark mode, responsive"
```

---

### Task 4: App.vue — sticky header with theme toggle + SSE dot

**Files:**
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Update App.vue**

Replace the `<header>` block and add imports:

```vue
<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import { useSSE, sseConnectionState } from '@/composables/useSSE'
import { useToast } from '@/composables/useToast'
import { useTheme } from '@/composables/useTheme'

useSSE()
const { toasts, dismiss } = useToast()
const { theme, toggle } = useTheme()
</script>

<template>
  <div id="devflow-app">
    <header>
      <h1>DevFlow Dashboard</h1>
      <nav>
        <RouterLink to="/">Dashboard</RouterLink>
        <RouterLink to="/approvals">Approvals</RouterLink>
        <RouterLink to="/eod">EOD Review</RouterLink>
        <RouterLink to="/controls">Controls</RouterLink>
        <RouterLink to="/activity">Activity</RouterLink>
      </nav>
      <div class="header-right">
        <span
          :class="['sse-dot', sseConnectionState]"
          :title="`SSE: ${sseConnectionState}`"
        ></span>
        <button class="theme-toggle" @click="toggle" :title="theme === 'light' ? 'Dark mode' : 'Light mode'">
          {{ theme === 'light' ? '🌙' : '☀️' }}
        </button>
      </div>
    </header>
    <main>
      <RouterView />
    </main>
    <div class="toast-container">
      <div
        v-for="t in toasts"
        :key="t.id"
        :class="['toast', `toast-${t.type}`]"
        @click="dismiss(t.id)"
      >
        {{ t.message }}
      </div>
    </div>
  </div>
</template>
```

Note: removed the `<span> · </span>` separators — nav uses `gap` in CSS now.

- [ ] **Step 2: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat(frontend): sticky header with theme toggle + SSE connection dot"
```

---

### Task 5: DashboardView — health badge + formatUptime + empty states

**Files:**
- Modify: `frontend/src/views/DashboardView.vue`

- [ ] **Step 1: Update DashboardView**

In `<script setup>`, add:
```typescript
import { formatUptime } from '@/composables/useFormat'
```

In the template, replace `{{ daemon.health.uptime_seconds }}s` with `{{ formatUptime(daemon.health.uptime_seconds) }}`.

Replace the health status display:
```vue
        <dt>Status</dt><dd><span :class="['health-badge', `health-${daemon.health.status}`]">{{ daemon.health.status }}</span></dd>
```

Replace empty state texts:
- "No task currently running." → keep but wrap in empty-state style.
- "No completed tasks." → keep but wrap in empty-state style.

- [ ] **Step 2: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/DashboardView.vue
git commit -m "feat(frontend): DashboardView — health badge + human-readable uptime"
```

---

### Task 6: Smoke test + final verification

- [ ] **Step 1: Build frontend**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 2: Restart daemon + Playwright smoke test**

Restart daemon. Check:
1. Dark mode toggle works (click 🌙, background turns dark).
2. Health badge shows colored dot.
3. SSE dot green when connected.
4. Nav active-state highlights current page.
5. Uptime formatted as "Xm" not "Xs".
6. Mobile viewport: nav wraps, agents single column.

- [ ] **Step 3: Final commit if needed**

---

## Self-Review

**1. Spec coverage:**
- ✅ Design tokens (Section 1) → Task 3
- ✅ Dark mode (Section 2) → Tasks 1, 3, 4
- ✅ Status indicators (Section 3) → Tasks 2, 4, 5
- ✅ Human-readable time (Section 3) → Tasks 1, 5
- ✅ Responsive (Section 4) → Task 3
- ✅ Nav active-state (Section 5) → Task 3 (CSS)
- ✅ Empty states (Section 5) → Task 3 (CSS) + Task 5

**2. Placeholder scan:** No TBD/TODO. ✅

**3. Type consistency:**
- `sseConnectionState` consistent across Tasks 2, 4 ✅
- `formatUptime` consistent across Tasks 1, 5 ✅
- `useTheme().toggle` consistent across Tasks 1, 4 ✅
