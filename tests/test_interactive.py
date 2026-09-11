from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import patch

from textual.app import App
from textual.widgets import DataTable, Input, Static

from cli.config import Task, TaskFilter
from cli.command_sdk import TableSnapshot, TaskContext, load_provider
from cli.command_sdk.screen import TaskTableScreen


class SdkTests(unittest.TestCase):
    def test_snapshot_rejects_inconsistent_rows_without_ui(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Количество значений'):
            TableSnapshot(('Port',), [('80', 'TCP')])

    def test_loader_rejects_sync_function(self) -> None:
        with self.assertRaisesRegex(TypeError, 'async'):
            load_provider('builtins:print')

    def test_configured_commands_load_without_textual(self) -> None:
        result = subprocess.run(
            [sys.executable, '-c', '''
import sys
from cli.config import load_tasks
from cli.command_sdk import load_provider
tasks, warnings = load_tasks()
assert not warnings, warnings
references = {reference for task in tasks for reference in task.view.values()}
assert references
for reference in references:
    load_provider(reference)
assert 'textual' not in sys.modules
'''], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_argv_is_not_interpreted_by_shell(self) -> None:
        value = 'a; echo nope $(echo nope)'
        result = await TaskContext().run(sys.executable, '-c', 'import sys; print(sys.argv[1])', value)
        self.assertEqual(result.strip(), value)

    async def test_nonzero_status_is_visible(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as error:
            await TaskContext().run(sys.executable, '-c', 'import sys; sys.stderr.write("failure"); sys.exit(2)')
        self.assertEqual(error.exception.stderr, 'failure')

    async def test_cancellation_reaps_process(self) -> None:
        real_create = asyncio.create_subprocess_exec
        processes = []

        async def capture(*args, **kwargs):
            process = await real_create(*args, **kwargs)
            processes.append(process)
            return process

        with patch('cli.command_sdk.asyncio.create_subprocess_exec', side_effect=capture):
            task = asyncio.create_task(TaskContext().run(sys.executable, '-c', 'import time; time.sleep(60)'))
            while not processes:
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(processes[0].returncode)


class TableScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_refresh_and_q_return(self) -> None:
        calls = []

        async def provider(context, filters):
            calls.append(dict(filters))
            return TableSnapshot(('Port',), [(filters['port'] or 'all',)])

        task = Task('example', 'Example', '', {}, filters=[TaskFilter('port', 'Port')], refresh=0.2)
        app = App()
        async with app.run_test() as pilot:
            original = app.screen
            screen = TaskTableScreen(task, provider)
            app.push_screen(screen)
            await pilot.pause()
            self.assertEqual(screen.query_one(DataTable).get_row_at(0)[0].plain, 'all')
            field = screen.query_one(Input)
            field.focus()
            field.value = '443'
            await pilot.pause()
            self.assertEqual(calls[-1], {'port': '443'})
            before = len(calls)
            await asyncio.sleep(0.25)
            await pilot.pause()
            self.assertGreater(len(calls), before)
            await pilot.press('enter', 'q')
            await pilot.pause()
            self.assertIs(app.screen, original)
            after = len(calls)
            await asyncio.sleep(0.25)
            self.assertEqual(len(calls), after)

    async def test_escape_cancels_pending_provider(self) -> None:
        cancelled = asyncio.Event()

        async def provider(context, filters):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        app = App()
        async with app.run_test() as pilot:
            original = app.screen
            app.push_screen(TaskTableScreen(Task('slow', 'Slow', '', {}), provider))
            await pilot.pause()
            await pilot.press('escape')
            await pilot.pause()
            await asyncio.wait_for(cancelled.wait(), 1)
            self.assertIs(app.screen, original)

    async def test_provider_error_does_not_close_manager(self) -> None:
        async def provider(context, filters):
            raise ValueError('bad filter')

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task('bad', 'Bad', '', {}), provider)
            app.push_screen(screen)
            await pilot.pause()
            self.assertIn('bad filter', str(screen.query_one('#task-status', Static).content))
            self.assertIs(app.screen, screen)
