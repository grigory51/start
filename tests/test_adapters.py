from __future__ import annotations

import json
import os
import subprocess
import sys
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

    def test_codex_skill_links_contents_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill_root = root / "skills" / "demo"
            references = skill_root / "references"
            references.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo\nargument-hint: ignored\n---\n\nDo it.\n"
            )
            (references / "guide.md").write_text("Original\n")
            skill = config.Skill(
                name="demo",
                path=skill_root,
                source="skills",
                enabled=True,
            )

            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}):
                rendered = adapters.codex_skill(skill)

            self.assertFalse((rendered / "SKILL.md").is_symlink())
            self.assertNotIn("argument-hint", (rendered / "SKILL.md").read_text())
            self.assertTrue((rendered / "references").is_symlink())
            (rendered / "references" / "guide.md").write_text("Changed\n")
            self.assertEqual((references / "guide.md").read_text(), "Changed\n")

    def test_codex_skill_links_compatible_frontmatter_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill_root = root / "skills" / "demo"
            skill_root.mkdir(parents=True)
            source = skill_root / "SKILL.md"
            source.write_text('---\nname: demo\ndescription: "Demo"\n---\n\nOriginal\n')
            skill = config.Skill(
                name="demo",
                path=skill_root,
                source="skills",
                enabled=True,
            )

            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}):
                rendered = adapters.codex_skill(skill)

            self.assertTrue((rendered / "SKILL.md").is_symlink())
            (rendered / "SKILL.md").write_text(
                '---\nname: demo\ndescription: "Demo"\n---\n\nChanged\n'
            )
            self.assertIn("Changed", source.read_text())

    def test_codex_skill_normalizes_invalid_plain_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill_root = root / "skills" / "demo"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Has a colon: and remains valid\n---\n\nDo it.\n"
            )
            skill = config.Skill(
                name="demo",
                path=skill_root,
                source="skills",
                enabled=True,
            )

            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}):
                rendered = adapters.codex_skill(skill)

            self.assertFalse((rendered / "SKILL.md").is_symlink())
            self.assertIn(
                'description: "Has a colon: and remains valid"',
                (rendered / "SKILL.md").read_text(),
            )

    def test_codex_skill_rejects_extra_links_into_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill_root = root / "skills" / "demo"
            (skill_root / "references").mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo\n---\n\nDo it.\n"
            )
            skill = config.Skill(
                name="demo",
                path=skill_root,
                source="skills",
                enabled=True,
                symlinks=[{"source": "shared", "destination": "references/extra"}],
            )

            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}),
                self.assertRaises(adapters.AdapterError),
            ):
                adapters.codex_skill(skill)

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
            (skill / "reference.md").write_text("Reference\n")
            hooks = plugin_root / "hooks"
            hooks.mkdir()
            (hooks / "hook-helper.py").write_text(
                "import sys\nprint(sys.argv[1])\n"
            )
            (hooks / "helper.py").symlink_to("hook-helper.py")
            (hooks / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": sys.executable,
                                            "args": [
                                                "${CLAUDE_PLUGIN_ROOT}/hooks/helper.py",
                                                "${tool_input.file_path}",
                                            ],
                                        },
                                        {
                                            "type": "command",
                                            "command": (
                                                f'{sys.executable} '
                                                '"${CLAUDE_PLUGIN_ROOT}/hooks/helper.py" '
                                                '"${tool_input.file_path}"'
                                            ),
                                        },
                                    ]
                                }
                            ]
                        }
                    }
                )
            )
            plugin = config.Plugin(
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
            self.assertFalse(
                (generated / "skills" / "demo-skill" / "reference.md").is_symlink()
            )
            generated_hooks = json.loads(
                (generated / "hooks" / "hooks.json").read_text()
            )
            command = generated_hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn("${PLUGIN_ROOT}/scripts/start-hook-adapter.py", command)
            self.assertFalse((generated / "hooks" / "helper.py").is_symlink())
            result = subprocess.run(
                [sys.executable, generated / "scripts" / "start-hook-adapter.py", "0"],
                input=json.dumps({"tool_input": {"file_path": "/tmp/demo.py"}}),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "/tmp/demo.py")
            result = subprocess.run(
                [sys.executable, generated / "scripts" / "start-hook-adapter.py", "1"],
                input=json.dumps(
                    {"tool_input": {"file_path": "/tmp/$(printf INJECTED)"}}
                ),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "/tmp/$(printf INJECTED)")
            self.assertEqual(adapters.validate_generated_plugin(generated), [])

    def test_native_codex_plugin_excludes_invalid_undeclared_skill(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin_root = root / "cad"
            (plugin_root / ".codex-plugin").mkdir(parents=True)
            (plugin_root / ".codex-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "cad",
                        "description": "CAD",
                        "skills": "./skills/",
                    }
                )
            )
            declared = plugin_root / "skills" / "cad"
            declared.mkdir(parents=True)
            (declared / "SKILL.md").write_text(
                "---\nname: cad\ndescription: CAD\n---\n\n# CAD\n"
            )
            undeclared = plugin_root / "viewer" / "skills" / "smui"
            undeclared.mkdir(parents=True)
            (undeclared / "SKILL.md").write_text("# smui\n")
            plugin = config.Plugin(
                path=plugin_root,
                source="cad",
                marketplace="personal",
                plugin="cad",
                enabled=True,
                description="CAD",
                native_platforms=("codex",),
                platform_paths={"codex": plugin_root},
            )

            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}):
                generated = adapters.codex_plugin(plugin)

            self.assertTrue((generated / "skills" / "cad" / "SKILL.md").is_file())
            self.assertFalse(
                (generated / "viewer" / "skills" / "smui" / "SKILL.md").exists()
            )
            self.assertTrue((undeclared / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
