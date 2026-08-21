from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class CodexProcessTreeTests(unittest.TestCase):
    def test_groups_and_sorts_nested_processes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            binary_dir = Path(raw)
            (binary_dir / "pgrep").write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  '-ix codex') printf '910010\\n910020\\n' ;;\n"
                "  '-P 910010') printf '910011\\n910012\\n' ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n"
            )
            (binary_dir / "ps").write_text(
                "#!/bin/sh\n"
                "case \"$2\" in\n"
                "  910010) echo '910010 1 910010 1.0 0.1 00:10 S codex' ;;\n"
                "  910011) echo '910011 910010 910010 2.0 0.2 00:05 S child' ;;\n"
                "  910012) echo '910012 910010 910010 4.0 0.4 00:03 S uv run freecad_mcp_server.py' ;;\n"
                "  910020) echo '910020 1 910020 0.5 1.5 07:00:00 S codex' ;;\n"
                "esac\n"
            )
            for executable in binary_dir.iterdir():
                executable.chmod(0o755)

            result = subprocess.run(
                ["bash", "scripts/codex-process-tree.sh"],
                cwd=Path(__file__).parent.parent,
                env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("Codex PID 910010 · 3 процессов · CPU 7.00% · MEM 0.70% · 00:10\n", result.stdout)
            self.assertIn("CWD: ?\n", result.stdout)
            self.assertIn("PROCESSES (2)\n", result.stdout)
            self.assertIn("MCP SERVERS (1)\n", result.stdout)
            self.assertIn("PID     PPID    PGID", result.stdout)
            self.assertIn("910010  1       910010", result.stdout)
            self.assertIn("↳ child\n", result.stdout)
            self.assertIn("↳ uv run freecad_mcp_server.py\n", result.stdout)
            self.assertLess(
                result.stdout.index("Codex PID 910020"),
                result.stdout.index("Codex PID 910010"),
            )


if __name__ == "__main__":
    unittest.main()
