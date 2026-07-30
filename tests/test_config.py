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


if __name__ == "__main__":
    unittest.main()
