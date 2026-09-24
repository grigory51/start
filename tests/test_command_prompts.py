from __future__ import annotations

import unittest
import asyncio
from unittest.mock import AsyncMock

from textual.app import App
from textual.widgets import DataTable, Input, Static

from cli.command_sdk import RowAction, RowDetails, RowPrompt, TableSnapshot
from cli.command_sdk.screen import RowDetailsScreen, RowPromptScreen, TaskTableScreen
from cli.config import Task


class CommandPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_cancel_returns_to_table_without_calling_handler(self) -> None:
        submit = AsyncMock()

        async def provider(context, filters):
            return TableSnapshot(
                ("PID",), [("101",)],
                actions=(RowAction(
                    "e", "Export",
                    lambda context, row: _prompt(submit),
                ),),
            )

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task("test", "Test", "", {}), provider)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            self.assertIsInstance(app.screen, RowPromptScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen, screen)
            submit.assert_not_awaited()

    async def test_prompt_submits_input_and_shows_followup_result(self) -> None:
        submit = AsyncMock(return_value=RowDetails("Export", "done"))

        async def provider(context, filters):
            return TableSnapshot(
                ("PID",), [("101",)],
                actions=(RowAction(
                    "e", "Export",
                    lambda context, row: _prompt(submit),
                ),),
            )

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task("test", "Test", "", {}), provider)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            prompt = app.screen
            self.assertIsInstance(prompt, RowPromptScreen)
            prompt.query_one(Input).value = "/tmp/session.md"
            await pilot.press("enter")
            await pilot.pause()
            submit.assert_awaited_once_with(screen.context, "/tmp/session.md")
            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen, screen)

    async def test_table_action_is_available_for_empty_table(self) -> None:
        handler = AsyncMock()

        async def provider(context, filters):
            return TableSnapshot(
                ("PID",), [],
                table_actions=(RowAction("r", "Resume", handler),),
            )

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task("test", "Test", "", {}), provider)
            app.push_screen(screen)
            await pilot.pause()
            self.assertIn("r — Resume", str(screen.query_one("#task-status", Static).content))
            screen.query_one(DataTable).focus()
            await pilot.press("r")
            await pilot.pause()
            handler.assert_awaited_once_with(screen.context, {})

    async def test_transcript_actions_keep_details_on_prompt_cancel_and_completion(self) -> None:
        submit = AsyncMock(return_value=RowDetails("Export", "done"))
        details = RowDetails("Session", "Transcript text", actions=(
            RowAction("e", "Export", lambda context, row: _prompt(submit)),
        ))
        app = App()
        async with app.run_test() as pilot:
            screen = RowDetailsScreen(details)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            self.assertIsInstance(app.screen, RowPromptScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen, screen)
            submit.assert_not_awaited()
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one(Input).value = "/tmp/export.md"
            await pilot.press("enter")
            await pilot.pause()
            submit.assert_awaited_once_with(screen.context, "/tmp/export.md")
            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen, screen)
            self.assertFalse(screen.action_running)

    async def test_details_pending_clears_and_close_cancels_action(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow(context, row):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        app = App()
        async with app.run_test() as pilot:
            screen = RowDetailsScreen(RowDetails("Session", "Transcript", (RowAction("e", "Export", slow),)))
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("e")
            await asyncio.wait_for(started.wait(), 1)
            self.assertTrue(screen.action_running)
            self.assertIn("Export…", str(screen.query_one("#details-status", Static).content))
            await pilot.press("escape")
            await asyncio.wait_for(cancelled.wait(), 1)
            self.assertFalse(screen.action_running)


async def _prompt(handler: AsyncMock) -> RowPrompt:
    return RowPrompt("Export", "Path", "", handler)
