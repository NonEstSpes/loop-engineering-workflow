# Development

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Lint

```bash
ruff check src tests
```

## Format

```bash
ruff format src tests
```

## Type check

```bash
mypy src
```

## Tests

Run all tests:

```bash
pytest -q
```

Run only unit tests:

```bash
pytest tests/unit -q
```

Run only integration tests:

```bash
pytest tests/integration -q
```

## Project layout

```
use-superpowers/
├── config/             # Agent, provider, workflow, and research source configuration
├── docs/               # Documentation
├── src/devflow/        # Source code
│   ├── cli.py
│   ├── config.py
│   ├── graph.py
│   ├── llm_factory.py
│   ├── mcp/            # Task source adapters
│   ├── nodes/          # LangGraph node implementations
│   ├── research/       # On-demand research sources and MCP client
│   ├── schemas.py
│   ├── state.py
│   ├── tools/          # Code and git helpers
│   └── utils/          # Structured LLM callers and tracing
└── tests/              # Unit and integration tests
```
