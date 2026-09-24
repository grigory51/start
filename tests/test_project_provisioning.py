from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from cli import adapters, claudejson, config, project


class ProjectProvisioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.global_root = self.root / 'global'
        self.global_root.mkdir()
        self.project_root = self.root / 'project'
        self.project_root.mkdir()
        self.global_config = self.global_root / 'config.toml'
        self.global_config.write_text('')
        self.local_config = self.global_root / 'config.local.toml'
        self.local_config.write_text(
            '[[ai.mcp]]\nname="grafana"\nenabled=false\n'
            '[ai.mcp.server]\nurl="https://grafana.example/mcp"\n'
            '[ai.mcp.server.headers]\nAuthorization="secret-token"\n'
        )
        self.claude_json = self.root / 'home' / '.claude.json'
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch.object(config, 'REPO_DIR', self.global_root))
        self.stack.enter_context(patch.object(config, 'CONFIG', self.global_config))
        self.stack.enter_context(patch.object(config, 'CONFIG_LOCAL', self.local_config))
        self.stack.enter_context(patch.object(claudejson, 'CLAUDE_JSON', self.claude_json))
        self.stack.enter_context(patch.object(adapters, 'platform_available', return_value=True))

    def plan(self, text: str, *, only: str | None = None, root: Path | None = None) -> project.ProjectPlan:
        destination = root or self.project_root
        path = destination / 'start.toml'
        path.write_text(text)
        return project.build_plan(config.load_project(path), only=only)

    def apply(self, plan: project.ProjectPlan, *, dry_run: bool = False) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(project.apply_plan(plan, dry_run=dry_run), 0, output.getvalue())
        return output.getvalue()

    def codex_config(self) -> dict:
        return tomllib.loads((self.project_root / '.codex/config.toml').read_text())

    def test_inherited_mcp_is_project_scoped_and_secret_is_not_printed(self) -> None:
        before = self.local_config.read_bytes()
        plan = self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n')
        output = self.apply(plan)
        server = self.codex_config()['mcp_servers']['grafana']
        self.assertEqual(server['http_headers']['Authorization'], 'secret-token')
        self.assertTrue(server['enabled'])
        data = json.loads(self.claude_json.read_text())
        self.assertEqual(data['projects'][str(self.project_root)]['mcpServers']['grafana']['headers']['Authorization'], 'secret-token')
        self.assertNotIn('secret-token', output)
        self.assertEqual(self.local_config.read_bytes(), before)
        other = self.root / 'other'
        other.mkdir()
        other_plan = self.plan('', root=other)
        self.assertNotIn('grafana', repr(other_plan.documents))
        self.assertFalse((other / '.mcp.json').exists())

    def test_disabled_server_uses_native_platform_overrides(self) -> None:
        plan = self.plan('[[ai.mcp]]\nname="grafana"\nenabled=false\n')
        self.apply(plan)
        self.assertEqual(self.codex_config()['mcp_servers']['grafana'], {'enabled': False})
        settings = json.loads((self.project_root / '.claude/settings.local.json').read_text())
        self.assertEqual(settings['disabledMcpServers'], ['grafana'])
        self.assertFalse(self.claude_json.exists())

    def test_removal_restores_original_settings_and_keeps_unmanaged_values(self) -> None:
        target = self.project_root / '.codex/config.toml'
        target.parent.mkdir()
        target.write_text('model="original"\n[features]\nunmanaged=true\n')
        self.apply(self.plan('[ai.platforms.codex.config]\nmodel="project"\n', only='ai:codex'))
        self.assertEqual(self.codex_config()['model'], 'project')
        self.apply(self.plan('', only='ai:codex'))
        self.assertEqual(self.codex_config(), {'model': 'original', 'features': {'unmanaged': True}})

    def test_removed_mcp_preserves_other_claude_projects(self) -> None:
        self.claude_json.parent.mkdir()
        original = {'projects': {'/foreign': {'mcpServers': {'other': {'command': 'other'}}}}, 'theme': 'dark'}
        self.claude_json.write_text(json.dumps(original))
        self.apply(self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n'))
        self.apply(self.plan(''))
        self.assertEqual(json.loads(self.claude_json.read_text()), original)
        self.assertNotIn('mcp_servers', self.codex_config())

    def test_repeated_apply_is_idempotent(self) -> None:
        source = '[[ai.mcp]]\nname="grafana"\nenabled=true\n'
        self.apply(self.plan(source))
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.apply(self.plan(source))
        after = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_dry_run_does_not_write_or_install_dependencies(self) -> None:
        plan = self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n')
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with patch('cli.project.plugins.check_requirements') as install:
            output = self.apply(plan, dry_run=True)
        after = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertIn('[dry-run]', output)
        self.assertNotIn('secret-token', output)
        install.assert_not_called()

    def test_tracked_destination_rejected_before_writes(self) -> None:
        subprocess.run(['git', 'init', '-q', str(self.project_root)], check=True)
        target = self.project_root / '.codex/config.toml'
        target.parent.mkdir()
        target.write_text('model="tracked"\n')
        subprocess.run(['git', '-C', str(self.project_root), 'add', '.codex/config.toml'], check=True)
        with self.assertRaisesRegex(ValueError, 'отслеживается Git'):
            self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n')
        self.assertEqual(target.read_text(), 'model="tracked"\n')
        self.assertFalse((self.project_root / '.start').exists())

    def test_codex_only_does_not_write_claude_state(self) -> None:
        plan = self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n', only='ai:codex')
        self.assertEqual(plan.platforms, ('codex',))
        self.apply(plan)
        self.assertFalse(self.claude_json.exists())
        self.assertFalse((self.project_root / '.claude').exists())

    def test_new_project_skills_and_agents_use_existing_adapters(self) -> None:
        skill = self.project_root / 'skills' / 'demo'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: Demo skill\n---\nDo work.\n')
        (skill / 'reference.md').write_text('Reference')
        agents = self.project_root / 'agents'
        agents.mkdir()
        (agents / 'helper.md').write_text('---\nname: helper\ndescription: Helper agent\n---\nHelp with work.\n')
        source = '[[ai.skills]]\npath="skills"\nenabled=["*"]\n[[ai.agents]]\npath="agents"\nenabled=["*"]\n'
        before = {path: path.read_bytes() for path in self.global_root.rglob('*') if path.is_file()}
        plan = self.plan(source)
        self.apply(plan)
        self.assertEqual((self.project_root / '.agents/skills/demo/SKILL.md').read_text(), adapters.normalized_skill_text(skill / 'SKILL.md'))
        self.assertEqual((self.project_root / '.agents/skills/demo/reference.md').resolve(), skill / 'reference.md')
        self.assertEqual((self.project_root / '.claude/skills/demo').resolve(), skill)
        agent_path = self.project_root / '.codex/agents/helper.toml'
        self.assertEqual(agent_path.read_text(), adapters.render_codex_agent(agents / 'helper.md'))
        self.assertEqual(self.codex_config()['agents']['helper']['config_file'], str(agent_path))
        self.assertEqual((self.project_root / '.claude/agents/helper.md').read_text(), (agents / 'helper.md').read_text())
        after = {path: path.read_bytes() for path in self.global_root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.apply(self.plan(''))
        self.assertFalse(agent_path.exists())
        self.assertFalse((self.project_root / '.claude/skills/demo').exists())

    def test_catalog_raw_settings_conflict_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Конфликт'):
            self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n'
                      '[ai.platforms.codex.config.mcp_servers.grafana]\nenabled=false\n')

    def test_manual_edit_of_managed_key_is_rejected(self) -> None:
        source = '[[ai.mcp]]\nname="grafana"\nenabled=true\n'
        self.apply(self.plan(source, only='ai:codex'))
        target = self.project_root / '.codex/config.toml'
        target.write_text(target.read_text().replace('enabled = true', 'enabled = false'))
        with self.assertRaisesRegex(ValueError, 'изменена вручную'):
            self.plan(source, only='ai:codex')
        self.assertFalse(self.codex_config()['mcp_servers']['grafana']['enabled'])

    def test_codex_cleanup_keeps_claude_ownership(self) -> None:
        source = '[[ai.mcp]]\nname="grafana"\nenabled=true\n'
        self.apply(self.plan(source))
        before = self.claude_json.read_bytes()
        self.apply(self.plan('', only='ai:codex'))
        self.assertEqual(self.claude_json.read_bytes(), before)
        state = json.loads((self.project_root / '.start/managed.json').read_text())
        self.assertTrue(state['documents'][str(self.claude_json)]['keys'])
        self.apply(self.plan('', only='ai:claude'))
        self.assertEqual(json.loads(self.claude_json.read_text()), {})

    def test_git_exclude_contains_individual_outputs_not_platform_directory(self) -> None:
        subprocess.run(['git', 'init', '-q', str(self.project_root)], check=True)
        self.apply(self.plan('[[ai.mcp]]\nname="grafana"\nenabled=true\n'))
        patterns = (self.project_root / '.git/info/exclude').read_text().splitlines()
        self.assertIn('/.codex/config.toml', patterns)
        self.assertIn('/.claude/settings.local.json', patterns)
        self.assertIn('/.start', patterns)
        self.assertNotIn('/.codex', patterns)
        self.assertNotIn('/.claude', patterns)
        result = subprocess.run(
            ['git', '-C', str(self.project_root), 'status', '--porcelain', '--untracked-files=all'],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), '?? start.toml')

    def plugin_source(self) -> str:
        plugin = self.project_root / 'plugins' / 'demo'
        for platform in ('claude', 'codex'):
            marker = plugin / f'.{platform}-plugin'
            marker.mkdir(parents=True, exist_ok=True)
            (marker / 'plugin.json').write_text(json.dumps({'name': 'demo', 'description': 'Project plugin'}))
        (plugin / 'reference.txt').write_text('Plugin reference')
        return '[[ai.plugins]]\npath="plugins/demo"\nenabled=true\n'

    def test_native_plugins_generate_project_marketplaces(self) -> None:
        source = self.plugin_source()
        self.apply(self.plan(source))
        marketplace = json.loads((self.project_root / '.agents/plugins/marketplace.json').read_text())
        ref = f"demo@{marketplace['name']}"
        self.assertTrue(self.codex_config()['plugins'][ref]['enabled'])
        bundle = self.project_root / '.start/codex-plugins/demo'
        self.assertEqual((bundle / 'reference.txt').read_text(), 'Plugin reference')
        settings = json.loads((self.project_root / '.claude/settings.local.json').read_text())
        self.assertTrue(settings['enabledPlugins'][ref])
        claude_marketplace = self.project_root / '.start/claude-marketplace'
        self.assertEqual((claude_marketplace / 'plugins/demo').resolve(), self.project_root / 'plugins/demo')
        self.assertEqual(json.loads((claude_marketplace / '.claude-plugin/marketplace.json').read_text())['plugins'][0]['source'], './plugins/demo')
        self.apply(self.plan(source))
        self.apply(self.plan(''))
        self.assertFalse(bundle.exists())
        self.assertFalse((claude_marketplace / 'plugins/demo').exists())

    def test_foreign_plugin_bundle_is_rejected(self) -> None:
        source = self.plugin_source()
        bundle = self.project_root / '.start/codex-plugins/demo'
        bundle.mkdir(parents=True)
        (bundle / 'foreign.txt').write_text('Keep this')
        with self.assertRaisesRegex(ValueError, 'не принадлежит start'):
            self.plan(source)
        self.assertEqual((bundle / 'foreign.txt').read_text(), 'Keep this')

    def test_new_hook_preserves_foreign_hooks_and_removes_only_own(self) -> None:
        hook = self.project_root / 'hooks' / 'session.sh'
        hook.parent.mkdir()
        hook.write_text('#!/bin/sh\nexit 0\n')
        settings_path = self.project_root / '.claude/settings.local.json'
        settings_path.parent.mkdir()
        original = {'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'foreign-command'}]}]}}
        settings_path.write_text(json.dumps(original))
        source = '[[ai.hooks]]\npath="hooks/session.sh"\nevents=["SessionStart"]\n'
        self.apply(self.plan(source))
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings['hooks']['SessionStart'][0], original['hooks']['SessionStart'][0])
        self.assertEqual(len(settings['hooks']['SessionStart']), 2)
        self.assertEqual((self.project_root / '.claude/hooks/session.sh').resolve(), hook)
        self.assertEqual((self.project_root / '.codex/hooks/session.sh').resolve(), hook)
        self.apply(self.plan(''))
        self.assertEqual(json.loads(settings_path.read_text()), original)
        self.assertFalse((self.project_root / '.codex/hooks/session.sh').exists())

    def test_skip_seed_preserves_prior_plugin_files_and_settings(self) -> None:
        source = self.plugin_source()
        self.apply(self.plan(source))
        old_codex = self.codex_config()['plugins']
        old_claude = json.loads((self.project_root / '.claude/settings.local.json').read_text())
        bundle = self.project_root / '.start/codex-plugins/demo'
        context = config.load_project(self.project_root / 'start.toml')
        self.apply(project.build_plan(context, skip_seed=True))
        self.assertEqual(self.codex_config()['plugins'], old_codex)
        self.assertEqual(json.loads((self.project_root / '.claude/settings.local.json').read_text()), old_claude)
        self.assertTrue((bundle / 'reference.txt').exists())
        self.assertTrue((self.project_root / '.start/claude-marketplace/plugins/demo').is_symlink())
        self.apply(self.plan(source))

    def test_skip_settings_preserves_claude_files_and_ownership(self) -> None:
        source = '[[ai.mcp]]\nname="grafana"\nenabled=true\n[ai.platforms.claude.settings]\nmodel="sonnet"\n'
        self.apply(self.plan(source))
        settings = self.project_root / '.claude/settings.local.json'
        before = {path: path.read_bytes() for path in (settings, self.claude_json)}
        (self.project_root / 'start.toml').write_text('')
        self.apply(project.build_plan(config.load_project(self.project_root / 'start.toml'), skip_settings=True))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.apply(self.plan(''))
        self.assertEqual(json.loads(settings.read_text()), {})
        self.assertEqual(json.loads(self.claude_json.read_text()), {})

    def test_symlink_start_config_is_rejected(self) -> None:
        actual = self.root / 'elsewhere.toml'
        actual.write_text('')
        link = self.project_root / 'start.toml'
        link.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            config.load_project(link)

    def test_project_requirement_shell_commands_are_rejected_without_execution(self) -> None:
        source = self.plugin_source() + '\n[[ai.plugins.requirements]]\nname="dependency"\ncheck="echo check"\ninstall="echo install"\nhint="Install manually"\n'
        with patch('cli.project.plugins.check_requirements') as check:
            with self.assertRaisesRegex(ValueError, 'requirements'):
                self.plan(source)
        check.assert_not_called()
        self.assertFalse((self.project_root / '.start').exists())

    def test_conflicting_platform_skill_enable_and_catalog_disable_is_rejected(self) -> None:
        skill = self.global_root / 'skills' / 'demo'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: Demo skill\n---\nDo work.\n')
        self.global_config.write_text('[[ai.skills]]\npath="skills"\nenabled=["*"]\n')
        with self.assertRaisesRegex(ValueError, 'Конфликт'):
            self.plan('[local.ai.skills]\nskills=[]\n[ai.platforms.codex.skills]\ndemo=true\n', only='ai:codex')
