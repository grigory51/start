"""claude.py — шаги домена Claude (~/.claude): agents, skills, commands, hooks,
CLAUDE.md, rules, statusline.

Раскладка:
  agents   — ~/.claude/agents это РЕАЛЬНАЯ папка с per-file symlink'ами.
             Источники берутся из [[agents]] в config.toml (репо + contrib).
             Per-file, чтобы смешивать агентов из разных источников в одной папке.
  skills   — ~/.claude/skills это РЕАЛЬНАЯ папка; каждый скил <name>/ — тоже
             РЕАЛЬНАЯ папка-зеркало с per-file symlink'ами на записи верхнего
             уровня source-папки скила + доп. symlink'и из [[skills.symlinks]].
  commands — per-file symlink в ~/.claude/commands/ (зеркало agents).
  hooks    — per-file symlink в ~/.claude/hooks/ (папку не трогаем: там лежат
             сторонние хуки не из репо).
  CLAUDE.md— один symlink на repo/ai/claude/CLAUDE.global.md.
  rules    — per-entry зеркало repo/ai/claude/rules/ в ~/.claude/rules/ (CC грузит
             все *.md оттуда автоматически при старте — always-on).
  statusline — symlink скрипта; settings.json пишет settings-слой.

Общая link-машинерия (Ctx, link, ensure_real_dir, …) — в install.py; здесь только
доменные шаги. Оркестрация — install.run_install.
"""

from __future__ import annotations

from . import config
from . import plugins
from .config import REPO_DIR
from .install import (
    CLAUDE_DIR,
    Ctx,
    _is_ours,
    _plural,
    _readlink,
    ensure_real_dir,
    link,
)


class SkillCollisionError(Exception):
    """destination из [[skills.symlinks]] совпал с реальной записью скила.

    Ошибка конфига: доп. symlink перекрыл бы родной файл/папку скила. Прерывает
    раскладку скилов (перехват в run_install)."""


def _is_our_mirror(d) -> bool:
    """d — наше зеркало скила: реальная папка, внутри только наши symlink'и.

    Защита от сноса чужой реальной папки в ~/.claude/skills."""
    if not d.is_dir() or d.is_symlink():
        return False
    for child in d.iterdir():
        if not (child.is_symlink() and _is_ours(_readlink(child))):
            return False
    return True


def _rmtree_mirror(d) -> None:
    """Снести папку-зеркало: unlink наших symlink'ов, затем rmdir (без рекурсии)."""
    for child in d.iterdir():
        child.unlink()
    d.rmdir()


