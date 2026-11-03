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


class FakeAnthropic:
    """Stands in for anthropic.Anthropic(): replays recorded Message fixtures in order
    and records every request it receives."""

    def __init__(self, *fixture_names: str):
        from anthropic.types import Message

        self.replies = [Message.model_validate(load_fixture(n)) for n in fixture_names]
        self.requests: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.replies.pop(0)
