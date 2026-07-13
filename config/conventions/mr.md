# Merge Request Conventions

## Title format
Use the pattern: `<type>(<scope>): <subject>`

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`
Scope: the module or component affected (optional)
Subject: imperative mood, lowercase, no period

Examples:
- `feat(auth): add session timeout`
- `fix(api): handle null response in user endpoint`
- `refactor(db): extract connection pool`

## Description format
```
## What
<one-sentence summary of the change>

## Why
<the problem or motivation>

## How
<bullet points of the key implementation decisions>

## Testing
<how the change was tested>
```

## Checklist
- [ ] Title follows the format above
- [ ] Description has What/Why/How/Testing sections
- [ ] No sensitive data (tokens, keys) in the description
