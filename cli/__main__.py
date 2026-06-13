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


def _cmd_add_submodule(args: argparse.Namespace) -> int:
    from .submodule import add_submodule
    res = add_submodule(
        args.url,
        name=args.name,
        skills_subdir=args.skills_subdir,
        do_install=not args.no_install,
    )
    print(("✓ " if res.ok else "✗ ") + res.message)
    if res.ok and res.install_errors:
        print(f"  install с предупреждениями: {res.install_errors}")
    return 0 if res.ok else 1


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

    addsub = sub.add_parser(
        "add-submodule",
        help="добавить git-сабмодуль в contrib/ и зарегистрировать source в config.toml")
    addsub.add_argument("url", help="URL git-репозитория со скилами")
    addsub.add_argument("--name", help="имя папки в contrib/ (по умолчанию из URL)")
    addsub.add_argument("--skills-subdir", default=None,
                        help="подпапка со скилами относительно корня сабмодуля "
                             "(по умолчанию автодетект; '' = корень)")
    addsub.add_argument("--no-install", action="store_true",
                        help="не раскладывать symlink'и после добавления")
    addsub.set_defaults(func=_cmd_add_submodule)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
