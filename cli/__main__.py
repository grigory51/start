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
        skip_seed=args.no_seed,
        skip_settings=args.no_settings,
    )
    return 1 if errors else 0


def _cmd_settings(args: argparse.Namespace) -> int:
    from . import config
    from . import settings as settings_mod
    if args.remove:
        return settings_mod.remove_managed(dry_run=args.dry_run)
    return settings_mod.merge_into_settings(config.load_plugins(), dry_run=args.dry_run)


def _cmd_seed(args: argparse.Namespace) -> int:
    """Пересобрать plugin seed (+ merge settings), не трогая loose-symlink'и."""
    from .install import Ctx
    from . import plugins as plugins_mod
    from . import settings as settings_mod
    ctx = Ctx(dry_run=args.dry_run, force=False)
    res = plugins_mod.build_seed(ctx)
    ctx.errors += settings_mod.merge_into_settings(res.plugins, dry_run=args.dry_run)
    if ctx.errors:
        print(f"Готово с предупреждениями: {ctx.errors}.")
    return 1 if ctx.errors else 0


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

    up = sub.add_parser("up", help="синхронизация: сабмодули + плагины (seed) + symlink'и + settings")
    up.add_argument("--dry-run", action="store_true", help="показать план без изменений")
    up.add_argument("--force", action="store_true",
                    help="перезаписать чужие файлы/симлинки (с бэкапом .bak)")
    up.add_argument("--no-submodules", action="store_true",
                    help="пропустить git submodule update")
    up.add_argument("--no-seed", action="store_true",
                    help="пропустить сборку plugin seed")
    up.add_argument("--no-settings", action="store_true",
                    help="пропустить merge ~/.claude/settings.json")
    up.set_defaults(func=_cmd_up)

    st = sub.add_parser("settings", help="merge managed-ключей в ~/.claude/settings.json (sidecar)")
    st.add_argument("--dry-run", action="store_true", help="показать diff без записи")
    st.add_argument("--remove", action="store_true",
                    help="удалить managed-ключи по sidecar (чужое не трогается)")
    st.set_defaults(func=_cmd_settings)

    sd = sub.add_parser("seed", help="пересобрать plugin seed (.seed/) + merge settings")
    sd.add_argument("--dry-run", action="store_true", help="показать план без изменений")
    sd.set_defaults(func=_cmd_seed)

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
