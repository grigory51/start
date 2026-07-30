from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import config, settings
from cli.ai import codex
from cli.install import Ctx


class HookTests(unittest.TestCase):
    def test_claude_commands_identify_platform_and_event(self) -> None:
        entries = [
            {
                "path": "ai/hooks/notify.sh",
                "events": ["Stop", "Notification"],
            }
        ]
        with (
            tempfile.TemporaryDirectory() as raw,
            patch.object(settings, "CLAUDE_DIR", Path(raw) / ".claude"),
            patch.object(config, "load_hooks", return_value=(entries, [])),
        ):
            hooks = settings._build_hooks_fragment()
        self.assertIn("START_AI_PLATFORM=claude START_AI_EVENT=Stop", str(hooks))
        self.assertIn(
            "START_AI_PLATFORM=claude START_AI_EVENT=Notification", str(hooks)
        )

    def test_codex_uses_native_notification_events(self) -> None:
        entries = [
            {
                "path": "ai/hooks/notify.sh",
                "events": ["Stop", "PermissionRequest"],
            }
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "ai" / "hooks" / "notify.sh"
            source.parent.mkdir(parents=True)
            source.write_text("#!/usr/bin/env bash\n")
            codex_home = root / ".codex"
            with (
                patch.dict(os.environ, {"HOME": str(root), "CODEX_HOME": str(codex_home)}),
                patch.object(codex, "REPO_DIR", root),
                patch.object(config, "load_hooks", return_value=(entries, [])),
            ):
                codex.install_hooks(Ctx(dry_run=False, force=False))
            hooks = json.loads((codex_home / "hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks), {"Stop", "PermissionRequest"})
        self.assertIn("START_AI_PLATFORM=codex START_AI_EVENT=Stop", str(hooks))
        self.assertIn(
            "START_AI_PLATFORM=codex START_AI_EVENT=PermissionRequest", str(hooks)
        )


if __name__ == "__main__":
    unittest.main()
