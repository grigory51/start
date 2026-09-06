from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import install


class ManagedPathTests(unittest.TestCase):
    def test_ownership_resolves_links_and_rejects_sibling_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            repo = root / "repo"
            data = root / "data"
            repo.mkdir()
            data.mkdir()
            alias = root / "alias"
            alias.symlink_to(data, target_is_directory=True)
            with (
                patch.object(install, "REPO_DIR", repo),
                patch.object(install.adapters, "data_dir", return_value=data),
            ):
                self.assertTrue(install._is_ours(repo / "skill"))
                self.assertTrue(install._is_ours(alias / "generated"))
                self.assertFalse(install._is_ours(root / "repo-other" / "skill"))
                self.assertFalse(install._is_ours(repo / ".." / "foreign"))


if __name__ == "__main__":
    unittest.main()
