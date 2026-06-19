"""claudejson.py — merge user-scope MCP в ~/.claude.json.

Claude Code читает MCP-серверы user-scope ТОЛЬКО из ~/.claude.json (top-level
`mcpServers`), а НЕ из settings.json. Менеджер декларирует MCP в config.toml ([[mcp]])
и пишет включённые сюда.

~/.claude.json — большой и критичный файл (oauth, per-project state, кеши). Поэтому:
  - мержим ТОЛЬКО свои под-ключи mcpServers (чужие сервера от `claude mcp add` не трогаем);
  - бэкап *.bak перед записью;
  - атомарная запись (temp в той же папке + os.replace) — файл нельзя оставить обрезанным;
  - при ошибке парсинга НЕ пишем (возвращаем error).

Владение ключами трекается в общем sidecar (см. cli/settings.py, секция
`claudeJsonMcpServers`): stale-ключи (наши прошлые, выпавшие из конфига) удаляются.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config

# ~/.claude.json лежит в $HOME (не в каталоге .claude). Отдельный env-override для тестов —
# CLAUDE_HOME указывает на каталог .claude и здесь НЕ применим.
CLAUDE_JSON = Path(os.environ.get("CLAUDE_AGENTS_CLAUDE_JSON", Path.home() / ".claude.json"))


def _normalize(server: dict) -> dict:
    """Привести inline-спеку к формату CC: stdio по умолчанию для command-серверов."""
    out = dict(server)
    if "type" not in out:
        if out.get("command"):
            out["type"] = "stdio"
        elif out.get("url"):
            out["type"] = "http"
    return out


def build_mcp_fragment() -> dict[str, dict]:
    """Включённые inline-MCP из config.toml → {name: server-spec}."""
    mcp, _ = config.load_mcp()
    return {m.name: _normalize(m.server) for m in mcp if m.enabled and m.server}


def _read() -> tuple[dict, bool]:
    """Прочитать ~/.claude.json. Возвращает (данные, ok). ok=False при ошибке парса."""
    if not CLAUDE_JSON.is_file():
        return {}, True
    try:
        return json.loads(CLAUDE_JSON.read_text()), True
    except (json.JSONDecodeError, OSError):
        return {}, False


def _atomic_write(data: dict) -> None:
    """Атомарная запись: temp в той же папке + os.replace. Бэкап *.bak до замены."""
    if CLAUDE_JSON.is_file():
        CLAUDE_JSON.with_suffix(".json.bak").write_text(CLAUDE_JSON.read_text())
    tmp = CLAUDE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, CLAUDE_JSON)


def merge_mcp(prev_keys: list[str], *, dry_run: bool = False) -> tuple[int, list[str]]:
    """Слить наши MCP в ~/.claude.json mcpServers. Возвращает (errors, owned_keys).

    Только свои под-ключи: добавляет/обновляет из конфига, удаляет наши прошлые (prev_keys),
    выпавшие из конфига. Чужие сервера не трогает.
    """
    frag = build_mcp_fragment()
    owned = sorted(frag.keys())

    data, ok = _read()
    if not ok:
        print("  ! ~/.claude.json не распарсился — MCP-merge пропущен (файл не тронут).")
        return 1, prev_keys  # сохраняем прошлое владение, файл не меняем

    node = data.get("mcpServers")
    if not isinstance(node, dict):
        node = {}

    changes: list[str] = []
    for k in prev_keys:
        if k not in frag and k in node:
            del node[k]
            changes.append(f"-mcp[{k}]")
    for k, v in frag.items():
        if node.get(k) != v:
            node[k] = v
            changes.append(f"~mcp[{k}]")

    if node:
        data["mcpServers"] = node
    elif "mcpServers" in data:
        del data["mcpServers"]

    if not changes:
        return 0, owned

    print(f"MCP -> {CLAUDE_JSON} (user-scope)")
    for c in sorted(set(changes)):
        print(f"  {c}")
    if dry_run:
        print("  [dry-run] ~/.claude.json не изменён")
        return 0, owned

    _atomic_write(data)
    print(f"  записано ({len(set(changes))} изм.), бэкап .claude.json.bak.")
    return 0, owned


def remove_mcp(prev_keys: list[str], *, dry_run: bool = False) -> None:
    """Удалить наши MCP (prev_keys) из ~/.claude.json. Чужие не трогаем."""
    data, ok = _read()
    if not ok:
        return
    node = data.get("mcpServers")
    if not isinstance(node, dict):
        return
    removed = [k for k in prev_keys if k in node]
    for k in removed:
        del node[k]
    if not removed:
        return
    if node:
        data["mcpServers"] = node
    else:
        del data["mcpServers"]
    for k in removed:
        print(f"  -mcp[{k}] (~/.claude.json)")
    if not dry_run:
        _atomic_write(data)
