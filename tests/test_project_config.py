from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import config


class ProjectConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.global_root = self.root / "start"
        self.project = self.root / "project"
        self.global_root.mkdir()
        self.project.mkdir()
        self.base = self.global_root / "config.toml"
        self.local = self.global_root / "config.local.toml"
        self.path = self.project / "start.toml"
        for name, value in (
            ("REPO_DIR", self.global_root), ("CONFIG", self.base), ("CONFIG_LOCAL", self.local)
        ):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_mcp_project_enables_global_disabled_server_and_merges_fields(self) -> None:
        self.base.write_text('''[[ai.mcp]]
name = "grafana"
enabled = false
[ai.mcp.server]
command = "grafana"
args = ["old"]
[ai.mcp.server.env]
TOKEN = "private"
HOST = "old"
''')
        self.local.write_text('[local.ai.mcp]\ngrafana = false\n')
        self.path.write_text('''[[ai.mcp]]
name = "grafana"
enabled = true
[ai.mcp.server]
args = ["new"]
[ai.mcp.server.env]
HOST = "new"
''')
        context = config.load_project(self.path)
        servers, warnings = config.load_mcp(context)
        self.assertFalse(warnings)
        self.assertEqual(len(servers), 1)
        self.assertTrue(servers[0].enabled)
        self.assertEqual(servers[0].server, {
            "command": "grafana", "args": ["new"], "env": {"TOKEN": "private", "HOST": "new"}
        })
        self.assertEqual(context.project_keys, {"mcp": {"grafana"}})
        self.assertFalse(config.load_mcp(config.global_context())[0][0].enabled)

    def test_local_only_mcp_and_project_local_has_last_word(self) -> None:
        self.local.write_text('''[[ai.mcp]]
name = "grafana"
enabled = false
[ai.mcp.server]
url = "https://example.test"
''')
        self.path.write_text('''[[ai.mcp]]
name = "grafana"
enabled = false
[local.ai.mcp]
grafana = true
''')
        context = config.load_project(self.path)
        self.assertTrue(config.load_mcp(context)[0][0].enabled)
        self.assertEqual(context.project_keys["mcp"], {"grafana"})

    def test_source_paths_keep_origin_and_discovery_reuses_context(self) -> None:
        for root, source, skill in ((self.global_root, "skills", "global"),
                                    (self.project, "extra", "project")):
            folder = root / source / skill
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"---\ndescription: {skill}\n---\n")
        self.base.write_text('''[[ai.skills]]
path = "skills"
enabled = ["*"]
''')
        self.path.write_text('''[[ai.skills]]
path = "extra"
[local.ai.skills]
skills = []
''')
        context = config.load_project(self.path)
        result = config.load(context)
        self.assertFalse(result.warnings)
        self.assertEqual([(s.name, s.enabled) for s in result.skills], [
            ("global", False), ("project", True)
        ])
        self.assertEqual(result.skills[0].path, self.global_root / "skills/global")
        self.assertEqual(result.skills[1].path, self.project / "extra/project")
        self.assertEqual(context.project_keys["skills"], {
            str(self.global_root / "skills"), str(self.project / "extra")
        })

    def test_same_source_merges_and_arrays_replace(self) -> None:
        self.base.write_text('''[[ai.skills]]
path = "skills"
enabled = ["a", "b"]
exclude = ["hidden"]
''')
        self.path.write_text('''[[ai.skills]]
path = "skills"
enabled = ["c"]
''')
        entries = config.load_project(self.path).document["ai"]["skills"]
        self.assertEqual(entries, [{"path": str(self.global_root / "skills"),
                                    "enabled": ["c"], "exclude": ["hidden"]}])

    def test_hooks_and_platform_settings(self) -> None:
        self.base.write_text('''[[ai.hooks]]
path = "hook.py"
events = ["SessionStart"]
[ai.platforms.codex.features]
a = true
b = true
''')
        self.path.write_text('''[[ai.hooks]]
path = "hook.py"
enabled = false
[ai.platforms.codex.features]
a = false
[ai.platforms.codex.config]
model = "example"
''')
        context = config.load_project(self.path)
        self.assertEqual(config.load_hooks("codex", context), ([], []))
        self.assertEqual(config.load_codex_flags("features", context), {"a": False, "b": True})
        self.assertEqual(context.project_keys["platforms"], {"codex"})

    def test_invalid_project_is_rejected_before_discovery(self) -> None:
        cases = [
            'not toml',
            '[files]\na = true',
            '[ai.statusline]\ncommand = "x"',
            '[local.ai.mcp]\nmissing = true',
            '[[ai.mcp]]\nname = "missing"\nenabled = true',
            '[[ai.skills]]\npath = "x"\n[[ai.skills]]\npath = "x"',
            '[ai]\nmcp = "bad"',
            '[[ai.plugins]]\npath = "x"\nenabled = []',
            '[[ai.skills]]\npath = "x"\nenabled = true',
            '[ai.platforms.codex]\nconfig = "bad"',
            '[ai.platforms.codex]\nunknown = {}',
        ]
        for text in cases:
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    config.load_project(self.path)

    def test_alias_duplicate_is_rejected(self) -> None:
        self.path.write_text('[[ai.skills]]\npath = "x"\n[[ai.skills]]\npath = "./x"')
        with self.assertRaisesRegex(ValueError, "Дубль"):
            config.load_project(self.path)


if __name__ == "__main__":
    unittest.main()
