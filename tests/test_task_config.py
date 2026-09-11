from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import config


class TaskConfigTests(unittest.TestCase):
    def load(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text(source)
            with patch.object(config, 'CONFIG', path):
                return config.load_tasks()

    def test_old_shell_task_and_live_task(self) -> None:
        tasks, warnings = self.load('''
[[commands.tasks]]
name = "old"
[commands.tasks.run]
darwin = "echo hi"
[[commands.tasks]]
name = "live"
category = "Сеть"
sudo = true
refresh = 1
[commands.tasks.view]
darwin = "custom.tasks:snapshot"
[[commands.tasks.filters]]
name = "protocol"
label = "Protocol"
default = "tcp"
options = ["tcp", "udp"]
''')
        self.assertFalse(warnings)
        with patch.object(config.sys, 'platform', 'darwin'):
            self.assertEqual(tasks[0].command, 'echo hi')
            self.assertIsNone(tasks[0].provider)
            self.assertEqual(tasks[1].provider, 'custom.tasks:snapshot')
        with patch.object(config.sys, 'platform', 'linux'):
            self.assertIsNone(tasks[1].command)
        self.assertEqual(tasks[1].filters[0].default, 'tcp')
        self.assertTrue(tasks[1].sudo)
        self.assertEqual(tasks[0].category, 'Общее')
        self.assertEqual(tasks[1].category, 'Сеть')

    def test_invalid_task_does_not_hide_following_tasks(self) -> None:
        for fragment in ('refresh = 0', 'refresh = nan', 'filters = "broken"',
                         'filters = [{name="bad name"}]',
                         'filters = [{name="x", default="udp", options=["tcp"]}]'):
            with self.subTest(fragment=fragment):
                tasks, warnings = self.load(f'''
[[commands.tasks]]
name = "bad"
{fragment}
[commands.tasks.view]
darwin = "custom:snapshot"
[[commands.tasks]]
name = "good"
[commands.tasks.run]
darwin = "echo good"
''')
                self.assertEqual([task.name for task in tasks], ['good'])
                self.assertTrue(warnings)

    def test_ambiguous_launch_and_invalid_provider_rejected(self) -> None:
        for definition in ('[commands.tasks.view]\ndarwin="bad command"',
                           '[commands.tasks.view]\ndarwin="custom:run"\n[commands.tasks.run]\ndarwin="echo hi"'):
            tasks, warnings = self.load('[[commands.tasks]]\nname="bad"\n' + definition)
            self.assertFalse(tasks)
            self.assertTrue(warnings)
