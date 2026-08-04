#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID


def main() -> int:
    parser = argparse.ArgumentParser(description="Экспорт сессии Codex в Markdown.")
    parser.add_argument("session_id", nargs="?")
    parser.add_argument("output", nargs="?")
    args = parser.parse_args()

    raw_session_id = args.session_id or input("Session ID: ").strip()
    try:
        session_id = str(UUID(raw_session_id))
    except ValueError:
        parser.error("session_id должен быть UUID")

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    matches = [
        path
        for root in (codex_home / "sessions", codex_home / "archived_sessions")
        if root.is_dir()
        for path in root.rglob(f"*-{session_id}.jsonl")
    ]
    if not matches:
        parser.error(f"сессия {session_id} не найдена в {codex_home}")
    source = max(matches, key=lambda path: path.stat().st_mtime)

    default_output = Path.home() / "Downloads" / f"codex-{session_id}.md"
    if args.output:
        output = Path(args.output).expanduser()
    else:
        raw_output = input(f"Файл [{default_output}]: ").strip()
        output = Path(raw_output).expanduser() if raw_output else default_output
    if output.exists():
        parser.error(f"файл уже существует: {output}")

    sections = [f"# Codex session {session_id}"]
    with source.open() as transcript:
        for line_number, line in enumerate(transcript, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"некорректный JSONL, строка {line_number}: {source}") from error
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

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(sections) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
