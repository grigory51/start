from __future__ import annotations

import subprocess
import unittest

from cli.completion import COMPLETION_PATH, generate
from cli.sections import M_TARGETS


class CompletionTests(unittest.TestCase):
    def test_generated_script_is_current(self) -> None:
        self.assertEqual(COMPLETION_PATH.read_text(), generate())

    def test_sections_and_empty_completion_after_section(self) -> None:
        script = generate() + '''
COMP_WORDS=(start "")
COMP_CWORD=1
_start_completion
printf '%s\\n' "${COMPREPLY[@]}"
COMP_WORDS=(start tab:commands "")
COMP_CWORD=2
_start_completion
test "${#COMPREPLY[@]}" -eq 0
'''
        result = subprocess.run(
            ["bash", "--noprofile", "--norc"], input=script,
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(
            set(result.stdout.splitlines()),
            {"tab:ai", "tab:agents", "tab:skills", "tab:plugins", "tab:mcp", "tab:status", "tab:files", "tab:commands",
             "up", "settings", "seed", "add-submodule", "completion"},
        )

    def test_bash_colon_word_break(self) -> None:
        script = generate() + r'''
COMP_WORDBREAKS=' :'
COMP_WORDS=(start tab : sk)
COMP_CWORD=3
_start_completion
printf '%s\n' "${COMPREPLY[@]}"
COMP_WORDS=(start tab : skills "")
COMP_CWORD=4
_start_completion
test "${#COMPREPLY[@]}" -eq 0
'''
        result = subprocess.run(
            ["bash", "--noprofile", "--norc"], input=script,
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout.splitlines(), ["skills"])

    def test_old_section_aliases_still_resolve(self) -> None:
        self.assertEqual(M_TARGETS["scripts"], M_TARGETS["commands"])
        self.assertEqual(M_TARGETS["ai:claude"], M_TARGETS["status"])
        self.assertEqual(M_TARGETS["ai:codex"], M_TARGETS["status"])
