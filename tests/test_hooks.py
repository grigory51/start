from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import config, settings
from cli.ai import codex
from cli.install import Ctx


class HookTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("jq"),
        "notify hook requires jq",
    )
    def test_notify_ignores_codex_subagent_permission_requests(self) -> None:
        child_session_id = "019fb8bb-0000-7000-8000-000000000001"
        root_session_id = "019fb8bb-0000-7000-8000-000000000002"

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            child_transcript = root / "child.jsonl"
            child_transcript.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": child_session_id,
                            "session_id": root_session_id,
                            "thread_source": "subagent",
                        },
                    }
                )
                + "\n"
            )
            root_transcript = root / "root.jsonl"
            root_transcript.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": root_session_id,
                            "session_id": root_session_id,
                            "thread_source": "cli",
                        },
                    }
                )
                + "\n"
            )

            bin_dir = root / "bin"
            bin_dir.mkdir()
            notifier = bin_dir / "terminal-notifier"
            notifier.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_CAPTURE"\n'
            )
            notifier.chmod(0o755)

            capture = root / "notifications"
            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(root),
                    "NOTIFY_CAPTURE": str(capture),
                    "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                    "START_AI_EVENT": "PermissionRequest",
                    "START_AI_PLATFORM": "codex",
                }
            )
            hook = Path(__file__).parents[1] / "ai" / "hooks" / "notify.sh"

            subprocess.run(
                ["bash", str(hook)],
                input=json.dumps(
                    {
                        "session_id": root_session_id,
                        "transcript_path": str(child_transcript),
                    }
                ),
                text=True,
                env=environment,
                check=True,
            )
            self.assertFalse(capture.exists())

            subprocess.run(
                ["bash", str(hook)],
                input=json.dumps(
                    {
                        "session_id": root_session_id,
                        "transcript_path": str(root_transcript),
                    }
                ),
                text=True,
                env=environment,
                check=True,
            )
            self.assertIn("Codex просит разрешение", capture.read_text())

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
