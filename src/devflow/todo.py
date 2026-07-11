"""TODO.md front-end for the orchestrator.

``TODO.md`` is the entry queue of actionable tasks: the orchestrator reads it to
pick the next task (by priority), the reporter writes a short inline result back
into the same line when the task finishes. Tasks may be references to an
external tracker (``#r2 [#251977](url) — Title``) or free-form human entries
(``#r1 — Fix the thing``).

The module is intentionally free of any LangGraph / LLM / MCP dependencies: it
operates purely on :class:`pathlib.Path` and :class:`devflow.state.Task` so it
can be unit-tested in isolation and reused by both the CLI and the orchestrator
node.

Priority tags use ``#r0`` (highest) through ``#r5`` (lowest). Tasks without a
priority tag are preserved on disk but ignored by :func:`select_next_todo`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from devflow.state import (
    CHECKBOX_DONE,
    CHECKBOX_IN_PROGRESS,
    CHECKBOX_OPEN,
    Task,
    TodoItem,
)

# ``#r0`` .. ``#r5`` priority tag. Word boundary after the digit so that
# ``#r50`` is not matched as ``#r5``.
PRIORITY_RE = re.compile(r"#r([0-5])\b")

_CHECKBOXES = (CHECKBOX_OPEN, CHECKBOX_IN_PROGRESS, CHECKBOX_DONE)

# Markdown link forms a tracker-backed task may carry, with optional URL:
#   [#251977](https://tracker.example/issues/251977)  -> link, hydrated via API
#   [#MOCK-1]                                          -> ref only, still a tracker id
_LINK_RE = re.compile(r"\[#([0-9A-Za-z\-_]+)\](?:\((https?://\S+?)\))?")
# Bare numeric reference when no bracket form is used: ``#251977``.
_BARE_REF_RE = re.compile(r"(?<!\[)#([0-9]{2,})\b")

# Redmine priority names (Russian + English canonical) mapped to r-levels.
# Anything unknown/missing collapses to r5 (lowest actionable).
_PRIORITY_MAP: dict[str, int] = {
    "немедленный": 0,
    "immediate": 0,
    "срочный": 1,
    "urgent": 1,
    "высокий": 2,
    "high": 2,
    "нормальный": 3,
    "normal": 3,
    "низкий": 4,
    "low": 4,
}

# Sentinel id prefix for human-written entries that have no tracker reference
# is re-exported from :mod:`devflow.state` (see LOCAL_ID_PREFIX).

# Inline result suffixes appended by the reporter.
_RESULT_SUFFIX = {
    "done": "✅ done: {text}",
    "problem": "⚠️ problem: {text}",
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_todo(path: Path) -> list[TodoItem]:
    """Parse a ``TODO.md`` file into :class:`TodoItem` records.

    Non-task lines (blank lines, headings, prose without a checkbox) are kept
    as items with ``checkbox=None`` so that callers can reconstruct the file
    verbatim. Missing file returns an empty list.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    items: list[TodoItem] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        items.append(_parse_line(raw, line_no))
    return items


def _parse_line(raw: str, line_no: int) -> TodoItem:
    """Parse a single raw line into a :class:`TodoItem`."""
    checkbox = _match_checkbox(raw)

    # Non-task lines: keep the raw text for round-trip, nothing else to do.
    if checkbox is None:
        return TodoItem(
            raw_line=raw,
            line_no=line_no,
            checkbox=None,
            priority=None,
            task_ref=None,
            url=None,
            title=raw.strip(),
            result=None,
        )

    priority = _match_priority(raw)
    task_ref, url = _match_ref(raw)

    # Extract the inline result (if any) from the raw line BEFORE stripping,
    # because _extract_title removes the result suffix.
    result = _extract_result(raw)
    title = _extract_title(raw, checkbox)

    return TodoItem(
        raw_line=raw,
        line_no=line_no,
        checkbox=checkbox,
        priority=priority,
        task_ref=task_ref,
        url=url,
        title=title,
        result=result,
    )


