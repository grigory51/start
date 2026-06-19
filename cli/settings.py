"""settings.py — безопасная композиция ~/.claude/settings.json (sidecar-merge).

CC не умеет import — всё в одном settings.json на scope. Менеджеру нужно дописать
свои ключи (enabledPlugins/extraKnownMarketplaces от seed-сборки, env.SEED_DIR,
mcpServers, hooks), НЕ затронув чужие (permissions/model/statusLine/чужие плагины…).

JSON без комментариев → роль begin/end-маркеров играет **sidecar-манифест**
~/.claude/.claude-agents-managed.json: список под-ключей, которые менеджер записал в
прошлый раз. На каждом merge: новые = из конфига; stale = (прошлые − новые) удаляются;
чужие под-ключи не трогаются. Идемпотентно, отменяемо (settings --remove).

Плагины: build_seed (cli/plugins.py) уже как сайд-эффект пишет extraKnownMarketplaces[mp]
+ enabledPlugins[ref]=true в реальный settings.json. Здесь мы (a) берём эти ключи под
sidecar-контроль; (b) выставляем enabledPlugins[ref]=false для выключенных; (c) пишем
env.CLAUDE_CODE_PLUGIN_SEED_DIR; (d) mcpServers; (e) hooks; (f) пруним выпавшее.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config
from .plugins import SEED_DIR

CLAUDE_DIR = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
SETTINGS = CLAUDE_DIR / "settings.json"
SIDECAR = CLAUDE_DIR / ".claude-agents-managed.json"

SEED_ENV_KEY = "CLAUDE_CODE_PLUGIN_SEED_DIR"


# --- sidecar ------------------------------------------------------------------

def _empty_sidecar() -> dict:
    """Пустой манифест: по разделу — список/набор наших под-ключей."""
    return {
        "enabledPlugins": [],         # ["plugin@mp", ...]
        "extraKnownMarketplaces": [],  # ["mp", ...]
        "mcpServers": [],             # legacy: наши MCP в settings.json (мёртвый ключ, чистим)
        "claudeJsonMcpServers": [],   # ["name", ...] — наши MCP в ~/.claude.json (user-scope)
        "hookCommands": [],           # ["bash \"...notify.sh\"", ...] — сигнатуры наших hook-команд
        "env": [],                    # ["CLAUDE_CODE_PLUGIN_SEED_DIR"]
        "statusLine": False,          # владеем ли мы settings.statusLine
    }


def _load_sidecar() -> dict:
    if not SIDECAR.is_file():
        return _empty_sidecar()
    try:
        data = json.loads(SIDECAR.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_sidecar()
    base = _empty_sidecar()
    for k, v in data.items():
        if k not in base:
            continue
        base[k] = list(v) if isinstance(v, list) else v
    return base


# --- фрагмент (что хотим иметь) -----------------------------------------------

def build_fragment(plugins: list[config.Plugin]) -> dict:
    """Собрать managed-фрагмент из текущего config.toml + seed.

    enabledPlugins/extraKnownMarketplaces — по [[plugins]] (включённые true, выключенные
    false; extraKnownMarketplaces по marketplace включённых). env.SEED_DIR на наш .seed.
    hooks — из [[hooks]] (script→events). MCP сюда НЕ входят — user-scope MCP пишутся в
    ~/.claude.json (см. cli/claudejson.py), т.к. CC не читает mcpServers из settings.json.
    """
    enabled_plugins: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    for p in plugins:
        enabled_plugins[p.ref] = p.enabled
        if p.enabled:
            marketplaces[p.marketplace] = {
                "source": {"source": "directory", "path": str(p.path)}
            }

    hooks = _build_hooks_fragment()

    frag = {
        "enabledPlugins": enabled_plugins,
        "extraKnownMarketplaces": marketplaces,
        "env": {SEED_ENV_KEY: str(SEED_DIR)},
        "hooks": hooks,
    }
    sl = config.load_statusline()
    if sl:
        frag["statusLine"] = {"type": "command", "command": sl["command"]}
    return frag


def _build_hooks_fragment() -> dict:
    """hooks-фрагмент из [[hooks]] config.toml: {event: [{hooks:[{type:command,command}]}]}.

    [[hooks]] запись: path (rel к репо, *.sh) + events (список имён событий CC).
    Команда — `bash "<~/.claude/hooks/basename>"` (файл туда симлинкает install-слой).
    Группируем по событию.
    """
    warnings: list[str] = []
    base = config._load_doc(config.CONFIG, warnings)
    by_event: dict[str, list[dict]] = {}
    for entry in base.get("hooks", []):
        path = (entry.get("path") or "").strip()
        events = entry.get("events") or []
        if not path or not events:
            continue
        name = Path(path).name
        cmd = f'bash "{CLAUDE_DIR / "hooks" / name}"'
        for ev in events:
            by_event.setdefault(ev, []).append(
                {"hooks": [{"type": "command", "command": cmd, "timeout": 5}]})
    return by_event


# --- merge --------------------------------------------------------------------

def _read_settings() -> dict:
    if not SETTINGS.is_file():
        return {}
    try:
        return json.loads(SETTINGS.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _hook_cmd_signatures(hooks_frag: dict) -> list[str]:
    """Список command-строк наших hook-записей (для трекинга/пруна в sidecar)."""
    sigs: list[str] = []
    for groups in hooks_frag.values():
        for g in groups:
            for h in g.get("hooks", []):
                if h.get("command"):
                    sigs.append(h["command"])
    return sigs


def merge_into_settings(plugins: list[config.Plugin], *, dry_run: bool = False) -> int:
    """Слить managed-фрагмент в ~/.claude/settings.json по sidecar. Возвращает errors.

    Алгоритм:
      1. frag = build_fragment; prev = sidecar.
      2. Для dict-разделов (enabledPlugins/extraKnownMarketplaces/mcpServers/env):
         выставить наши под-ключи; удалить prev-наши, которых больше нет (stale);
         чужие не трогать.
      3. hooks: добавить наши записи; удалить prev-наши (по command-сигнатуре);
         чужие записи (другой command) не трогать.
      4. Бэкап settings.json.bak; запись; обновление sidecar.
    """
    frag = build_fragment(plugins)
    prev = _load_sidecar()
    cur = _read_settings()

    changes: list[str] = []

    # --- dict-разделы (значение по ключу) ---
    def merge_dict(section: str, values: dict, prev_keys: list[str]) -> list[str]:
        node = cur.get(section)
        if not isinstance(node, dict):
            node = {}
        # удалить наши stale-ключи
        for k in prev_keys:
            if k not in values and k in node:
                del node[k]
                changes.append(f"-{section}[{k}]")
        # выставить текущие
        for k, v in values.items():
            if node.get(k) != v:
                node[k] = v
                changes.append(f"~{section}[{k}]")
        if node:
            cur[section] = node
        elif section in cur:
            del cur[section]
        return sorted(values.keys())

    new_sidecar = _empty_sidecar()
    new_sidecar["enabledPlugins"] = merge_dict(
        "enabledPlugins", frag["enabledPlugins"], prev["enabledPlugins"])
    new_sidecar["extraKnownMarketplaces"] = merge_dict(
        "extraKnownMarketplaces", frag["extraKnownMarketplaces"], prev["extraKnownMarketplaces"])
    # Legacy: ранее MCP ошибочно писались в settings.json mcpServers (CC их не читает).
    # frag без mcpServers → merge_dict с {} вычистит наши прошлые settings-записи.
    new_sidecar["mcpServers"] = merge_dict("mcpServers", {}, prev["mcpServers"])

    # --- env (вложенный dict, единственный наш ключ — SEED_DIR) ---
    env_node = cur.get("env") if isinstance(cur.get("env"), dict) else {}
    for k in prev["env"]:
        if k not in frag["env"] and k in env_node:
            del env_node[k]
            changes.append(f"-env[{k}]")
    for k, v in frag["env"].items():
        if env_node.get(k) != v:
            env_node[k] = v
            changes.append(f"~env[{k}]")
    if env_node:
        cur["env"] = env_node
    new_sidecar["env"] = sorted(frag["env"].keys())

    # --- statusLine (целиком наш top-level ключ, если объявлен [statusline]) ---
    want_sl = frag.get("statusLine")
    if want_sl is not None:
        if cur.get("statusLine") != want_sl:
            cur["statusLine"] = want_sl
            changes.append("~statusLine")
        new_sidecar["statusLine"] = True
    elif prev["statusLine"] and "statusLine" in cur:
        # Раньше владели, теперь [statusline] убран → снять наш statusLine.
        del cur["statusLine"]
        changes.append("-statusLine")
        new_sidecar["statusLine"] = False

    # --- hooks (event → list of matcher-groups) ---
    new_sigs = _hook_cmd_signatures(frag["hooks"])
    prev_sigs = set(prev["hookCommands"])
    hooks_node = cur.get("hooks") if isinstance(cur.get("hooks"), dict) else {}
    # удалить наши прошлые записи (по command-сигнатуре)
    for ev in list(hooks_node.keys()):
        kept = []
        for g in hooks_node[ev]:
            g_sigs = {h.get("command") for h in g.get("hooks", [])}
            is_ours_stale = bool(g_sigs & prev_sigs) and not (g_sigs & set(new_sigs))
            if is_ours_stale:
                changes.append(f"-hooks[{ev}]")
                continue
            kept.append(g)
        if kept:
            hooks_node[ev] = kept
        else:
            del hooks_node[ev]
    # добавить текущие наши (если ещё нет с такой командой в этом событии)
    for ev, groups in frag["hooks"].items():
        existing = hooks_node.get(ev, [])
        existing_sigs = {h.get("command") for g in existing for h in g.get("hooks", [])}
        for g in groups:
            g_sigs = {h.get("command") for h in g.get("hooks", [])}
            if not (g_sigs & existing_sigs):
                existing.append(g)
                changes.append(f"+hooks[{ev}]")
        if existing:
            hooks_node[ev] = existing
    if hooks_node:
        cur["hooks"] = hooks_node
    elif "hooks" in cur:
        del cur["hooks"]
    new_sidecar["hookCommands"] = new_sigs

    # --- user-scope MCP → ~/.claude.json (CC не читает MCP из settings.json) ---
    from . import claudejson
    mcp_errors, mcp_owned = claudejson.merge_mcp(prev["claudeJsonMcpServers"], dry_run=dry_run)
    new_sidecar["claudeJsonMcpServers"] = mcp_owned

    # Sidecar мог устареть, даже если в settings нечего менять: напр. CC сам записал
    # enabledPlugins/extraKnownMarketplaces при `plugin install` (значения совпали с
    # фрагментом → changes пуст), но владение этими ключами ещё не зафиксировано.
    # Поэтому переписываем sidecar всегда, когда он отличается от целевого набора.
    sidecar_drift = new_sidecar != prev

    # --- вывод/запись ---
    if not changes and not sidecar_drift:
        print("Settings -> без изменений.")
        print()
        return mcp_errors

    print(f"Settings -> {SETTINGS}")
    for c in sorted(set(changes)):
        print(f"  {c}")
    if not changes and sidecar_drift:
        print("  (settings уже актуальны; обновляю sidecar-манифест владения)")
    if dry_run:
        print("  [dry-run] settings.json не изменён")
        print()
        return mcp_errors

    if changes and SETTINGS.is_file():
        SETTINGS.with_suffix(".json.bak").write_text(SETTINGS.read_text())
    if changes:
        SETTINGS.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n")
    SIDECAR.write_text(json.dumps(new_sidecar, indent=2, ensure_ascii=False) + "\n")
    tail = f"записано ({len(set(changes))} изм.), бэкап settings.json.bak, " if changes else ""
    print(f"  {tail}sidecar обновлён.")
    print()
    return mcp_errors


def remove_managed(*, dry_run: bool = False) -> int:
    """Удалить из settings.json всё, что числится в sidecar. Чужое не трогать."""
    prev = _load_sidecar()
    cur = _read_settings()
    removed: list[str] = []

    for section in ("enabledPlugins", "extraKnownMarketplaces", "mcpServers"):
        node = cur.get(section)
        if isinstance(node, dict):
            for k in prev[section]:
                if k in node:
                    del node[k]
                    removed.append(f"{section}[{k}]")
            if not node:
                cur.pop(section, None)

    env_node = cur.get("env")
    if isinstance(env_node, dict):
        for k in prev["env"]:
            if k in env_node:
                del env_node[k]
                removed.append(f"env[{k}]")
        if not env_node:
            cur.pop("env", None)

    if prev.get("statusLine") and "statusLine" in cur:
        del cur["statusLine"]
        removed.append("statusLine")

    sigs = set(prev["hookCommands"])
    hooks_node = cur.get("hooks")
    if isinstance(hooks_node, dict):
        for ev in list(hooks_node.keys()):
            kept = [g for g in hooks_node[ev]
                    if not ({h.get("command") for h in g.get("hooks", [])} & sigs)]
            if len(kept) != len(hooks_node[ev]):
                removed.append(f"hooks[{ev}]")
            if kept:
                hooks_node[ev] = kept
            else:
                del hooks_node[ev]
        if not hooks_node:
            cur.pop("hooks", None)

    # user-scope MCP в ~/.claude.json (отдельный файл; печатает свои строки сам).
    mcp_keys = prev.get("claudeJsonMcpServers", [])

    if not removed and not mcp_keys:
        print("Settings --remove: нечего удалять.")
        return 0

    print("Settings --remove:")
    for r in removed:
        print(f"  -{r}")
    from . import claudejson
    claudejson.remove_mcp(mcp_keys, dry_run=dry_run)
    if dry_run:
        print("  [dry-run] не изменено")
        return 0

    if removed:
        if SETTINGS.is_file():
            SETTINGS.with_suffix(".json.bak").write_text(SETTINGS.read_text())
        SETTINGS.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n")
    if SIDECAR.is_file():
        SIDECAR.unlink()
    print("  sidecar очищен.")
    return 0
