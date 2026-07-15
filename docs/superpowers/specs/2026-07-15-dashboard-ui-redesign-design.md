# Dashboard P1 — UI/UX Redesign

**Date:** 2026-07-15
**Status:** Draft (pending approval)
**Subproject:** P1 из серии dashboard improvements
**Depends on:** P3 + Enhancements A/B + P2 (применяется поверх готового функционала)

## Context

Текущий `style.css` явно помечен «NOT a design system» — хардкод цветов, нет
CSS-переменных, нет адаптивности, нет dark mode, нет статус-индикаторов.
Компоненты используют inline-цвета (`#0366d6`, `#cb2431`) вместо семантических
токенов. Uptime показывается в секундах. Nav без active-state.

Этот подпроект (P1) вводит полноценную дизайн-систему на CSS-переменных,
dark mode, статус-индикаторы, человекочитаемое время, адаптивность и
полированные empty states.

## Goals

1. **Дизайн-токены** (CSS variables): цвета, типографика, отступы, радиусы, тени.
2. **Dark mode**: toggle в header, `localStorage` persistence, `[data-theme]`.
3. **Статус-индикаторы**: health badge (цветной круг), SSE connection dot,
   loading skeletons/spinners.
4. **Человекочитаемое время**: uptime «1h 23m», relative timestamps.
5. **Адаптивность**: mobile (<768px) / tablet (768-1024) / desktop (>1024).
6. **Nav active-state**: подсветка текущей страницы.
7. **Empty states**: иконка + текст + подсказка везде где есть пустые списки.

## Non-Goals

- Смена стек-фреймворка (остаётся vanilla CSS, без Tailwind/UnoCSS).
- Анимации/переходы (минимальные, только hover/focus).
- Новый функционал — чисто визуальная полировка существующего.
- Локализация (i18n) — остаётся смешанный RU/EN как сейчас.

## Section 1 — Дизайн-токены (CSS variables)

Полная замена `frontend/src/style.css`. Новая структура:

### Цветовая система

```css
:root {
  /* Surface */
  --color-bg: #f7f7f8;
  --color-surface: #ffffff;
  --color-surface-hover: #f0f0f1;
  --color-border: #e1e1e3;
  /* Text */
  --color-text: #1a1a1a;
  --color-text-muted: #666;
  --color-text-inverse: #ffffff;
  /* Semantic */
  --color-primary: #0366d6;
  --color-primary-hover: #0256b9;
  --color-success: #28a745;
  --color-warning: #d97706;
  --color-danger: #cb2431;
  /* Typography */
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  --font-size-base: 14px;
  --font-size-sm: 0.85em;
  --font-size-lg: 1.25rem;
  --font-size-xl: 1.5rem;
  /* Spacing scale */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 1rem;
  --space-4: 1.5rem;
  --space-5: 2rem;
  /* Radii */
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  /* Shadows */
  --shadow-card: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-card-hover: 0 2px 8px rgba(0,0,0,0.12);
  /* Transitions */
  --transition-fast: 150ms ease;
}
```

### Dark theme override

```css
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
```

Все компоненты используют только `var(--color-*)` — никаких хардкод-цветов.

## Section 2 — Dark mode toggle

**Composable `useTheme.ts`:**
```typescript
export function useTheme() {
  const theme = ref<'light' | 'dark'>(
    (localStorage.getItem('devflow-theme') as 'light' | 'dark') || 'light'
  )

  function toggle() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    localStorage.setItem('devflow-theme', theme.value)
    document.documentElement.setAttribute('data-theme', theme.value)
  }

  // Apply on mount.
  onMounted(() => {
    document.documentElement.setAttribute('data-theme', theme.value)
  })

  return { theme, toggle }
}
```

**Header toggle** в `App.vue`: кнопка ☀️/🌙 в правой части header.

## Section 3 — Статус-индикаторы

### Health badge

В `DashboardView.vue` заменяет текстовый «healthy»:
- 🟢 зелёный круг + «healthy»
- 🟡 жёлтый круг + «degraded» (task running)
- 🔴 красный круг + «error» (errors_last_24h > 0)

