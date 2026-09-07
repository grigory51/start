from __future__ import annotations

import io
import json
import os
import subprocess
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

    def test_agents_are_registered_by_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            codex_home = root / ".codex"
            generated = (
                root / "data" / "start" / "generated" / "codex" / "agents"
                / "architect.toml"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text(
                'name = "architect"\n'
                'description = "Architect"\n'
                'developer_instructions = "Read only."\n'
            )
            legacy = codex_home / "agents" / "architect.toml"
            legacy.parent.mkdir(parents=True)
            legacy.symlink_to(generated)
            (codex_home / ".start-agents-managed.json").write_text(
                '{"names": ["architect.toml"]}\n'
            )
            agent = config.Agent(
                name="architect",
                path=generated,
                description="Architect",
            )

            with (
                patch.dict(
                    os.environ,
                    {
                        "CODEX_HOME": str(codex_home),
                        "XDG_DATA_HOME": str(root / "data"),
                    },
                ),
                patch.object(config, "_discover_agents", return_value=([agent], [])),
                patch.object(config, "load_mcp", return_value=([], [])),
                patch.object(config, "load_codex_flags", return_value={}),
                patch.object(config, "load_statusline", return_value=None),
            ):
                ctx = Ctx(dry_run=False, force=False)
                agents = codex.install_agents(ctx, [])
                codex.merge_config(ctx, agent_configs=agents)
                codex.remove_legacy_agent_links(ctx)

            self.assertFalse(legacy.exists())
            merged = tomllib.loads((codex_home / "config.toml").read_text())
            self.assertEqual(
                merged["agents"]["architect"],
                {
                    "description": "Architect",
                    "config_file": str(generated),
                },
            )

    def test_merge_preserves_foreign_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            codex_home = home / ".codex"
            codex_home.mkdir()
            sites_skill = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-bundled"
                / "sites"
                / "1.0.0"
                / "skills"
                / "sites-building"
                / "SKILL.md"
            )
            sites_skill.parent.mkdir(parents=True)
            sites_skill.write_text("---\nname: sites-building\ndescription: Test\n---\n")
            (codex_home / "config.toml").write_text(
                'model = "custom"\n'
                '[[skills.config]]\n'
                f'path = "{codex_home / "skills" / "canvas-design" / "SKILL.md"}"\n'
                'enabled = true\n'
                '[[skills.config]]\n'
                'path = "/foreign/SKILL.md"\n'
                'enabled = false\n'
                '[plugins."claude-mem@personal"]\nenabled = true\n'
                '[plugins."sites@openai-bundled"]\nenabled = true\n'
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
                patch.object(
                    config,
                    "load_codex_flags",
                    side_effect=lambda section: {
                        "plugins": {"sites@openai-bundled": False},
                        "skills": {"canvas-design": False},
                        "features": {"apps": False},
                    }[section],
                ),
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
            self.assertFalse(merged["plugins"]["sites@openai-bundled"]["enabled"])
            self.assertEqual(len(merged["skills"]["config"]), 3)
            self.assertFalse(merged["skills"]["config"][0]["enabled"])
            self.assertEqual(merged["skills"]["config"][1]["path"], "/foreign/SKILL.md")
            self.assertEqual(merged["skills"]["config"][2]["path"], str(sites_skill))
            self.assertFalse(merged["skills"]["config"][2]["enabled"])
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
            self.assertEqual(
                sidecar["plugin_overrides"],
                {"sites@openai-bundled": True},
            )
            self.assertEqual(
                sidecar["skill_overrides"],
                {
                    str(codex_home / "skills" / "canvas-design" / "SKILL.md"): True,
                    str(sites_skill): None,
                },
            )
            self.assertTrue(sidecar["status_line"])
            self.assertIn("MCP ->", output.getvalue())
            self.assertIn("Итого: 0, изменено 0.", output.getvalue())
            self.assertNotIn("Codex config", output.getvalue())

            with (
                patch.dict(os.environ, {"HOME": str(home), "CODEX_HOME": str(codex_home)}),
                patch.object(config, "load_mcp", return_value=([], [])),
                patch.object(
                    config,
                    "load_codex_flags",
                    side_effect=lambda section: (
                        {"apps": False} if section == "features" else {}
                    ),
                ),
                patch.object(config, "load_statusline", return_value=status),
            ):
                codex.merge_config(Ctx(dry_run=False, force=False))

            restored = tomllib.loads((codex_home / "config.toml").read_text())
            self.assertTrue(restored["plugins"]["sites@openai-bundled"]["enabled"])
            self.assertTrue(restored["skills"]["config"][0]["enabled"])
            self.assertEqual(restored["skills"]["config"][1]["path"], "/foreign/SKILL.md")
            self.assertEqual(len(restored["skills"]["config"]), 2)
            restored_sidecar = json.loads(
                (codex_home / ".start-config-managed.json").read_text()
            )
            self.assertEqual(restored_sidecar["plugin_overrides"], {})
            self.assertEqual(restored_sidecar["skill_overrides"], {})

    def test_failed_plugin_reinstall_keeps_previous_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            plugin_root = home / "source" / "demo"
            manifest = plugin_root / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"name": "demo", "description": "Demo"})
            )
            sidecar = home / ".agents" / "plugins" / ".start-managed.json"
            sidecar.parent.mkdir(parents=True)
            sidecar.write_text(
                json.dumps({"names": ["demo"], "plugins": {"demo": "old"}})
            )
            plugin = config.Plugin(
                path=plugin_root,
                source="demo",
                marketplace="personal",
                plugin="demo",
                enabled=True,
                platform_paths={"codex": plugin_root},
            )
            remove_failed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="remove failed",
            )

            with (
                patch("pathlib.Path.home", return_value=home),
                patch.dict(
                    os.environ,
                    {
                        "CODEX_HOME": str(home / ".codex"),
                        "XDG_DATA_HOME": str(home / ".local" / "share"),
                    },
                ),
                patch.object(codex, "_installed_plugins", return_value={"demo@personal"}),
                patch.object(codex, "_run_plugin_command", return_value=remove_failed),
            ):
                ctx = Ctx(dry_run=False, force=False)
                codex.install_plugins(ctx, [plugin])

            saved = json.loads(sidecar.read_text())
            self.assertEqual(saved["plugins"]["demo"], "old")
            self.assertEqual(ctx.errors, 1)


if __name__ == "__main__":
    unittest.main()
