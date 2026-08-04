from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CodexSessionDumpTests(unittest.TestCase):
    def test_exports_only_visible_conversation(self) -> None:
        session_id = "019fcc20-8045-7a72-93a3-3a92c63910c9"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = root / ".codex" / "sessions" / "2026" / "08" / "04" / f"rollout-{session_id}.jsonl"
            session.parent.mkdir(parents=True)
            events = [
                {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"text": "hidden"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "Вопрос"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "<skill>hidden</skill>"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Ответ"}]}},
            ]
            session.write_text("".join(json.dumps(event) + "\n" for event in events))
            output = root / "session.md"
            environment = {**os.environ, "CODEX_HOME": str(root / ".codex")}

            subprocess.run(
                [sys.executable, "scripts/codex-session-dump.py", session_id, str(output)],
                cwd=Path(__file__).parent.parent,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                output.read_text(),
                f"# Codex session {session_id}\n\n## Пользователь\n\nВопрос\n\n## Codex\n\nОтвет\n",
            )


if __name__ == "__main__":
    unittest.main()
