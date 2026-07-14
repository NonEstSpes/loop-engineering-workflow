# DevFlow Dashboard (frontend)

Vue 3 SPA dashboard for the devflow daemon.

## Prerequisites

- Node.js ≥ 18
- The devflow daemon running on `localhost:8787` (for API + dev proxy)

## Development

```bash
cd frontend
npm install
npm run dev      # starts Vite on http://localhost:5173 (proxies /api → :8787)
```

Open http://localhost:5173. The Vite dev server proxies `/api/*` to the
daemon on `:8787`.

## Production build

```bash
npm run build    # outputs dist/ (served by the daemon in production)
```

The daemon serves `dist/` at `/` when `daemon.serve_frontend=true` (default)
and the `frontend_dist` path exists (`frontend/dist` by default). No separate
web server needed — one process, one port (`:8787`).

## Regenerate API types from OpenAPI

With the daemon running:
```bash
npm run gen:types
```
This regenerates `src/api/schema.ts` from `/openapi.json`. The hand-written
`src/api/types.ts` is the current source of truth.

## Structure

- `src/api/` — typed fetch client + response types
- `src/stores/` — Pinia stores (daemon, tasks, approvals, eod)
- `src/composables/` — useSSE (live event stream), usePolling
- `src/views/` — page components (Dashboard, Approvals, EOD Review, Task Detail)
- `src/router/` — Vue Router config

## Notes

UI is functional skeletons — no component library or design system.
Visual polish is a separate task.
