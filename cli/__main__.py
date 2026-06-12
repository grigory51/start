"""Точка входа CLI. Подкоманды: up, ui.

Запуск: `uv run claude-agents <команда>` (или `python -m cli`).
"""

from __future__ import annotations

import argparse
import sys

from .up import run_up


def _cmd_up(args: argparse.Namespace) -> int:
    errors = run_up(
        dry_run=args.dry_run,
        force=args.force,
        skip_submodules=args.no_submodules,
    )
    return 1 if errors else 0


def _cmd_manage(args: argparse.Namespace) -> int:
    from .manage import run_manage  # ленивый импорт: textual тянем только для manage
    return run_manage()


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="claude-agents",
        description="Линковка персональных агентов/навыков/hooks в ~/.claude и TUI.",
    )
    sub = ap.add_subparsers(dest="cmd")

    up = sub.add_parser("up", help="синхронизация: сабмодули + symlink'и")
    up.add_argument("--dry-run", action="store_true", help="показать план без изменений")
    up.add_argument("--force", action="store_true",
                    help="перезаписать чужие файлы/симлинки (с бэкапом .bak)")
    up.add_argument("--no-submodules", action="store_true",
                    help="пропустить git submodule update")
    up.set_defaults(func=_cmd_up)

    manage = sub.add_parser("manage", help="TUI: агенты и скилы")
    manage.set_defaults(func=_cmd_manage)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
