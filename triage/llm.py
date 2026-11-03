"""Call Claude for one issue, validate the tool output, retry once with the errors."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Prices
from .prompt import TOOL_NAME
from .validate import validate_output


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    api_calls: int = 0

    def add(self, usage) -> None:
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.api_calls += 1

    def cost(self, prices: Prices | None) -> float | None:
        if prices is None:
            return None
        return (
            self.input_tokens * prices.input
            + self.output_tokens * prices.output
            + self.cache_write_tokens * prices.cache_write
            + self.cache_read_tokens * prices.cache_read
        ) / 1_000_000


@dataclass
class TriageResult:
    output: dict | None
    errors: list[str]
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0

    @property
    def valid(self) -> bool:
        return self.output is not None and not self.errors


def _tool_call(message, tool_name: str):
    for block in message.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block
    return None


def call_tool(
    client,
    request: dict,
    tool_name: str,
    validate: Callable[[dict], list[str]],
    max_attempts: int = 2,
) -> TriageResult:
    """Send `request`, expect one call to `tool_name`, check it with `validate`.

    On a bad answer the errors go back once as a tool_result with is_error, appended after
    the original turn so the cached prefix stays the same.
    """
    messages = list(request["messages"])
    usage = Usage()
    output, errors = None, ["no response"]
    start = time.monotonic()
    for _ in range(max_attempts):
        message = client.messages.create(**{**request, "messages": list(messages)})
        usage.add(message.usage)
        if message.stop_reason == "refusal":
            output, errors = None, ["model declined the request (stop_reason=refusal)"]
            break
        if message.stop_reason == "max_tokens":
            output, errors = None, ["response hit max_tokens before finishing"]
            break
        call = _tool_call(message, tool_name)
        messages.append({"role": "assistant", "content": message.content})
        if call is None:
            output, errors = None, [f"model did not call {tool_name}"]
            messages.append(
                {"role": "user", "content": f"Please record your answer by calling {tool_name}."}
            )
            continue
        output = dict(call.input)
        errors = validate(output)
        if not errors:
            break
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "is_error": True,
                        "content": "Fix these problems and call the tool again:\n- "
                        + "\n- ".join(errors),
                    }
                ],
            }
        )
    latency_ms = int((time.monotonic() - start) * 1000)
    return TriageResult(output=output, errors=errors, usage=usage, latency_ms=latency_ms)


def triage_issue(
    client,
    request: dict,
    areas: list[str],
    allowed_urls: list[str],
    max_attempts: int = 2,
    version: str = "v2",
    duplicate: int | None = None,
) -> TriageResult:
    """Send `request` (from prompt.build_request) and validate the record_triage answer."""
    return call_tool(
        client,
        request,
        TOOL_NAME,
        lambda out: validate_output(out, areas, allowed_urls, version, duplicate),
        max_attempts,
    )
