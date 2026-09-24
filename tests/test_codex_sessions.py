from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cli.command_sdk import TaskContext
from cli.commands.codex_sessions import (
    Session,
    _linux_session_processes,
    export_session,
    load_sessions,
    resume_session,
    session_processes,
)


FIRST = "019fcc20-8045-7a72-93a3-3a92c63910c9"
SECOND = "019fcc20-8045-7a72-93a3-3a92c63910ca"


class CodexSessionsTests(unittest.IsolatedAsyncioTestCase):
    def rollout(self, root: Path, folder: str, session_id: str, first: str) -> Path:
        path = root / ".codex" / folder / f"rollout-{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(first + "\nthis line is deliberately not json\n")
        return path

    def test_loads_metadata_falls_back_to_filename_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {"CODEX_HOME": str(Path(raw) / ".codex")}, clear=True):
            root = Path(raw)
            old = self.rollout(root, "sessions/2026", FIRST, '{"type":"session_meta","payload":{"id":"' + FIRST + '","cwd":"/old"}}')
            latest = self.rollout(root, "archived_sessions", FIRST, '{"type":"session_meta","payload":{"session_id":"' + FIRST + '","cwd":"/new"}}')
            fallback = self.rollout(root, "sessions", SECOND, "{}")
            os.utime(old, (10, 10))
            os.utime(latest, (20, 20))
            sessions = {session.id: session for session in load_sessions()}
        self.assertEqual(sessions[FIRST].cwd, "/new")
        self.assertEqual(sessions[FIRST].path, latest)
        self.assertEqual(sessions[SECOND].path, fallback)

    def test_marks_subagents_and_guardian_in_metadata(self) -> None:
        sources = [
            ({"source": "cli", "thread_source": "user"}, False),
            ({"source": "cli"}, False),
            ({"source": {"subagent": {"thread_spawn": {"parent_thread_id": FIRST}}}}, True),
            ({"source": {"subagent": {"other": "guardian"}}, "thread_source": "guardian_review"}, True),
            ({"thread_source": "subagent"}, True),
        ]
        for metadata, expected in sources:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as raw, patch.dict(
                os.environ, {"CODEX_HOME": str(Path(raw) / ".codex")}, clear=True
            ):
                self.rollout(Path(raw), "sessions", FIRST, json.dumps({
                    "type": "session_meta", "payload": {"id": FIRST, "cwd": "/work", **metadata}
                }))
                self.assertEqual(load_sessions()[0].is_subagent, expected)
                self.assertEqual(load_sessions()[0].is_subagent, expected)

    def test_export_requires_new_absolute_destination(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "rollout.jsonl"
            source.write_text('{"type":"response_item","payload":{"type":"message","role":"user","content":[{"text":"Hi"}]}}\n')
            session = Session(FIRST, "/work", source, 0)
            with self.assertRaisesRegex(ValueError, "абсолютным"):
                export_session(session, "session.md")
            output = root / "export.md"
            self.assertEqual(export_session(session, str(output)), output)
            self.assertIn("Hi", output.read_text())
            with self.assertRaisesRegex(ValueError, "существует"):
                export_session(session, str(output))

    async def test_resume_opens_terminal_with_quoted_cwd_and_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {"CODEX_HOME": "/custom codex"}, clear=True):
            cwd = Path(raw) / "project; echo injected"
            cwd.mkdir()
            context = TaskContext()
            with patch("cli.commands.codex_sessions.sys.platform", "darwin"), patch.object(context, "run", AsyncMock()) as run:
                await resume_session(context, Session(FIRST, str(cwd), Path("unused"), 0))
        self.assertEqual(run.await_args.args[:3], ("osascript", "-e", run.await_args.args[2]))
        command = run.await_args.args[3]
        self.assertIn("cd '" + str(cwd) + "'", command)
        self.assertNotIn("; echo injected &&", command)
        self.assertIn("CODEX_HOME='/custom codex' codex resume " + FIRST, command)

    async def test_resume_linux_uses_new_gnome_terminal_window(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {}, clear=True):
            context = TaskContext()
            with patch("cli.commands.codex_sessions.sys.platform", "linux"), patch.object(context, "run", AsyncMock()) as run:
                await resume_session(context, Session(FIRST, raw, Path("unused"), 0))
        self.assertEqual(run.await_args.args, ("gnome-terminal", "--window", "--working-directory", raw, "--", "codex", "resume", FIRST))

    async def test_processes_are_mapped_only_from_rollout_files(self) -> None:
        context = TaskContext()
        output = "p100\nn/tmp/rollout-" + FIRST + ".jsonl\nn/work/project\np200\nn/tmp/rollout-" + SECOND + ".jsonl\nn/work/project\np300\nn/work/project\n"
        with patch("cli.commands.codex_sessions.sys.platform", "darwin"), patch.object(context, "run", AsyncMock(return_value=output)) as run:
            result = await session_processes(context, ["100", "200", "300", "invalid"])
        self.assertEqual(result, {FIRST: ["100"], SECOND: ["200"]})
        self.assertEqual(run.await_args.args, ("lsof", "-a", "-p", "100,200,300", "-Fpn"))

    def test_malformed_metadata_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {"CODEX_HOME": str(Path(raw) / ".codex")}, clear=True):
            root = Path(raw)
            self.rollout(root, "sessions", FIRST, "not json")
            with self.assertRaisesRegex(ValueError, "metadata"):
                load_sessions()

    async def test_resume_rejects_missing_cwd_before_terminal(self) -> None:
        context = TaskContext()
        with patch.object(context, "run", AsyncMock()) as run:
            with self.assertRaisesRegex(ValueError, "CWD"):
                await resume_session(context, Session(FIRST, "/definitely/missing", Path("unused"), 0))
        run.assert_not_awaited()

    async def test_resume_rejects_cwd_missing_from_legacy_metadata(self) -> None:
        context = TaskContext()
        with patch.object(context, "run", AsyncMock()) as run:
            with self.assertRaisesRegex(ValueError, "не указан"):
                await resume_session(context, Session(FIRST, "", Path("unused"), 0))
        run.assert_not_awaited()

    def test_linux_processes_keep_valid_fd_when_another_disappears(self) -> None:
        vanished = Path("/proc/100/fd/3")
        valid = Path("/proc/100/fd/4")
        with patch("cli.commands.codex_sessions.Path.iterdir", return_value=[vanished, valid]), patch(
            "cli.commands.codex_sessions.Path.readlink",
            side_effect=[FileNotFoundError(), Path("/tmp/rollout-" + FIRST + ".jsonl")],
        ):
            self.assertEqual(_linux_session_processes(["100"]), {FIRST: ["100"]})


if __name__ == "__main__":
    unittest.main()