def install_agents(ctx: Ctx) -> None:
    dst_root = CLAUDE_DIR / "agents"
    ctx.say(f"Агенты -> {dst_root}/  (per-file symlink)")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    # Миграция старой схемы (folder-symlink ~/.claude/agents -> repo/agents)
    # в реальную папку под per-file линки. Тот же механизм, что у скилов.
    ok, migrated = ensure_real_dir(ctx, dst_root)
    if not ok:
        ctx.say()
        return

    agents, warnings = config._discover_agents()
    for w in warnings:
        ctx.say(f"  ! {w}")
        ctx.errors += 1

    wanted = {a.name + ".md" for a in agents}

    # Убрать наши устаревшие symlink'и (агент выпал из конфига/репо).
    # Пропускаем, если папку только что освободили (мигрировали).
    if not migrated and dst_root.is_dir() and not dst_root.is_symlink():
        for entry in sorted(dst_root.iterdir()):
            if entry.is_symlink() and _is_ours(_readlink(entry)) and entry.name not in wanted:
                ctx.say(f"  - {entry.name} больше не активен — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)

    changed = 0
    for a in agents:
        st = link(ctx, a.path, dst_root / (a.name + ".md"), assume_absent=migrated, quiet=True)
        changed += st == "linked"
    delta = f", изменено {changed}" if changed else " — без изменений"
    ag = _plural(len(agents), "агент", "агента", "агентов")
    ctx.say(f"  Итого: {ag}{delta}.")
    ctx.say()


def install_commands(ctx: Ctx) -> None:
    """Slash-команды -> ~/.claude/commands/ (per-file symlink). Зеркало install_agents."""
    dst_root = CLAUDE_DIR / "commands"
    commands, warnings = config._discover_commands()
    if not commands and not warnings:
        return  # нет [[commands]] — ничего не печатаем
    ctx.say(f"Команды -> {dst_root}/  (per-file symlink)")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    ok, migrated = ensure_real_dir(ctx, dst_root)
    if not ok:
        ctx.say()
        return

    for w in warnings:
        ctx.say(f"  ! {w}")
        ctx.errors += 1

    wanted = {c.name + ".md" for c in commands}

    if not migrated and dst_root.is_dir() and not dst_root.is_symlink():
        for entry in sorted(dst_root.iterdir()):
            if entry.is_symlink() and _is_ours(_readlink(entry)) and entry.name not in wanted:
                ctx.say(f"  - {entry.name} больше не активна — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)

    changed = 0
    for c in commands:
        st = link(ctx, c.path, dst_root / (c.name + ".md"), assume_absent=migrated, quiet=True)
        changed += st == "linked"
    delta = f", изменено {changed}" if changed else " — без изменений"
    cm = _plural(len(commands), "команда", "команды", "команд")
    ctx.say(f"  Итого: {cm}{delta}.")
    ctx.say()


def _mirror_skill(ctx: Ctx, skill: config.Skill, dst) -> tuple[int, int]:
    """Разложить per-file зеркало одного скила в dst (реальная папка).

    На каждую запись верхнего уровня source-папки скила — отдельный symlink
    (файл или папка как dir-symlink, без рекурсии внутрь). Плюс доп. symlink'и
    из [[skills.symlinks]]. Коллизия destination с записью скила → SkillCollisionError.

    Вывод агрегирован: одна строка на скил (link() в quiet). Возвращает
    (total, linked) — всего элементов в зеркале, из них поставлено/обновлено.
    """
    ok, migrated = ensure_real_dir(ctx, dst)
    if not ok:
        return 0, 0

    linked = 0
    # 1) per-file зеркало записей верхнего уровня source-папки скила.
    src_entries = {p.name: p for p in sorted(skill.path.iterdir())}
    for name, src in src_entries.items():
        st = link(ctx, src, dst / name, kind="/" if src.is_dir() else "",
                  assume_absent=migrated, quiet=True)
        linked += st == "linked"

    # 2) доп. symlink'и [[skills.symlinks]] с проверкой коллизий.
    wanted_extra: set[str] = set()
    for sl in skill.symlinks:
        dest = sl["destination"]
        if dest in src_entries:
            raise SkillCollisionError(
                f"{skill.name}/{dest}: [[skills.symlinks]] перекрывает родную "
                f"запись скила — исправьте config.toml")
        extra_src = (REPO_DIR / sl["source"]).resolve()
        st = link(ctx, extra_src, dst / dest,
                  kind="/" if extra_src.is_dir() else "", assume_absent=migrated, quiet=True)
        linked += st == "linked"
        wanted_extra.add(dest)

    total = len(src_entries) + len(wanted_extra)

    # Одна строка на скил: + если что-то менялось, иначе = (без изменений).
    items = _plural(total, "элемент", "элемента", "элементов")
    if linked:
        ctx.say(f"  + {skill.name}/ — {items} (+{linked})")
    else:
        ctx.say(f"  = {skill.name}/ — {items}")

    # 3) внутренняя чистка: наши symlink'и в зеркале без соответствия (источник
    #    удалён или destination убран из config). Пропускаем после миграции.
    if not migrated and dst.is_dir() and not dst.is_symlink():
        wanted = set(src_entries) | wanted_extra
        for entry in sorted(dst.iterdir()):
            if (entry.is_symlink() and _is_ours(_readlink(entry))
                    and entry.name not in wanted):
                ctx.say(f"  - {skill.name}/{entry.name} больше не актуален — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)

    return total, linked


def install_skills(ctx: Ctx) -> None:
    dst_root = CLAUDE_DIR / "skills"
    ctx.say(f"Навыки -> {dst_root}/  (per-skill зеркало, per-file symlink)")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    # ~/.claude/skills сам по себе — реальная папка (не folder-symlink).
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

    # (a) Чистка верхнего уровня: скил выпал из конфига/репо/выключен.
    #     Удаляем наш устаревший folder-symlink (старая схема) или зеркало целиком.
    #     Чужое не трогаем. Пропускаем, если папку только что мигрировали.
    if not migrated and dst_root.is_dir() and not dst_root.is_symlink():
        for entry in sorted(dst_root.iterdir()):
            if entry.name in wanted:
                continue
            if entry.is_symlink() and _is_ours(_readlink(entry)):
                ctx.say(f"  - {entry.name} больше не активен — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)
            elif _is_our_mirror(entry):
                ctx.say(f"  - {entry.name}/ больше не активен — удаляю зеркало")
                ctx.do(f"rm -rf {entry}", lambda e=entry: _rmtree_mirror(e))

    total_links = 0
    changed = 0
    for s in skills:
        t, linked = _mirror_skill(ctx, s, dst_root / s.name)
        total_links += t
        changed += linked

    # Внешние зависимости скилов ([[skills.requirements]]): напр. локальный плеер
    # для рендера/верификации. requirements одинаковы для всех скилов источника
    # (прокинуты из записи) — проверяем один раз на источник, у которого есть
    # включённый скил. Менеджер сам не ставит, только подсказывает.
    seen_req_sources: set[str] = set()
    for s in skills:
        if not s.requirements or s.source in seen_req_sources:
            continue
        seen_req_sources.add(s.source)
        plugins.check_requirements(ctx, s.source, s.requirements)

    delta = f", изменено {changed}" if changed else " — без изменений"
    sk = _plural(len(skills), "скил", "скила", "скилов")
    ln = _plural(total_links, "symlink", "symlink'а", "symlink'ов")
    ctx.say(f"  Итого: {sk}, {ln}{delta}.")
    ctx.say()


def install_hooks(ctx: Ctx) -> None:
    dst = CLAUDE_DIR / "hooks"
    ctx.say(f"Hooks -> {dst}/")
    if not ctx.dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    hooks_src = REPO_DIR / "ai" / "claude" / "hooks"
    if hooks_src.is_dir():
        for f in sorted(hooks_src.glob("*.sh")):
            link(ctx, f, dst / f.name)
    ctx.say()


def install_claude_md(ctx: Ctx) -> None:
    """Глобальный CLAUDE.md -> symlink на repo/ai/claude/CLAUDE.global.md.

    Грузится во всех сессиях. Управляется из репо (правь ai/claude/CLAUDE.global.md,
    не ~/.claude/CLAUDE.md). Чужой существующий файл бэкапится при --force.
    """
    src = REPO_DIR / "ai" / "claude" / "CLAUDE.global.md"
    if not src.is_file():
        return
    ctx.say(f"CLAUDE.md -> {CLAUDE_DIR / 'CLAUDE.md'}")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    link(ctx, src, CLAUDE_DIR / "CLAUDE.md")
    ctx.say()


def install_rules(ctx: Ctx) -> None:
    """Правила -> ~/.claude/rules/ (per-entry symlink).

    Каталог ai/claude/rules/ разбивает глобальные инструкции на тематические файлы.
    Claude Code (v2.0.64+) автоматически грузит все *.md из ~/.claude/rules/ при
    старте — как CLAUDE.md, но по темам. Источник фиксирован (репо), как у
    CLAUDE.md/hooks. Зеркало per-entry (файл или подпапка dir-symlink), чтобы не
    перетирать чужие правила в ~/.claude/rules/ и поддержать вложенные каталоги.
    Скрытые записи (напр. .gitkeep) пропускаем — CC их не грузит.
    """
    src_root = REPO_DIR / "ai" / "claude" / "rules"
    if not src_root.is_dir():
        return
    entries = sorted(p for p in src_root.iterdir() if not p.name.startswith("."))
    if not entries:
        return
    dst_root = CLAUDE_DIR / "rules"
    ctx.say(f"Правила -> {dst_root}/  (per-file symlink)")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    ok, migrated = ensure_real_dir(ctx, dst_root)
    if not ok:
        ctx.say()
        return

    wanted = {p.name for p in entries}

    # Чистка наших устаревших symlink'ов (правило убрали из репо). Пропускаем
    # сразу после миграции папки. Чужое не трогаем.
    if not migrated and dst_root.is_dir() and not dst_root.is_symlink():
        for entry in sorted(dst_root.iterdir()):
            if entry.is_symlink() and _is_ours(_readlink(entry)) and entry.name not in wanted:
                ctx.say(f"  - {entry.name} больше не активно — удаляю symlink")
                ctx.do(f"rm {entry}", entry.unlink)

    changed = 0
    for src in entries:
        st = link(ctx, src, dst_root / src.name,
                  kind="/" if src.is_dir() else "", assume_absent=migrated, quiet=True)
        changed += st == "linked"
    delta = f", изменено {changed}" if changed else " — без изменений"
    rl = _plural(len(entries), "правило", "правила", "правил")
    ctx.say(f"  Итого: {rl}{delta}.")
    ctx.say()


def install_statusline(ctx: Ctx) -> None:
    """Statusline-скрипт -> symlink в ~/.claude/<dest>. settings.json пишет settings-слой."""
    sl = config.load_statusline()
    if not sl:
        return
    src = (REPO_DIR / sl["path"]).resolve()
    ctx.say(f"Statusline -> {CLAUDE_DIR / sl['dest']}")
    if not ctx.dry_run:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    link(ctx, src, CLAUDE_DIR / sl["dest"])
    # Зависимости команды statusline (напр. node для .mjs): без них CC молча не
    # запускает статусбар — проверяем и подсказываем, как поставить.
    plugins.check_requirements(ctx, "config.toml [statusline]",
                               sl.get("requirements", []))
    ctx.say()