def _match_checkbox(raw: str) -> str | None:
    """Return the checkbox marker if ``raw`` is a markdown task list item.

    Requires a leading ``-``/``*`` bullet so that prose mentioning ``[ ]`` in
    passing (e.g. ``"Note: use the [ ] symbol"``) is not misread as a task.
    """
    match = re.match(r"\s*[-*]\s+\[( |~|x)\]", raw)
    if match is None:
        return None
    return f"[{match.group(1)}]"


def _match_priority(raw: str) -> int | None:
    match = PRIORITY_RE.search(raw)
    return int(match.group(1)) if match else None


def _match_ref(raw: str) -> tuple[str | None, str | None]:
    link = _LINK_RE.search(raw)
    if link:
        return link.group(1), link.group(2)
    bare = _BARE_REF_RE.search(raw)
    if bare:
        return bare.group(1), None
    return None, None


def _extract_title(raw: str, checkbox: str) -> str:
    """Strip checkbox, priority tag, link/ref and result suffix from a line."""
    text = raw
    text = text.replace(checkbox, "", 1)
    # Remove the markdown link form once, if present.
    text = _LINK_RE.sub("", text)
    # Remove the bare reference once, if present.
    text = _BARE_REF_RE.sub("", text, count=1)
    text = PRIORITY_RE.sub("", text, count=1)
    return _strip_result(text).strip(" \t-—–")


def _strip_result(title: str) -> str:
    """Remove a trailing inline result suffix (added by the reporter)."""
    pattern = re.compile(
        r"\s*[—–-]\s*(?:✅\s*done|⚠️\s*problem)\s*:\s*.+$",
        flags=re.IGNORECASE,
    )
    return pattern.sub("", title)


def _extract_result(title: str) -> str | None:
    """Return the inline result text if the title already carries one."""
    pattern = re.compile(
        r"\s*[—–-]\s*(?:✅\s*done|⚠️\s*problem)\s*:\s*(.+)$",
        flags=re.IGNORECASE,
    )
    match = pattern.search(title)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_next_todo(items: Iterable[TodoItem]) -> TodoItem | None:
    """Pick the next task to work on.

    Rules (per the agreed spec):
      * only items with a checkbox of ``[ ]`` and a ``#rX`` tag are candidates;
      * the smallest ``r`` wins (``r0`` is highest priority);
      * ties broken by the topmost line (smallest ``line_no``).
    """
    candidates = [
        item
        for item in items
        if item.checkbox == CHECKBOX_OPEN and item.priority is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda it: (it.priority, it.line_no))


# ---------------------------------------------------------------------------
# Writing back
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _rewrite_line(path: Path, line_no: int, new_line: str) -> bool:
    """Replace a single 1-based line in ``path``.

    Returns ``True`` if the line was found and replaced, ``False`` otherwise
    (so callers can fall back, e.g. append to the end).
    """
    lines = _read_lines(path)
    idx = line_no - 1
    if 0 <= idx < len(lines):
        lines[idx] = new_line
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    return False


def _locate_line(path: Path, item: TodoItem) -> int | None:
    """Find the current 1-based line number for ``item``.

    The stored ``line_no`` may be stale if the user reordered the file between
    the orchestrator and reporter runs. We re-find by raw line first, then by
    task ref / title, so we update the right line.
    """
    lines = _read_lines(path)
    # 1. exact raw match
    for i, line in enumerate(lines, start=1):
        if line == item.raw_line:
            return i
    # 2. match by task ref
    if item.task_ref is not None:
        for i, line in enumerate(lines, start=1):
            ref, _ = _match_ref(line)
            if ref == item.task_ref:
                return i
    # 3. match by checkbox + title
    for i, line in enumerate(lines, start=1):
        if _match_checkbox(line) and _extract_title(line, item.checkbox or "") == item.title:
            return i
    return None


def _replace_checkbox(raw: str, new_checkbox: str) -> str:
    """Swap the checkbox marker in a raw line, preserving everything else."""
    for marker in _CHECKBOXES:
        if marker in raw:
            return raw.replace(marker, new_checkbox, 1)
    return raw


def mark_in_progress(path: Path, item: TodoItem) -> None:
    """Mark ``item`` as in-progress (``[~]``) in the TODO file.

    If the originating line can no longer be located (user deleted it), this
    is a no-op: there is nothing to flip.
    """
    line_no = _locate_line(path, item)
    if line_no is None:
        return
    new_line = _replace_checkbox(item.raw_line, CHECKBOX_IN_PROGRESS)
    _rewrite_line(path, line_no, new_line)


