"""Проектный provisioning общего AI-каталога без изменения глобальных установок."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any

import tomlkit

from . import adapters, claudejson, config, plugins
from .ai.codex import _codex_mcp, _tree_hash
from .install import Ctx


@dataclass
class ProjectPlan:
    root: Path
    platforms: tuple[str, ...]
    skip_settings: bool = False
    skip_seed: bool = False
    documents: dict[Path, dict] = field(default_factory=dict)
    files: dict[Path, str] = field(default_factory=dict)
    links: dict[Path, Path] = field(default_factory=dict)
    bundles: list[tuple[config.Plugin, Path]] = field(default_factory=list)
    requirements: dict[str, list[dict]] = field(default_factory=dict)


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = tomlkit.parse(path.read_text()) if path.suffix == '.toml' else json.loads(path.read_text())
    except (ValueError, OSError) as error:
        raise ValueError(f"Не удалось прочитать конфигурацию: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Ожидалась таблица: {path}")
    return value


def _leaves(value: dict, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    result = {}
    for key, child in value.items():
        path = (*prefix, key)
        if isinstance(child, dict):
            result.update(_leaves(child, path))
        else:
            result[path] = child
    return result


def _get(document: dict, path: tuple[str, ...]) -> tuple[bool, Any]:
    node = document
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            return False, None
        node = node[key]
    return path[-1] in node, node.get(path[-1])


def _set(document: dict, path: tuple[str, ...], exists: bool, value: Any) -> None:
    node = document
    parents = []
    for key in path[:-1]:
        if key not in node:
            if not exists:
                return
            node[key] = {}
        if not isinstance(node[key], dict):
            raise ValueError(f"Конфликт формы настройки: {'.'.join(path)}")
        parents.append((node, key))
        node = node[key]
    if exists:
        node[path[-1]] = deepcopy(value)
    else:
        node.pop(path[-1], None)
        for parent, key in reversed(parents):
            if parent[key]:
                break
            del parent[key]


def _reconcile(current: dict, wanted: dict, previous: list[dict]) -> tuple[dict, list[dict]]:
    result = deepcopy(current)
    for entry in previous:
        path = tuple(entry['path'])
        exists, value = _get(result, path)
        if exists and value == entry['written']:
            _set(result, path, entry['existed'], entry['original'])
        else:
            raise ValueError(f"Managed-настройка изменена вручную: {'.'.join(path)}")
    records = []
    for path, value in _leaves(wanted).items():
        exists, original = _get(result, path)
        if exists and isinstance(original, list) and isinstance(value, list):
            if path[0] == 'hooks' or path[-1] == 'disabledMcpServers':
                value = deepcopy(original) + [item for item in value if item not in original]
            elif path == ('skills', 'config') or path == ('plugins',):
                identity = 'path' if path[0] == 'skills' else 'name'
                overridden = {item[identity] for item in value}
                value = [item for item in original if item.get(identity) not in overridden] + value
        records.append(dict(path=list(path), existed=exists, original=original, written=value))
        _set(result, path, True, value)
    return result, records


def _checked(result: tuple[list, list[str]]) -> list:
    values, warnings = result
    if warnings:
        raise ValueError('Ошибка каталога проекта: ' + '; '.join(warnings))
    return values


def build_plan(
    context: config.ConfigContext, *, only: str | None = None,
    skip_settings: bool = False, skip_seed: bool = False,
) -> ProjectPlan:
    platforms = tuple(platform for platform in adapters.PLATFORMS
                      if only in (None, 'ai', f'ai:{platform}') and adapters.platform_available(platform))
    plan = ProjectPlan(context.root, platforms, skip_settings=skip_settings, skip_seed=skip_seed)
    global_context = config.global_context()
    root = context.root
    managed = root / '.start'
    codex: dict = {}
    claude: dict = {}
    global_skills = {s.name: s for s in config.load(global_context).enabled_skills}
    trusted_requirements = {s.source: s.requirements for s in config.load(global_context).skills}
    trusted_requirements.update({p.source: p.requirements for p in _checked(config._discover_plugins(global_context))})
    skills = config.load(context)
    if skills.warnings:
        raise ValueError('Ошибка источников skills проекта')
    agents = _checked(config._discover_agents(context))
    global_agents = {a.name: a for a in _checked(config._discover_agents(global_context))}
    effective_agents = {a.name: a for a in agents}
    for name, agent in global_agents.items():
        if agent.source in context.project_keys.get('agents', set()) and name not in effective_agents:
            raise ValueError(f"Платформы не поддерживают проектное отключение глобального агента: {name}")

    for platform in platforms:
        platform_config = context.project_document.get('ai', {}).get('platforms', {}).get(platform, {})
        destination = codex if platform == 'codex' else claude
        raw = platform_config.get('config' if platform == 'codex' else 'settings', {})
        if not isinstance(raw, dict):
            raise ValueError(f"Неверные настройки платформы {platform}")
        if platform == 'codex':
            forbidden = {'openai_base_url', 'chatgpt_base_url', 'apps_mcp_product_sku', 'model_provider',
                         'model_providers', 'notify', 'profile', 'profiles', 'experimental_realtime_ws_base_url', 'otel'}
            if forbidden & raw.keys():
                raise ValueError('Настройка Codex недоступна на уровне проекта')
            for section in ('features', 'plugins'):
                for key, value in platform_config.get(section, {}).items():
                    destination.setdefault(section, {})[key] = {'enabled': value} if section == 'plugins' else value
            for name, enabled in platform_config.get('skills', {}).items():
                if not isinstance(enabled, bool) or name not in global_skills:
                    raise ValueError(f"Неизвестный skill или неверное enabled: {name}")
                destination.setdefault('skills', {}).setdefault('config', []).append({
                    'path': str(Path.home() / '.agents/skills' / name / 'SKILL.md'), 'enabled': enabled})

        servers = _checked(config.load_mcp(context))
        for server in servers:
            if server.name not in context.project_keys.get('mcp', set()) or not adapters.supports(server, platform):
                continue
            if server.enabled and not server.server:
                raise ValueError(f"Нет определения MCP: {server.name}")
            if platform == 'codex':
                destination.setdefault('mcp_servers', {})[server.name] = (
                    _codex_mcp(server) | {'enabled': True} if server.enabled else {'enabled': False}
                )
            elif not skip_settings:
                if server.enabled:
                    plan.documents.setdefault(claudejson.CLAUDE_JSON, {}).setdefault('projects', {}).setdefault(
                        str(root), {}).setdefault('mcpServers', {})[server.name] = claudejson._normalize(server.server)
                else:
                    destination.setdefault('disabledMcpServers', []).append(server.name)

        for skill in skills.skills:
            old = global_skills.get(skill.name)
            if skill.source not in context.project_keys.get('skills', set()) or not adapters.supports(skill, platform):
                continue
            if old and skill == old:
                continue
            if platform == 'claude' and old and not skill.enabled:
                raise ValueError(f"Claude не поддерживает проектное отключение глобального skill: {skill.name}")
            if platform == 'codex' and old:
                destination.setdefault('skills', {}).setdefault('config', []).append(
                    {'path': str(Path.home() / '.agents' / 'skills' / old.name / 'SKILL.md'), 'enabled': False})
            if not skill.enabled:
                continue
            skill_dir = root / ('.agents' if platform == 'codex' else '.claude') / 'skills' / skill.name
            if platform == 'codex':
                plan.files[skill_dir / 'SKILL.md'] = adapters.normalized_skill_text(skill.path / 'SKILL.md')
                for item in skill.path.iterdir():
                    if item.name != 'SKILL.md':
                        plan.links[skill_dir / item.name] = item
            else:
                plan.links[skill_dir] = skill.path
            plan.requirements[skill.source] = skill.requirements

        for agent in agents:
            if agent.source not in context.project_keys.get('agents', set()) or not adapters.supports(agent, platform):
                continue
            if platform == 'codex':
                path = root / '.codex' / 'agents' / f'{agent.name}.toml'
                plan.files[path] = agent.path.read_text() if agent.path.suffix == '.toml' else adapters.render_codex_agent(agent.path)
                destination.setdefault('agents', {})[agent.name] = {'description': agent.description, 'config_file': str(path)}
            else:
                path = root / '.claude' / 'agents' / f'{agent.name}.md'
                plan.files[path] = agent.path.read_text() if agent.path.suffix == '.md' else adapters.render_claude_agent(agent.path)

        global_paths = {entry['path'] for entry in _checked(config.load_hooks(platform, global_context))}
        if global_paths & context.project_keys.get('hooks', set()):
            raise ValueError("Проектное переопределение глобальных hooks не поддерживается платформой")
        for hook in _checked(config.load_hooks(platform, context)):
            if hook['path'] not in context.project_keys.get('hooks', set()):
                continue
            source = Path(hook['path'])
            if not source.is_file():
                raise ValueError(f"Hook не найден: {source}")
            target = root / f'.{platform}' / 'hooks' / source.name
            plan.links[target] = source
            for event in hook['events']:
                command = f'START_AI_PLATFORM={platform} START_AI_EVENT={shlex.quote(event)} bash {shlex.quote(str(target))}'
                destination.setdefault('hooks', {}).setdefault(event, []).append(
                    {'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]})

        if not skip_seed:
            marketplace_name = 'start-project-' + hashlib.sha256(str(root).encode()).hexdigest()[:12]
            marketplace_entries = []
            for plugin in _checked(config._discover_plugins(context)):
                if plugin.source not in context.project_keys.get('plugins', set()) or not adapters.supports(plugin, platform):
                    continue
                old_plugins = {p.source: p for p in _checked(config._discover_plugins(global_context))}
                old = old_plugins.get(plugin.source)
                section = 'plugins' if platform == 'codex' else 'enabledPlugins'
                old_ref = f'{plugin.plugin}@personal' if platform == 'codex' else plugin.ref
                destination.setdefault(section, {})
                if old:
                    destination[section][old_ref] = {'enabled': False} if platform == 'codex' else False
                if not plugin.enabled:
                    continue
                if old and old.enabled and plugin == old:
                    destination[section][old_ref] = {'enabled': True} if platform == 'codex' else True
                    continue
                plan.requirements[plugin.source] = plugin.requirements
                if platform == 'codex':
                    bundle = managed / 'codex-plugins' / plugin.plugin
                    adapters.codex_plugin(plugin, dry_run=True, destination=bundle)
                    plan.bundles.append((plugin, bundle))
                    marketplace_entries.append({'name': plugin.plugin, 'source': {'source': 'local', 'path': './' + str(bundle.relative_to(root))},
                                                'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}})
                else:
                    if 'claude' not in plugin.platform_paths:
                        raise ValueError(f"Нет Claude-представления плагина: {plugin.plugin}")
                    plugin_dir = managed / 'claude-marketplace' / 'plugins' / plugin.plugin
                    plan.links[plugin_dir] = plugin.platform_paths['claude']
                    marketplace_entries.append({'name': plugin.plugin, 'source': './plugins/' + plugin.plugin})
                ref = f'{plugin.plugin}@{marketplace_name}'
                destination[section][ref] = {'enabled': True} if platform == 'codex' else True
            if len({entry['name'] for entry in marketplace_entries}) != len(marketplace_entries):
                raise ValueError('Неоднозначные имена plugins в проектном marketplace')
            if marketplace_entries:
                if platform == 'codex':
                    plan.documents[root / '.agents/plugins/marketplace.json'] = {'name': marketplace_name, 'plugins': marketplace_entries}
                else:
                    marketplace_root = managed / 'claude-marketplace'
                    plan.documents[marketplace_root / '.claude-plugin/marketplace.json'] = {
                        'name': marketplace_name, 'owner': {'name': 'start'}, 'plugins': marketplace_entries}
                    destination.setdefault('extraKnownMarketplaces', {})[marketplace_name] = {
                        'source': {'source': 'directory', 'path': str(marketplace_root)}}

        skill_flags = destination.get('skills', {}).get('config', []) if platform == 'codex' else []
        flags_by_path = {}
        for entry in skill_flags:
            if entry['path'] in flags_by_path and flags_by_path[entry['path']] != entry['enabled']:
                raise ValueError("Конфликт общего каталога и platform skills")
            flags_by_path[entry['path']] = entry['enabled']
        if skill_flags:
            destination['skills']['config'] = [{'path': path, 'enabled': enabled} for path, enabled in flags_by_path.items()]
        # Raw platform settings cannot silently override generated catalog fields.
        generated_keys = _leaves(destination)
        if any(left[:len(right)] == right or right[:len(left)] == left
               for left in generated_keys for right in _leaves(raw)):
            raise ValueError(f"Конфликт общего каталога и настроек {platform}")
        destination.update(config._merge_tables(destination, raw))
    if 'codex' in platforms:
        plan.documents[root / '.codex/config.toml'] = codex
    if 'claude' in platforms and not skip_settings:
        plan.documents[root / '.claude/settings.local.json'] = claude
    for source, requirements in plan.requirements.items():
        if requirements and requirements != trusted_requirements.get(source):
            raise ValueError(f"Проектные shell requirements нужно установить вручную: {source}")
    validate_plan(plan)
    return plan


def _owner(path: Path, root: Path) -> str:
    if path == claudejson.CLAUDE_JSON:
        return 'claude'
    relative = str(path.relative_to(root))
    return 'claude' if relative.startswith(('.claude/', '.start/claude-')) else 'codex'


def _active(path: Path, platform: str, plan: ProjectPlan) -> bool:
    if platform not in plan.platforms:
        return False
    if plan.skip_settings and platform == 'claude' and path in {
        claudejson.CLAUDE_JSON, plan.root / '.claude/settings.local.json'
    }:
        return False
    if plan.skip_seed and path.is_relative_to(plan.root) and str(path.relative_to(plan.root)).startswith(
        ('.start/codex-plugins/', '.start/claude-marketplace/', '.agents/plugins/')
    ):
        return False
    return True


def _records(path: Path, records: list[dict], plan: ProjectPlan) -> tuple[list[dict], list[dict]]:
    active, preserved = [], []
    for entry in records:
        plugin_setting = entry['path'][0] in {'plugins', 'enabledPlugins', 'extraKnownMarketplaces'}
        (preserved if plan.skip_seed and plugin_setting else active).append(entry)
    return active, preserved


def _state(plan: ProjectPlan) -> dict:
    return _read(plan.root / '.start/managed.json')


def _git(plan: ProjectPlan, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(plan.root), *args], capture_output=True, text=True)


def _digest(path: Path) -> str:
    if path.is_symlink():
        return 'link:' + os.readlink(path)
    if path.is_dir():
        return 'tree:' + _tree_hash(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_plan(plan: ProjectPlan) -> None:
    previous = _state(plan)
    if (plan.root / '.start/managed.json').is_symlink():
        raise ValueError('Sidecar не может быть symlink')
    paths = set(plan.documents) | set(plan.files) | set(plan.links) | {path for _, path in plan.bundles}
    paths.update(Path(path) for path, entry in previous.get('documents', {}).items()
                 if _active(Path(path), entry['platform'], plan))
    paths.update(Path(path) for path, entry in previous.get('files', {}).items()
                 if _active(Path(path), entry['platform'], plan))
    for path in paths | {plan.root / '.start/managed.json'}:
        if path != claudejson.CLAUDE_JSON:
            if not path.is_relative_to(plan.root):
                raise ValueError(f"Назначение за пределами проекта: {path}")
            for parent in path.parents:
                if parent == plan.root:
                    break
                if parent.is_symlink():
                    raise ValueError(f"Родитель назначения является symlink: {parent}")
            if _git(plan, 'ls-files', '--error-unmatch', '--', str(path)).returncode == 0:
                raise ValueError(f"Конфиг назначения отслеживается Git: {path}")
        if path in plan.documents or str(path) in previous.get('documents', {}):
            if path.is_symlink():
                raise ValueError(f"Конфиг назначения является symlink: {path}")
            entry = previous.get('documents', {}).get(str(path), {})
            active, _ = _records(path, entry.get('keys', []), plan)
            _reconcile(_read(path), plan.documents.get(path, {}), active)
        elif path != plan.root / '.start/managed.json' and (path.exists() or path.is_symlink()):
            entry = previous.get('files', {}).get(str(path))
            if not entry or entry['digest'] != _digest(path):
                raise ValueError(f"Файл назначения не принадлежит start или изменён: {path}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.start-next')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(text)
    os.replace(temporary, path)


def apply_plan(plan: ProjectPlan, *, dry_run: bool = False, force: bool = False) -> int:
    """Применить проверенный план; sidecar хранит исходные значения ключей."""
    try:
        validate_plan(plan)
        previous = _state(plan)
        documents = deepcopy(previous.get('documents', {}))
        files = deepcopy(previous.get('files', {}))
        desired = dict(plan.documents)
        for path, entry in documents.items():
            if _active(Path(path), entry['platform'], plan):
                desired.setdefault(Path(path), {})
        writes = {}
        for path, fragment in desired.items():
            active, preserved = _records(path, documents.get(str(path), {}).get('keys', []), plan)
            data, records = _reconcile(_read(path), fragment, active)
            writes[path] = data
            documents[str(path)] = {'platform': _owner(path, plan.root), 'keys': records + preserved}
        print(f'Проект: {plan.root}')
        for path in sorted(set(writes) | set(plan.files) | set(plan.links)):
            print(f"  {'[dry-run] ' if dry_run else ''}{path}")
        if dry_run:
            return 0
        # Исключения записываются до файлов, которые могут содержать личные значения.
        git_exclude = _git(plan, 'rev-parse', '--path-format=absolute', '--git-path', 'info/exclude')
        if git_exclude.returncode == 0:
            git_root = Path(_git(plan, 'rev-parse', '--show-toplevel').stdout.strip())
            exclude = Path(git_exclude.stdout.strip())
            text = exclude.read_text() if exclude.exists() else ''
            candidates = set(writes) | set(plan.files) | set(plan.links) | {plan.root / '.start'}
            patterns = set(text.splitlines())
            additions = []
            for path in sorted(candidates):
                if path.is_relative_to(git_root):
                    relative = path.relative_to(git_root).as_posix()
                    escaped = ''.join('\\' + char if char in '*?[]\\ ' else char for char in relative)
                    for pattern in ('/' + escaped, '/' + escaped + '.start-next'):
                        if pattern not in patterns:
                            additions.append(pattern)
            if additions:
                _atomic_write(exclude, text.rstrip() + '\n' + '\n'.join(additions) + '\n')
        ctx = Ctx(False, force)
        for source, requirements in plan.requirements.items():
            plugins.check_requirements(ctx, source, requirements)
        if ctx.errors:
            return ctx.errors
        for plugin, destination in plan.bundles:
            adapters.codex_plugin(plugin, destination=destination)
            files[str(destination)] = {'platform': 'codex', 'digest': _digest(destination)}
        for path, entry in list(files.items()):
            if _active(Path(path), entry['platform'], plan) and Path(path) not in plan.files and Path(path) not in plan.links and Path(path) not in {dest for _, dest in plan.bundles}:
                if Path(path).is_dir() and not Path(path).is_symlink():
                    shutil.rmtree(path)
                else:
                    Path(path).unlink(missing_ok=True)
                del files[path]
        for path, content in plan.files.items():
            if not path.is_file() or path.read_text() != content:
                _atomic_write(path, content)
            files[str(path)] = {'platform': _owner(path, plan.root), 'digest': _digest(path)}
        for path, source in plan.links.items():
            if not path.is_symlink() or path.readlink() != source:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.unlink(missing_ok=True)
                path.symlink_to(source, target_is_directory=source.is_dir())
            files[str(path)] = {'platform': _owner(path, plan.root), 'digest': _digest(path)}
        for path, data in writes.items():
            if not data == _read(path):
                _atomic_write(path, tomlkit.dumps(data) if path.suffix == '.toml' else json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        _atomic_write(plan.root / '.start/managed.json', json.dumps({'documents': documents, 'files': files}, ensure_ascii=False, indent=2) + '\n')
        return 0
    except (ValueError, OSError, adapters.AdapterError) as error:
        print(f'Ошибка provisioning проекта {plan.root}: {type(error).__name__}: {error}')
        return 1
