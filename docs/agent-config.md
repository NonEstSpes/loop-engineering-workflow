# Agent configuration format

Agent configs live in `config/agents/*.md`. Each file is a Markdown document
with YAML frontmatter followed by the system prompt.

## Frontmatter schema

```yaml
---
name: planner
provider: openai
model: gpt-4o
temperature: 0.3
max_tokens: 4096
skills:
  - requirements-analysis
  - architecture-planning
tools:
  - file_tree
  - file_read
auto_approve: false
---
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Agent identifier. Defaults to the filename stem. |
| `provider` | string | Provider key defined in `providers.yaml`. |
| `model` | string | Model name passed to the provider. |
| `temperature` | float | Sampling temperature. |
| `max_tokens` | int | Optional maximum response tokens. |
| `skills` | list[str] | Informative tags describing agent capabilities. |
| `tools` | list[str] | Tools the agent may use. |
| `auto_approve` | bool | Skip human approval for the `plan_approval` agent. |

## System prompt

Everything after the closing `---` is treated as the system prompt and passed
to the LLM. Keep prompts focused on the agent's role and expected output
format. Structured agents should include a JSON schema example in their prompt.

## Example: planner

See `config/agents/planner.md` for a complete example.

## Research agent

The `research` agent config in `config/agents/research.md` provides the system prompt used when synthesizing findings from multiple research sources. It is invoked implicitly by the `research` node, not as a regular workflow node.
