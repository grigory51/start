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

Seed — производный артефакт: полностью пересобирается на каждом `up` (gitignore .seed/).
Требует `claude` в PATH; без него фаза плагинов пропускается с предупреждением.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config
from .config import REPO_DIR

SEED_DIR = REPO_DIR / ".seed"


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


def check_requirements(ctx, plugins) -> None:
    """Проверить внешние зависимости включённых плагинов ([[plugins.requirements]]).

    Для каждого требования прогоняется `check` (shell). При ненулевом коде печатается
    `hint` — как поставить. Менеджер сам зависимость НЕ ставит (внешний софт). Не
    фатально: только предупреждение, ctx.errors не растёт.
    """
    for p in plugins:
        if not p.enabled:
            continue
        for req in p.requirements:
            try:
                rc = subprocess.run(req["check"], shell=True, cwd=REPO_DIR,
                                    capture_output=True).returncode
            except OSError:
                rc = 1
            if rc != 0:
                label = req.get("name") or req["check"]
                ctx.say(f"  ! {p.ref}: требуется «{label}» — не найдено.")
                ctx.say(f"    Установить: {req['hint']}")


def build_seed(ctx) -> SeedResult:
    """Пересобрать plugin seed из всех включённых [[plugins]]-источников.

    ctx — Ctx из install.py (say/do/dry_run). Полная пересборка SEED_DIR (детерминизм,
    нет stale). Для каждого enabled-плагина: marketplace add + install. enabled=false
    плагины в seed не ставятся (их enabledPlugins=false выставит settings-слой).

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
    check_requirements(ctx, enabled)

    # Полная пересборка: сносим старый seed (наш производный каталог).
    if SEED_DIR.exists():
        ctx.do(f"rm -rf {SEED_DIR}", lambda: shutil.rmtree(SEED_DIR))
    ctx.do(f"mkdir -p {SEED_DIR}", lambda: SEED_DIR.mkdir(parents=True, exist_ok=True))

    for p in enabled:
        # Предупреждение про SessionStart-хуки плагина (CC выполнит их при старте).
        for cmd in p.session_start_hooks:
            ctx.say(f"  ⚠ {p.ref}: SessionStart-хук выполнится при старте CC: {cmd}")

        if ctx.dry_run:
            ctx.say(f"  [dry-run] claude plugin marketplace add {p.path}")
            ctx.say(f"  [dry-run] claude plugin install {p.ref} --scope user")
            res.built.append(p.ref)
            continue

        add = _run(["claude", "plugin", "marketplace", "add", str(p.path)], SEED_DIR)
        if add.returncode != 0:
            err = (add.stderr or add.stdout).strip().splitlines()[-1:] or [""]
            ctx.say(f"  ! {p.ref}: marketplace add не удался — {err[0]}")
            ctx.errors += 1
            continue

        inst = _run(["claude", "plugin", "install", p.ref, "--scope", "user"], SEED_DIR)
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
        mp_link = SEED_DIR / "marketplaces" / p.marketplace
        mp_link.parent.mkdir(parents=True, exist_ok=True)
        if mp_link.is_symlink() or mp_link.exists():
            mp_link.unlink()
        mp_link.symlink_to(p.path)

        ctx.say(f"  + {p.ref} -> seed")
        res.built.append(p.ref)

    n = len(res.built)
    disabled = [p.ref for p in plugins if not p.enabled]
    tail = f", выключено {len(disabled)}" if disabled else ""
    ctx.say(f"  Итого: собрано {n} плагин(ов) в seed{tail}.")
    ctx.say()
    return res
