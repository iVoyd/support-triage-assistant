import json
from pathlib import Path

import pytest

from triage.config import load_config
from triage.db import connect

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def cfg():
    return load_config(ROOT / "triage.toml")
