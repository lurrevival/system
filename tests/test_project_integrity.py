"""Offline project checks; no Discord login or API keys required."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProjectIntegrity(unittest.TestCase):
    def test_all_cogs_listed_in_main_exist(self):
        module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        listed = next(node.value for node in module.body
                      if isinstance(node, ast.Assign) and any(
                          isinstance(target, ast.Name) and target.id == "COGS" for target in node.targets
                      ))
        for string in listed.elts:
            name = ast.literal_eval(string)
            self.assertTrue((ROOT / (name.replace(".", "/") + ".py")).is_file(), name)

    def test_internal_imports_resolve(self):
        for path in ROOT.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        root_module = node.module.split(".")[0]
                        if root_module in {"cogs", "utils", "config", "database"}:
                            module_file = ROOT / (node.module.replace(".", "/") + ".py")
                            package_init = ROOT / node.module.replace(".", "/") / "__init__.py"
                            self.assertTrue(module_file.is_file() or package_init.is_file(),
                                            f"{path}: {node.module}")
                    elif isinstance(node, ast.Import):
                        for name in node.names:
                            if name.name.split(".")[0] in {"cogs", "utils", "config", "database"}:
                                module_file = ROOT / (name.name.replace(".", "/") + ".py")
                                package_init = ROOT / name.name.replace(".", "/") / "__init__.py"
                                self.assertTrue(module_file.is_file() or package_init.is_file(),
                                                f"{path}: {name.name}")

    def test_help_lists_real_features(self):
        menu = (ROOT / "cogs" / "general.py").read_text(encoding="utf-8")
        afk = (ROOT / "cogs" / "afk.py").read_text(encoding="utf-8")
        self.assertIn('admin_group("afk"', afk)
        self.assertIn('"afk":', menu)
        self.assertIn('s["afk"]', menu)

    def test_no_actual_secret_configuration_is_packed(self):
        self.assertFalse((ROOT / ".env").exists())
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(example, r'(?m)^DISCORD_TOKEN=\s*$')
        self.assertIn("BOT_NAME=dor", example)

    def test_guided_setup_present(self):
        self.assertTrue((ROOT / "utils" / "setup_pages.py").is_file())
        guide = (ROOT / "utils" / "setup_pages.py").read_text(encoding="utf-8")
        for page in ("logs", "welcome", "autorole", "tickets", "security", "levels", "voice", "afk"):
            with self.subTest(page=page):
                self.assertIn(f'("{page}"', guide)


if __name__ == "__main__":
    unittest.main()
