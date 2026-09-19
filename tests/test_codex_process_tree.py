from __future__ import annotations

import subprocess
import unittest
from unittest.mock import AsyncMock, patch

from cli.command_sdk import TaskContext, load_provider
from cli.commands.codex_process_tree import COLUMNS, process_details, snapshot as sessions_snapshot, tree_snapshot as snapshot, open_session, elapsed_seconds
from cli import config


PROCESSES = """100 1 100 1.0 0.1 00:10 S /bin/codex
101 100 100 2.0 0.2 00:05 S child --path /path with spaces
102 100 100 4.0 0.4 00:03 S uv run freecad_mcp_server.py
103 102 100 0.0 0.1 00:02 S worker
104 101 100 0.0 0.0 00:01 S /bin/codex
200 1 200 0.5 1.5 07:00:00 S /bin/codex
300 1 300 0.0 0.0 00:01 S unrelated
"""


class CodexProcessTreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tree_order_mcp_inheritance_and_no_duplicate_nested_codex(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock(side_effect=['100\n104\n200\n999\n', PROCESSES])) as run:
            result = await snapshot(context, {})
        self.assertEqual(result.columns, COLUMNS)
        self.assertEqual([row[1] for row in result.rows], ['200', '100', '101', '104', '102', '103'])
        rows = {row[1]: dict(zip(COLUMNS, row)) for row in result.rows}
        self.assertEqual(rows['103']['TYPE'], 'mcp')
        self.assertEqual(rows['104']['SESSION'], '100')
        self.assertEqual(rows['101']['COMMAND'], '↳ child --path /path with spaces')
        self.assertEqual(rows['103']['COMMAND'], '  ↳ worker')
        self.assertEqual(run.await_count, 2)
        self.assertEqual(run.await_args_list[0].args, ('pgrep', '-ix', 'codex'))
        self.assertEqual(run.await_args_list[1].args, (
            'ps', '-ww', '-e', '-o', 'pid=,ppid=,pgid=,pcpu=,pmem=,etime=,stat=,args=',
        ))
        self.assertEqual(result.actions[0].key, 'enter')

    async def test_filters(self) -> None:
        for filters, expected in [
            ({'session': '100', 'kind': 'mcp'}, ['102', '103']),
            ({'command': 'PATH WITH SPACES'}, ['101']),
            ({'kind': 'process'}, ['200', '100', '101', '104']),
            ({'session': '999'}, []),
        ]:
            with self.subTest(filters=filters):
                context = TaskContext()
                with patch.object(context, 'run', AsyncMock(side_effect=['100\n200\n', PROCESSES])):
                    result = await snapshot(context, filters)
                self.assertEqual([row[1] for row in result.rows], expected)

    async def test_empty_and_invalid_input(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock(return_value='')) as run:
            self.assertEqual((await snapshot(context, {})).rows, [])
            run.assert_awaited_once()
            run.reset_mock()
            for filters in ({'session': '0'}, {'session': 'a'}, {'session': '-1'}, {'kind': 'invalid'}):
                with self.assertRaises(ValueError):
                    await snapshot(context, filters)
            run.assert_not_awaited()

    async def test_command_error_propagates(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock(side_effect=subprocess.CalledProcessError(2, 'pgrep'))):
            with self.assertRaises(subprocess.CalledProcessError):
                await snapshot(context, {})

    async def test_details_include_full_command_cwd_and_unfiltered_totals(self) -> None:
        context = TaskContext()
        with patch('cli.commands.codex_process_tree.sys.platform', 'darwin'), patch.object(
            context, 'run', AsyncMock(side_effect=['100\n200\n', PROCESSES, 'p100\nn/path with spaces\n'])
        ) as run:
            details = await process_details(context, {'SESSION': '100', 'PID': '101'})
        self.assertIn('child --path /path with spaces', details.text)
        self.assertIn('CWD сессии: /path with spaces', details.text)
        self.assertIn('Всего процессов: 5 · CPU: 7.00% · MEM: 0.80%', details.text)
        self.assertEqual(run.await_args.args, ('lsof', '-a', '-p', '100', '-d', 'cwd', '-Fpn'))

    async def test_details_for_exited_process(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock(return_value='')) as run:
            details = await process_details(context, {'SESSION': '100', 'PID': '101'})
        self.assertIn('завершился', details.text)
        run.assert_awaited_once()

    async def test_linux_cwd_unavailable(self) -> None:
        context = TaskContext()
        with patch('cli.commands.codex_process_tree.sys.platform', 'linux'), patch(
            'cli.commands.codex_process_tree.Path.readlink', side_effect=PermissionError('denied')
        ), patch.object(context, 'run', AsyncMock(side_effect=['100\n', PROCESSES])):
            details = await process_details(context, {'SESSION': '100', 'PID': '100'})
        self.assertIn('Недоступен: denied', details.text)

    def test_native_task_registration(self) -> None:
        tasks, warnings = config.load_tasks()
        self.assertFalse(warnings)
        task = next(task for task in tasks if task.name == 'codex-process-tree')
        self.assertFalse(task.run)
        for platform in ('darwin', 'linux'):
            self.assertIs(load_provider(task.view[platform]), sessions_snapshot)

    async def test_sessions_show_cwd_totals_and_filter(self) -> None:
        context = TaskContext()
        for query, expected in [('', ['200', '100']), ('project a', ['100'])]:
            with patch('cli.commands.codex_process_tree.sys.platform', 'darwin'), patch.object(
                context, 'run', AsyncMock(side_effect=[
                    '100\n200\n', PROCESSES, 'p100\nn/work/project a\np200\nn/work/project b\n',
                ])
            ) as run:
                result = await sessions_snapshot(context, {'cwd': query})
            self.assertEqual([row[0] for row in result.rows], expected)
            row = next(row for row in result.rows if row[0] == '100')
            self.assertEqual(row, ('100', '/work/project a', '5', '7.00', '0.80', '00:10'))
            self.assertEqual(run.await_args.args, ('lsof', '-a', '-p', '200,100', '-d', 'cwd', '-Fpn'))

    async def test_open_session_keeps_fixed_pid_and_process_filters(self) -> None:
        context = TaskContext()
        child = await open_session(context, {'PID': '100', 'CWD': '/work/project'})
        self.assertIn('/work/project', child.task.title)
        with patch.object(context, 'run', AsyncMock(side_effect=['100\n200\n', PROCESSES])):
            result = await child.provider(context, {'kind': 'mcp'})
        self.assertEqual([row[1] for row in result.rows], ['102', '103'])

    async def test_empty_sessions_do_not_query_cwd(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock(return_value='')) as run:
            result = await sessions_snapshot(context, {})
        self.assertEqual(result.rows, [])
        run.assert_awaited_once()

    async def test_session_sorting(self) -> None:
        for column, ascending in [('PID', ['100', '200']), ('CWD', ['100', '200']),
                                  ('CPU %', ['200', '100']), ('MEM %', ['100', '200']),
                                  ('PROCESSES', ['200', '100']), ('ELAPSED', ['100', '200'])]:
            for order in ('asc', 'desc'):
                with self.subTest(column=column, order=order):
                    context = TaskContext()
                    with patch('cli.commands.codex_process_tree.sys.platform', 'darwin'), patch.object(
                        context, 'run', AsyncMock(side_effect=[
                            '100\n200\n', PROCESSES, 'p100\nn/a\np200\nn/b\n',
                        ])
                    ):
                        result = await sessions_snapshot(context, {'sort': column, 'order': order})
                    self.assertEqual([row[0] for row in result.rows],
                                     ascending if order == 'asc' else list(reversed(ascending)))

    async def test_tree_sorting_keeps_parent_and_descendants_together(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock(side_effect=['100\n200\n', PROCESSES])):
            result = await snapshot(context, {'session': '100', 'sort': 'CPU %', 'order': 'desc'})
        self.assertEqual([row[1] for row in result.rows], ['100', '102', '103', '101', '104'])

    async def test_invalid_sorting_is_rejected_before_commands(self) -> None:
        context = TaskContext()
        with patch.object(context, 'run', AsyncMock()) as run:
            for provider in (snapshot, sessions_snapshot):
                for filters in ({'sort': 'invalid'}, {'order': 'invalid'}):
                    with self.assertRaises(ValueError):
                        await provider(context, filters)
            run.assert_not_awaited()

    def test_elapsed_sort_value_handles_days_hours_and_minutes(self) -> None:
        self.assertEqual([elapsed_seconds(value) for value in ('59:59', '01:00:00', '1-00:00:00')],
                         [3599, 3600, 86400])
