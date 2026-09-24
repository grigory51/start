"""up.py — полная синхронизация: обновить сабмодули + разложить symlink'и.

Эквивалент старого `make up`. Вынесено отдельно, чтобы UI мог дёргать тот же
код после toggle скила.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from . import adapters, config, project
from .config import REPO_DIR
from .install import run_install


def find_project_config(directory: Path) -> Path | None:
    """Найти ближайший start.toml, не выходя за границу текущего Git-проекта."""
    directory = directory.resolve()
    ancestors = (directory, *directory.parents)
    git_root = next((path for path in ancestors if (path / ".git").exists()), None)
    for path in ancestors if git_root is not None else (directory,):
        candidate = path / "start.toml"
        if candidate.is_file():
            return candidate
        if path == git_root:
            break
    return None


def update_submodules(*, quiet: bool = False) -> int:
    """git submodule update --init --recursive. Возвращает returncode."""
    cmd = ["git", "submodule", "update", "--init", "--recursive"]
    if quiet:
        cmd.append("--quiet")
    proc = subprocess.run(cmd, cwd=REPO_DIR)
    return proc.returncode


def run_up(*, dry_run: bool = False, force: bool = False, quiet: bool = False,
           skip_submodules: bool = False, skip_seed: bool = False,
           skip_settings: bool = False, only: str | None = None) -> int:
    """Сабмодули + install (seed + symlink + settings). Возвращает число ошибок install.

    skip_submodules: пропустить git-шаг. skip_seed/skip_settings: пропустить сборку
    плагинов / merge settings (для быстрого toggle loose-скилов из UI). only:
    "ai" | "ai:claude" | "ai:codex" | "files" — ограничить синхронизацию;
    git-шаг не нужен (dotfiles в репо, не в сабмодулях).
    """
    project_plan = None
    if only != "files":
        try:
            config.load_mcp()
            project_file = find_project_config(Path(os.environ.get("START_INVOKE_DIR") or Path.cwd()))
            if project_file is not None:
                project_plan = project.build_plan(
                    config.load_project(project_file), only=only,
                    skip_seed=skip_seed, skip_settings=skip_settings,
                )
        except config.FileValueError as error:
            print(f"  ! {error}; установка отменена.")
            return 1
        except (ValueError, OSError, adapters.AdapterError) as error:
            # Текст ошибки TOML или валидации может содержать секреты из входного файла.
            print(f"  ! Проектный start.toml не прошёл проверку ({type(error).__name__}); установка отменена.")
            return 1
    need_submodules = not skip_submodules and not dry_run and only != "files"
    if need_submodules:
        rc = update_submodules(quiet=quiet)
        if rc != 0 and not quiet:
            print(f"  ! git submodule update вернул {rc}")
    errors = run_install(dry_run=dry_run, force=force, quiet=quiet,
                         skip_seed=skip_seed, skip_settings=skip_settings, only=only)
    if project_plan is not None:
        errors += project.apply_plan(project_plan, dry_run=dry_run, force=force)
    return errors
