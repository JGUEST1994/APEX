"""Windows helper files: the double-click launchers and the example scheduler config."""

import json
import re
from pathlib import Path

import pytest

from apex.domain.instruments import by_symbol
from apex.scheduler import parse_jobs

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["start-apex.bat", "start-scheduler.bat"])
def test_launchers_use_crlf_and_only_real_commands(name):
    raw = (ROOT / name).read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") > 5, "batch files need CRLF line endings"
    text = raw.decode()
    assert 'cd /d "%~dp0"' in text and ".venv\\Scripts\\activate.bat" in text
    import apex.cli as cli_mod  # every "apex <cmd>" in the file must be a real command

    src = Path(cli_mod.__file__).read_text()
    for cmd in re.findall(r"^apex ([a-z-]+)", text, re.M):
        assert f'"{cmd}"' in src, f"{name} runs unknown command apex {cmd}"
    assert "APEX_TRADING_MODE" not in text  # launchers never change the trading mode


def test_example_jobs_parse_and_use_known_markets_and_free_sources():
    cfg = json.loads((ROOT / "jobs.example.json").read_text())
    jobs = parse_jobs(cfg)
    universe = by_symbol()
    assert {j.type for j in jobs} == {"update", "backup"}  # never paper trading by default
    for j in (j for j in jobs if j.type == "update"):
        assert j.spec["provider"] in {"coinbase", "binance", "kraken"}  # no key needed
        assert set(j.spec["symbols"]) <= set(universe)
    assert "jobs.json" in (ROOT / ".gitignore").read_text().split()
