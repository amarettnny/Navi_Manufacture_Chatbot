"""
Provider abstraction.

Each provider exposes one method, .run(), that takes:
  - a neutral message history (list of {"role": "user"|"assistant", "content": str})
  - the shared tool registry from queries.py
  - a system prompt
and returns a (reply_text, tool_trace) tuple.

The provider handles its own tool-use loop internally and translates the
provider-specific message shape on the way in and out, so callers never see
Anthropic content blocks or OpenAI tool_calls. The neutral history makes it
safe to switch providers mid-conversation.

Two providers are supported:
  - AnthropicProvider — uses the native Anthropic Messages API.
  - OpenRouterProvider — uses the OpenAI SDK pointed at OpenRouter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from anthropic import Anthropic
from openai import OpenAI

import queries

MAX_TOOL_ITERATIONS = 8
MAX_TOOL_RESULT_CHARS = 60_000


class Provider(Protocol):
    name: str
    model: str

    def run(
        self,
        history: list[dict],
        system: str,
        db_path: Path,
    ) -> tuple[str, list[dict]]:
        ...


def _dispatch_and_log(name: str, args: dict, db_path: Path,
                      trace: list[dict]) -> tuple[str, bool]:
    """Run a tool call, record it in the trace, return (content_str, ok)."""
    try:
        result = queries.dispatch(name, args, db_path=db_path)
        ok = True
        error = None
    except Exception as e:
        result = None
        ok = False
        error = f"{type(e).__name__}: {e}"

    trace.append({
        "tool": name,
        "input": args,
        "ok": ok,
        "error": error,
        "result_size": (
            len(result) if isinstance(result, list) else
            (1 if result is not None else 0)
        ),
    })

    if ok:
        content_str = json.dumps(result, ensure_ascii=False, default=str)
    else:
        content_str = f"ERROR: {error}"

    if len(content_str) > MAX_TOOL_RESULT_CHARS:
        content_str = content_str[:MAX_TOOL_RESULT_CHARS] + "\n…(truncated)"
    return content_str, ok


# ────────────────────────────── Anthropic ──────────────────────────────


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get(
            "ANTHROPIC_MODEL", "claude-sonnet-4-5"
        )
        self.client = Anthropic()

    def run(self, history, system, db_path):
        # Anthropic uses content blocks; start with the neutral history as
        # plain text turns and let the model append tool_use blocks itself.
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in history
        ]
        trace: list[dict] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                tools=queries.TOOLS,
                messages=messages,
            )

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                text = "".join(b.text for b in resp.content if b.type == "text")
                return text.strip(), trace

            # Echo the assistant turn (with its tool_use blocks) back in.
            messages.append({"role": "assistant", "content": resp.content})

            results_block = []
            for tu in tool_uses:
                content_str, ok = _dispatch_and_log(
                    tu.name, dict(tu.input), db_path, trace
                )
                results_block.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content_str,
                    "is_error": not ok,
                })
            messages.append({"role": "user", "content": results_block})

        return (
            "Ran out of tool-call iterations before producing a final answer. "
            "Try rephrasing your question.",
            trace,
        )


# ─────────────────────────── OpenRouter ───────────────────────────


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Translate our shared TOOLS schema (Anthropic shape) to the OpenAI
    function-calling shape that OpenRouter expects."""
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, model: str | None = None):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set; cannot use the OpenRouter "
                "provider."
            )
        self.model = model or os.environ.get(
            "OPENROUTER_MODEL", "google/gemma-4-31b-it:free"
        )
        # OpenRouter is OpenAI-compatible; reuse the OpenAI SDK.
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self._openai_tools = _anthropic_tools_to_openai(queries.TOOLS)

    def run(self, history, system, db_path):
        # OpenAI shape: system prompt is its own message, then user/assistant
        # turns. Tool results are role="tool" messages keyed by tool_call_id.
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend({"role": m["role"], "content": m["content"]} for m in history)
        trace: list[dict] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self._openai_tools,
                max_tokens=2048,
            )
            msg = resp.choices[0].message
            tool_calls = msg.tool_calls or []

            if not tool_calls:
                return (msg.content or "").strip(), trace

            # Echo the assistant turn with its tool_calls.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                content_str, _ok = _dispatch_and_log(
                    tc.function.name, args, db_path, trace
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content_str,
                })

        return (
            "Ran out of tool-call iterations before producing a final answer. "
            "Try rephrasing your question.",
            trace,
        )


# ─────────────────────────── factory ───────────────────────────


def make_provider(name: str) -> Provider:
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openrouter":
        return OpenRouterProvider()
    raise ValueError(f"Unknown provider: {name}")


def available_providers() -> list[dict]:
    """Report which providers can be used right now, based on env vars.
    Useful for the frontend to grey out options that aren't configured."""
    return [
        {
            "name": "anthropic",
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            "ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "label": "Claude (Anthropic)",
        },
        {
            "name": "openrouter",
            "model": os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free"),
            "ready": bool(os.environ.get("OPENROUTER_API_KEY")),
            "label": "Gemma 4 31B (OpenRouter, free)",
        },
    ]
