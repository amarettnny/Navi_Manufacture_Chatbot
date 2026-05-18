"""
FastAPI chat server.

POST /chat with:
  {
    "messages": [{"role": "user"|"assistant", "content": "..."}, ...],
    "provider": "anthropic" | "openrouter"   # optional, defaults to anthropic
  }
  → {"reply": str, "tool_trace": [...], "provider": str, "model": str}

GET /health → liveness + which providers are configured.

Environment:
  ANTHROPIC_API_KEY   required for the Anthropic provider
  ANTHROPIC_MODEL     optional, default claude-sonnet-4-5
  OPENROUTER_API_KEY  required for the OpenRouter provider
  OPENROUTER_MODEL    optional, default google/gemma-4-31b-it:free
  CHATBOT_DB_PATH     optional, default ./manufacturing.db

Run with:
  uvicorn server:app --reload --port 8000
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Load .env from the project root *before* any provider tries to read env vars.
# Walks up from this file to find the closest .env; silent if missing (so the
# server still runs when env vars are set the conventional way, e.g. in prod).
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import providers
import queries

DB_PATH = Path(os.environ.get("CHATBOT_DB_PATH", queries.DEFAULT_DB))

SYSTEM_PROMPT = """You are an assistant for a textile factory's manufacturing
operations data. The dataset covers machines, products, manufacturing routes
(BOMs and ordered steps), and per-step parameters.

The data is partly in Turkish (machine types like Yıkama=washing, Kurutma=drying,
Ram=stenter, Şardon=brushing/raising, Kalite Kontrol=quality control). Parameter
keys are also Turkish, e.g. "Sıcaklık (°C)" = temperature, "Hız (mt/dk)" = speed,
"Gramaj (gr/m²)" = grammage. When the user asks in English, translate as needed
when calling tools.

Tool selection rules:
- Use the curated tools (get_route, find_products_by_machine, rank_machines_by_product_count,
  etc.) for simple direct lookups — they are reliable and fast.
- Use run_sql for everything else: listing parameter keys for a machine type,
  aggregations, rankings, cross-table joins, filtering by value, comparisons.
  A single run_sql call is almost always better than many sequential curated calls.
- If run_sql returns an error field, read it, fix the SQL, and retry immediately.
- Never loop through every machine/product making individual tool calls when one
  run_sql query can return the full result set at once.

You must base every numeric claim on a tool result. Never write numbers from
memory or estimation. Do NOT invent product codes, machine codes, parameter keys,
or numeric values — always look them up. If a tool returns nothing, say so honestly.

CRITICAL — output rules:
- Never show your reasoning, planning, or intermediate calculations to the user.
- Call tools silently. Once you have all the data you need, write ONLY the final
  answer directly.
- Do NOT narrate what you are about to do, what tools you will call, or how you
  will compute the result.
- If you use <think> tags internally, they will be stripped — do not rely on them
  for content that should reach the user.

Format final answers in clear prose. When showing route steps or parameter
listings, use compact markdown tables. Keep numeric values to a sensible
precision (cycle times in seconds, 1-2 decimals; temperatures as integers).

A product often has many BOMs that share an identical routing pattern.
get_route groups these by step signature; report patterns, not individual BOMs,
unless the user specifically asks about a BOM.
"""


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    provider: str = "anthropic"


class ChatResponse(BaseModel):
    reply: str
    tool_trace: list[dict[str, Any]]
    provider: str
    model: str


app = FastAPI(title="Manufacturing Ops Chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "db_path": str(DB_PATH),
        "db_present": DB_PATH.exists(),
        "providers": providers.available_providers(),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not DB_PATH.exists():
        raise HTTPException(
            500, f"Database not found at {DB_PATH}. Run load_data.py."
        )
    try:
        provider = providers.make_provider(req.provider)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))

    try:
        reply, trace = provider.run(req.messages, SYSTEM_PROMPT, DB_PATH)
    except Exception as e:
        # Surface provider errors so the user can tell why things failed
        # (rate limits, auth, model typos, etc.).
        raise HTTPException(
            502, f"{provider.name} provider error: {type(e).__name__}: {e}"
        )

    return ChatResponse(
        reply=reply,
        tool_trace=trace,
        provider=provider.name,
        model=provider.model,
    )
