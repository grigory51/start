"""Global Codex backend for the shared AI catalog."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import tomlkit

from .. import adapters, config, plugins
from ..config import REPO_DIR
from ..install import Ctx, _is_ours, _readlink, link


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
            sources[skill.name] = adapters.codex_skill(skill, dry_run=ctx.dry_run)
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {skill.name}: Codex adapter: {exc}")
            ctx.errors += 1
    _managed_link_set(
        ctx,
        "Навыки",
        agents_dir() / "skills",
        sources,
        agents_dir() / ".start-skills-managed.json",
    )


def install_agents(
    ctx: Ctx,
    plugin_list: list[config.Plugin],
) -> dict[str, dict[str, str]]:
    agents, warnings = config._discover_agents()
    for warning in warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    sources: dict[str, tuple[Path, str]] = {}
    catalog_count = 0
    skills = config.load().skills
    for agent in agents:
        if not adapters.supports(agent, "codex"):
            continue
        for warning in adapters.agent_skill_warnings(
            agent, "codex", skills, plugin_list
        ):
            ctx.say(f"  ! {warning}")
        try:
            if ctx.dry_run:
                adapters.render_codex_agent(agent.path)
                source = (
                    adapters.data_dir()
                    / "generated"
                    / "codex"
                    / "agents"
                    / f"{agent.name}.toml"
                )
            else:
                source = adapters.codex_agent(agent)
            sources[agent.name] = (source, agent.description)
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
                rendered = adapters.render_codex_agent(raw_source)
                description = str(tomlkit.parse(rendered).get("description") or "")
                if ctx.dry_run:
                    source = (
                        adapters.data_dir()
                        / "generated"
                        / "codex"
                        / "agents"
                        / f"{raw_source.stem}.toml"
                    )
                else:
                    source = adapters.materialize_agent(
                        raw_source,
                        "codex",
                        raw_source.stem,
                    )
                if raw_source.stem not in sources:
                    sources[raw_source.stem] = (source, description)
                    companion_count += 1
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {plugin.plugin}: companion agents: {exc}")
            ctx.errors += 1

    ctx.say(
        f"Агенты ({catalog_count} catalog + {companion_count} plugin companions) "
        f"-> {codex_dir() / 'config.toml'}"
    )
    ctx.say(f"  Итого: {len(sources)}.")
    ctx.say()
    return {
        name: {"description": description, "config_file": str(source)}
        for name, (source, description) in sources.items()
    }


def remove_legacy_agent_links(ctx: Ctx) -> None:
    legacy_dir = codex_dir() / "agents"
    sidecar = codex_dir() / ".start-agents-managed.json"
    previous = _read_json(sidecar, {"names": []})
    removed = 0
    for filename in previous.get("names", []):
        target = legacy_dir / filename
        if target.is_symlink() and _is_ours(_readlink(target)):
            ctx.do(f"rm {target}", target.unlink)
            removed += 1
    if not ctx.dry_run:
        _write_json(sidecar, {"names": []})
    if removed:
        ctx.say(f"  - legacy symlink агентов: {removed}")


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


def _disabled_plugin_skills(plugin_overrides: dict[str, bool]) -> dict[str, bool]:
    """Expand disabled plugins to native Codex skill overrides."""
    overrides: dict[str, bool] = {}
    for ref, enabled in plugin_overrides.items():
        if enabled:
            continue
        plugin, separator, marketplace = ref.rpartition("@")
        if not separator:
            continue
        cache = codex_dir() / "plugins" / "cache" / marketplace / plugin
        for path in sorted(cache.glob("*/skills/**/SKILL.md")):
            overrides[str(path)] = False
    return overrides


def merge_config(
    ctx: Ctx,
    disabled_plugin_refs: set[str] | None = None,
    agent_configs: dict[str, dict[str, str]] | None = None,
) -> None:
    """Merge owned MCP, agent, plugin, skill, feature and HUD fields."""
    target = codex_dir() / "config.toml"
    sidecar = codex_dir() / ".start-config-managed.json"
    previous = _read_json(
        sidecar,
        {
            "mcp": [],
            "plugin_overrides": {},
            "skill_overrides": {},
            "agents": {},
            "features": [],
            "status_line": False,
        },
    )
    try:
        doc = tomlkit.parse(target.read_text()) if target.is_file() else tomlkit.document()
    except Exception as exc:
        ctx.say(f"  ! Codex config не разобран: {exc}")
        ctx.errors += 1
        return

    mcp_changes: list[str] = []
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
            mcp_changes.append(f"- {name}")
    for name, value in wanted.items():
        if mcp_table.get(name) != value:
            mcp_table[name] = value
            mcp_changes.append(f"~ {name}")

    config_changes: list[str] = []
    plugin_table = doc.get("plugins")
    for ref in sorted(disabled_plugin_refs or set()):
        if plugin_table is not None and ref in plugin_table:
            del plugin_table[ref]
            config_changes.append(f"-plugins.{ref}")
        hooks_table = doc.get("hooks")
        hook_state = hooks_table.get("state") if hooks_table is not None else None
        if hook_state is not None:
            for key in list(hook_state):
                if str(key).startswith(ref + ":"):
                    del hook_state[key]
                    config_changes.append(f"-hooks.state.{key}")

    wanted_plugin_overrides = config.load_codex_flags("plugins")
    raw_originals = previous.get("plugin_overrides", {})
    original_plugin_states = (
        dict(raw_originals) if isinstance(raw_originals, dict) else {}
    )
    for ref in list(original_plugin_states):
        if ref in wanted_plugin_overrides:
            continue
        entry = plugin_table.get(ref) if plugin_table is not None else None
        original = original_plugin_states.pop(ref)
        if entry is not None and isinstance(original, bool):
            if entry.get("enabled") != original:
                entry["enabled"] = original
                config_changes.append(f"~plugins.{ref}.enabled")
        elif entry is not None and "enabled" in entry:
            del entry["enabled"]
            if len(entry) == 0:
                del plugin_table[ref]
            config_changes.append(f"-plugins.{ref}.enabled")

    for ref, enabled in wanted_plugin_overrides.items():
        if plugin_table is None:
            plugin_table = tomlkit.table()
            doc["plugins"] = plugin_table
        entry = plugin_table.get(ref)
        if entry is None:
            entry = tomlkit.table()
            plugin_table[ref] = entry
        if ref not in original_plugin_states:
            current = entry.get("enabled")
            original_plugin_states[ref] = current if isinstance(current, bool) else None
        if entry.get("enabled") != enabled:
            entry["enabled"] = enabled
            config_changes.append(f"~plugins.{ref}.enabled")

    wanted_skill_overrides = {
        str(codex_dir() / "skills" / name / "SKILL.md"): enabled
        for name, enabled in config.load_codex_flags("skills").items()
    }
    wanted_skill_overrides.update(_disabled_plugin_skills(wanted_plugin_overrides))
    raw_skill_originals = previous.get("skill_overrides", {})
    original_skill_states: dict[str, bool | None] = {}
    if isinstance(raw_skill_originals, dict):
        for path, state in raw_skill_originals.items():
            if not Path(path).is_absolute():
                path = str(codex_dir() / "skills" / path / "SKILL.md")
            original_skill_states[path] = state if isinstance(state, bool) else None
    skills_table = doc.get("skills")
    skill_entries = skills_table.get("config") if skills_table is not None else None
    for path in list(original_skill_states):
        if path in wanted_skill_overrides:
            continue
        entry = next(
            (item for item in skill_entries or [] if str(item.get("path")) == path),
            None,
        )
        original = original_skill_states.pop(path)
        if entry is not None and isinstance(original, bool):
            if entry.get("enabled") != original:
                entry["enabled"] = original
                config_changes.append(f"~skills.{Path(path).parent.name}.enabled")
        elif entry is not None:
            skill_entries.remove(entry)
            config_changes.append(f"-skills.{Path(path).parent.name}")

    for path, enabled in wanted_skill_overrides.items():
        if skills_table is None:
            skills_table = tomlkit.table()
            doc["skills"] = skills_table
        if skill_entries is None:
            skill_entries = tomlkit.aot()
            skills_table["config"] = skill_entries
        entry = next(
            (item for item in skill_entries if str(item.get("path")) == path),
            None,
        )
        if entry is None:
            entry = tomlkit.table()
            entry["path"] = path
            skill_entries.append(entry)
        if path not in original_skill_states:
            current = entry.get("enabled")
            original_skill_states[path] = current if isinstance(current, bool) else None
        if entry.get("enabled") != enabled:
            entry["enabled"] = enabled
            config_changes.append(f"~skills.{Path(path).parent.name}.enabled")

    wanted_agents = agent_configs or {}
    agents_table = doc.get("agents")
    if agents_table is None:
        agents_table = tomlkit.table()
        doc["agents"] = agents_table
    raw_agent_originals = previous.get("agents", {})
    original_agents = (
        dict(raw_agent_originals) if isinstance(raw_agent_originals, dict) else {}
    )
    for name in list(original_agents):
        if name in wanted_agents:
            continue
        original = original_agents.pop(name)
        if isinstance(original, dict):
            agents_table[name] = original
        elif name in agents_table:
            del agents_table[name]
        config_changes.append(f"-agents.{name}")
    for name, value in wanted_agents.items():
        current = agents_table.get(name)
        if name not in original_agents:
            original_agents[name] = (
                current.unwrap() if hasattr(current, "unwrap") else None
            )
        current_value = current.unwrap() if hasattr(current, "unwrap") else current
        if current_value != value:
            agents_table[name] = value
            config_changes.append(f"~agents.{name}")

    features_table = doc.get("features")
    if features_table is None:
        features_table = tomlkit.table()
        doc["features"] = features_table
    wanted_features = config.load_codex_flags("features")
    for name in previous.get("features", []):
        if name not in wanted_features and name in features_table:
            del features_table[name]
            config_changes.append(f"-features.{name}")
    for name, value in wanted_features.items():
        if features_table.get(name) != value:
            features_table[name] = value
            config_changes.append(f"~features.{name}")

    status = config.load_statusline("codex")
    tui = doc.get("tui")
    if tui is None:
        tui = tomlkit.table()
        doc["tui"] = tui
    if status:
        if list(tui.get("status_line", [])) != status["items"]:
            tui["status_line"] = status["items"]
            config_changes.append("~tui.status_line")
        if ("status_line_use_colors" not in tui
                or bool(tui.get("status_line_use_colors")) != status["use_colors"]):
            tui["status_line_use_colors"] = status["use_colors"]
            config_changes.append("~tui.status_line_use_colors")
    elif previous.get("status_line"):
        for key in ("status_line", "status_line_use_colors"):
            if key in tui:
                del tui[key]
                config_changes.append(f"-tui.{key}")

    desired_sidecar = {
        "mcp": sorted(wanted),
        "plugin_overrides": original_plugin_states,
        "skill_overrides": original_skill_states,
        "agents": original_agents,
        "features": sorted(wanted_features),
        "status_line": bool(status),
    }
    drift = desired_sidecar != previous
    changes = mcp_changes + config_changes
    ctx.say(f"MCP -> {target}")
    for change in mcp_changes:
        ctx.say(f"  {change}")
    ctx.say(f"  Итого: {len(wanted)}, изменено {len(mcp_changes)}.")
    ctx.say()
    if config_changes:
        ctx.say(f"Config -> {target}")
        for change in config_changes:
            ctx.say(f"  {change}")
    else:
        ctx.say("Config -> без изменений.")
    if not changes and not drift:
        ctx.say()
        return
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
        "Hooks",
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
        ctx.say(f"Hooks config -> {target} [dry-run]")
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


def _run_plugin_command(
    action: Literal["add", "remove"],
    ref: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["codex", "plugin", action, ref, "--json"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )


def install_plugins(ctx: Ctx, plugin_list: list[config.Plugin]) -> set[str]:
    marketplace_root = agents_dir() / "plugins"
    marketplace = marketplace_root / "marketplace.json"
    plugin_root = personal_plugins_dir()
    legacy_wrong_root = marketplace_root / "plugins"
    sidecar = marketplace_root / ".start-managed.json"
    previous = _read_json(sidecar, {"plugins": {}, "names": []})
    previous_hashes = previous.get("plugins", {}) if isinstance(previous, dict) else {}
    previous_names = set(previous.get("names", [])) if isinstance(previous, dict) else set()
    disabled_names = {
        plugin.plugin
        for plugin in plugin_list
        if not plugin.enabled and adapters.supports(plugin, "codex")
    }
    catalog_names = {
        plugin.plugin
        for plugin in plugin_list
        if adapters.supports(plugin, "codex")
    }
    stale_names = previous_names | disabled_names
    current = _read_json(
        marketplace,
        {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []},
    )
    if current.get("name") != "personal":
        ctx.say(f"  ! {marketplace}: marketplace name должен быть personal")
        ctx.errors += 1
        return set()

    foreign = [
        entry
        for entry in current.get("plugins", [])
        if str(entry.get("name") or "") not in previous_names | catalog_names
    ]
    foreign_names = {str(entry.get("name") or "") for entry in foreign}
    managed_entries: list[dict] = []
    hashes: dict[str, str] = {}
    paths: dict[str, Path] = {}
    failed_names: set[str] = set()

    for plugin in plugin_list:
        if not plugin.enabled or not adapters.supports(plugin, "codex"):
            continue
        try:
            source = adapters.codex_plugin(plugin, dry_run=ctx.dry_run)
        except (OSError, adapters.AdapterError) as exc:
            ctx.say(f"  ! {plugin.plugin}: Codex plugin adapter: {exc}")
            ctx.errors += 1
            failed_names.add(plugin.plugin)
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

    stale_names = (previous_names - set(paths) - failed_names) | disabled_names

    ctx.say(f"Плагины -> {marketplace}")
    if not ctx.dry_run:
        plugin_root.mkdir(parents=True, exist_ok=True)
    # Migration from the initial adapter implementation, which interpreted the
    # marketplace-relative path against ~/.agents/plugins instead of $HOME.
    for name in sorted(previous_names):
        stale = legacy_wrong_root / name
        if stale.is_symlink() and _is_ours(_readlink(stale)):
            ctx.say(f"  ~ {name}: переношу managed symlink в {plugin_root}")
            ctx.do(f"rm {stale}", stale.unlink)
    for name, source in sorted(paths.items()):
        if ctx.dry_run and not source.exists():
            ctx.say(f"  [dry-run] ln -sfn {source} {plugin_root / name}")
        else:
            link(ctx, source, plugin_root / name, quiet=True)

    if ctx.dry_run:
        for name in sorted(paths):
            ctx.say(f"  [dry-run] codex plugin add {name}@personal")
        ctx.say()
        return {f"{name}@personal" for name in stale_names}

    installed = _installed_plugins()
    removed_refs: set[str] = set()
    removed_names: set[str] = set()
    for name in sorted(stale_names):
        ref = f"{name}@personal"
        if ref in installed:
            proc = _run_plugin_command("remove", ref)
            if proc.returncode:
                ctx.say(f"  ! remove {ref}: {(proc.stderr or proc.stdout).strip()}")
                ctx.errors += 1
                continue
        removed_refs.add(ref)
        removed_names.add(name)
    retained_entries = [
        entry
        for entry in current.get("plugins", [])
        if str(entry.get("name") or "") in failed_names | (stale_names - removed_names)
    ]
    desired = dict(current)
    desired["plugins"] = foreign + retained_entries + managed_entries
    _write_json(marketplace, desired)
    for name in sorted(removed_names):
        stale = plugin_root / name
        if stale.is_symlink() and _is_ours(_readlink(stale)):
            ctx.do(f"rm {stale}", stale.unlink)
    for name in sorted(paths):
        ref = f"{name}@personal"
        changed = previous_hashes.get(name) != hashes[name]
        if ref in installed and changed:
            proc = _run_plugin_command("remove", ref)
            if proc.returncode:
                ctx.say(f"  ! remove {ref}: {(proc.stderr or proc.stdout).strip()}")
                ctx.errors += 1
                if name in previous_hashes:
                    hashes[name] = previous_hashes[name]
                else:
                    hashes.pop(name)
                continue
            installed.discard(ref)
        if ref not in installed:
            proc = _run_plugin_command("add", ref)
            if proc.returncode:
                ctx.say(f"  ! install {ref}: {(proc.stderr or proc.stdout).strip()}")
                ctx.errors += 1
                if name in previous_hashes:
                    hashes[name] = previous_hashes[name]
                else:
                    hashes.pop(name)
            else:
                ctx.say(f"  + {ref}")
    retained_names = failed_names | (stale_names - removed_names)
    retained_hashes = {
        name: previous_hashes[name]
        for name in retained_names
        if name in previous_hashes
    }
    _write_json(
        sidecar,
        {
            "names": sorted(set(paths) | retained_names),
            "plugins": retained_hashes | hashes,
        },
    )
    ctx.say()
    return removed_refs


def install_codex(ctx: Ctx) -> None:
    plugin_list, warnings = config._discover_plugins()
    for warning in warnings:
        ctx.say(f"  ! {warning}")
        ctx.errors += 1
    disabled_plugin_refs = install_plugins(ctx, plugin_list)
    install_skills(ctx)
    agent_configs = install_agents(ctx, plugin_list)
    install_hooks(ctx)
    install_instructions(ctx)
    config_errors = ctx.errors
    merge_config(ctx, disabled_plugin_refs, agent_configs)
    if ctx.errors == config_errors:
        remove_legacy_agent_links(ctx)
