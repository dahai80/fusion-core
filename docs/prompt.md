# prompt

Prompt-template management. Engine only — no domain content. mtime-gated in-memory cache, `{{var}}` rendering.

## Symbols

- [`PromptManager`](#promptmanager)

## PromptManager

```python
class PromptManager:
    def __init__(self, prompts_dir: str | Path)
    def get(self, name: str) -> str
    def render(self, template_name: str, **variables: Any) -> str
    def list_names(self) -> list[str]
    def clear_cache(self) -> None
```

### __init__

`prompts_dir` must exist as a directory — else `FileNotFoundError` (fail visibly, no silent empty manager).

### get

```python
def get(self, name: str) -> str
```

Reads template `name` (resolves `name`, then `name.txt`, then `name.md`). **mtime-gated cache** (E3): each entry stores `(text, mtime_ns)`. On `get`, if the cached mtime still matches the file's current `st_mtime_ns`, the cached text is returned (no IO); if the file changed on disk, it is re-read and the cache updated. This fixes the old permanent-cache defect where on-disk edits after first read were silently invisible.

### render

```python
def render(self, template_name: str, **variables: Any) -> str
```

Loads `template_name` (via `get`, so mtime-gated), substitutes `{var}` placeholders with `variables`. Escaping: `{{` → `{`, `}}` → `}`. Missing variable → `KeyError` (fail visibly, no silent empty substitution).

### clear_cache

```python
def clear_cache(self) -> None
```

Drops all cached entries (E3). Next `get`/`render` re-reads from disk. Use when an external process rewrites templates in place without bumping mtime, or to force a refresh in tests.

```python
mgr = PromptManager("prompts/")
text = mgr.render("grade", subject="math", score=9)
# prompts/grade.txt: "Subject: {subject}, Score: {score}"
# → "Subject: math, Score: 9"
```

### list_names

```python
def list_names(self) -> list[str]
```

Sorted list of template stems (files with `.txt`/`.md` suffix in `prompts_dir`).

## Example

```
prompts/
  summarize.txt    # "Summarize the following in {words} words:\n\n{text}"
  grade.md         # "Grade this answer for {subject}. Answer: {answer}"
```

```python
from fusion_core import PromptManager

mgr = PromptManager("prompts/")
prompt = mgr.render("summarize", words=50, text="...")
print(mgr.list_names())  # ["grade", "summarize"]
```

## Design notes

- Engine only, no domain content: K12/finance/health prompts live in their own projects; core just loads + renders.
- mtime-gated cache (E3): on-disk template edits are picked up automatically (mtime change invalidates the entry), replacing the old permanent-cache behavior that silently ignored edits. `clear_cache()` forces a full refresh.
- Missing var raises `KeyError` (no silent `{var}` left in output).
- Missing dir/file raises `FileNotFoundError` (no silent empty).
