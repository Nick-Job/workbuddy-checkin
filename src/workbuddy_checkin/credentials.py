from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


def default_home() -> Path:
    override = os.environ.get("WORKBUDDY_CHECKIN_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".workbuddy"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


@contextmanager
def exclusive_lock(path: Path, timeout: float = 30.0) -> Iterator[None]:
    ensure_private_dir(path.parent)
    handle = open(path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(" ")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for credential lock")
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def mask_token(token: Optional[str]) -> str:
    if not token:
        return ""
    token = str(token)
    if len(token) <= 12:
        return token[:2] + "***" + token[-2:]
    return token[:6] + "..." + token[-4:]


def looks_like_refresh_token(token: str) -> bool:
    token = (token or "").strip()
    if len(token) < 100 or token.count(".") != 2:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_=.")
    return all(char in allowed for char in token)


@dataclass
class Credential:
    refresh_token: str
    source: str = "manual"
    saved_at: int = field(default_factory=lambda: int(time.time()))
    domain: Optional[str] = None

    @property
    def masked(self) -> str:
        return mask_token(self.refresh_token)


class CredentialStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path).expanduser() if path else default_home() / "checkin-rt.json"
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def load(self) -> Optional[Credential]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        token = raw.get("rt") or raw.get("refreshToken") or raw.get("refresh_token")
        if not isinstance(token, str) or not token.strip():
            return None
        return Credential(
            refresh_token=token.strip(),
            source=str(raw.get("source") or "unknown"),
            saved_at=int(raw.get("saved_at") or 0),
            domain=raw.get("domain") if isinstance(raw.get("domain"), str) else None,
        )

    def save(self, credential: Credential) -> None:
        payload: Dict[str, Any] = {
            "v": 1,
            "rt": credential.refresh_token,
            "source": credential.source,
            "saved_at": int(credential.saved_at or time.time()),
            "version": "workbuddy-checkin/0.1.0",
        }
        if credential.domain:
            payload["domain"] = credential.domain
        with exclusive_lock(self.lock_path):
            atomic_write_json(self.path, payload)

    def clear(self) -> bool:
        with exclusive_lock(self.lock_path):
            if not self.path.exists():
                return False
            atomic_write_json(self.path, {})
        return True

    def info(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "configured": False,
            "path": str(self.path),
        }
        try:
            result["exists"] = self.path.exists()
            if self.path.exists():
                mode = stat.S_IMODE(self.path.stat().st_mode)
                result["mode"] = oct(mode)
                result["private"] = (mode & 0o077) == 0
        except OSError:
            result["exists"] = False
        credential = self.load()
        if credential:
            result.update(
                configured=True,
                source=credential.source,
                saved_at=credential.saved_at,
                token_masked=credential.masked,
            )
        return result
