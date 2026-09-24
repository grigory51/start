from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli import config, plugins
from cli.ai import codex
from cli.install import Ctx


class RequirementTests(unittest.TestCase):
    requirement = {'name': 'engine', 'check': 'probe', 'install': 'setup', 'hint': 'install engine'}

    def test_installed_does_not_run_installer(self) -> None:
        with patch('cli.plugins.subprocess.run', return_value=subprocess.CompletedProcess('probe', 0)) as run:
            ctx = Ctx(False, False)
            plugins.check_requirements(ctx, 'skill', [self.requirement])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(ctx.errors, 0)

    def test_missing_installs_and_rechecks(self) -> None:
        with patch('cli.plugins.subprocess.run', side_effect=[
            subprocess.CompletedProcess('probe', 1), subprocess.CompletedProcess('setup', 0),
            subprocess.CompletedProcess('probe', 0),
        ]) as run:
            ctx = Ctx(False, False)
            plugins.check_requirements(ctx, 'skill', [self.requirement])
        self.assertEqual([call.args[0] for call in run.call_args_list], ['probe', 'setup', 'probe'])
        self.assertEqual(ctx.errors, 0)

    def test_dry_run_does_not_install(self) -> None:
        with patch('cli.plugins.subprocess.run', return_value=subprocess.CompletedProcess('probe', 1)) as run:
            ctx = Ctx(True, False)
            plugins.check_requirements(ctx, 'skill', [self.requirement])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(ctx.errors, 0)

    def test_failed_install_or_verification_counts_error(self) -> None:
        for results in ([1, 1], [1, 0, 1]):
            with self.subTest(results=results), patch('cli.plugins.subprocess.run', side_effect=[
                subprocess.CompletedProcess('command', code, '', 'failure' if code else '') for code in results
            ]):
                ctx = Ctx(False, False)
                plugins.check_requirements(ctx, 'skill', [self.requirement])
                self.assertEqual(ctx.errors, 1)

    def test_manual_requirement_still_only_warns(self) -> None:
        req = {key: value for key, value in self.requirement.items() if key != 'install'}
        with patch('cli.plugins.subprocess.run', return_value=subprocess.CompletedProcess('probe', 1)) as run:
            ctx = Ctx(False, False)
            plugins.check_requirements(ctx, 'skill', [req])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(ctx.errors, 0)

    def test_parser_keeps_install_and_rejects_wrong_type(self) -> None:
        warnings: list[str] = []
        self.assertEqual(config._parse_requirements({'requirements': [self.requirement]}, 'skill', warnings), [self.requirement])
        self.assertEqual(warnings, [])
        self.assertEqual(config._parse_requirements({'requirements': [{**self.requirement, 'install': []}]}, 'skill', warnings), [])
        self.assertEqual(len(warnings), 1)

    def test_codex_checks_enabled_sources_once(self) -> None:
        skills = [config.Skill(name, Path('/skills') / name, 'source', True,
                               requirements=[self.requirement], platforms=platforms)
                  for name, platforms in [('one', ('codex',)), ('two', ('codex',)), ('other', ('claude',))]]
        with patch.object(codex.config, 'load', return_value=SimpleNamespace(warnings=[], enabled_skills=skills)), patch.object(
            codex.adapters, 'codex_skill', return_value=Path('/generated/skill')
        ), patch.object(codex, '_managed_link_set'), patch.object(codex.plugins, 'check_requirements') as check:
            ctx = Ctx(True, False)
            codex.install_skills(ctx)
        check.assert_called_once_with(ctx, 'source', [self.requirement])
