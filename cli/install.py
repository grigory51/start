"""install.py — раскладывает symlink'и агентов/навыков/hooks в ~/.claude.

Раскладка:
  agents  — folder-symlink ~/.claude/agents -> repo/agents
            (новые агенты подхватываются без повторного запуска).
  skills  — ~/.claude/skills это РЕАЛЬНАЯ папка с per-skill symlink'ами.
            Источники и список выключенных скилов берутся из config.toml
            (см. cli/config.py). Per-skill, чтобы смешивать источники в одной папке.
  hooks   — per-file symlink в ~/.claude/hooks/ (папку не трогаем: там лежат
            сторонние хуки не из репо).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config
from .config import REPO_DIR

CLAUDE_DIR = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))


# --- вывод --------------------------------------------------------------------

class Ctx:
    def __init__(self, dry_run: bool, force: bool) -> None:
        self.dry_run = dry_run
        self.force = force
        self.errors = 0

    def say(self, msg: str = "") -> None:
        print(msg)

    def do(self, action: str, fn) -> None:
        """Выполнить fn(), либо в dry-run только напечатать action."""
        if self.dry_run:
            self.say(f"  [dry-run] {action}")
        else:
            fn()


# --- линковка -----------------------------------------------------------------

def _backup_or_skip(ctx: Ctx, dst: Path, kind: str) -> bool:
    """dst существует и это НЕ наш symlink. True если место освобождено."""
    if ctx.force:
        bak = dst.with_name(dst.name + ".bak")
        ctx.say(f"  ~ {dst.name}{kind} существует — бэкап в {bak.name}")
        ctx.do(f"mv {dst} {bak}", lambda: dst.replace(bak))
        return True
    ctx.say(f"  ! {dst.name}{kind} существует и это не наш symlink — пропуск (--force)")
    return False


def link(ctx: Ctx, src: Path, dst: Path, *, kind: str = "", assume_absent: bool = False) -> None:
    """Поставить symlink dst -> src. kind: '' для файла/скила, '/' для папки.

    assume_absent: считать dst отсутствующим (для dry-run после запланированной,
    но ещё не выполненной миграции родительской папки — иначе пути резолвятся
    сквозь устаревший folder-symlink и дают ложное «уже существует»).
    """
    name = dst.name
    if not src.exists():
        ctx.say(f"  ! пропуск {name}{kind} — источник не найден: {src}")
        ctx.errors += 1
        return

    if not assume_absent:
        # Уже корректный symlink на наш источник.
        if dst.is_symlink() and _readlink(dst) == src:
            ctx.say(f"  = {name}{kind} уже подключён")
            return

        if dst.exists() or dst.is_symlink():
            if not _backup_or_skip(ctx, dst, kind):
                return

    ctx.say(f"  + {name}{kind} -> {src}")
    ctx.do(f"ln -sfn {src} {dst}", lambda: _symlink(src, dst))


def _readlink(p: Path) -> Path:
    """Абсолютный таргет симлинка (для сравнения с нашим src)."""
    target = Path(os.readlink(p))
    return target if target.is_absolute() else (p.parent / target).resolve()


def _symlink(src: Path, dst: Path) -> None:
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src)


def _is_ours(p: Path) -> bool:
    try:
        p.resolve().relative_to(REPO_DIR)
        return True
    except ValueError:
        return False


# --- миграция folder-symlink -> реальная папка --------------------------------

def ensure_real_dir(ctx: Ctx, dst: Path) -> tuple[bool, bool]:
    """Гарантировать, что dst — реальная папка под per-skill линки.

    Старая схема ставила ~/.claude/skills как folder-symlink в repo/skills.
    Снимаем такой линк и создаём настоящую папку. Чужую папку/файл не трогаем
    без --force.

    Возвращает (ok, migrated): ok — папка готова (или будет готова в dry-run);
    migrated — место освобождалось (старый symlink/файл убран), значит для
    планирования per-skill линков dst надо считать пустым.
    """
    name = dst.name
    migrated = False
    if dst.is_symlink():
        tgt = _readlink(dst)
        if _is_ours(tgt):
            ctx.say(f"  ~ {name}/ — миграция folder-symlink → реальная папка")
            ctx.do(f"rm {dst}", dst.unlink)
            migrated = True
        elif ctx.force:
            ctx.say(f"  ~ {name}/ — чужой symlink, бэкап в {name}.bak")
            ctx.do(f"mv {dst} {dst.name}.bak", lambda: dst.replace(dst.with_name(name + ".bak")))
            migrated = True
        else:
            ctx.say(f"  ! {name}/ — чужой symlink, пропуск (--force)")
            ctx.errors += 1
            return False, False
    elif dst.exists() and not dst.is_dir():
        if not _backup_or_skip(ctx, dst, "/"):
            return False, False
        migrated = True

    if not dst.exists():
        ctx.do(f"mkdir -p {dst}", lambda: dst.mkdir(parents=True))
    return True, migrated


# --- сборка ---------------------------------------------------------------------

def install_agents(ctx: Ctx) -> None:
    ctx.say(f"Агенты -> {CLAUDE_DIR}/agents")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    link(ctx, REPO_DIR / "agents", CLAUDE_DIR / "agents", kind="/")
    ctx.say()


def install_skills(ctx: Ctx) -> None:
    dst_root = CLAUDE_DIR / "skills"
    ctx.say(f"Навыки -> {dst_root}/  (per-skill symlink)")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    ok, migrated = ensure_real_dir(ctx, dst_root)
    if not ok:
        ctx.say()
        return

    cfg = config.load()
    for w in cfg.warnings:
        ctx.say(f"  ! {w}")
        ctx.errors += 1

    skills = cfg.enabled_skills
    wanted = {s.name for s in skills}

    # Убрать наши устаревшие symlink'и (скил выпал из конфига/репо/выключен).
    # Пропускаем, если папку только что освободили (мигрировали) — там нечего чистить.
    if not migrated and dst_root.is_dir() and not dst_root.is_symlink():
        for entry in sorted(dst_root.iterdir()):
            if entry.is_symlink() and _is_ours(_readlink(entry)) and entry.name not in wanted:
                ctx.say(f"  - {entry.name} больше не активен — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)

    for s in skills:
        link(ctx, s.path, dst_root / s.name, assume_absent=migrated)
    ctx.say()


def install_hooks(ctx: Ctx) -> None:
    dst = CLAUDE_DIR / "hooks"
    ctx.say(f"Hooks -> {dst}/")
    if not ctx.dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    hooks_src = REPO_DIR / "hooks"
    if hooks_src.is_dir():
        for f in sorted(hooks_src.glob("*.sh")):
            link(ctx, f, dst / f.name)
    ctx.say()


def run_install(*, dry_run: bool = False, force: bool = False, quiet: bool = False) -> int:
    """Разложить symlink'и. Возвращает число ошибок (0 — успех).

    quiet: подавить вступительный/финальный баннер (для вызова из UI).
    """
    ctx = Ctx(dry_run, force)
    if not quiet:
        ctx.say(f"Репо:        {REPO_DIR}")
        ctx.say(f"Назначение:  {CLAUDE_DIR}")
        if ctx.dry_run:
            ctx.say("(dry-run: изменения не применяются)")
        ctx.say()

    install_agents(ctx)
    install_skills(ctx)
    install_hooks(ctx)

    if not quiet:
        if ctx.errors:
            ctx.say(f"Готово с предупреждениями: {ctx.errors}.")
        else:
            ctx.say("Готово.")
        ctx.say()
        ctx.say("Запуск оркестратора:  claude --agent architect")
        ctx.say("Список агентов:        /agents  (внутри сессии Claude Code)")
        ctx.say()
        ctx.say("Нотификации: hooks/notify.sh подключён в ~/.claude/hooks/, событийные")
        ctx.say("хуки регистрируются в ~/.claude/settings.json вручную (см. README).")
    return ctx.errors
