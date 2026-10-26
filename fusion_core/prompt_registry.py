from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fusion_core.prompt import _render_template

if TYPE_CHECKING:
    from fusion_core.mlx_client import FusionMLXClient

logger = logging.getLogger(__name__)

_VERSION_ID_LEN = 16
_INDEX_NAME = "index.json"
_SNAPSHOT_DIR = "versions"


class PromptVersionError(Exception):
    pass


class UnknownPromptVersionError(PromptVersionError, KeyError):
    pass


class SnapshotIntegrityError(PromptVersionError):
    pass


@dataclass(frozen=True, slots=True)
class PromptVersionRecord:
    name: str
    version_id: str
    sha256: str
    template_text: str
    created_at: str
    source: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version_id": self.version_id,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "source": self.source,
        }


def compute_version_id(name: str, template_text: str) -> str:
    digest = hashlib.sha256(f"{name}\x00{template_text}".encode()).hexdigest()
    return digest[:_VERSION_ID_LEN]


class PromptVersionRegistry:
    def __init__(self, registry_dir: str | Path):
        self.registry_dir = Path(registry_dir)
        self._versions_dir = self.registry_dir / _SNAPSHOT_DIR
        self._index_path = self.registry_dir / _INDEX_NAME
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._versions_dir.mkdir(parents=True, exist_ok=True)
        self._index: list[dict[str, str]] = self._load_index()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _load_index(self) -> list[dict[str, str]]:
        if not self._index_path.is_file():
            return []
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise PromptVersionError(f"cannot read prompt version index {self._index_path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
            raise PromptVersionError(f"malformed prompt version index: {self._index_path}")
        return data["versions"]

    def _save_index(self) -> None:
        tmp = self._index_path.with_suffix(".json.tmp")
        payload = json.dumps({"versions": self._index}, indent=2, ensure_ascii=False)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._index_path)

    def _snapshot_path(self, version_id: str) -> Path:
        return self._versions_dir / f"{version_id}.txt"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def register(self, name: str, template_text: str, *, source: str = "manual") -> PromptVersionRecord:
        if not name or not isinstance(name, str):
            raise ValueError("prompt name must be a non-empty string")
        if not isinstance(template_text, str):
            raise ValueError("template_text must be a string")
        version_id = compute_version_id(name, template_text)
        for entry in self._index:
            if entry.get("version_id") == version_id:
                logger.debug("prompt version already registered: %s@%s", name, version_id)
                return self._record_from_entry(entry)
        sha256 = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
        created_at = self._now()
        snapshot = self._snapshot_path(version_id)
        if snapshot.is_file():
            existing = snapshot.read_text(encoding="utf-8")
            if existing != template_text:
                raise SnapshotIntegrityError(
                    f"snapshot collision for version {version_id}: stored content differs from provided template"
                )
        else:
            self._atomic_write(snapshot, template_text)
        entry = {
            "name": name,
            "version_id": version_id,
            "sha256": sha256,
            "created_at": created_at,
            "source": source,
        }
        self._index.append(entry)
        self._save_index()
        logger.info("registered prompt version %s@%s (source=%s)", name, version_id, source)
        return PromptVersionRecord(template_text=template_text, **entry)

    def register_from_manager(self, manager: Any, name: str) -> PromptVersionRecord:
        get = getattr(manager, "get", None)
        if get is None:
            raise TypeError("manager must expose get(name) -> template text (e.g. PromptManager)")
        text = get(name)
        return self.register(name, text, source=f"prompt_manager:{getattr(manager, 'prompts_dir', '?')}")

    def _record_from_entry(self, entry: dict[str, str]) -> PromptVersionRecord:
        snapshot = self._snapshot_path(entry["version_id"])
        if not snapshot.is_file():
            raise SnapshotIntegrityError(f"snapshot file missing for version {entry['version_id']} ({entry['name']})")
        text = snapshot.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != entry.get("sha256"):
            raise SnapshotIntegrityError(
                f"snapshot integrity check failed for version {entry['version_id']} "
                f"({entry['name']}): expected sha256 {entry.get('sha256')}, got {digest}"
            )
        return PromptVersionRecord(template_text=text, **entry)

    def _find_entry(self, prompt_version: str) -> dict[str, str]:
        for entry in self._index:
            if entry.get("version_id") == prompt_version:
                return entry
        raise UnknownPromptVersionError(f"unknown prompt_version: {prompt_version!r}")

    def get_record(self, prompt_version: str) -> PromptVersionRecord:
        return self._record_from_entry(self._find_entry(prompt_version))

    def render(self, prompt_version: str, **variables: Any) -> str:
        record = self.get_record(prompt_version)
        return _render_template(record.template_text, variables, template_name=record.name)

    def replay(
        self,
        prompt_version: str,
        client: FusionMLXClient,
        *,
        model: str | None = None,
        **variables: Any,
    ) -> Any:
        prompt = self.render(prompt_version, **variables)
        logger.info(
            "replaying prompt_version=%s model=%s prompt_len=%d",
            prompt_version,
            model or "client-default",
            len(prompt),
        )
        return client.chat(prompt, model=model)

    def list_versions(self, name: str | None = None) -> list[PromptVersionRecord]:
        records = []
        for entry in self._index:
            if name is not None and entry.get("name") != name:
                continue
            records.append(self._record_from_entry(entry))
        return records

    def latest(self, name: str) -> PromptVersionRecord:
        entries = [entry for entry in self._index if entry.get("name") == name]
        if not entries:
            raise UnknownPromptVersionError(f"no registered versions for prompt {name!r}")
        return self._record_from_entry(entries[-1])

    def reload(self) -> None:
        self._index = self._load_index()
        logger.debug("prompt version index reloaded (%d entries)", len(self._index))


_VERSION_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def is_valid_version_id(prompt_version: str) -> bool:
    return isinstance(prompt_version, str) and bool(_VERSION_ID_RE.match(prompt_version))
