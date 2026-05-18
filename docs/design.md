# Manufacturing Ops Chatbot — Design Doc

## Purpose
Let an operator, planner, or process engineer ask questions in natural language
about a textile factory's machines, products, BOMs/routes, and step
parameters, and get accurate answers grounded in the underlying dataset.

## Data model

From `seed_data.json`:

- **machines** — `code`, `name`, `type`. 17 machines spanning 8 types:
  Yıkama (washing), Kurutma (drying), Ram (stenter), Şardon (brushing),
  Kalite Kontrol (QC), Final Kalite Kontrol (final QC), Sarma (winding),
  Tüp Açma (tube opening).
- **products** — `code`, `group`. 50 products, mostly group "Mamul".
- **routes** — 626 routes (one per product × BOM variant). Each has 3–8
  ordered steps with `machine_code`, `cycle_time_seconds`, `min_batch_qty`.
  A product often has many BOMs that differ only in which specific machine
  of a given type is selected at each step.
- **parameters** — ~77 k rows. One row per (product, BOM, machine, sequence,
  key). 247 distinct keys, mostly Turkish (`Sıcaklık (°C)`, `Hız (mt/dk)`,
  `Gramaj (gr/m²)`, etc.).

## Core functionalities

### 1. Catalog browsing
- "List all machines" / "What machines do we have for washing?"
- "How many products are there?"
- "What types of machines exist and how many of each?"

### 2. Route lookup
- "Show the route for product 607C11020S9K.3."
- "Which machines does product L3AY1000009K go through, in order?"
- "What's the cycle time for the BAL 1 step on product X?"
- "Total cycle time for product X."

### 3. Reverse lookup (machine → products)
- "Which products use machine HKK 2?"
- "What products require a Şardon step?"

### 4. Parameter inspection
- "What temperature is used on BAL 1 for product X?"
- "Show all parameters for step 3 of product L3AY1000009K."
- "What's the speed setting for the Ram step on product Y?"

### 5. Comparison & aggregation
- "Compare cycle times across products that use KUR 1."
- "Average minibatch quantity on RAM machines."
- "Which product has the longest total route time?"
- "Top 5 products by number of steps."

### 6. Constraint queries
- "Find products with min batch qty above 500 kg on any step."
- "Routes with more than 6 steps."

### 7. Conversational features
- Follow-up context: "and what about its temperature settings?"
- Clarification when a product code is missing or ambiguous.
- Mixed Turkish / English handling.

## Non-functional requirements
- **Grounded.** No invented product codes, parameter keys, or values.
- **Fast.** Most queries respond in < 3 s (local SQLite, not vector store).
- **Safe.** No raw SQL from the LLM; only the predefined tool surface.
- **Inspectable.** UI shows the tool calls that produced each answer.

## Architecture

```
Frontend (single-file HTML, model picker)
        │  POST /chat { messages, provider }
        ▼
FastAPI server (server.py)
  └── providers.py
        ├── AnthropicProvider  ─► Claude API (Anthropic Messages, content blocks)
        └── OpenRouterProvider ─► OpenRouter via OpenAI SDK (chat.completions, tool_calls)
              │
              └── shared tool-use loop: dispatches to queries.py → SQLite
                                                ▲
                                                └ load_data.py (one-shot)
```

The LLM — whichever provider is chosen — is restricted to the same fixed
tool surface defined in `queries.py`. Each provider translates the shared
tool schema into its native shape (Anthropic content blocks vs OpenAI
function-calling) and handles its own tool-result loop internally. The
frontend keeps a provider-neutral message history (`{role, content}` strings),
so switching providers mid-conversation is safe.

## Tool surface

| Tool | Purpose |
|---|---|
| `count_summary` | Overall dataset counts. |
| `list_machines(type_filter?)` | Catalog with optional type filter. |
| `list_machine_types` | Distinct types + counts. |
| `list_products(group?, limit?)` | Browse products. |
| `search_products(query)` | Substring on product code. |
| `get_route(product_code)` | Full BOMs grouped by step signature. |
| `find_products_by_machine(machine_code)` | Reverse lookup. |
| `find_products_by_machine_type(type)` | Reverse lookup by type. |
| `get_step_parameters(product_code, sequence)` | All params for one step. |
| `find_parameter(product_code, key_substring)` | Search params by key. |
| `list_parameter_keys(top_n?)` | Discover what keys exist. |
| `aggregate_cycle_time(product_code?, machine_code?)` | Totals/averages. |
| `longest_routes(top_n?)` | Products with the most steps. |

## Dedup philosophy

A product often has dozens of BOMs that differ only in which specific machine
of a given type is chosen at each step (e.g. KUR 1 vs KUR 2). Returning all of
them inflates LLM context. Two design choices:

- `get_route` **groups BOMs by step signature** and returns one row per
  distinct pattern with a `bom_count_in_group`.
- `find_parameter` and `get_step_parameters` **deduplicate** identical
  (sequence, machine, key, value) tuples and report a `bom_count`.

## UI

- Single chat column, max-width ~760 px.
- Right sidebar shows tool trace (what was called, with what arguments).
- Suggested questions on empty state.
- Industrial / utilitarian aesthetic: dark, monospace, brass accent.

## Out of scope (v1)
- Editing data; importing new datasets at runtime.
- User accounts; multi-tenant separation.
- Plotting / charting.
- Excel export.
- Streaming responses (planned for v2).

## Future work
- **Streaming.** Use the Anthropic SDK's `stream=True` and SSE to the
  frontend so long answers appear progressively.
- **Charts.** When the LLM returns tabular data, render bar charts /
  histograms inline (e.g. cycle-time distributions).
- **Caching.** Memoize tool calls within a conversation.
- **Eval set.** A small JSONL of (question, expected key facts) so we can
  regression-test the system prompt and tool surface as they evolve.
- **Multi-dataset.** Allow uploading additional `seed_data.json` files and
  switch between them.
