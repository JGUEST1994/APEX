"""Architecture guard: the trading core must never depend on external AI (spec Section 2)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "apex"
CORE = ["core", "domain", "db", "marketdata", "strategies", "backtest", "risk", "execution", "ml", "regime"]


def _imports(path: Path) -> set[str]:
    mods = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


@pytest.mark.parametrize("pkg", CORE)
def test_core_does_not_import_ai(pkg):
    for f in (SRC / pkg).rglob("*.py") if (SRC / pkg).exists() else []:
        bad = {m for m in _imports(f) if m == "apex.ai" or m.startswith("apex.ai.")}
        assert not bad, f"{f.relative_to(SRC)} imports {bad}"
