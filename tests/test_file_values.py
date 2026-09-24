from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cli import config
from cli.up import run_up


class FileValuesTests(unittest.TestCase):
    def test_nested_values_and_terminal_newline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'token').write_text(' secret\ninside \n')
            value = {'env': {'TOKEN': {'$file': 'token'}}, 'args': ['plain']}
            self.assertEqual(config._file_values(value, root, read=True),
                             {'env': {'TOKEN': ' secret\ninside '}, 'args': ['plain']})
            self.assertEqual(value['env']['TOKEN'], {'$file': 'token'})

    def test_invalid_missing_empty_and_non_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ({'$file': ''}, {'$file': 4}, {'$file': 'token', 'extra': True}):
                with self.subTest(value=value), self.assertRaises(config.FileValueError):
                    config._file_values(value, root, read=True)
            for data in (None, b'', b' \n', b'\xffSECRET'):
                if data is not None:
                    (root / 'token').write_bytes(data)
                with self.subTest(data=data), self.assertRaises(config.FileValueError) as error:
                    config._file_values({'$file': 'token'}, root, read=True)
                self.assertNotIn('SECRET', str(error.exception))

    def test_global_origin_survives_project_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            (root / 'token').write_text('global')
            (project / 'token').write_text('project')
            base = root / 'config.toml'
            base.write_text('[[ai.mcp]]\nname="grafana"\nenabled=false\n'
                            '[ai.mcp.server.env]\nTOKEN={"$file"="token"}\n')
            path = project / 'start.toml'
            path.write_text('[[ai.mcp]]\nname="grafana"\nenabled=true\n')
            with patch.object(config, 'REPO_DIR', root), patch.object(config, 'CONFIG', base), \
                 patch.object(config, 'CONFIG_LOCAL', root / 'absent.toml'):
                context = config.load_project(path)
                self.assertEqual(config.load_mcp(context)[0][0].server['env']['TOKEN'], 'global')
                path.write_text(path.read_text() + '[ai.mcp.server.env]\nTOKEN={"$file"="token"}\n')
                self.assertEqual(config.load_mcp(config.load_project(path))[0][0].server['env']['TOKEN'], 'project')
                (root / 'token').unlink()
                self.assertFalse(config.load_mcp()[0][0].enabled)

    def test_missing_file_stops_before_global_changes(self) -> None:
        with patch('cli.up.config.load_mcp', side_effect=config.FileValueError('Missing token file')), \
             patch('cli.up.update_submodules') as update, patch('cli.up.run_install') as install, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_up(), 1)
        update.assert_not_called()
        install.assert_not_called()
        self.assertIn('Missing token file', output.getvalue())

    def test_reference_is_atomic_and_home_expands(self) -> None:
        merged = config._merge_tables({'token': {'$file': 'old'}}, {'token': {'$file': 'new'}})
        self.assertEqual(merged, {'token': {'$file': 'new'}})
        self.assertEqual(config._file_values({'$file': '~/token'}, Path('/unused')),
                         {'$file': str(Path.home() / 'token')})
