import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "addons" / "lan_ma55"


@pytest.fixture(scope="session")
def scenarios():
    code = """import {scenario,SCENARIOS} from './tests/cases.mjs';
console.log(JSON.stringify(Object.fromEntries(Object.entries(SCENARIOS).map(([k,v])=>[k,scenario(v)]))))"""
    raw = json.loads(
        subprocess.check_output(
            ["node", "--input-type=module", "-e", code], cwd=PLUGIN, text=True
        )
    )
    for case in raw.values():
        for bar in case["bars"]:
            bar["adjust_factor"] = 1
    return raw


@pytest.fixture(scope="session")
def frozen_cases():
    pack = os.environ.get("MA55_FROZEN_PACK")
    if (
        not (os.environ.get("MA55_REFERENCE_REPO") or os.environ.get("MA55_REFERENCE_ARCHIVE"))
        or not pack
        or not (Path(pack) / "manifest.json").is_file()
    ):
        pytest.skip("Pinned reference repository or private frozen pack not mounted")
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(PLUGIN / "tests" / "oracle.py")],
        input=json.dumps(dict(action="frozen")),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def batches(universe, bars, size=128):
    for offset in range(0, len(universe), size):
        yield [(row["symbol"], bars) for row in universe[offset : offset + size]]


def universe(count):
    return [
        dict(symbol=f"{i:06d}.SZ", asset_type="stock", raw_tick=0.01)
        for i in range(count)
    ]
