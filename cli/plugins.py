"""plugins.py — сборка plugin seed для нативных CC-плагинов.

Нативный плагин (каталог с .claude-plugin/) НЕ симлинкуется: вместо этого менеджер
строит **plugin seed** и отдаёт его Claude Code через CLAUDE_CODE_PLUGIN_SEED_DIR.
CC при старте читает seed read-only (без клона, без промпта) и сам регистрирует
skills/agents/commands/hooks/MCP плагина, ставя ${CLAUDE_PLUGIN_ROOT} на seed-копию.

Seed строится «руками самого CC» (build-via-CC): для каждого включённого плагина
вызывается неинтерактивный CLI

    CLAUDE_CODE_PLUGIN_CACHE_DIR=<seed> claude plugin marketplace add <abs plugin dir>
    CLAUDE_CODE_PLUGIN_CACHE_DIR=<seed> claude plugin install <plugin>@<mp> --scope user

CC наполняет <seed>/{known_marketplaces.json,installed_plugins.json,cache/<mp>/<plugin>/<ver>}.
Эти же команды как сайд-эффект пишут extraKnownMarketplaces + enabledPlugins в реальный
~/.claude/settings.json — эти ключи затем берёт под контроль cli/settings.py (sidecar).

Seed — производный артефакт: пересобирается на каждом `up` (gitignore .seed/.seed.store).
Требует `claude` в PATH; без него фаза плагинов пропускается с предупреждением.

Double-buffer, чтобы `up` не ломал бегущие сессии: реальные буферы лежат в .seed.store/{0,1},
а .seed — стабильный symlink на активный. Сборка идёт в НЕактивный буфер, затем .seed
атомарно (os.replace симлинка) переключается на свежий. Живая сессия читает целый старый
буфер до момента swap; старый буфер остаётся альтернативным (перезапишется на следующей сборке).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config
from .config import REPO_DIR

SEED_DIR = REPO_DIR / ".seed"          # стабильный путь (env CLAUDE_CODE_PLUGIN_SEED_DIR) — symlink на активный буфер
SEED_STORE = REPO_DIR / ".seed.store"  # реальные буферы сборки (double-buffer: 0/1)


def _pick_buffer() -> Path:
    """Выбрать НЕактивный буфер для сборки (double-buffer 0/1).

    Активный = цель текущего symlink .seed. Собираем в другой, чтобы живая сессия
    продолжала читать целый старый буфер до атомарного swap. Первый запуск (симлинка
    нет) → буфер "0".
    """
    live = None
    if SEED_DIR.is_symlink():
        live = Path(os.readlink(SEED_DIR)).name
    return SEED_STORE / ("1" if live == "0" else "0")


def _swap_seed_symlink(buf: Path) -> None:
    """Атомарно указать .seed на buf.

    Стационарно .seed — symlink: os.replace(tmp_symlink, .seed) переименовывает
    поверх старого симлинка одним syscall (атомарно, без окна отсутствия). Разовая
    миграция со старой схемы (.seed — реальный каталог): сносим его перед заменой.
    """
    tmp = SEED_DIR.parent / ".seed.swap"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(buf, tmp)
    if SEED_DIR.is_symlink():
        os.replace(tmp, SEED_DIR)                       # атомарная замена симлинка
    else:
        if SEED_DIR.exists():
            shutil.rmtree(SEED_DIR)                     # миграция со старой in-place схемы
        os.replace(tmp, SEED_DIR)


@dataclass
class SeedResult:
    """Итог build_seed: какие плагины собраны/включены + предупреждения."""
    plugins: list[config.Plugin]        # все обнаруженные [[plugins]] (для settings/sidecar)
    built: list[str]                    # ref'ы успешно собранных в seed
    errors: int = 0


def claude_available() -> bool:
    """`claude` в PATH? (без него seed не собрать)."""
    return shutil.which("claude") is not None


def _run(cmd: list[str], seed: Path) -> subprocess.CompletedProcess:
    """Запустить claude-команду с CLAUDE_CODE_PLUGIN_CACHE_DIR=seed, захватив вывод."""
    env = {**os.environ, "CLAUDE_CODE_PLUGIN_CACHE_DIR": str(seed)}
    return subprocess.run(cmd, env=env, cwd=REPO_DIR,
                          capture_output=True, text=True)


def check_requirements(ctx, ref: str, requirements) -> None:
    """Проверить внешние зависимости одного источника ({name, check, hint}).

    Для каждого требования прогоняется `check` (shell). При ненулевом коде печатается
    `hint` — как поставить. Менеджер сам зависимость НЕ ставит (внешний софт). Не
    фатально: только предупреждение, ctx.errors не растёт. ref — источник требования
    (ref плагина / «config.toml [statusline]») для вывода.
    """
    for req in requirements:
        try:
            rc = subprocess.run(req["check"], shell=True, cwd=REPO_DIR,
                                capture_output=True).returncode
        except OSError:
            rc = 1
        if rc != 0:
            label = req.get("name") or req["check"]
            ctx.say(f"  ! {ref}: требуется «{label}» — не найдено.")
            ctx.say(f"    Установить: {req['hint']}")


def build_seed(ctx) -> SeedResult:
    """Пересобрать plugin seed из всех включённых [[plugins]]-источников.

    ctx — Ctx из install.py (say/do/dry_run). Сборка в свежий буфер + атомарный swap
    .seed (детерминизм, нет stale, не рвёт живые сессии). Для каждого enabled-плагина:
    marketplace add + install. enabled=false плагины в seed не ставятся (их
    enabledPlugins=false выставит settings-слой).

    Возвращает SeedResult со всеми обнаруженными плагинами (для settings/sidecar) и
    списком собранных ref'ов. Если claude недоступен — пустой built + предупреждение.
    """
    plugins, warnings = config._discover_plugins()
    for w in warnings:
        ctx.say(f"  ! {w}")
        ctx.errors += 1

    res = SeedResult(plugins=plugins, built=[])

    if not plugins:
        ctx.say("Плагины -> seed: источников [[plugins]] нет.")
        ctx.say()
        return res

    ctx.say(f"Плагины -> {SEED_DIR}/  (CLAUDE_CODE_PLUGIN_SEED_DIR)")

    if not claude_available():
        ctx.say("  ! `claude` не найден в PATH — сборка seed пропущена.")
        ctx.say("    Плагины не будут подключены, пока не появится CLI claude + повторный up.")
        ctx.errors += 1
        ctx.say()
        return res

    enabled = [p for p in plugins if p.enabled]

    # Проверка внешних зависимостей плагинов (бинари): предупреждаем + подсказка.
    for p in enabled:
        check_requirements(ctx, p.ref, p.requirements)

    # Double-buffer: собираем в НЕактивный буфер, живой .seed не трогаем до swap.
    # Это не ломает бегущие сессии (они читают целый старый буфер до атомарной замены
    # симлинка). Пересобираем только выбранный буфер (детерминизм, нет stale).
    buf = _pick_buffer()
    if buf.exists():
        ctx.do(f"rm -rf {buf}", lambda: shutil.rmtree(buf))
    ctx.do(f"mkdir -p {buf}", lambda: buf.mkdir(parents=True, exist_ok=True))

    for p in enabled:
        # Предупреждение про SessionStart-хуки плагина (CC выполнит их при старте).
        for cmd in p.session_start_hooks:
            ctx.say(f"  ⚠ {p.ref}: SessionStart-хук выполнится при старте CC: {cmd}")

        if ctx.dry_run:
            ctx.say(f"  [dry-run] claude plugin marketplace add {p.path}")
            ctx.say(f"  [dry-run] claude plugin install {p.ref} --scope user")
            res.built.append(p.ref)
            continue

        add = _run(["claude", "plugin", "marketplace", "add", str(p.path)], buf)
        if add.returncode != 0:
            err = (add.stderr or add.stdout).strip().splitlines()[-1:] or [""]
            ctx.say(f"  ! {p.ref}: marketplace add не удался — {err[0]}")
            ctx.errors += 1
            continue

        inst = _run(["claude", "plugin", "install", p.ref, "--scope", "user"], buf)
        if inst.returncode != 0:
            err = (inst.stderr or inst.stdout).strip().splitlines()[-1:] or [""]
            ctx.say(f"  ! {p.ref}: install не удался — {err[0]}")
            ctx.errors += 1
            continue

        # CC при чтении seed probит $SEED/marketplaces/<name>/ физически. Для local
        # `directory` source CC НЕ клонирует туда контент (оставляет пусто, указывая на
        # сабмодуль абс. путём) → без этого плагин не материализуется. Досоздаём симлинк
        # marketplaces/<name> -> корень плагина: probing находит marketplace.json,
        # CC грузит skills/agents/commands/hooks/MCP. Симлинк на наш же репо, seed
        # read-only → CC только читает.
        mp_link = buf / "marketplaces" / p.marketplace
        mp_link.parent.mkdir(parents=True, exist_ok=True)
        if mp_link.is_symlink() or mp_link.exists():
            mp_link.unlink()
        mp_link.symlink_to(p.path)

        ctx.say(f"  + {p.ref} -> seed")
        res.built.append(p.ref)

    # Атомарно переключить .seed на свежий буфер (без окна отсутствия для сессий).
    ctx.do(f"ln -sfn {buf} {SEED_DIR}  (атомарный swap)", lambda: _swap_seed_symlink(buf))

    n = len(res.built)
    disabled = [p.ref for p in plugins if not p.enabled]
    tail = f", выключено {len(disabled)}" if disabled else ""
    ctx.say(f"  Итого: собрано {n} плагин(ов) в seed{tail}.")
    ctx.say()
    return res
