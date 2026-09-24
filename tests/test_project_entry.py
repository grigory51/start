from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from cli.up import find_project_config, run_up


class ProjectDiscoveryTests(unittest.TestCase):
    def test_git_root_and_nearest_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / '.git').mkdir()
            nested = root / 'nested'
            nested.mkdir()
            child = nested / 'child'
            child.mkdir()
            (root / 'start.toml').touch()
            self.assertEqual(find_project_config(child), root / 'start.toml')
            (nested / 'start.toml').touch()
            self.assertEqual(find_project_config(child), nested / 'start.toml')

    def test_worktree_boundary_does_not_leak_parent_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            (parent / 'start.toml').touch()
            root = parent / 'worktree'
            root.mkdir()
            (root / '.git').write_text('gitdir: /unused/worktrees/example')
            child = root / 'child'
            child.mkdir()
            self.assertIsNone(find_project_config(child))
            (root / 'start.toml').touch()
            self.assertEqual(find_project_config(child), root / 'start.toml')

    def test_outside_git_checks_only_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'start.toml').touch()
            child = root / 'child'
            child.mkdir()
            self.assertIsNone(find_project_config(child))
            (child / 'start.toml').touch()
            self.assertEqual(find_project_config(child), child / 'start.toml')

    def test_launcher_preserves_invocation_directory(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / 'scripts/run.sh'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            binary = root / 'uv'
            binary.write_text('#!/bin/bash\nprintf "%s\\n%s\\n" "$START_INVOKE_DIR" "$PWD"\n')
            binary.chmod(0o755)
            environment = dict(os.environ, PATH=f'{root}:/usr/bin:/bin', START_INVOKE_DIR='/stale')
            result = subprocess.run(
                ['bash', str(launcher), 'up'], cwd=root, env=environment,
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(result.stdout.splitlines(), [str(root), str(launcher.parents[1])])


class ProjectEntryTests(unittest.TestCase):
    def test_without_project_preserves_global_options(self) -> None:
        with patch('cli.up.find_project_config', return_value=None), \
             patch('cli.up.update_submodules', return_value=0) as submodules, \
             patch('cli.up.run_install', return_value=2) as install, \
             patch('cli.up.project.build_plan') as build:
            self.assertEqual(run_up(force=True, quiet=True, skip_seed=True), 2)
        submodules.assert_called_once_with(quiet=True)
        install.assert_called_once_with(
            dry_run=False, force=True, quiet=True, skip_seed=True, skip_settings=False, only=None,
        )
        build.assert_not_called()

    def test_project_preflight_and_apply_order_and_options(self) -> None:
        context, plan = object(), object()
        calls = Mock()
        with patch('cli.up.find_project_config', return_value=Path('/project/start.toml')), \
             patch('cli.up.config.load_project', return_value=context) as load, \
             patch('cli.up.project.build_plan', return_value=plan) as build, \
             patch('cli.up.update_submodules', return_value=0) as submodules, \
             patch('cli.up.run_install', return_value=2) as install, \
             patch('cli.up.project.apply_plan', return_value=3) as apply:
            for name, function in [('load', load), ('build', build), ('submodules', submodules),
                                   ('install', install), ('apply', apply)]:
                calls.attach_mock(function, name)
            self.assertEqual(run_up(only='ai:codex', force=True, skip_settings=True), 5)
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ['load', 'build', 'submodules', 'install', 'apply'])
        build.assert_called_once_with(context, only='ai:codex', skip_seed=False, skip_settings=True)
        apply.assert_called_once_with(plan, dry_run=False, force=True)

    def test_invalid_project_stops_before_any_global_mutations(self) -> None:
        for error in (ValueError('secret-token'), OSError('secret-token'),
                      tomllib.TOMLDecodeError('secret-token', '', 0)):
            with self.subTest(error=type(error).__name__), \
                 patch('cli.up.find_project_config', return_value=Path('/project/start.toml')), \
                 patch('cli.up.config.load_project', side_effect=error), \
                 patch('cli.up.update_submodules') as submodules, \
                 patch('cli.up.run_install') as install, \
                 patch('cli.up.project.apply_plan') as apply, \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(run_up(), 1)
                self.assertNotIn('secret-token', output.getvalue())
                submodules.assert_not_called()
                install.assert_not_called()
                apply.assert_not_called()

    def test_files_only_skips_project_and_submodules(self) -> None:
        with patch('cli.up.find_project_config') as discover, \
             patch('cli.up.update_submodules') as submodules, \
             patch('cli.up.run_install', return_value=0), \
             patch('cli.up.project.apply_plan') as apply:
            self.assertEqual(run_up(only='files'), 0)
        discover.assert_not_called()
        submodules.assert_not_called()
        apply.assert_not_called()

    def test_plan_validation_failure_stops_global_provisioning(self) -> None:
        with patch('cli.up.find_project_config', return_value=Path('/project/start.toml')), \
             patch('cli.up.config.load_project'), \
             patch('cli.up.project.build_plan', side_effect=ValueError('secret-token')), \
             patch('cli.up.update_submodules') as submodules, \
             patch('cli.up.run_install') as install, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(run_up(), 1)
        self.assertNotIn('secret-token', output.getvalue())
        submodules.assert_not_called()
        install.assert_not_called()

    def test_dry_run_skips_submodules_and_reaches_project(self) -> None:
        with patch('cli.up.find_project_config', return_value=Path('/project/start.toml')), \
             patch('cli.up.config.load_project'), \
             patch('cli.up.project.build_plan', return_value='plan'), \
             patch('cli.up.update_submodules') as submodules, \
             patch('cli.up.run_install', return_value=0), \
             patch('cli.up.project.apply_plan', return_value=0) as apply:
            self.assertEqual(run_up(dry_run=True), 0)
        submodules.assert_not_called()
        apply.assert_called_once_with('plan', dry_run=True, force=False)

    def test_invocation_directory_and_python_fallback(self) -> None:
        for invoke, expected in [('/project', Path('/project')), ('', Path.cwd())]:
            with self.subTest(invoke=invoke), \
                 patch.dict(os.environ, {'START_INVOKE_DIR': invoke}), \
                 patch('cli.up.find_project_config', return_value=None) as discover, \
                 patch('cli.up.run_install', return_value=0):
                run_up(skip_submodules=True)
                discover.assert_called_once_with(expected)
