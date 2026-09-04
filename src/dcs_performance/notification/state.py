"""Small atomic state store used to make notification sends idempotent."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


STATE_VERSION = 1


class NotificationStateError(RuntimeError):
    """Raised when the send-state file cannot be safely read or written."""


class NotificationState:
    """JSON-backed state keyed by Result Package ``run_id``."""

    def __init__(self, path: Path, document: Mapping[str, Any] | None = None) -> None:
        self.path = path
        self._document: dict[str, Any] = dict(document or {"version": STATE_VERSION, "runs": {}})
        self._document.setdefault("version", STATE_VERSION)
        self._document.setdefault("runs", {})

    @classmethod
    def load(cls, path: Path) -> "NotificationState":
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NotificationStateError(f"无法读取邮件发送状态: {path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise NotificationStateError(f"邮件发送状态必须是 JSON 对象: {path}")
        if raw.get("version", STATE_VERSION) != STATE_VERSION:
            raise NotificationStateError(f"不支持的邮件发送状态版本: {raw.get('version')!r}")
        runs = raw.get("runs", {})
        if not isinstance(runs, Mapping):
            raise NotificationStateError(f"邮件发送状态 runs 必须是对象: {path}")
        return cls(path, {"version": STATE_VERSION, "runs": dict(runs)})

    def is_sent(self, run_id: str) -> bool:
        record = self.records.get(run_id)
        return isinstance(record, Mapping) and record.get("status") == "sent"

    @property
    def records(self) -> dict[str, Any]:
        runs = self._document.setdefault("runs", {})
        if not isinstance(runs, dict):
            raise NotificationStateError("邮件发送状态 runs 不是对象")
        return runs

    def record(
        self,
        run_id: str,
        *,
        status: str,
        package_path: Path,
        recipients: tuple[str, ...],
        subject: str,
        error: str | None = None,
    ) -> None:
        previous = self.records.get(run_id)
        attempts = 0
        if isinstance(previous, Mapping):
            try:
                attempts = int(previous.get("attempts", 0))
            except (TypeError, ValueError):
                attempts = 0
        record: dict[str, Any] = {
            "status": status,
            "attempts": max(attempts, 0) + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "package_path": str(package_path),
            "recipients": list(recipients),
            "subject": subject,
        }
        if status == "sent":
            record["sent_at"] = record["updated_at"]
        if error:
            record["error"] = error
        self.records[run_id] = record
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            fd, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                text=True,
            )
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self._document, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise NotificationStateError(f"无法保存邮件发送状态: {self.path}: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


__all__ = ["NotificationState", "NotificationStateError", "STATE_VERSION"]
