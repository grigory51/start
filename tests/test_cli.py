from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import patch

from cli.__main__ import build_parser, main
from cli.sections import M_ALIASES, M_TARGETS


class CliTests(unittest.TestCase):
    def test_default_and_sections_open_tui(self) -> None:
        for section in (None, *(name for name in M_TARGETS if name not in M_ALIASES)):
            with self.subTest(section=section), patch('sys.argv', ['start', *([f"tab:{section}"] if section else [])]), \
                 patch('cli.manage.run_manage', return_value=0) as manage:
                self.assertEqual(main(), 0)
                manage.assert_called_once_with(target=section)

    def test_help_does_not_open_tui(self) -> None:
        with patch('sys.argv', ['start', '--help']), patch('cli.manage.run_manage') as manage, \
             redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 0)
        manage.assert_not_called()

    def test_up_keeps_its_handler_and_flags(self) -> None:
        with patch('sys.argv', ['start', 'up', '--only', 'ai:codex', '--dry-run']), \
             patch('cli.__main__.run_up', return_value=0) as up, patch('cli.manage.run_manage') as manage:
            self.assertEqual(main(), 0)
        self.assertTrue(up.call_args.kwargs['dry_run'])
        self.assertEqual(up.call_args.kwargs['only'], 'ai:codex')
        manage.assert_not_called()

    def test_old_manage_layer_is_removed(self) -> None:
        for command in ('manage', 'm', 'skills', 'mcp'):
            with self.subTest(command=command), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                build_parser().parse_args([command])
