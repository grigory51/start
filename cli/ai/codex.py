"""Global Codex backend for the shared AI catalog."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import tomlkit

from .. import adapters, config, plugins
from ..config import REPO_DIR
from ..install import Ctx, _is_ours, _readlink, ensure_real_dir, link


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def agents_dir() -> Path:
    return Path.home() / ".agents"


def personal_plugins_dir() -> Path:
    """Codex resolves personal marketplace ``./plugins/x`` against ``$HOME``."""
    return Path.home() / "plugins"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".next")
    staged.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(staged, path)


def _managed_link_set(
    ctx: Ctx,
    title: str,
    destination: Path,
    sources: dict[str, Path],
    sidecar: Path,
) -> None:
    ctx.say(f"{title} -> {destination}/")
    previous = _read_json(sidecar, {"names": []})
    previous_names = set(previous.get("names", [])) if isinstance(previous, dict) else set()
    if not ctx.dry_run:
        destination.mkdir(parents=True, exist_ok=True)

    for name in sorted(previous_names - set(sources)):
        target = destination / name
        if target.is_symlink() and _is_ours(_readlink(target)):
            ctx.say(f"  - {name}")
            ctx.do(f"rm {target}", target.unlink)

    changed = 0
    for name, source in sorted(sources.items()):
        if ctx.dry_run and not source.exists():
            ctx.say(f"  [dry-run] ln -sfn {source} {destination / name}")
            status = "linked"
        else:
            status = link(ctx, source, destination / name, quiet=True)
        changed += status == "linked"
    ctx.say(f"  Итого: {len(sources)}, изменено {changed}.")
    if not ctx.dry_run:
        _write_json(sidecar, {"names": sorted(sources)})
    ctx.say()


def install_skills(ctx: Ctx) -> None:
    cfg = config.load()
    for warning in cfg.warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    sources: dict[str, Path] = {}
    for skill in cfg.enabled_skills:
        if not adapters.supports(skill, "codex"):
            continue
        try:
            if ctx.dry_run:
                adapters.normalized_skill_text(skill.path / "SKILL.md")
                sources[skill.name] = (
                    adapters.data_dir() / "generated" / "codex" / "skills" / skill.name
                )
            else:
                sources[skill.name] = adapters.codex_skill(skill)
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {skill.name}: Codex adapter: {exc}")
            ctx.errors += 1
    _managed_link_set(
        ctx,
        "Навыки Codex",
        agents_dir() / "skills",
        sources,
        agents_dir() / ".start-skills-managed.json",
    )


def install_agents(ctx: Ctx, plugin_list: list[config.Plugin]) -> None:
    agents, warnings = config._discover_agents()
    for warning in warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    sources: dict[str, Path] = {}
    catalog_count = 0
    for agent in agents:
        if not adapters.supports(agent, "codex"):
            continue
        try:
            if ctx.dry_run:
                adapters.render_codex_agent(agent.path)
                sources[f"{agent.name}.toml"] = (
                    adapters.data_dir()
                    / "generated"
                    / "codex"
                    / "agents"
                    / f"{agent.name}.toml"
                )
            else:
                sources[f"{agent.name}.toml"] = adapters.codex_agent(agent)
            catalog_count += 1
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {agent.name}: Codex adapter: {exc}")
            ctx.errors += 1

    companion_count = 0
    for plugin in plugin_list:
        if not plugin.enabled or not adapters.supports(plugin, "codex"):
            continue
        try:
            source_root = (
                plugin.platform_paths.get("claude") or plugin.platform_paths.get("codex")
            )
            agent_sources = []
            if source_root and (source_root / "agents").is_dir():
                agent_sources = sorted((source_root / "agents").glob("*.md"))
                agent_sources += sorted((source_root / "agents").glob("*.toml"))
            for raw_source in agent_sources:
                if ctx.dry_run:
                    adapters.render_codex_agent(raw_source)
                    source = (
                        adapters.data_dir()
                        / "generated"
                        / "codex"
                        / "agents"
                        / f"{raw_source.stem}.toml"
                    )
                else:
                    source = adapters.materialize_agent(raw_source, "codex", raw_source.stem)
                if source.name not in sources:
                    sources[source.name] = source
                    companion_count += 1
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {plugin.plugin}: companion agents: {exc}")
            ctx.errors += 1

    _managed_link_set(
        ctx,
        f"Агенты Codex ({catalog_count} catalog + {companion_count} plugin companions)",
        codex_dir() / "agents",
        sources,
        codex_dir() / ".start-agents-managed.json",
    )


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    return text[end + 4:].lstrip("\n") if end >= 0 else text


def install_instructions(ctx: Ctx) -> None:
    global_source = REPO_DIR / "ai" / "instructions" / "global.md"
    rules_source = REPO_DIR / "ai" / "rules" / "my-principles.md"
    if not global_source.is_file():
        return
    text = global_source.read_text().rstrip()
    if rules_source.is_file():
        text += "\n\n" + _strip_frontmatter(rules_source.read_text()).rstrip()
    generated = adapters.data_dir() / "generated" / "codex" / "AGENTS.md"
    if not ctx.dry_run:
        generated.parent.mkdir(parents=True, exist_ok=True)
        staged = generated.with_suffix(".md.next")
        staged.write_text(text + "\n")
        os.replace(staged, generated)
    ctx.say(f"AGENTS.md -> {codex_dir() / 'AGENTS.md'}")
    if not ctx.dry_run:
        codex_dir().mkdir(parents=True, exist_ok=True)
    if ctx.dry_run:
        ctx.say(f"  [dry-run] ln -sfn {generated} {codex_dir() / 'AGENTS.md'}")
    else:
        link(ctx, generated, codex_dir() / "AGENTS.md")
    ctx.say()


def _codex_mcp(server: config.McpServer) -> dict:
    value = dict(server.server or {})
    if "headers" in value:
        value["http_headers"] = value.pop("headers")
    return value


def merge_config(ctx: Ctx) -> None:
    """Merge owned MCP and native HUD fields into ~/.codex/config.toml."""
    target = codex_dir() / "config.toml"
    sidecar = codex_dir() / ".start-config-managed.json"
    previous = _read_json(sidecar, {"mcp": [], "status_line": False})
    try:
        doc = tomlkit.parse(target.read_text()) if target.is_file() else tomlkit.document()
    except Exception as exc:
        ctx.say(f"  ! Codex config не разобран: {exc}")
        ctx.errors += 1
        return

    changes: list[str] = []
    mcp_table = doc.get("mcp_servers")
    if mcp_table is None:
        mcp_table = tomlkit.table()
        doc["mcp_servers"] = mcp_table
    servers, warnings = config.load_mcp()
    for warning in warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    wanted = {
        server.name: _codex_mcp(server)
        for server in servers
        if server.enabled and adapters.supports(server, "codex")
    }
    for name in previous.get("mcp", []):
        if name not in wanted and name in mcp_table:
            del mcp_table[name]
            changes.append(f"-mcp_servers.{name}")
    for name, value in wanted.items():
        if mcp_table.get(name) != value:
            mcp_table[name] = value
            changes.append(f"~mcp_servers.{name}")

    status = config.load_statusline("codex")
    tui = doc.get("tui")
    if tui is None:
        tui = tomlkit.table()
        doc["tui"] = tui
    if status:
        if list(tui.get("status_line", [])) != status["items"]:
            tui["status_line"] = status["items"]
            changes.append("~tui.status_line")
        if ("status_line_use_colors" not in tui
                or bool(tui.get("status_line_use_colors")) != status["use_colors"]):
            tui["status_line_use_colors"] = status["use_colors"]
            changes.append("~tui.status_line_use_colors")
    elif previous.get("status_line"):
        for key in ("status_line", "status_line_use_colors"):
            if key in tui:
                del tui[key]
                changes.append(f"-tui.{key}")

    desired_sidecar = {"mcp": sorted(wanted), "status_line": bool(status)}
    drift = desired_sidecar != previous
    if not changes and not drift:
        ctx.say("Codex config -> без изменений.")
        ctx.say()
        return
    ctx.say(f"Codex config -> {target}")
    for change in changes:
        ctx.say(f"  {change}")
    if ctx.dry_run:
        ctx.say("  [dry-run] config.toml не изменён")
        ctx.say()
        return
    codex_dir().mkdir(parents=True, exist_ok=True)
    if changes and target.is_file():
        target.with_suffix(".toml.bak").write_text(target.read_text())
    if changes:
        staged = target.with_suffix(".toml.next")
        staged.write_text(tomlkit.dumps(doc))
        os.replace(staged, target)
    _write_json(sidecar, desired_sidecar)
    ctx.say("  sidecar обновлён.")
    ctx.say()


_CODEX_HOOK_EVENTS = {
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "UserPromptSubmit",
    "SubagentStop",
    "Stop",
}


def install_hooks(ctx: Ctx) -> None:
    hook_dir = codex_dir() / "hooks"
    entries, warnings = config.load_hooks("codex")
    for warning in warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    sources: dict[str, Path] = {}
    fragment: dict[str, list[dict]] = {}
    for entry in entries:
        source = (REPO_DIR / entry["path"]).resolve()
        sources[source.name] = source
        for event in entry["events"]:
            if event not in _CODEX_HOOK_EVENTS:
                continue
            command = (
                f"START_AI_PLATFORM=codex START_AI_EVENT={event} "
                f'bash "{hook_dir / source.name}"'
            )
            fragment.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
            )
    _managed_link_set(
        ctx,
        "Hooks Codex",
        hook_dir,
        sources,
        codex_dir() / ".start-hooks-files-managed.json",
    )

    target = codex_dir() / "hooks.json"
    sidecar = codex_dir() / ".start-hooks-managed.json"
    previous = _read_json(sidecar, {"commands": []})
    current = _read_json(target, {})
    hooks_node = current.get("hooks") if isinstance(current.get("hooks"), dict) else {}
    old_commands = set(previous.get("commands", []))
    for event in list(hooks_node):
        kept = []
        for group in hooks_node[event]:
            commands = {
                hook.get("command")
                for hook in group.get("hooks", [])
                if isinstance(hook, dict)
            }
            if commands & old_commands:
                continue
            kept.append(group)
        if kept:
            hooks_node[event] = kept
        else:
            del hooks_node[event]
    new_commands: list[str] = []
    for event, groups in fragment.items():
        hooks_node.setdefault(event, []).extend(groups)
        for group in groups:
            new_commands.extend(hook["command"] for hook in group["hooks"])
    if hooks_node:
        current["hooks"] = hooks_node
    else:
        current.pop("hooks", None)
    if ctx.dry_run:
        ctx.say(f"Codex hooks config -> {target} [dry-run]")
        ctx.say()
        return
    _write_json(target, current)
    _write_json(sidecar, {"commands": sorted(new_commands)})


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode())
        try:
            digest.update(item.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def _installed_plugins() -> set[str]:
    proc = subprocess.run(
        ["codex", "plugin", "list", "--json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        return set()
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return set()
    return {
        str(item.get("pluginId"))
        for item in data.get("installed", [])
        if item.get("installed")
    }


def install_plugins(ctx: Ctx, plugin_list: list[config.Plugin]) -> None:
    marketplace_root = agents_dir() / "plugins"
    marketplace = marketplace_root / "marketplace.json"
    plugin_root = personal_plugins_dir()
    legacy_wrong_root = marketplace_root / "plugins"
    sidecar = marketplace_root / ".start-managed.json"
    previous = _read_json(sidecar, {"plugins": {}, "names": []})
    previous_hashes = previous.get("plugins", {}) if isinstance(previous, dict) else {}
    previous_names = set(previous.get("names", [])) if isinstance(previous, dict) else set()
    current = _read_json(
        marketplace,
        {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []},
    )
    if current.get("name") != "personal":
        ctx.say(f"  ! {marketplace}: marketplace name должен быть personal")
        ctx.errors += 1
        return

    foreign = [
        entry
        for entry in current.get("plugins", [])
        if str(entry.get("name") or "") not in previous_names
    ]
    foreign_names = {str(entry.get("name") or "") for entry in foreign}
    managed_entries: list[dict] = []
    hashes: dict[str, str] = {}
    paths: dict[str, Path] = {}

    for plugin in plugin_list:
        if not plugin.enabled or not adapters.supports(plugin, "codex"):
            continue
        try:
            if "codex" in plugin.native_platforms:
                source = plugin.platform_paths["codex"]
            elif ctx.dry_run:
                source_root = plugin.platform_paths.get("claude", plugin.path)
                for skill in adapters._skill_roots(source_root, "claude"):
                    adapters.normalized_skill_text(skill / "SKILL.md")
                source = (
                    adapters.data_dir()
                    / "generated"
                    / "codex"
                    / "plugins"
                    / plugin.plugin
                )
            else:
                source = adapters.codex_plugin(plugin)
            if "codex" not in plugin.native_platforms and not ctx.dry_run:
                errors = adapters.validate_generated_plugin(source)
                if errors:
                    raise adapters.AdapterError("; ".join(errors))
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {plugin.plugin}: Codex plugin adapter: {exc}")
            ctx.errors += 1
            continue
        if plugin.plugin in foreign_names:
            ctx.say(f"  ! personal marketplace уже содержит чужой plugin '{plugin.plugin}'")
            ctx.errors += 1
            continue
        paths[plugin.plugin] = source
        hashes[plugin.plugin] = (
            "dry-run" if ctx.dry_run else _tree_hash(source)
        )
        managed_entries.append(
            {
                "name": plugin.plugin,
                "source": {"source": "local", "path": f"./plugins/{plugin.plugin}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
        )
        plugins.check_requirements(ctx, plugin.plugin, plugin.requirements)

    ctx.say(f"Плагины Codex -> {marketplace}")
    if not ctx.dry_run:
        plugin_root.mkdir(parents=True, exist_ok=True)
    # Migration from the initial adapter implementation, which interpreted the
    # marketplace-relative path against ~/.agents/plugins instead of $HOME.
    for name in sorted(previous_names):
        stale = legacy_wrong_root / name
        if stale.is_symlink() and _is_ours(_readlink(stale)):
            ctx.say(f"  ~ {name}: переношу managed symlink в {plugin_root}")
            ctx.do(f"rm {stale}", stale.unlink)
    for name in sorted(previous_names - set(paths)):
        stale = plugin_root / name
        if stale.is_symlink() and _is_ours(_readlink(stale)):
            ctx.do(f"rm {stale}", stale.unlink)
    for name, source in sorted(paths.items()):
        if ctx.dry_run and not source.exists():
            ctx.say(f"  [dry-run] ln -sfn {source} {plugin_root / name}")
        else:
            link(ctx, source, plugin_root / name, quiet=True)

    desired = dict(current)
    desired["plugins"] = foreign + managed_entries
    if ctx.dry_run:
        for name in sorted(paths):
            ctx.say(f"  [dry-run] codex plugin add {name}@personal")
        ctx.say()
        return
    _write_json(marketplace, desired)

    installed = _installed_plugins()
    for name in sorted(previous_names - set(paths)):
        ref = f"{name}@personal"
        if ref in installed:
            proc = adapters.run_command(["codex", "plugin", "remove", ref, "--json"])
            if proc.returncode:
                ctx.say(f"  ! remove {ref}: {(proc.stderr or proc.stdout).strip()}")
                ctx.errors += 1
    for name in sorted(paths):
        ref = f"{name}@personal"
        changed = previous_hashes.get(name) != hashes[name]
        if ref in installed and changed:
            adapters.run_command(["codex", "plugin", "remove", ref, "--json"])
            installed.discard(ref)
        if ref not in installed:
            proc = adapters.run_command(["codex", "plugin", "add", ref, "--json"])
            if proc.returncode:
                ctx.say(f"  ! install {ref}: {(proc.stderr or proc.stdout).strip()}")
                ctx.errors += 1
            else:
                ctx.say(f"  + {ref}")
    _write_json(sidecar, {"names": sorted(paths), "plugins": hashes})
    ctx.say()


def install_codex(ctx: Ctx) -> None:
    plugin_list, warnings = config._discover_plugins()
    for warning in warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    install_plugins(ctx, plugin_list)
    install_skills(ctx)
    install_agents(ctx, plugin_list)
    install_hooks(ctx)
    install_instructions(ctx)
    merge_config(ctx)
