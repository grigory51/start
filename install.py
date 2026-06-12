#!/usr/bin/env python3
"""install.py — подключает персональных агентов, навыки и hooks из этого репо
в ~/.claude/ через symlink'и (идемпотентно). Только stdlib, Python 3.11+.

Раскладка:
  agents  — folder-symlink ~/.claude/agents -> repo/agents
            (новые агенты подхватываются без повторного запуска).
  skills  — ~/.claude/skills это РЕАЛЬНАЯ папка с per-skill symlink'ами.
            Источники (repo/skills, contrib/* сабмодули) перечислены в skills.toml.
            Per-skill, чтобы смешивать несколько источников в одной папке.
  hooks   — per-file symlink в ~/.claude/hooks/ (папку не трогаем: там лежат
            сторонние хуки не из репо).

Использование:
  ./install.py            создать/обновить symlink'и
  ./install.py --dry-run  показать план без изменений
  ./install.py --force    перезаписать чужие файлы/симлинки (с бэкапом .bak)
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
CONFIG = REPO_DIR / "skills.toml"
CONFIG_LOCAL = REPO_DIR / "skills.local.toml"  # gitignored, переопределяет CONFIG


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


def _is_ours(p: Path) -> bool:
    try:
        p.resolve().relative_to(REPO_DIR)
        return True
    except ValueError:
        return False


# --- скилы --------------------------------------------------------------------

def is_skill(p: Path) -> bool:
    return p.is_dir() and (p / "SKILL.md").is_file()


def discover_skills(ctx: Ctx) -> list[Path]:
    """Собрать список папок-скилов из источников skills.toml.

    Возвращает абсолютные пути. Конфликты имён логирует и берёт первое вхождение.
    """
    found: dict[str, Path] = {}

    for src in _config_sources(ctx):
        if src.name in found:
            ctx.say(f"  ! дубль имени скила '{src.name}': {src} — пропуск "
                    f"(уже взят {found[src.name]})")
            ctx.errors += 1
            continue
        found[src.name] = src

    return list(found.values())


def _config_sources(ctx: Ctx) -> list[Path]:
    """Источники скилов из skills.toml, переопределённые skills.local.toml."""
    entries = _merged_entries(ctx)

    result: list[Path] = []
    for rel, entry in entries:
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            ctx.say(f"  ! источник не найден: {rel} (нет папки; для сабмодуля — "
                    f"git submodule update --init)")
            ctx.errors += 1
            continue

        include = entry.get("include", "*")
        exclude = set(entry.get("exclude", []))
        available = {p.name: p for p in root.iterdir() if is_skill(p)}

        if include == "*":
            names = sorted(available)
        else:
            names = list(include)
            for n in names:
                if n not in available:
                    ctx.say(f"  ! {rel}: скил '{n}' не найден (нет папки с SKILL.md)")
                    ctx.errors += 1

        for n in names:
            if n in exclude or n not in available:
                continue
            result.append(available[n])

    return result


def _load_entries(ctx: Ctx, path: Path) -> list[dict]:
    """Прочитать [[source]] из одного TOML-файла. [] если файла нет/ошибка."""
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        ctx.say(f"  ! ошибка разбора {path.name}: {e}")
        ctx.errors += 1
        return []

    out: list[dict] = []
    for entry in data.get("source", []):
        if not entry.get("path"):
            ctx.say(f"  ! [[source]] без path в {path.name} — пропуск")
            ctx.errors += 1
            continue
        out.append(entry)
    return out


def _merged_entries(ctx: Ctx) -> list[tuple[str, dict]]:
    """База skills.toml ⊕ overlay skills.local.toml.

    Слияние по `path`: запись из local с тем же path ПЕРЕОПРЕДЕЛЯЕТ базовую
    (include/exclude берутся из local целиком). Новый path — добавляется.
    `enabled = false` в любой записи — источник выключается.

    Возвращает [(path, entry)] в порядке: база (сохраняя позицию), затем
    новые local-источники.
    """
    base = _load_entries(ctx, CONFIG)
    local = _load_entries(ctx, CONFIG_LOCAL)

    if not base and not local and not CONFIG.is_file():
        ctx.say(f"  i {CONFIG.name} не найден — скилы не линкуются")
        return []
    if local:
        ctx.say(f"  i применён overlay {CONFIG_LOCAL.name}")

    merged: dict[str, dict] = {}
    order: list[str] = []
    for entry in base:
        p = entry["path"]
        if p not in merged:
            order.append(p)
        merged[p] = entry
    for entry in local:
        p = entry["path"]
        if p not in merged:
            order.append(p)
        merged[p] = entry  # local выигрывает целиком

    result: list[tuple[str, dict]] = []
    for p in order:
        entry = merged[p]
        if entry.get("enabled", True) is False:
            ctx.say(f"  i источник {p} выключен (enabled = false)")
            continue
        result.append((p, entry))
    return result


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

    skills = discover_skills(ctx)
    wanted = {s.name for s in skills}

    # Убрать наши устаревшие symlink'и (скил выпал из конфига/репо).
    # Пропускаем, если папку только что освободили (мигрировали) — там нечего чистить.
    if not migrated and dst_root.is_dir() and not dst_root.is_symlink():
        for entry in sorted(dst_root.iterdir()):
            if entry.is_symlink() and _is_ours(_readlink(entry)) and entry.name not in wanted:
                ctx.say(f"  - {entry.name} больше не в конфиге — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)

    for src in skills:
        link(ctx, src, dst_root / src.name, assume_absent=migrated)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="показать план без изменений")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать чужие файлы/симлинки (с бэкапом .bak)")
    args = ap.parse_args()

    ctx = Ctx(args.dry_run, args.force)
    ctx.say(f"Репо:        {REPO_DIR}")
    ctx.say(f"Назначение:  {CLAUDE_DIR}")
    if ctx.dry_run:
        ctx.say("(dry-run: изменения не применяются)")
    ctx.say()

    install_agents(ctx)
    install_skills(ctx)
    install_hooks(ctx)

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
    return 1 if ctx.errors else 0


if __name__ == "__main__":
    sys.exit(main())
