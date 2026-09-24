"""Хранение, экспорт и возобновление локальных сессий Codex."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from ..command_sdk import TaskContext


_ROLLOUT_ID = re.compile(
    r"^rollout.*?(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
_metadata_cache: dict[Path, tuple[int, int, str | None, str, bool]] = {}


@dataclass(frozen=True)
class Session:
    id: str
    cwd: str
    path: Path
    updated: float
    is_subagent: bool = False


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _session_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _metadata(path: Path) -> tuple[str | None, str, bool]:
    """Прочитать только первую запись JSONL: transcript может быть очень большим."""
    stat = path.stat()
    cached = _metadata_cache.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2], cached[3], cached[4]
    session_id: str | None = None
    cwd = ""
    is_subagent = False
    with path.open(encoding="utf-8") as source:
        first = source.readline()
    try:
        event = json.loads(first) if first else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"некорректный JSONL metadata: {path}") from error
    if isinstance(event, dict):
        payload = event.get("payload")
        data = payload if isinstance(payload, dict) else event
        for candidate in (data, event):
            source = candidate.get("source")
            thread_source = candidate.get("thread_source")
            is_subagent = is_subagent or (isinstance(source, dict) and "subagent" in source)
            is_subagent = is_subagent or thread_source in ("subagent", "guardian_review")
            session_id = session_id or _session_id(candidate.get("id")) or _session_id(candidate.get("session_id"))
            raw_cwd = candidate.get("cwd")
            if not cwd and isinstance(raw_cwd, str):
                cwd = raw_cwd
    _metadata_cache[path] = (stat.st_mtime_ns, stat.st_size, session_id, cwd, is_subagent)
    return session_id, cwd, is_subagent


def load_sessions() -> list[Session]:
    """Вернуть по одной самой свежей записи на UUID из активного и архива."""
    latest: dict[str, Session] = {}
    for root in (codex_home() / "sessions", codex_home() / "archived_sessions"):
        if not root.is_dir():
            continue
        for path in root.rglob("rollout*.jsonl"):
            try:
                session_id, cwd, is_subagent = _metadata(path)
                stat = path.stat()
            except FileNotFoundError:
                continue
            if session_id is None:
                match = _ROLLOUT_ID.match(path.name)
                session_id = _session_id(match.group("id")) if match else None
            if session_id is None:
                continue
            session = Session(session_id, cwd, path, stat.st_mtime, is_subagent=is_subagent)
            if session_id not in latest or latest[session_id].updated < session.updated:
                latest[session_id] = session
    return sorted(latest.values(), key=lambda session: (-session.updated, session.id))


def session_transcript(session: Session) -> str:
    sections = [f"# Codex session {session.id}"]
    try:
        source = session.path.open(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"не удалось открыть сессию {session.path}: {error}") from error
    with source:
        for line_number, line in enumerate(source, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"некорректный JSONL, строка {line_number}: {session.path}"
                ) from error
            payload = event.get("payload", {})
            if event.get("type") != "response_item" or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = "\n".join(
                str(item["text"])
                for item in payload.get("content", [])
                if isinstance(item, dict) and "text" in item
            ).strip()
            if not text or text.startswith("<skill>"):
                continue
            title = "Пользователь" if role == "user" else "Codex"
            sections.append(f"## {title}\n\n{text}")
    return "\n\n".join(sections) + "\n"


def export_session(session: Session, output: str) -> Path:
    """Экспортировать transcript без перезаписи уже существующего файла."""
    try:
        session_id = str(UUID(session.id))
    except ValueError as error:
        raise ValueError("session.id должен быть UUID") from error
    destination = Path(output).expanduser()
    if not destination.is_absolute():
        raise ValueError("путь для экспорта должен быть абсолютным")
    destination = destination.resolve(strict=False)
    if destination.exists():
        raise ValueError(f"файл уже существует: {destination}")
    text = session_transcript(Session(session_id, session.cwd, session.path, session.updated))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as target:
            target.write(text)
    except FileExistsError as error:
        raise ValueError(f"файл уже существует: {destination}") from error
    except OSError as error:
        raise ValueError(f"не удалось записать экспорт {destination}: {error}") from error
    return destination


async def resume_session(context: TaskContext, session: Session) -> None:
    """Открыть возобновление завершённой сессии в отдельном окне терминала."""
    try:
        session_id = str(UUID(session.id))
    except ValueError as error:
        raise ValueError("session.id должен быть UUID") from error
    if not session.cwd:
        raise ValueError("CWD сессии не указан")
    cwd = Path(session.cwd).expanduser()
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError(f"CWD сессии недоступен: {cwd}")
    custom_home = os.environ.get("CODEX_HOME")
    if sys.platform == "darwin":
        resume = f"codex resume {shlex.quote(session_id)}"
        if custom_home:
            resume = f"CODEX_HOME={shlex.quote(custom_home)} {resume}"
        shell_command = f"cd {shlex.quote(str(cwd))} && {resume}"
        script = "on run argv\n"
        script += "tell application \"Terminal\"\nactivate\ndo script (item 1 of argv)\nend tell\nend run"
        await context.run("osascript", "-e", script, shell_command)
        return
    if sys.platform == "linux":
        arguments = ["gnome-terminal", "--window", "--working-directory", str(cwd), "--"]
        arguments.extend(("codex", "resume", session_id))
        if custom_home:
            arguments[0:0] = ["env", f"CODEX_HOME={custom_home}"]
        await context.run(*arguments)
        return
    raise ValueError(f"resume Codex не поддерживается на {sys.platform}")


def _rollout_id(path: str) -> str | None:
    match = _ROLLOUT_ID.match(Path(path).name)
    return _session_id(match.group("id")) if match else None


def _linux_session_processes(pids: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for pid in pids:
        try:
            descriptors = list((Path("/proc") / pid / "fd").iterdir())
        except FileNotFoundError:
            continue
        for descriptor in descriptors:
            try:
                session_id = _rollout_id(str(descriptor.readlink()))
            except FileNotFoundError:
                continue
            if session_id:
                found.setdefault(session_id, []).append(pid)
    return found


async def session_processes(context: TaskContext, pids: list[str]) -> dict[str, list[str]]:
    """Сопоставить PID сессиям только по открытым rollout transcript-файлам."""
    valid = [pid for pid in pids if pid.isdecimal() and int(pid) > 0]
    result: dict[str, list[str]] = {}
    if not valid:
        return result
    if sys.platform == "linux":
        return await asyncio.to_thread(_linux_session_processes, valid)
    output = await context.run("lsof", "-a", "-p", ",".join(valid), "-Fpn", allowed_codes=(0, 1))
    pid = ""
    for line in output.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and pid in valid:
            session_id = _rollout_id(line[1:])
            if session_id:
                processes = result.setdefault(session_id, [])
                if pid not in processes:
                    processes.append(pid)
    return result
