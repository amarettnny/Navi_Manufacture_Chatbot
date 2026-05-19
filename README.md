# Navi_Manufacture_Chatbot

## Instructions to run it **locally**: 

### 1 Set up your API keys
Considering the API budget and context length for embedded LLM, two options of models are provided:

| Provider | Default model | Notes |
|---|---|---|
| **Anthropic** | `claude-sonnet-4-5` | Highest reliability; Fastest output generated; Higher cost |
| **OpenRouter** | `nvidia/nemotron-3-super-120b-a12b:free` | Free community tier; Considerable context length provided | subject to upstream rate limits |

The status dot in the top-right reflects live state: gray = no API key configured, green = ready, pulsing yellow = processing a request.

```bash
cp .env.example .env
```
   edit .env and paste in at least one of the following LLM API key:
- ANTHROPIC_API_KEY (from https://console.anthropic.com/settings/keys)
- OPENROUTER_API_KEY (from https://openrouter.ai/keys)

### 2 Backend
run following commands in terminal (environment interpretor set to backend/.venv/bin/python):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python load_data.py ../data/seed_data_parameters.json
uvicorn server:app --reload --port 8000
```

### 3 Frontend (separate terminal)
```bash
cd frontend
python -m http.server 8080
```
**The chatbot frontend is deployed at http://localhost:8080**

## Chatbot functionality

Navi is an AI assistant backed by a SQLite database of textile factory operations data. It answers natural-language questions about machines, products, manufacturing routes, and process parameters. altogether translating between English questions and Turkish-keyed data automatically.

### What you can ask

**Machines**
- List all machines or filter by type — *"What machine types exist and how many of each?"*
- Find the busiest machines by product volume — *"Which machines handle the most products?"*

**Products**
- Browse or search the product catalog — *"Find products with code starting with L3AY"*
- Find products that pass through a specific machine or machine type — *"Which products go through RAM 1?"*
- Rank products by route complexity — *"List the 5 products with the longest routes"*

**Manufacturing routes**
- Get the full ordered step-by-step route for any product, including machine assignments, cycle times, and minimum batch quantities — *"Show the route for product 607C11020S9K.3"*
- Products often have many BOM variants; the chatbot groups BOMs that share an identical step structure and reports patterns rather than flooding you with duplicates

**Process parameters**
- Look up any parameter for a product at a specific step — *"What's the temperature on BAL 1 for product L3AY1000009K?"*
- List all parameter keys tracked for a given machine type — *"What parameters are tracked on the Ram step?"*
- Search parameters by keyword across all steps of a product — supports both English (*"temperature"*) and Turkish (*"Sıcaklık"*) terms

**Aggregations and comparisons**
- Cycle time statistics scoped to a product or machine (sum, average, min, max)
- Any cross-table question the pre-built tools don't cover is handled by a read-only SQL escape hatch the model can invoke internally

### Edge cases handled

- **Multi-BOM products** — products with dozens of BOM variants sharing the same routing are collapsed into patterns so answers stay concise, with the full BOM count reported.
- **Turkish / English bilingual data** — machine types (`Yıkama` = washing, `Kurutma` = drying, `Ram` = stenter, `Şardon` = brushing, `Kalite Kontrol` = QC) and parameter keys (`Sıcaklık (°C)` = temperature, `Hız (mt/dk)` = speed, `Gramaj (gr/m²)` = grammage) are translated on the fly.
- **Concurrent prompts** — if you send a second question while the first is still processing, it is queued and runs immediately after, in order. A visual indicator shows whether a response is thinking or in queue.
- **Navigation during processing** — navigating to another conversation while a response is in flight does not cancel it. The result is saved to that session's history and the UI updates when you return.
- **SQL safety** — `run_sql` only accepts `SELECT`/`WITH` queries. Write operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, etc.) and multi-statement inputs are rejected before execution. The connection also runs under `PRAGMA query_only = ON`.
- **Large result sets** — tool results are capped at 60,000 characters and SQL queries at 500 rows to prevent context overflow.
- **Model reasoning leakage** — some open-weight models emit visible chain-of-thought (`<think>` / `<thinking>` blocks). These are stripped from the reply before it reaches you.
- **Incomplete answers from tool iteration limits** — the model is allowed up to 20 sequential tool calls per response, preventing runaway loops while still handling complex multi-step lookups.