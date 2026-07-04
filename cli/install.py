"""install.py — раскладывает symlink'и агентов/навыков/hooks в ~/.claude.

Раскладка:
  agents  — ~/.claude/agents это РЕАЛЬНАЯ папка с per-file symlink'ами.
            Источники берутся из [[agents]] в config.toml (репо + contrib).
            Per-file, чтобы смешивать агентов из разных источников в одной папке.
  skills  — ~/.claude/skills это РЕАЛЬНАЯ папка; каждый скил <name>/ — тоже
            РЕАЛЬНАЯ папка-зеркало с per-file symlink'ами на записи верхнего
            уровня source-папки скила + доп. symlink'и из [[skills.symlinks]]
            (напр. plugin-root scripts/). Источники и enabled — из config.toml.
  hooks   — per-file symlink в ~/.claude/hooks/ (папку не трогаем: там лежат
            сторонние хуки не из репо).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config
from . import plugins
from . import settings
from .config import REPO_DIR

CLAUDE_DIR = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 скил / 2-4 скила / 5+ скилов."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return f"{n} {one}"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


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


def link(ctx: Ctx, src: Path, dst: Path, *, kind: str = "",
         assume_absent: bool = False, quiet: bool = False) -> str:
    """Поставить symlink dst -> src. kind: '' для файла/скила, '/' для папки.

    assume_absent: считать dst отсутствующим (для dry-run после запланированной,
    но ещё не выполненной миграции родительской папки — иначе пути резолвятся
    сквозь устаревший folder-symlink и дают ложное «уже существует»).
    quiet: не печатать построчно (для агрегации в одну строку, см. _mirror_skill).

    Возвращает статус: "linked" (поставлен/обновлён), "exists" (уже корректен),
    "skipped" (чужой, без --force), "error" (источник не найден).
    """
    name = dst.name
    if not src.exists():
        ctx.say(f"  ! пропуск {name}{kind} — источник не найден: {src}")
        ctx.errors += 1
        return "error"

    if not assume_absent:
        # Уже корректный symlink на наш источник.
        if dst.is_symlink() and _readlink(dst) == src:
            if not quiet:
                ctx.say(f"  = {name}{kind} уже подключён")
            return "exists"

        if dst.exists() or dst.is_symlink():
            # Наш же (возможно устаревший — напр. после переезда источника в репо)
            # symlink — переставляем без бэкапа/force. Чужой файл/симлинк — как раньше.
            ours = dst.is_symlink() and _is_ours(_readlink(dst))
            if not ours and not _backup_or_skip(ctx, dst, kind):
                return "skipped"

    if not quiet:
        ctx.say(f"  + {name}{kind} -> {src}")
    ctx.do(f"ln -sfn {src} {dst}", lambda: _symlink(src, dst))
    return "linked"


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


class SkillCollisionError(Exception):
    """destination из [[skills.symlinks]] совпал с реальной записью скила.

    Ошибка конфига: доп. symlink перекрыл бы родной файл/папку скила. Прерывает
    раскладку скилов (перехват в run_install)."""


def _is_our_mirror(d: Path) -> bool:
    """d — наше зеркало скила: реальная папка, внутри только наши symlink'и.

    Защита от сноса чужой реальной папки в ~/.claude/skills."""
    if not d.is_dir() or d.is_symlink():
        return False
    for child in d.iterdir():
        if not (child.is_symlink() and _is_ours(_readlink(child))):
            return False
    return True


def _rmtree_mirror(d: Path) -> None:
    """Снести папку-зеркало: unlink наших symlink'ов, затем rmdir (без рекурсии)."""
    for child in d.iterdir():
        child.unlink()
    d.rmdir()


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


def _mirror_skill(ctx: Ctx, skill: config.Skill, dst: Path) -> tuple[int, int]:
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
    hooks_src = REPO_DIR / "ai" / "hooks"
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