CSS: `.health-badge` с `::before` dot, цвет через `var(--color-success/warning/danger)`.

### SSE connection indicator

В header `App.vue` — маленькая точка слева от nav:
- 🟢 зелёная = connected (EventSource open)
- 🟠 оранжевая = reconnecting (5с backoff)
- 🔴 красная = error (max retries)

`useSSE.ts` расширяется: `connectionState: ref<'connected'|'reconnecting'|'error'>`.

### Loading states

Все stores уже имеют `loading: ref<boolean>`. UI:
- Spinner (CSS-only `<div class="spinner">`)
- Skeleton для таблиц (пульсирующий placeholder)

### Человекочитаемое время

Новый composable `useFormat.ts`:
- `formatUptime(seconds: number): string` → «1h 23m», «45m», «30s»
- `formatRelative(iso: string): string` → «2 мин назад», «только что», «5 ч назад»
- `formatTimestamp(iso: string): string` → «15.07 15:30» (локализованный)

Применяется в: DashboardView (uptime), ActivityView (timestamps), QueuePanel (updated_at).

## Section 4 — Адаптивность

### Breakpoints

```css
/* Mobile-first: default = mobile */
#devflow-app { padding: var(--space-2); }

/* Tablet */
@media (min-width: 768px) {
  #devflow-app { padding: var(--space-3) var(--space-4); }
}

/* Desktop */
@media (min-width: 1024px) {
  #devflow-app { max-width: 1200px; margin: 0 auto; padding: var(--space-3) var(--space-4) var(--space-5); }
}
```

### Responsive patterns

- **Nav**: desktop — горизонтальный в header; mobile (<768px) — hamburger menu.
- **Agents layout** (`grid-template-columns: 200px 1fr`): mobile → `1fr` (стопкой).
- **Tables** (TasksTab, approvals): mobile — карточки вместо таблицы (через CSS grid).
- **Config form**: mobile — single column; desktop — 2 columns где уместно.

## Section 5 — Nav active-state + Empty states

### Nav active-state

`App.vue` — `RouterLink` с `active-class="nav-active"`:
```css
.nav-active {
  color: var(--color-primary);
  font-weight: 600;
  border-bottom: 2px solid var(--color-primary);
}
```

### Empty states

Компонент `<EmptyState :icon="📦" title="Нет задач" hint="Они появятся после парсинга Redmine" />`.

Заменяет унылые «No completed tasks.» / «No pending approvals.» во всех views.

## File inventory

**Frontend — new:**
- `frontend/src/composables/useTheme.ts` — dark mode toggle + persistence
- `frontend/src/composables/useFormat.ts` — formatUptime, formatRelative, formatTimestamp

**Frontend — fully rewritten:**
- `frontend/src/style.css` — полная замена на дизайн-токены + dark theme + responsive

**Frontend — modified:**
- `frontend/src/App.vue` — header (sticky, dark toggle, SSE dot, hamburger mobile), toast-container, nav active-class
- `frontend/src/composables/useSSE.ts` — connectionState ref
- `frontend/src/views/DashboardView.vue` — health badge, formatUptime, empty states
- `frontend/src/views/ActivityView.vue` — formatRelative, empty state (если P2 уже сделан)
- `frontend/src/views/ApprovalsView.vue` — empty state, responsive
- `frontend/src/views/EodReviewView.vue` — empty state, responsive
- `frontend/src/components/controls/*.vue` — var(--color-*), empty states, responsive

## Testing

Frontend: `npm run typecheck` + `npm run build` (нет test runner в этом раунде).
Ручной smoke-чеклист:
1. Dark mode toggle работает, сохраняется после reload.
2. Health badge показывает правильный цвет.
3. SSE dot меняется при отключении daemon.
4. Uptime «1h 23m» вместо «5023s».
5. Nav active-state подсвечивает текущую страницу.
6. Mobile (<768px) — nav hamburger, single column.
7. Empty states показываются для пустых списков.

## Open questions

Нет — все решения согласованы.
