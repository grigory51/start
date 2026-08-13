from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from textual.app import App
from textual.widgets import Checkbox

from cli import config
from cli.manage import PluginSettingsScreen, PluginsPane


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
