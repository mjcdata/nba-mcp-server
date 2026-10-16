"""Offline repository configuration and approved-phase boundary checks."""

import ast
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProjectSetupTests(unittest.TestCase):
    """Check structure, package configuration, and later-phase scope boundaries."""

    def test_required_files_exist(self):
        """Ensure the approved files and package marker are present."""
        paths = [
            "README.md", "AGENTS.md", "pyproject.toml",
            "docs/ARCHITECTURE.md", "docs/DATA_DICTIONARY.md",
            "docs/WORK_PLAN.md", "src/nba_mcp/__init__.py",
            "src/nba_mcp/server.py", "src/nba_mcp/nba_data.py",
            "src/nba_mcp/database.py", "src/nba_mcp/scoring.py",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertTrue((ROOT / path).read_text().strip())

    def test_project_configuration(self):
        """Validate TOML metadata, source layout, and the approved dependency."""
        with (ROOT / "pyproject.toml").open("rb") as stream:
            config = tomllib.load(stream)
        self.assertEqual(config["project"]["name"], "nba-mcp-server")
        self.assertEqual(config["project"]["requires-python"], ">=3.11")
        self.assertEqual(config["project"]["dependencies"], ["nba_api==1.11.4", "fastmcp==4.1.0"])
        self.assertEqual(config["project"]["scripts"]["nba-mcp-server"], "nba_mcp.server:main")
        self.assertEqual(config["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(config["tool"]["setuptools"]["packages"]["find"]["where"], ["src"])

    def test_modules_remain_placeholders(self):
        """Keep package initialization free of server or network side effects."""
        for name in ("__init__.py",):
            path = ROOT / "src/nba_mcp" / name
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(), filename=str(path))
                self.assertEqual(len(tree.body), 1)
                self.assertIsInstance(tree.body[0], ast.Expr)
                self.assertIsInstance(tree.body[0].value, ast.Constant)
                self.assertIsInstance(tree.body[0].value.value, str)


if __name__ == "__main__":
    unittest.main()
