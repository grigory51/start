from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import tomllib

from cli import config
from cli.ai import codex
from cli.install import Ctx


class CodexConfigTests(unittest.TestCase):
    def test_personal_plugin_path_is_home_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with patch("pathlib.Path.home", return_value=Path(raw)):
                self.assertEqual(codex.personal_plugins_dir(), Path(raw) / "plugins")

    def test_merge_preserves_foreign_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            codex_home = home / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                'model = "custom"\n'
                '[plugins."claude-mem@personal"]\nenabled = true\n'
                '[plugins."foreign@other"]\nenabled = true\n'
                '[hooks.state."claude-mem@personal:hooks/hooks.json:session_start:0:0"]\n'
                'trusted_hash = "managed"\n'
                '[hooks.state."foreign@other:hooks/hooks.json:session_start:0:0"]\n'
                'trusted_hash = "foreign"\n'
                '[tui]\nanimations = false\n'
                '[mcp_servers.foreign]\ncommand = "foreign"\n'
                '[features]\nforeign = true\nold_managed = true\n'
            )
            (codex_home / ".start-config-managed.json").write_text(
                '{"mcp": [], "features": ["old_managed"], "status_line": false}\n'
            )
            status = {
                "items": ["model-with-reasoning", "context-remaining"],
                "use_colors": True,
            }
            with (
                patch.dict(os.environ, {"HOME": str(home), "CODEX_HOME": str(codex_home)}),
                patch.object(config, "load_mcp", return_value=([], [])),
                patch.object(config, "load_codex_features", return_value={"apps": False}),
                patch.object(config, "load_statusline", return_value=status),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    codex.merge_config(
                        Ctx(dry_run=False, force=False),
                        {"claude-mem@personal"},
                    )
            merged = tomllib.loads((codex_home / "config.toml").read_text())
            self.assertEqual(merged["model"], "custom")
            self.assertFalse(merged["tui"]["animations"])
            self.assertNotIn("claude-mem@personal", merged["plugins"])
            self.assertTrue(merged["plugins"]["foreign@other"]["enabled"])
            self.assertNotIn(
                "claude-mem@personal:hooks/hooks.json:session_start:0:0",
                merged["hooks"]["state"],
            )
            self.assertIn(
                "foreign@other:hooks/hooks.json:session_start:0:0",
                merged["hooks"]["state"],
            )
            self.assertEqual(merged["mcp_servers"]["foreign"]["command"], "foreign")
            self.assertTrue(merged["features"]["foreign"])
            self.assertFalse(merged["features"]["apps"])
            self.assertNotIn("old_managed", merged["features"])
            self.assertEqual(merged["tui"]["status_line"], status["items"])
            sidecar = json.loads((codex_home / ".start-config-managed.json").read_text())
            self.assertEqual(sidecar["features"], ["apps"])
            self.assertTrue(sidecar["status_line"])
            self.assertIn("MCP ->", output.getvalue())
            self.assertIn("Итого: 0, изменено 0.", output.getvalue())
            self.assertNotIn("Codex config", output.getvalue())


if __name__ == "__main__":
    unittest.main()
