"""install.py — общая link-машинерия + оркестратор раскладки symlink'ов.

Здесь только переиспользуемые примитивы (Ctx, link, ensure_real_dir, …) и
оркестратор run_install. Доменные шаги вынесены:
  • Claude (~/.claude) — cli/claude.py: agents/skills/commands/hooks/CLAUDE.md/rules/statusline;
  • Files  ($HOME)     — cli/files.py: dotfiles ([[dotfiles]]).

Общие свойства раскладки: реальные папки под per-file/per-entry symlink'и (чтобы
смешивать источники и не перетирать чужое), автоматическая миграция старой схемы
folder-symlink → реальная папка (ensure_real_dir), прун наших устаревших symlink'ов.
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


def _section(ctx: Ctx, title: str) -> None:
    """Заголовок-разделитель домена в выводе (Claude / Files)."""
    ctx.say(f"══ {title} " + "═" * max(0, 40 - len(title)))
    ctx.say()


def run_install(*, dry_run: bool = False, force: bool = False, quiet: bool = False,
                skip_seed: bool = False, skip_settings: bool = False,
                only: str | None = None) -> int:
    """Разложить плагины (seed) + symlink'и + смержить settings. Возвращает число ошибок.

    Два домена, разделённые в выводе заголовками:
      • Claude — seed + settings + agents/skills/commands/hooks/CLAUDE.md/rules/statusline;
      • Files  — симлинки [[dotfiles]] в $HOME (общий сетап машины).

    Порядок внутри Claude (важен для миграции skills→plugins):
      1. seed build — собрать включённые [[plugins]] через claude CLI;
      2. settings merge — enabledPlugins + SEED_DIR + mcp + hooks (до прун-фаз symlink'ов,
         чтобы плагин был зарегистрирован раньше, чем исчезнут его старые loose-зеркала);
      3. loose-symlink'и: agents, skills (с пруном выпавшего), commands, hooks, CLAUDE.md,
         rules, statusline.

    quiet: подавить баннер. skip_seed/skip_settings: пропустить соответствующую фазу
    (быстрый toggle loose из UI). only: "claude" | "files" — гонять только один домен
    (None = оба).
    """
    from . import claude
    from . import files

    do_claude = only in (None, "claude")
    do_files = only in (None, "files")
    ctx = Ctx(dry_run, force)
    if not quiet:
        ctx.say(f"Репо:        {REPO_DIR}")
        ctx.say(f"Назначение:  {CLAUDE_DIR}  |  $HOME")
        if ctx.dry_run:
            ctx.say("(dry-run: изменения не применяются)")
        ctx.say()

    if do_claude:
        _section(ctx, "Claude")
        # 1. Плагины → seed (включённые [[plugins]] собираются самим claude CLI).
        plugin_list = config.load_plugins()
        if not skip_seed:
            seed_res = plugins.build_seed(ctx)
            plugin_list = seed_res.plugins

        # 2. Settings merge (enabledPlugins/SEED_DIR/mcp/hooks) — до прун-фаз symlink'ов.
        if not skip_settings:
            ctx.errors += settings.merge_into_settings(plugin_list, dry_run=dry_run)

        # 3. Loose-symlink'и.
        claude.install_agents(ctx)
        try:
            claude.install_skills(ctx)
        except claude.SkillCollisionError as e:
            # Коллизия [[skills.symlinks]] — фатально для skills-фазы: прерываем
            # раскладку скилов. Остальное всё ещё разложим. Ненулевой ctx.errors → exit 1.
            ctx.say(f"  ! {e}")
            ctx.errors += 1
            ctx.say()
        claude.install_commands(ctx)
        claude.install_hooks(ctx)
        claude.install_claude_md(ctx)
        claude.install_rules(ctx)
        claude.install_statusline(ctx)

    if do_files:
        _section(ctx, "Files")
        files.install_files(ctx)

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