def mark_done(
    path: Path,
    item: TodoItem,
    result: str,
    *,
    kind: str = "done",
) -> None:
    """Mark ``item`` as done (``[x]``) and append a short inline result.

    ``kind`` is ``"done"`` for an approved verdict, ``"problem"`` otherwise.
    A previous result suffix on the line is replaced rather than duplicated.

    If the originating line was deleted between the orchestrator and reporter
    runs, the result line is appended to the end of the file so the outcome is
    not silently lost.
    """
    suffix_template = _RESULT_SUFFIX.get(kind, _RESULT_SUFFIX["done"])
    suffix = suffix_template.format(text=result.replace("\n", " ").strip())

    # Rebuild the line from its raw form, stripping any prior result so we do
    # not accumulate multiple " — ✅ done: …" tails across re-runs.
    base = _strip_result(item.raw_line)
    base = _replace_checkbox(base, CHECKBOX_DONE)
    new_line = f"{base} — {suffix}"

    line_no = _locate_line(path, item)
    if line_no is not None and _rewrite_line(path, line_no, new_line):
        return
    # Line vanished entirely: append so the result is not lost.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(new_line + "\n")


# ---------------------------------------------------------------------------
# Generation from a tracker
# ---------------------------------------------------------------------------


def priority_from_task(task: Task) -> int:
    """Map a task's Redmine priority (in ``metadata``) to an r-level (0..5)."""
    raw = str(task.metadata.get("priority") or "").strip().lower()
    return _PRIORITY_MAP.get(raw, 5)


def generate_todo_from_source(tasks: Iterable[Task]) -> list[TodoItem]:
    """Build a fresh list of :class:`TodoItem` from tracker tasks.

    Tasks are sorted by priority (lowest r first) then by id so the most
    urgent item ends up at the top of the file. Each item's ``url`` /
    ``task_ref`` come from the task metadata when available.
    """
    sortable = sorted(
        tasks,
        key=lambda t: (priority_from_task(t), str(t.id)),
    )
    items: list[TodoItem] = []
    for task in sortable:
        r = priority_from_task(task)
        url = task.metadata.get("redmine_url")
        task_ref = str(task.id)
        if url:
            body = f"- {CHECKBOX_OPEN} #r{r} [#{task_ref}]({url}) — {task.title}"
        else:
            body = f"- {CHECKBOX_OPEN} #r{r} #{task_ref} — {task.title}"
        items.append(
            TodoItem(
                raw_line=body,
                line_no=0,  # assigned during render
                checkbox=CHECKBOX_OPEN,
                priority=r,
                task_ref=task_ref,
                url=url,
                title=task.title,
                result=None,
            )
        )
    return items


def render_todo(items: Iterable[TodoItem], *, header: str | None = None) -> str:
    """Serialize :class:`TodoItem` records into ``TODO.md`` text.

    ``header`` may carry an optional markdown preamble (e.g. a generation
    timestamp); ``None`` omits it.
    """
    lines: list[str] = []
    if header:
        lines.append(header.rstrip("\n"))
        lines.append("")
    for item in items:
        lines.append(item.raw_line)
    return "\n".join(lines) + ("\n" if lines else "")


def write_todo(
    path: Path,
    items: Iterable[TodoItem],
    *,
    header: str | None = None,
) -> None:
    """Write a brand-new ``TODO.md`` from ``items`` (overwrites)."""
    path.write_text(render_todo(items, header=header), encoding="utf-8")


def ensure_todo(
    path: Path,
    items_factory: Callable[[], list[TodoItem]],
    *,
    header: str | None = None,
) -> list[TodoItem]:
    """Return the parsed TODO entries, generating the file if it is missing.

    ``items_factory`` is called only when ``path`` does not exist; its result is
    written to disk and re-parsed so callers always get back real
    :class:`TodoItem` objects with correct ``line_no`` values.
    """
    if path.exists():
        return parse_todo(path)
    items = items_factory()
    write_todo(path, items, header=header)
    return parse_todo(path)
