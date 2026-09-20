from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from textual.app import App
from textual.widgets import DataTable, Input, LoadingIndicator, Static, TextArea

from cli.config import Task, TaskFilter
from cli.manage import ManagerApp
from cli.command_sdk import RowAction, RowDetails, RowTable, TableSnapshot, TaskContext, load_provider
from cli.command_sdk.screen import ConfirmActionScreen, RowDetailsScreen, TaskTableScreen


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

class RowActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_requires_confirmation_and_captures_selected_row(self) -> None:
        handler = AsyncMock()

        async def provider(context, filters):
            return TableSnapshot(('PID',), [('123',)], actions=(
                RowAction('k', 'Terminate', handler, 'Terminate {PID}?'),
            ))

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task('test', 'Test', '', {}), provider)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press('k')
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmActionScreen)
            handler.assert_not_awaited()
            await pilot.press('escape')
            await pilot.pause()
            handler.assert_not_awaited()
            self.assertIs(app.screen, screen)
            await pilot.press('k')
            await pilot.pause()
            await pilot.press('y')
            await pilot.pause()
            handler.assert_awaited_once_with(screen.context, {'PID': '123'})
            self.assertIs(app.screen, screen)

class ManagerNavigationApp(App):
    BINDINGS = ManagerApp.BINDINGS
    action_toggle_domain = ManagerApp.action_toggle_domain


class CommandNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_shortcuts_do_not_act_inside_command(self) -> None:
        calls = []

        async def provider(context, filters):
            calls.append(filters)
            return TableSnapshot(('PID',), [('123',)])

        app = ManagerNavigationApp()
        async with app.run_test() as pilot:
            original = app.screen
            screen = TaskTableScreen(Task('test', 'Test', '', {}, refresh=3600), provider)
            app.push_screen(screen)
            await pilot.pause()
            before = len(calls)
            await pilot.press('f2', 'ctrl+r')
            await pilot.pause()
            self.assertIs(app.screen, screen)
            self.assertEqual(len(calls), before)
            await pilot.press('escape')
            await pilot.pause()
            self.assertIs(app.screen, original)


class ProcessDetailsScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_opens_details_and_escape_returns_to_same_table(self) -> None:
        details = RowDetails('PID 456', '/path with spaces/python ' + '--argument ' * 100)
        handler = AsyncMock(return_value=details)

        async def provider(context, filters):
            return TableSnapshot(('PID',), [('123',), ('456',)], actions=(
                RowAction('enter', 'Details', handler),
            ))

        app = ManagerNavigationApp()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task('test', 'Test', '', {}, refresh=0.2,
                                         filters=[TaskFilter('port', 'Port')]), provider)
            app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one(DataTable)
            field = screen.query_one(Input)
            field.focus()
            await pilot.press('enter')
            await pilot.pause()
            self.assertIs(app.screen, screen)
            handler.assert_not_awaited()
            table.move_cursor(row=1)
            await pilot.press('enter')
            await pilot.pause()
            self.assertIsInstance(app.screen, RowDetailsScreen)
            handler.assert_awaited_once_with(screen.context, {'PID': '456'})
            self.assertEqual(app.screen.query_one(TextArea).text, details.text)
            self.assertTrue(screen.action_running)
            await pilot.press('f2', 'escape')
            await pilot.pause()
            self.assertIs(app.screen, screen)
            self.assertFalse(screen.action_running)
            self.assertEqual(table.cursor_row, 1)
            await pilot.press('enter')
            await pilot.pause()
            await pilot.press('q')
            await pilot.pause()
            self.assertIs(app.screen, screen)


class NestedTableTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_table_pauses_parent_and_returns_one_level(self) -> None:
        details = AsyncMock(return_value=RowDetails('Process', 'Full command'))
        child_provider = AsyncMock(return_value=TableSnapshot(
            ('PID',), [('101',)], (RowAction('enter', 'Details', details),),
        ))
        open_child = AsyncMock(return_value=RowTable(
            Task('child', 'Session tree', '', {}, refresh=0.2), child_provider,
        ))
        parent_provider = AsyncMock(return_value=TableSnapshot(
            ('PID', 'CWD'), [('100', '/project')], (RowAction('enter', 'Open', open_child),),
        ))
        app = ManagerNavigationApp()
        async with app.run_test() as pilot:
            original = app.screen
            parent = TaskTableScreen(Task('parent', 'Sessions', '', {}, refresh=0.2), parent_provider)
            app.push_screen(parent)
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            child = app.screen
            self.assertIsInstance(child, TaskTableScreen)
            self.assertIsNot(child, parent)
            self.assertTrue(parent.action_running)
            calls = parent_provider.await_count
            await pilot.pause(0.4)
            self.assertEqual(parent_provider.await_count, calls)
            self.assertGreater(child_provider.await_count, 1)
            await pilot.press('enter')
            await pilot.pause()
            self.assertIsInstance(app.screen, RowDetailsScreen)
            await pilot.press('escape')
            await pilot.pause()
            self.assertIs(app.screen, child)
            await pilot.press('f2', 'q')
            await pilot.pause()
            self.assertIs(app.screen, parent)
            self.assertFalse(parent.action_running)
            self.assertGreater(parent_provider.await_count, calls)
            await pilot.press('escape')
            await pilot.pause()
            self.assertIs(app.screen, original)


class LoadingStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_loading_refresh_and_error(self) -> None:
        pending = asyncio.Event()
        failure = False

        async def provider(context, filters):
            await pending.wait()
            if failure:
                raise ValueError('load failed')
            return TableSnapshot(('PID',), [('100',)])

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task('slow', 'Slow', '', {}, refresh=3600), provider)
            app.push_screen(screen)
            await pilot.pause()
            indicator = screen.query_one(LoadingIndicator)
            self.assertTrue(indicator.display)
            self.assertIn('Загрузка', str(screen.query_one('#task-status', Static).render()))
            pending.set()
            await pilot.pause()
            self.assertFalse(indicator.display)
            status = str(screen.query_one('#task-status', Static).render())
            table_region = screen.query_one(DataTable).region
            pending.clear()
            screen.action_reload()
            await pilot.pause()
            self.assertFalse(indicator.display)
            self.assertEqual(screen.query_one(DataTable).row_count, 1)
            self.assertEqual(str(screen.query_one('#task-status', Static).render()), status)
            self.assertEqual(screen.query_one(DataTable).region, table_region)
            failure = True
            pending.set()
            await pilot.pause()
            self.assertFalse(indicator.display)
            self.assertIn('load failed', str(screen.query_one('#task-status', Static).render()))

    async def test_cancelled_request_does_not_hide_new_loading(self) -> None:
        pending = asyncio.Event()
        cancelled = asyncio.Event()

        async def provider(context, filters):
            try:
                await pending.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return TableSnapshot(('PID',), [])

        app = App()
        async with app.run_test() as pilot:
            screen = TaskTableScreen(Task('slow', 'Slow', '', {}, refresh=3600), provider)
            app.push_screen(screen)
            await pilot.pause()
            screen.action_reload()
            await pilot.pause()
            self.assertTrue(cancelled.is_set())
            self.assertTrue(screen.query_one(LoadingIndicator).display)
            pending.set()
            await pilot.pause()
            self.assertFalse(screen.query_one(LoadingIndicator).display)
