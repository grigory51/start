#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.commands.codex_sessions import export_session, load_sessions


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

    session = next((item for item in load_sessions() if item.id == session_id), None)
    if session is None:
        parser.error(f"сессия {session_id} не найдена")

    default_output = Path.home() / "Downloads" / f"codex-{session_id}.md"
    if args.output:
        output = Path(args.output).expanduser().absolute()
    else:
        raw_output = input(f"Файл [{default_output}]: ").strip()
        output = Path(raw_output).expanduser().absolute() if raw_output else default_output
    try:
        print(export_session(session, str(output)))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
