# Commit Message Conventions

Follow the Conventional Commits specification:

```
<type>(<scope>): <subject>

<body>
```

## Types
- `feat`: a new feature
- `fix`: a bug fix
- `refactor`: code change that neither fixes a bug nor adds a feature
- `docs`: documentation only changes
- `test`: adding or correcting tests
- `chore`: build process, auxiliary tools, dependencies
- `perf`: code change that improves performance

## Rules
- Subject line: imperative mood ("add" not "added"), lowercase, no period, max 72 chars
- Body: explain *what* and *why* (not *how*), wrap at 72 chars
- Reference the task/issue ID when applicable

## Examples
```
feat(auth): add session timeout redirect

Redirect users to /login when the session expires instead of showing
a blank page. Refs #4321.
```
