from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import config


class ConfigTests(unittest.TestCase):
    def test_ai_catalog_defaults_to_both_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo\n---\n\nDo it.\n"
            )
            cfg = root / "config.toml"
            cfg.write_text(
                '[[ai.skills]]\npath = "skills"\nenabled = ["*"]\n'
            )
            local = root / "config.local.toml"
            with (
                patch.object(config, "REPO_DIR", root),
                patch.object(config, "CONFIG", cfg),
                patch.object(config, "CONFIG_LOCAL", local),
            ):
                result = config.load()
            self.assertEqual(result.skills[0].platforms, ("claude", "codex"))

    def test_platform_only_is_explicit(self) -> None:
        warnings: list[str] = []
        self.assertEqual(
            config._platforms({"platforms": ["claude"]}, "demo", warnings),
            ("claude",),
        )
        self.assertEqual(warnings, [])

    def test_legacy_claude_catalog_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = root / "config.toml"
            cfg.write_text('[[claude.skills]]\npath = "skills"\n')
            with (
                patch.object(config, "REPO_DIR", root),
                patch.object(config, "CONFIG", cfg),
                patch.object(config, "CONFIG_LOCAL", root / "config.local.toml"),
            ):
                result = config.load()
            self.assertTrue(any("устаревшая секция" in item for item in result.warnings))
            self.assertEqual(result.skills, [])

    def test_hooks_have_platform_specific_events(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = root / "config.toml"
            cfg.write_text(
                '[[ai.hooks]]\npath = "ai/hooks/notify.sh"\n'
                'events = { claude = ["Stop", "Notification"], '
                'codex = ["Stop", "PermissionRequest"] }\n'
            )
            with (
                patch.object(config, "CONFIG", cfg),
                patch.object(config, "CONFIG_LOCAL", root / "config.local.toml"),
            ):
                claude, claude_warnings = config.load_hooks("claude")
                codex, codex_warnings = config.load_hooks("codex")
            self.assertEqual(claude[0]["events"], ["Stop", "Notification"])
            self.assertEqual(codex[0]["events"], ["Stop", "PermissionRequest"])
            self.assertEqual(claude_warnings + codex_warnings, [])

    def test_local_mcp_extends_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = root / "config.toml"
            cfg.write_text(
                '[[ai.mcp]]\nname = "global"\nenabled = true\n'
                '[ai.mcp.server]\ncommand = "global"\n'
            )
            local = root / "config.local.toml"
            local.write_text(
                '[[ai.mcp]]\nname = "monium_mcp"\nenabled = true\n'
                '[ai.mcp.server]\ncommand = "ya"\n'
                '[[ai.mcp]]\nname = "yandex_messenger"\nenabled = true\n'
                '[ai.mcp.server]\nurl = "https://example.test/sse"\n'
                'headers = { Authorization = "OAuth token" }\n'
                '[local.ai.mcp]\nmonium_mcp = false\n'
            )
            with (
                patch.object(config, "CONFIG", cfg),
                patch.object(config, "CONFIG_LOCAL", local),
            ):
                servers, warnings = config.load_mcp()

            by_name = {server.name: server for server in servers}
            self.assertEqual(set(by_name), {"global", "monium_mcp", "yandex_messenger"})
            self.assertFalse(by_name["monium_mcp"].enabled)
            self.assertTrue(by_name["monium_mcp"].local_only)
            self.assertFalse(by_name["monium_mcp"].enabled_local)
            self.assertEqual(
                by_name["yandex_messenger"].server["headers"]["Authorization"],
                "OAuth token",
            )
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
