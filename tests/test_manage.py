from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from textual.app import App
from textual.widgets import Checkbox, DataTable

from cli import config
from cli.manage import CommandsPane, PluginSettingsScreen, PluginsPane


class CommandsPaneTests(unittest.TestCase):
    def test_reload_and_run_have_separate_bindings(self) -> None:
        self.assertEqual(
            [(binding.key, binding.action) for binding in CommandsPane.BINDINGS],
            [("enter", "run"), ("r", "reload")],
        )


class CommandCategoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_categories_group_tasks_and_headers_do_not_run(self) -> None:
        tasks = [
            config.Task('one', 'One', '', {config.sys.platform: 'echo one'}, category='Codex'),
            config.Task('two', 'Two', '', {config.sys.platform: 'echo two'}, category='Сеть'),
            config.Task('three', 'Three', '', {config.sys.platform: 'echo three'}, category='Codex'),
            config.Task('missing', 'Missing', '', {}, category='Codex'),
        ]
        app = App()
        with patch('cli.manage.config.load_tasks', return_value=(tasks, [])):
            async with app.run_test() as pilot:
                pane = CommandsPane()
                await app.mount(pane)
                await pilot.pause()
                self.assertEqual(
                    [task.name if task else None for task in pane._row_map],
                    [None, 'one', 'three', None, 'two', None, None, 'missing'],
                )
                self.assertIs(pane._at_cursor(), tasks[0])
                table = pane.query_one(DataTable)
                self.assertEqual(table.get_row_at(0)[1].plain, '── Codex ──')
                table.move_cursor(row=0)
                self.assertIsNone(pane._at_cursor())
                with patch('cli.manage.subprocess.run') as run:
                    pane.action_run()
                    run.assert_not_called()
                pane.action_reload()
                self.assertIs(pane._at_cursor(), tasks[0])


class PluginSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_modal_stays_open_until_synchronization_finishes(self) -> None:
        self.assertEqual(
            [(binding.key, binding.action) for binding in PluginsPane.BINDINGS],
            [("g", "configure")],
        )
        plugin = config.Plugin(
            path=Path("/demo"),
            source="contrib/demo",
            marketplace="personal",
            plugin="demo",
            enabled=True,
            enabled_base=True,
            enabled_local=False,
        )
        app = App()

        async with app.run_test() as pilot:
            screen = PluginSettingsScreen(plugin)
            app.push_screen(screen)
            await pilot.pause()

            self.assertTrue(screen.query_one("#plugin-global", Checkbox).value)
            self.assertFalse(screen.query_one("#plugin-local", Checkbox).value)
            with patch.object(PluginSettingsScreen, "_save_worker") as save_worker:
                await pilot.press("tab", "tab", "enter")
                await pilot.pause()
            save_worker.assert_called_once_with(True, False)

            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen, screen)


if __name__ == "__main__":
    unittest.main()
