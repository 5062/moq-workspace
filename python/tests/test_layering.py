"""Check the direction of imports between the package layers.

Analysis must be able to re-run on a capture without SSH or host code, and the
decoders must stay usable without the DuckDB pipeline, so a lower layer
importing a higher one fails here instead of surfacing as a dependency cycle.
Capture primitives record processes and traces without knowing how they are
analyzed, so the experiment runner in `run` can drive them on any host.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "src" / "moq_trace"

# Each layer may import itself, the shared root modules, and the layers listed.
ALLOWED = {
    "decode": set(),
    "analysis": {"decode"},
    "plot": {"analysis"},
    "capture": set(),
    "run": {"analysis", "capture", "decode", "plot"},
}
LAYERS = set(ALLOWED)

# Sent as source to SSH hosts and run with their own Python, so it may import
# only the standard library.
STANDALONE = PACKAGE / "capture" / "resources.py"


def imported_modules(source: str, package: tuple[str, ...]) -> set[tuple[str, ...]]:
    """Return every module `source`, a module of `package`, imports, as absolute dotted parts.

    Relative imports resolve against `moq_trace`, and imports inside functions
    count, so a deferred import cannot hide an upward dependency.
    """

    found: set[tuple[str, ...]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(tuple(alias.name.split(".")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ("moq_trace", *package[: len(package) - (node.level - 1)])
            else:
                base = ()
            module = tuple(node.module.split(".")) if node.module else ()
            if module:
                found.add(base + module)
            else:
                found.update(base + (alias.name,) for alias in node.names)
    return found


def layers_in(source: str, package: tuple[str, ...]) -> set[str]:
    """Return the layers that `source`, a module of `package`, imports."""

    return {
        module[1]
        for module in imported_modules(source, package)
        if len(module) > 1 and module[0] == "moq_trace" and module[1] in LAYERS
    }


def _package(path: pathlib.Path) -> tuple[str, ...]:
    return path.relative_to(PACKAGE).parts[:-1]


def imported_layers(path: pathlib.Path) -> set[str]:
    return layers_in(path.read_text(), _package(path))


class LayeringTests(unittest.TestCase):
    def test_every_subpackage_is_a_layer(self) -> None:
        packages = {path.parent.name for path in PACKAGE.glob("*/__init__.py")}
        self.assertEqual(packages, LAYERS)

    def test_lower_layers_do_not_import_higher_ones(self) -> None:
        for layer, allowed in ALLOWED.items():
            for path in sorted((PACKAGE / layer).rglob("*.py")):
                with self.subTest(module=str(path.relative_to(PACKAGE))):
                    self.assertLessEqual(imported_layers(path) - {layer}, allowed)

    def test_shared_root_modules_do_not_import_a_layer(self) -> None:
        for path in sorted(PACKAGE.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertEqual(imported_layers(path), set())

    def test_standalone_collector_imports_only_the_standard_library(self) -> None:
        modules = imported_modules(STANDALONE.read_text(), _package(STANDALONE))
        self.assertLessEqual({module[0] for module in modules}, set(sys.stdlib_module_names))

    def test_the_guard_detects_an_upward_import(self) -> None:
        source = "from ..run import hosts\nfrom .. import labels, run\nfrom moq_trace.run import cli\n"
        self.assertEqual(layers_in(source, ("analysis",)), {"run"})
        self.assertEqual(
            layers_in("from . import sql\nfrom ..decode import ctf\n", ("analysis",)), {"analysis", "decode"}
        )
        self.assertEqual(layers_in("import moq_trace.plot.render\n", ("capture",)), {"plot"})
        self.assertEqual(layers_in("def f():\n    from ..analysis import sql\n", ("capture",)), {"analysis"})
        self.assertEqual(
            {module[0] for module in imported_modules("from .hosts import Host\n", ("capture",))}, {"moq_trace"}
        )


if __name__ == "__main__":
    unittest.main()
