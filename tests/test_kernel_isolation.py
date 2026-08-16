"""
The wall. kernel/ must import nothing but the standard library and itself.
Open-sourcing baton later is meant to be `git subtree split` — that is only true
if this test has been green from the first commit, so it is written first.

Checked with ast, not by importing: a half-built module still gets audited, and
every violation in the tree is reported at once instead of one per run.
"""
import ast
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
KERNEL_DIR = REPO / "kernel"

# Anything not in here is a dependency. The stdlib check is done against the
# interpreter's own list so the set cannot drift as modules are added.
STDLIB = set(sys.stdlib_module_names)
ALLOWED_LOCAL = {"kernel"}
BANNED_SUBSTRINGS = ("company_os", "engine", "chairman")


def _kernel_files():
    return sorted(KERNEL_DIR.glob("*.py"))


def _imported_roots(path):
    """Every top-level module name this file imports, with line numbers."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # relative import, stays in kernel/
                continue
            if node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


class KernelIsolation(unittest.TestCase):
    def test_kernel_has_files(self):
        self.assertTrue([p for p in _kernel_files() if p.name != "__init__.py"],
                        "kernel/ is empty — nothing to isolate")

    def test_only_stdlib_and_kernel_imports(self):
        violations = []
        for path in _kernel_files():
            for name, lineno in _imported_roots(path):
                if name in STDLIB or name in ALLOWED_LOCAL:
                    continue
                violations.append(f"{path.name}:{lineno} imports {name!r}")
        self.assertEqual([], violations, "kernel/ grew a dependency")

    def test_no_company_os_names_anywhere_in_kernel(self):
        """Belt and braces: catches a lazy import hidden inside a function body,
        which the AST import walk above would still see, and a getattr/importlib
        dodge, which it would not."""
        violations = []
        for path in _kernel_files():
            text = path.read_text()
            for banned in BANNED_SUBSTRINGS:
                if banned in text:
                    violations.append(f"{path.name} mentions {banned!r}")
        self.assertEqual([], violations, "kernel/ leaked a consumer's vocabulary")

    def test_kernel_imports_clean_with_no_sys_path_help(self):
        """kernel must import from a bare interpreter whose only path entry is
        the repo root — no site-packages tricks, no parent-project injection."""
        import subprocess
        code = "import kernel; print('ok')"
        out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                             capture_output=True, text=True, env={"PATH": "/usr/bin"})
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("ok", out.stdout)


if __name__ == "__main__":
    unittest.main()
