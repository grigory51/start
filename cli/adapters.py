"""Platform adapters for the shared ``[ai]`` catalog.

Sources remain in their native format.  This module materializes deterministic
runtime representations for the other platform under XDG data, never inside
third-party submodules.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import tomlkit

from . import config

PLATFORMS = ("claude", "codex")
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AdapterError(RuntimeError):
    """A source cannot be represented on the requested platform."""


def data_dir() -> Path:
    root = os.environ.get("XDG_DATA_HOME")
    return Path(root) / "start" if root else Path.home() / ".local" / "share" / "start"


def platform_available(platform: str) -> bool:
    return shutil.which(platform) is not None


def supports(component, platform: str) -> bool:
    return platform in getattr(component, "platforms", PLATFORMS)


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 4:].lstrip("\n")
    values: dict[str, object] = {}
    current_list: str | None = None
    for line in raw.splitlines():
        item = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if item and current_list:
            values.setdefault(current_list, [])
            assert isinstance(values[current_list], list)
            values[current_list].append(_scalar(item.group(1)))
            continue
        field = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", line)
        if not field:
            current_list = None
            continue
        key, raw_value = field.group(1), field.group(2) or ""
        if not raw_value:
            values[key] = []
            current_list = key
        else:
            values[key] = _scalar(raw_value)
            current_list = None
    return values, body


def _scalar(value: str) -> object:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [part.strip().strip("\"'") for part in value[1:-1].split(",") if part.strip()]
    return value


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalized_skill_text(source: Path) -> str:
    """Return a Codex-compatible SKILL.md with only name/description metadata."""
    text = source.read_text(errors="replace")
    meta, body = _split_frontmatter(text)
    name = str(meta.get("name") or source.parent.name).strip()
    description = str(meta.get("description") or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise AdapterError(f"{source}: некорректное имя скила {name!r}")
    if not description:
        raise AdapterError(f"{source}: в frontmatter отсутствует description")
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_quote(description)}\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def _replace_dir(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    old = destination.with_name(destination.name + ".old")
    if old.exists() or old.is_symlink():
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)
        else:
            old.unlink()
    if destination.exists() or destination.is_symlink():
        os.replace(destination, old)
    os.replace(staged, destination)
    if old.exists() or old.is_symlink():
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)
        else:
            old.unlink()


def materialize_skill(source: Path, destination: Path) -> Path:
    """Copy a complete skill and normalize SKILL.md atomically."""
    staged = destination.with_name(destination.name + ".next")
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(source, staged, symlinks=True)
    staged_skill = staged / "SKILL.md"
    if staged_skill.is_symlink():
        staged_skill.unlink()
    staged_skill.write_text(normalized_skill_text(source / "SKILL.md"))
    _replace_dir(staged, destination)
    return destination


def codex_skill(skill: config.Skill) -> Path:
    destination = materialize_skill(
        skill.path,
        data_dir() / "generated" / "codex" / "skills" / skill.name,
    )
    for item in skill.symlinks:
        source = (config.REPO_DIR / item["source"]).resolve()
        target = destination / item["destination"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source)
    return destination


def _agent_metadata(source: Path) -> tuple[dict[str, object], str]:
    if source.suffix == ".toml":
        doc = tomlkit.parse(source.read_text())
        body = str(doc.get("developer_instructions") or "")
        return dict(doc), body
    return _split_frontmatter(source.read_text(errors="replace"))


def render_codex_agent(source: Path) -> str:
    meta, body = _agent_metadata(source)
    name = str(meta.get("name") or source.stem).strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description or not body.strip():
        raise AdapterError(f"{source}: агенту нужны name, description и инструкции")

    instructions = body.rstrip()
    skills = meta.get("skills")
    if isinstance(skills, list) and skills:
        names = ", ".join(str(item) for item in skills)
        instructions += f"\n\nПеред работой используй доступные навыки: {names}."

    tools = str(meta.get("tools") or "")
    is_orchestrator = "Agent(" in tools
    can_write = is_orchestrator or any(token in tools for token in ("Write", "Edit"))

    doc = tomlkit.document()
    doc["name"] = name
    doc["description"] = description
    doc["developer_instructions"] = tomlkit.string(instructions, multiline=True)
    doc["sandbox_mode"] = "workspace-write" if can_write else "read-only"
    return tomlkit.dumps(doc)


def render_claude_agent(source: Path) -> str:
    meta, body = _agent_metadata(source)
    name = str(meta.get("name") or source.stem).strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description or not body.strip():
        raise AdapterError(f"{source}: агенту нужны name, description и инструкции")
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_quote(description)}\n"
        "model: inherit\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def materialize_agent(source: Path, platform: str, name: str | None = None) -> Path:
    name = name or source.stem
    suffix = ".toml" if platform == "codex" else ".md"
    destination = data_dir() / "generated" / platform / "agents" / f"{name}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = render_codex_agent(source) if platform == "codex" else render_claude_agent(source)
    staged = destination.with_suffix(destination.suffix + ".next")
    staged.write_text(text)
    os.replace(staged, destination)
    return destination


def codex_agent(agent: config.Agent) -> Path:
    if agent.path.suffix == ".toml":
        return agent.path
    return materialize_agent(agent.path, "codex", agent.name)


def claude_agent(agent: config.Agent) -> Path:
    if agent.path.suffix == ".md":
        return agent.path
    return materialize_agent(agent.path, "claude", agent.name)


def _plugin_manifest(plugin_root: Path, platform: str) -> dict:
    return config._json_object(plugin_root / f".{platform}-plugin" / "plugin.json")


def _skill_roots(plugin_root: Path, platform: str) -> list[Path]:
    manifest = _plugin_manifest(plugin_root, platform)
    raw = manifest.get("skills")
    candidates: list[Path] = []
    if isinstance(raw, str):
        candidates.append((plugin_root / raw).resolve())
    elif isinstance(raw, list):
        candidates.extend((plugin_root / str(item)).resolve() for item in raw)
    elif (plugin_root / "skills").is_dir():
        candidates.append(plugin_root / "skills")

    out: list[Path] = []
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            out.append(candidate)
        elif candidate.is_dir():
            out.extend(sorted(p for p in candidate.iterdir() if config.is_skill(p)))
    return out


def _copy_optional(source_root: Path, destination: Path, name: str) -> None:
    source = source_root / name
    if not source.exists():
        return
    target = destination / name
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


_HOOK_ADAPTER = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
commands = json.load(open(os.path.join(root, "hooks", "original-commands.json")))
payload = json.load(sys.stdin)
command = commands[int(sys.argv[1])]
env = dict(os.environ, PLUGIN_ROOT=root, CLAUDE_PLUGIN_ROOT=root)
payloads = [payload]
if payload.get("tool_name") == "apply_patch":
    patch = payload.get("tool_input", {}).get("command", "")
    paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch, re.M)
    payloads = []
    for path in paths:
        item = dict(payload)
        item["tool_name"] = "Write"
        item["tool_input"] = {"file_path": os.path.abspath(os.path.join(payload.get("cwd", "."), path))}
        payloads.append(item)
for item in payloads:
    result = subprocess.run(command, shell=True, input=json.dumps(item), text=True, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
'''


def _adapt_hooks(source_root: Path, destination: Path) -> None:
    source = source_root / "hooks" / "hooks.json"
    if not source.is_file():
        return
    data = config._json_object(source)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    commands: list[str] = []
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            if isinstance(matcher, str) and "apply_patch" not in matcher:
                group["matcher"] = matcher + "|apply_patch"
            for hook in group.get("hooks", []):
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                original = str(hook.get("command") or "")
                if not original:
                    continue
                index = len(commands)
                commands.append(original)
                hook["command"] = (
                    f'python3 "${{PLUGIN_ROOT}}/scripts/start-hook-adapter.py" {index}'
                )
    hook_dir = destination / "hooks"
    script_dir = destination / "scripts"
    hook_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "hooks.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    (hook_dir / "original-commands.json").write_text(
        json.dumps(commands, indent=2, ensure_ascii=False) + "\n"
    )
    adapter = script_dir / "start-hook-adapter.py"
    adapter.write_text(_HOOK_ADAPTER)
    adapter.chmod(0o755)


def _command_skill(source: Path, destination: Path) -> None:
    meta, body = _split_frontmatter(source.read_text(errors="replace"))
    description = str(meta.get("description") or f"Run the {source.stem} workflow.")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "SKILL.md").write_text(
        "---\n"
        f"name: {source.stem}\n"
        f"description: {_yaml_quote(description)}\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def generate_codex_plugin(plugin: config.Plugin) -> Path:
    """Generate a valid Codex bundle for a Claude-native plugin."""
    destination = data_dir() / "generated" / "codex" / "plugins" / plugin.plugin
    staged = destination.with_name(destination.name + ".next")
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    source_root = plugin.platform_paths.get("claude", plugin.path)
    source_manifest = _plugin_manifest(source_root, "claude")

    manifest = {
        "name": plugin.plugin,
        "version": str(source_manifest.get("version") or "0.0.0"),
        "description": str(
            source_manifest.get("description") or plugin.description or plugin.plugin
        ),
    }
    for key in ("author", "homepage", "repository", "license", "keywords"):
        if key in source_manifest:
            manifest[key] = source_manifest[key]
    author = manifest.get("author")
    if not isinstance(author, dict) or not str(author.get("name") or "").strip():
        author = {"name": "Upstream plugin author"}
        manifest["author"] = author
    display_name = plugin.plugin.replace("-", " ").title()
    inherited_interface = source_manifest.get("interface")
    if not isinstance(inherited_interface, dict):
        inherited_interface = {}
    description = manifest["description"]
    interface = {
        "displayName": display_name,
        "shortDescription": description[:120],
        "longDescription": description,
        "developerName": str(author["name"]),
        "category": "Productivity",
        "capabilities": [],
        "defaultPrompt": f"Help me use {display_name}.",
        **inherited_interface,
    }
    manifest["interface"] = interface

    skills = _skill_roots(source_root, "claude")
    commands_dir = source_root / "commands"
    if skills or commands_dir.is_dir():
        manifest["skills"] = "./skills/"
        for skill in skills:
            materialize_skill(skill, staged / "skills" / skill.name)
        if commands_dir.is_dir():
            for command in sorted(commands_dir.glob("*.md")):
                _command_skill(command, staged / "skills" / command.stem)

    for name in ("scripts", "assets", "references", ".mcp.json", ".app.json"):
        _copy_optional(source_root, staged, name)
    if (staged / ".mcp.json").is_file():
        manifest["mcpServers"] = "./.mcp.json"
    if (staged / ".app.json").is_file():
        manifest["apps"] = "./.app.json"
    _adapt_hooks(source_root, staged)

    manifest_dir = staged / ".codex-plugin"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    _replace_dir(staged, destination)
    return destination


def codex_plugin(plugin: config.Plugin) -> Path:
    native = plugin.platform_paths.get("codex")
    return native if native else generate_codex_plugin(plugin)


def plugin_agents(plugin: config.Plugin, platform: str) -> list[Path]:
    """Materialize companion agents bundled by a plugin."""
    source_root = plugin.platform_paths.get("claude") or plugin.platform_paths.get("codex")
    if not source_root:
        return []
    agents_dir = source_root / "agents"
    if not agents_dir.is_dir():
        return []
    out: list[Path] = []
    patterns = ("*.md", "*.toml")
    for pattern in patterns:
        for source in sorted(agents_dir.glob(pattern)):
            out.append(materialize_agent(source, platform, source.stem))
    return out


def validate_generated_plugin(path: Path) -> list[str]:
    errors: list[str] = []
    manifest = config._json_object(path / ".codex-plugin" / "plugin.json")
    name = str(manifest.get("name") or "")
    if not _NAME_RE.fullmatch(name) or path.name != name:
        errors.append("plugin name должен совпадать с папкой и быть в hyphen-case")
    if not str(manifest.get("description") or "").strip():
        errors.append("plugin manifest требует description")
    if "hooks" in manifest:
        errors.append("hooks не должны объявляться в plugin.json")
    return errors


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd or config.REPO_DIR, capture_output=True, text=True)
