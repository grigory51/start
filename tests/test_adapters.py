from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tomllib

from cli import adapters, config


class AdapterTests(unittest.TestCase):
    def test_skill_frontmatter_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skill = Path(raw) / "demo"
            skill.mkdir()
            source = skill / "SKILL.md"
            source.write_text(
                "---\n"
                "name: demo\n"
                "description: Has a colon: and remains valid\n"
                "argument-hint: ignored\n"
                "compatibility: claude\n"
                "---\n\n"
                "# Demo\n"
            )
            rendered = adapters.normalized_skill_text(source)
            self.assertIn('description: "Has a colon: and remains valid"', rendered)
            self.assertNotIn("argument-hint", rendered)
            self.assertNotIn("compatibility", rendered)

    def test_symlinked_skill_frontmatter_becomes_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rules = root / "rules"
            rules.mkdir()
            (rules / "demo.md").write_text(
                "---\nname: demo\ndescription: Demo\n---\n\nDo it.\n"
            )
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").symlink_to("../../rules/demo.md")
            rendered = adapters.materialize_skill(skill, root / "generated" / "demo")
            self.assertFalse((rendered / "SKILL.md").is_symlink())
            self.assertIn("description: \"Demo\"", (rendered / "SKILL.md").read_text())

    def test_markdown_agent_becomes_codex_toml(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "programmer.md"
            source.write_text(
                "---\n"
                "name: programmer\n"
                "description: Writes code\n"
                "tools: Read, Write, Edit\n"
                "model: sonnet\n"
                "codex_model: gpt-5.6-terra\n"
                "codex_reasoning_effort: medium\n"
                "skills:\n"
                "  - my-principles\n"
                "---\n\n"
                "Implement the task.\n"
            )
            rendered = tomllib.loads(adapters.render_codex_agent(source))
            self.assertEqual(rendered["name"], "programmer")
            self.assertEqual(rendered["sandbox_mode"], "workspace-write")
            self.assertIn("my-principles", rendered["developer_instructions"])
            self.assertEqual(rendered["model"], "gpt-5.6-terra")
            self.assertEqual(rendered["model_reasoning_effort"], "medium")

    def test_read_only_agent_stays_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "reviewer.md"
            source.write_text(
                "---\nname: reviewer\ndescription: Reviews code\n"
                "tools: Read, Grep, Bash\n---\n\nReview only.\n"
            )
            rendered = tomllib.loads(adapters.render_codex_agent(source))
            self.assertEqual(rendered["sandbox_mode"], "read-only")

    def test_claude_plugin_generates_codex_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin_root = root / "demo-plugin"
            (plugin_root / ".claude-plugin").mkdir(parents=True)
            (plugin_root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "demo-plugin",
                        "version": "1.2.3",
                        "description": "Demo",
                        "skills": "./skills/",
                    }
                )
            )
            skill = plugin_root / "skills" / "demo-skill"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: Demo skill\n"
                "argument-hint: x\n---\n\nDo it.\n"
            )
            hooks = plugin_root / "hooks"
            hooks.mkdir()
            (hooks / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "echo check"}
                                    ]
                                }
                            ]
                        }
                    }
                )
            )
            plugin = config.Plugin(
                name="demo-plugin",
                path=plugin_root,
                source="demo-plugin",
                marketplace="demo",
                plugin="demo-plugin",
                enabled=True,
                description="Demo",
                native_platforms=("claude",),
                platform_paths={"claude": plugin_root},
            )
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}):
                generated = adapters.generate_codex_plugin(plugin)
            manifest = json.loads(
                (generated / ".codex-plugin" / "plugin.json").read_text()
            )
            self.assertEqual(manifest["name"], "demo-plugin")
            self.assertEqual(manifest["author"]["name"], "Upstream plugin author")
            self.assertIsInstance(manifest["interface"], dict)
            self.assertNotIn("hooks", manifest)
            normalized = (generated / "skills" / "demo-skill" / "SKILL.md").read_text()
            self.assertNotIn("argument-hint", normalized)
            generated_hooks = json.loads(
                (generated / "hooks" / "hooks.json").read_text()
            )
            command = generated_hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn("${PLUGIN_ROOT}/scripts/start-hook-adapter.py", command)
            self.assertEqual(adapters.validate_generated_plugin(generated), [])


if __name__ == "__main__":
    unittest.main()
