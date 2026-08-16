"""
The wall. This is what makes baton publishable and keeps `pip install baton-kernel`
a zero-dependency install forever.

Three layered rules, each checked independently:

    baton/*.py            core — stdlib + baton core ONLY. No providers, no
                          adapters, no consumer vocabulary.
    baton/providers/*     may use stdlib + baton core. Talks HTTP through
                          urllib; may NOT import a vendor SDK.
    baton/adapters/*      may use stdlib + baton core + providers, and may
                          import a host application LAZILY (inside a function).

Checked with ast, not by importing: a half-built module still gets audited, every
violation in the tree is reported at once, and a lazy import inside a function
body is still visible.
"""
import ast
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PKG = SRC / "baton"

STDLIB = set(sys.stdlib_module_names)
CORE_MODULES = {"baton"}

# If any of these ever appear in the dependency graph, `pip install baton-kernel`
# stops being free of transitive dependencies. That is the whole product promise.
VENDOR_SDKS = {
    "google", "google_genai", "genai", "generativeai",
    "anthropic", "openai", "cohere", "mistralai", "ollama",
    "langchain", "langgraph", "llama_index", "autogen",
    "requests", "httpx", "aiohttp", "pydantic", "numpy",
}

# Names that belong to a specific consumer and must never reach the core.
CONSUMER_WORDS = ("company_os", "chairman")


def _files(root):
    return sorted(p for p in root.glob("*.py"))


def core_files():
    return _files(PKG)


def provider_files():
    return _files(PKG / "providers")


def adapter_files():
    return [p for p in (PKG / "adapters").rglob("*.py")]


def imports_of(path, *, include_lazy=True):
    """(module_root, lineno, is_lazy) for every import in the file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    lazy_nodes = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(fn):
                lazy_nodes.add(id(inner))

    found = []
    for node in ast.walk(tree):
        lazy = id(node) in lazy_nodes
        if lazy and not include_lazy:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno, lazy))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            found.append((node.module.split(".")[0], node.lineno, lazy))
    return found


class PackageShape(unittest.TestCase):
    def test_src_layout_exists(self):
        self.assertTrue(PKG.is_dir(), "src/baton is missing")
        self.assertTrue((PKG / "__init__.py").is_file())

    def test_there_are_core_modules_to_check(self):
        self.assertTrue([p for p in core_files() if p.name != "__init__.py"],
                        "core is empty — nothing to isolate")

    def test_pyproject_declares_no_dependencies(self):
        """The product promise, asserted against the file that actually ships it."""
        text = (REPO / "pyproject.toml").read_text()
        self.assertIn("dependencies = []", text,
                      "baton-kernel grew a runtime dependency")
        self.assertIn('name = "baton-kernel"', text)

    def test_no_dependency_manifest_sneaked_in(self):
        for name in ("requirements.txt", "Pipfile", "poetry.lock", "setup.py"):
            self.assertFalse((REPO / name).exists(),
                             f"{name} appeared — baton is stdlib-only by design")


class CoreIsolation(unittest.TestCase):
    def test_core_imports_only_stdlib_and_itself(self):
        violations = []
        for path in core_files():
            for name, lineno, _lazy in imports_of(path):
                if name in STDLIB or name in CORE_MODULES:
                    continue
                violations.append(f"{path.name}:{lineno} imports {name!r}")
        self.assertEqual([], violations, "the core grew a dependency")

    def test_core_does_not_import_providers_or_adapters(self):
        """The core must not know that any specific model or host exists.
        Direction of dependency is the whole architecture.

        Checked against real imports, not a substring search — the package
        docstring shows `from baton.providers import gemini` in its usage
        example, and documentation is not a dependency."""
        violations = []
        for path in core_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                mod = ""
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = ",".join(a.name for a in node.names)
                if "baton.providers" in mod or "baton.adapters" in mod:
                    violations.append(f"{path.name}:{node.lineno} imports {mod}")
        self.assertEqual([], violations, "the core reached downward")

    def test_core_contains_no_consumer_vocabulary(self):
        violations = []
        for path in core_files():
            text = path.read_text()
            for word in CONSUMER_WORDS:
                if word in text:
                    violations.append(f"{path.name} mentions {word!r}")
        self.assertEqual([], violations, "the core leaked a consumer's vocabulary")


class ProviderIsolation(unittest.TestCase):
    def test_providers_use_no_vendor_sdk(self):
        """A provider talks HTTP through urllib. The moment one imports an SDK,
        `pip install baton-kernel` stops being dependency-free."""
        violations = []
        for path in provider_files():
            for name, lineno, _lazy in imports_of(path):
                if name in VENDOR_SDKS:
                    violations.append(f"{path.name}:{lineno} imports SDK {name!r}")
                elif name not in STDLIB and name not in CORE_MODULES:
                    violations.append(f"{path.name}:{lineno} imports {name!r}")
        self.assertEqual([], violations, "a provider took a dependency")

    def test_providers_do_not_import_adapters(self):
        for path in provider_files():
            self.assertNotIn("baton.adapters", path.read_text(),
                             f"{path.name} reached into an adapter")


class AdapterIsolation(unittest.TestCase):
    def test_adapters_import_their_host_lazily_only(self):
        """An adapter may bind to a host application, but only inside a function.
        A module-level import would make `import baton` fail on a machine that
        does not have that host installed."""
        violations = []
        for path in adapter_files():
            for name, lineno, lazy in imports_of(path):
                if name in STDLIB or name in CORE_MODULES:
                    continue
                if not lazy:
                    violations.append(
                        f"{path.relative_to(SRC)}:{lineno} imports {name!r} at module level")
        self.assertEqual([], violations, "an adapter made its host mandatory")


class ImportsCleanFromABareInterpreter(unittest.TestCase):
    def test_import_baton_with_nothing_but_src_on_the_path(self):
        out = subprocess.run(
            [sys.executable, "-c", "import baton; print(baton.__all__ and 'ok')"],
            cwd=str(SRC), capture_output=True, text=True, env={"PATH": "/usr/bin"})
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("ok", out.stdout)

    def test_import_providers_with_nothing_but_src_on_the_path(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import baton.providers.gemini as g; print('ok', g.MODEL)"],
            cwd=str(SRC), capture_output=True, text=True, env={"PATH": "/usr/bin"})
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("ok", out.stdout)

    def test_importing_baton_does_not_pull_in_any_adapter(self):
        """`import baton` on a machine with no Company OS must work and must not
        drag a host application into memory."""
        code = ("import baton, sys; "
                "print('leaked' if any('adapters' in m for m in sys.modules) else 'ok')")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(SRC),
                             capture_output=True, text=True, env={"PATH": "/usr/bin"})
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("ok", out.stdout)


if __name__ == "__main__":
    unittest.main()
