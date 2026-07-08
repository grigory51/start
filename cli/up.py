"""up.py — полная синхронизация: обновить сабмодули + разложить symlink'и.

Эквивалент старого `make up`. Вынесено отдельно, чтобы UI мог дёргать тот же
код после toggle скила.
"""

from __future__ import annotations

import subprocess

from .config import REPO_DIR
from .install import run_install


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
    "claude" | "files" — гонять только один домен (None = оба); при only="files"
    git-шаг не нужен (dotfiles в репо, не в сабмодулях).
    """
    need_submodules = not skip_submodules and not dry_run and only != "files"
    if need_submodules:
        rc = update_submodules(quiet=quiet)
        if rc != 0 and not quiet:
            print(f"  ! git submodule update вернул {rc}")
    return run_install(dry_run=dry_run, force=force, quiet=quiet,
                       skip_seed=skip_seed, skip_settings=skip_settings, only=only)