def install_dotfiles(ctx: Ctx) -> None:
    """Dotfiles ([[dotfiles]]) -> symlink'и в $HOME. Не про Claude Code — общий сетап машины.

    dst строится от $HOME (в отличие от прочих install_* — те от CLAUDE_DIR). Прун
    снятых записей НЕ делаем: итерировать/удалять в $HOME небезопасно — убрал запись
    из config, симлинк снимаешь вручную.
    """
    entries, warnings = config.load_dotfiles()
    for w in warnings:
        ctx.say(f"  ! {w}")
    if not entries:
        ctx.say("  (нет записей [[dotfiles]])")
        ctx.say()
        return
    for e in entries:
        src = (REPO_DIR / e["source"]).resolve()
        dst = Path(e["target"]).expanduser()
        kind = "/" if src.is_dir() else ""
        if not ctx.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)   # напр. ~/.config/nvim/
        link(ctx, src, dst, kind=kind)
    ctx.say()


def _section(ctx: Ctx, title: str) -> None:
    """Заголовок-разделитель домена в выводе (Claude Code / Dotfiles)."""
    ctx.say(f"══ {title} " + "═" * max(0, 40 - len(title)))
    ctx.say()


def run_install(*, dry_run: bool = False, force: bool = False, quiet: bool = False,
                skip_seed: bool = False, skip_settings: bool = False,
                only: str | None = None) -> int:
    """Разложить плагины (seed) + symlink'и + смержить settings. Возвращает число ошибок.

    Два домена, разделённые в выводе заголовками:
      • Claude Code — seed + settings + agents/skills/commands/hooks/CLAUDE.md/statusline;
      • Dotfiles    — симлинки [[dotfiles]] в $HOME (общий сетап машины).

    Порядок внутри Claude Code (важен для миграции skills→plugins):
      1. seed build — собрать включённые [[plugins]] через claude CLI;
      2. settings merge — enabledPlugins + SEED_DIR + mcp + hooks (до прун-фаз symlink'ов,
         чтобы плагин был зарегистрирован раньше, чем исчезнут его старые loose-зеркала);
      3. loose-symlink'и: agents, skills (с пруном выпавшего), commands, hooks-файлы.

    quiet: подавить баннер. skip_seed/skip_settings: пропустить соответствующую фазу
    (быстрый toggle loose из UI). only: "claude" | "dotfiles" — гонять только один домен
    (None = оба).
    """
    do_claude = only in (None, "claude")
    do_dotfiles = only in (None, "dotfiles")
    ctx = Ctx(dry_run, force)
    if not quiet:
        ctx.say(f"Репо:        {REPO_DIR}")
        ctx.say(f"Назначение:  {CLAUDE_DIR}  |  $HOME")
        if ctx.dry_run:
            ctx.say("(dry-run: изменения не применяются)")
        ctx.say()

    if do_claude:
        _section(ctx, "Claude Code")
        # 1. Плагины → seed (включённые [[plugins]] собираются самим claude CLI).
        plugin_list = config.load_plugins()
        if not skip_seed:
            seed_res = plugins.build_seed(ctx)
            plugin_list = seed_res.plugins

        # 2. Settings merge (enabledPlugins/SEED_DIR/mcp/hooks) — до прун-фаз symlink'ов.
        if not skip_settings:
            ctx.errors += settings.merge_into_settings(plugin_list, dry_run=dry_run)

        # 3. Loose-symlink'и.
        install_agents(ctx)
        try:
            install_skills(ctx)
        except SkillCollisionError as e:
            # Коллизия [[skills.symlinks]] — фатально для skills-фазы: прерываем
            # раскладку скилов. Хуки всё ещё разложим. Ненулевой ctx.errors → exit 1.
            ctx.say(f"  ! {e}")
            ctx.errors += 1
            ctx.say()
        install_commands(ctx)
        install_hooks(ctx)
        install_claude_md(ctx)
        install_statusline(ctx)

    if do_dotfiles:
        _section(ctx, "Dotfiles")
        install_dotfiles(ctx)

    if not quiet:
        if ctx.errors:
            ctx.say(f"Готово с предупреждениями: {ctx.errors}.")
        else:
            ctx.say("Готово.")
        if do_claude:
            ctx.say()
            ctx.say("Запуск оркестратора:  claude --agent architect")
            ctx.say("Список агентов:        /agents  (внутри сессии Claude Code)")
            ctx.say("Плагины:              /plugin  (после перезапуска claude — seed читается на старте)")
    return ctx.errors
