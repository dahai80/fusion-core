# prompt

Prompt-template management. Engine only — no domain content. Permanent in-memory cache, `{{var}}` rendering.

## Symbols

- [`PromptManager`](#promptmanager)

## PromptManager

```python
class PromptManager:
    def __init__(self, prompts_dir: str | Path)
    def get(self, name: str) -> str
    def render(self, template_name: str, **variables: Any) -> str
    def list_names(self) -> list[str]
```

### __init__

`prompts_dir` must exist as a directory — else `FileNotFoundError` (fail visibly, no silent empty manager).

### get

```python
def get(self, name: str) -> str
```

Reads template `name` (resolves `name`, then `name.txt`, then `name.md`). **Permanent cache** (I8): first read wins, cached for the instance lifetime. On-disk edits after first read are **not** picked up. Templates are treated as immutable runtime assets. If hot-reload is ever needed, gate the cache on file mtime like `config.load_settings` (R1).

### render

```python
def render(self, template_name: str, **variables: Any) -> str
```

Loads `template_name`, substitutes `{var}` placeholders with `variables`. Escaping: `{{` → `{`, `}}` → `}`. Missing variable → `KeyError` (fail visibly, no silent empty substitution).

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
- Permanent cache (I8): deliberate — templates are immutable runtime assets, hot-reload would need an mtime gate (parallel to `config` R1). Documented in source comment + README.
- Missing var raises `KeyError` (no silent `{var}` left in output).
- Missing dir/file raises `FileNotFoundError` (no silent empty).
