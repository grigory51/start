"""files.py — шаг домена Files ($HOME): dotfiles ([[dotfiles]]) симлинками.

Не про Claude Code — общий сетап машины (~/.vimrc и т.п.). Общая link-машинерия —
в install.py; здесь только доменный шаг. Оркестрация — install.run_install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config
from .config import REPO_DIR
from .install import Ctx, link


def install_launcher(ctx: Ctx) -> None:
    """Симлинк ~/.local/bin/start -> scripts/run.sh, чтобы звать `start` из любого места.

    run.sh резолвит REPO от своего реального пути (сквозь симлинк), поэтому лаунчер
    работает независимо от cwd. ~/.local/bin обычно уже в PATH; каталог создаём при
    отсутствии.
    """
    src = (REPO_DIR / "scripts" / "run.sh").resolve()
    dst = Path.home() / ".local" / "bin" / "start"
    if not ctx.dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
    link(ctx, src, dst)


def install_files(ctx: Ctx) -> None:
    """Dotfiles ([[files.dotfiles]]) -> symlink'и в $HOME + опциональный posthook.

    dst строится от $HOME (в отличие от Claude-шагов — те от CLAUDE_DIR). Прун
    снятых записей НЕ делаем: итерировать/удалять в $HOME небезопасно — убрал запись
    из config, симлинк снимаешь вручную.

    Запись: source (обяз.), target (опц. — если задан, ставим symlink), posthook
    (опц. — shell-команда после раскладки записи; напр. для iTerm — указать iTerm на
    custom-папку в репо через `defaults write`). Если target нет — symlink не ставим,
    только posthook.
    """
    install_launcher(ctx)
    entries, warnings = config.load_dotfiles()
    for w in warnings:
        ctx.say(f"  ! {w}")
    if not entries:
        ctx.say("  (нет записей [[files.dotfiles]])")
        ctx.say()
        return
    for e in entries:
        src = (REPO_DIR / e["source"]).resolve()
        target = e.get("target") or ""
        dst = Path(target).expanduser() if target else None
        if dst is not None:
            kind = "/" if src.is_dir() else ""
            if not ctx.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)   # напр. ~/.config/nvim/
            link(ctx, src, dst, kind=kind)
        if e.get("posthook"):
            _run_posthook(ctx, e["posthook"], src, dst)
    ctx.say()


def _run_posthook(ctx: Ctx, cmd: str, src: Path, dst: Path | None) -> None:
    """Выполнить posthook записи [[files.dotfiles]].

    Команда — доверенный shell из версионного config.toml. Запускается в корне репо;
    в окружении доступны SOURCE (абсолютный путь source в репо) и TARGET (развёрнутый
    target или пустая строка). Идемпотентность — на совести команды (напр. `defaults
    write` идемпотентна). Ненулевой rc — предупреждение (не фатально), ctx.errors += 1.
    """
    if ctx.dry_run:
        ctx.say(f"  [dry-run] posthook: {cmd}")
        return
    env = {**os.environ, "SOURCE": str(src), "TARGET": str(dst) if dst else ""}
    proc = subprocess.run(cmd, shell=True, cwd=REPO_DIR, env=env,
                          capture_output=True, text=True)
    if proc.returncode == 0:
        ctx.say(f"  + posthook: {cmd}")
    else:
        err = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
        ctx.say(f"  ! posthook не удался (rc {proc.returncode}): {cmd}")
        ctx.say(f"    {err[0]}")
        ctx.errors += 1
