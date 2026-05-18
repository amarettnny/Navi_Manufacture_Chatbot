"""
Query layer. These functions are exposed to the LLM as tools.

Every function:
  - Takes simple typed arguments.
  - Returns JSON-serializable dicts/lists.
  - Never executes arbitrary SQL — only parameterized queries.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB = Path(__file__).parent / "manufacturing.db"


def _conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or DEFAULT_DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(cur) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ────────────────────────────── catalog ──────────────────────────────


def list_machines(type_filter: Optional[str] = None, db_path: Optional[Path] = None
                  ) -> list[dict]:
    """List machines, optionally filtering by type (substring match, case-insensitive)."""
    sql = "SELECT code, name, type FROM machines"
    args: tuple = ()
    if type_filter:
        sql += " WHERE LOWER(type) LIKE ?"
        args = (f"%{type_filter.lower()}%",)
    sql += " ORDER BY type, code"
    with _conn(db_path) as c:
        return _rows(c.execute(sql, args))


def list_machine_types(db_path: Optional[Path] = None) -> list[dict]:
    """Distinct machine types and how many machines fall under each."""
    sql = "SELECT type, COUNT(*) AS machine_count FROM machines GROUP BY type ORDER BY machine_count DESC"
    with _conn(db_path) as c:
        return _rows(c.execute(sql))


def list_products(group: Optional[str] = None, limit: int = 50,
                  db_path: Optional[Path] = None) -> list[dict]:
    """List products with optional group filter. Defaults to 50 max."""
    sql = 'SELECT code, "group" FROM products'
    args: tuple = ()
    if group:
        sql += ' WHERE "group" = ?'
        args = (group,)
    sql += " ORDER BY code LIMIT ?"
    args = args + (limit,)
    with _conn(db_path) as c:
        return _rows(c.execute(sql, args))


def search_products(query: str, limit: int = 20, db_path: Optional[Path] = None
                    ) -> list[dict]:
    """Substring search on product code (case-insensitive)."""
    sql = ('SELECT code, "group" FROM products WHERE LOWER(code) LIKE ? '
           'ORDER BY code LIMIT ?')
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (f"%{query.lower()}%", limit)))


def count_summary(db_path: Optional[Path] = None) -> dict:
    """Overall dataset counts — useful for orientation questions."""
    with _conn(db_path) as c:
        return {
            "machines":   c.execute("SELECT COUNT(*) FROM machines").fetchone()[0],
            "products":   c.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "routes":     c.execute("SELECT COUNT(*) FROM routes").fetchone()[0],
            "parameters": c.execute("SELECT COUNT(*) FROM parameters").fetchone()[0],
        }


# ────────────────────────────── routes ──────────────────────────────


def get_route(product_code: str, db_path: Optional[Path] = None) -> dict:
    """Full route(s) for a product. BOMs with identical step structures are
    grouped together so the response stays compact even when a product has
    many BOM variants that share the same routing."""
    with _conn(db_path) as c:
        routes = _rows(c.execute(
            "SELECT bom_code, version FROM routes WHERE product_code = ? "
            "ORDER BY bom_code",
            (product_code,),
        ))
        if not routes:
            return {"product_code": product_code, "routes": [],
                    "note": "No route found for that product code."}

        # Group BOMs by their step signature.
        groups: dict[tuple, dict] = {}
        for r in routes:
            steps = _rows(c.execute(
                "SELECT rs.sequence, rs.machine_code, m.type AS machine_type, "
                "rs.cycle_time_seconds, rs.min_batch_qty "
                "FROM route_steps rs JOIN machines m ON m.code = rs.machine_code "
                "WHERE rs.bom_code = ? ORDER BY rs.sequence",
                (r["bom_code"],),
            ))
            sig = tuple(
                (s["sequence"], s["machine_code"],
                 round(s["cycle_time_seconds"] or 0.0, 4),
                 s["min_batch_qty"])
                for s in steps
            )
            if sig not in groups:
                groups[sig] = {
                    "example_bom_code": r["bom_code"],
                    "version": r["version"],
                    "bom_count_in_group": 1,
                    "steps": steps,
                    "total_cycle_seconds": sum(
                        (s["cycle_time_seconds"] or 0.0) for s in steps),
                }
            else:
                groups[sig]["bom_count_in_group"] += 1

    return {
        "product_code": product_code,
        "total_boms": len(routes),
        "distinct_step_patterns": len(groups),
        "patterns": list(groups.values()),
    }


def find_products_by_machine(machine_code: str, limit: int = 50,
                             db_path: Optional[Path] = None) -> list[dict]:
    """Products whose route includes the given machine."""
    sql = ("SELECT DISTINCT r.product_code, rs.sequence "
           "FROM route_steps rs JOIN routes r ON r.bom_code = rs.bom_code "
           "WHERE rs.machine_code = ? ORDER BY r.product_code LIMIT ?")
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (machine_code, limit)))


def find_products_by_machine_type(machine_type: str, limit: int = 100,
                                  db_path: Optional[Path] = None) -> list[dict]:
    """Products whose route includes any machine of the given type."""
    sql = ("SELECT DISTINCT r.product_code "
           "FROM route_steps rs "
           "JOIN routes r   ON r.bom_code   = rs.bom_code "
           "JOIN machines m ON m.code       = rs.machine_code "
           "WHERE LOWER(m.type) LIKE ? "
           "ORDER BY r.product_code LIMIT ?")
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (f"%{machine_type.lower()}%", limit)))


def aggregate_cycle_time(product_code: Optional[str] = None,
                         machine_code: Optional[str] = None,
                         db_path: Optional[Path] = None) -> dict:
    """Sum and average cycle times, optionally scoped to a product or machine."""
    where, args = [], []
    if product_code:
        where.append("r.product_code = ?")
        args.append(product_code)
    if machine_code:
        where.append("rs.machine_code = ?")
        args.append(machine_code)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (f"SELECT COUNT(*) AS n_steps, "
           f"SUM(rs.cycle_time_seconds) AS total_seconds, "
           f"AVG(rs.cycle_time_seconds) AS avg_seconds, "
           f"MIN(rs.cycle_time_seconds) AS min_seconds, "
           f"MAX(rs.cycle_time_seconds) AS max_seconds "
           f"FROM route_steps rs JOIN routes r ON r.bom_code = rs.bom_code "
           f"{where_sql}")
    with _conn(db_path) as c:
        row = dict(c.execute(sql, tuple(args)).fetchone())
    row["scope"] = {"product_code": product_code, "machine_code": machine_code}
    return row


def rank_machines_by_product_count(top_n: int = 20, db_path: Optional[Path] = None
                                   ) -> list[dict]:
    """Rank machines by the number of distinct products whose route includes them.
    Use this for questions like 'which machines are used by the most products'."""
    sql = (
        "SELECT rs.machine_code, m.type AS machine_type, "
        "COUNT(DISTINCT r.product_code) AS product_count "
        "FROM route_steps rs "
        "JOIN routes r   ON r.bom_code   = rs.bom_code "
        "JOIN machines m ON m.code       = rs.machine_code "
        "GROUP BY rs.machine_code "
        "ORDER BY product_count DESC "
        "LIMIT ?"
    )
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (top_n,)))


def longest_routes(top_n: int = 10, db_path: Optional[Path] = None) -> list[dict]:
    """Products with the most steps in their route."""
    sql = ("SELECT r.product_code, r.bom_code, COUNT(rs.sequence) AS step_count, "
           "SUM(rs.cycle_time_seconds) AS total_cycle_seconds "
           "FROM routes r JOIN route_steps rs ON rs.bom_code = r.bom_code "
           "GROUP BY r.bom_code ORDER BY step_count DESC, total_cycle_seconds DESC "
           "LIMIT ?")
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (top_n,)))


# ──────────────────────────── parameters ────────────────────────────


def get_step_parameters(product_code: str, sequence: int,
                        db_path: Optional[Path] = None) -> list[dict]:
    """All parameters for one step of a product's route, deduplicated across
    BOMs (a bom_count column shows how many BOMs share the setting)."""
    sql = ("SELECT key, value, value_text, unit, machine_code, COUNT(*) AS bom_count "
           "FROM parameters WHERE product_code = ? AND sequence = ? "
           "GROUP BY key, value, value_text, unit, machine_code "
           "ORDER BY key, machine_code")
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (product_code, sequence)))


def find_parameter(product_code: str, key_substring: str,
                   db_path: Optional[Path] = None) -> list[dict]:
    """Find parameters for a product whose key matches a substring.
    Results are deduplicated across BOM versions: identical (sequence, machine,
    key, value, value_text, unit) tuples are collapsed into one row with a
    bom_count showing how many BOMs share that setting."""
    sql = ("SELECT sequence, machine_code, key, value, value_text, unit, "
           "COUNT(*) AS bom_count "
           "FROM parameters WHERE product_code = ? AND LOWER(key) LIKE ? "
           "GROUP BY sequence, machine_code, key, value, value_text, unit "
           "ORDER BY sequence, key, machine_code")
    with _conn(db_path) as c:
        return _rows(c.execute(sql, (product_code, f"%{key_substring.lower()}%")))


def list_parameter_keys(top_n: int = 30, db_path: Optional[Path] = None) -> dict:
    """The most common parameter keys across the entire dataset.
    Always reports total_distinct_keys so the caller knows if the result is partial."""
    with _conn(db_path) as c:
        total = c.execute("SELECT COUNT(DISTINCT key) FROM parameters").fetchone()[0]
        rows = _rows(c.execute(
            "SELECT key, COUNT(*) AS occurrences FROM parameters "
            "GROUP BY key ORDER BY occurrences DESC LIMIT ?",
            (top_n,),
        ))
    return {
        "total_distinct_keys": total,
        "showing": len(rows),
        "truncated": len(rows) < total,
        "keys": rows,
    }


def run_sql(query: str, limit: int = 500, db_path: Optional[Path] = None) -> dict:
    """Execute a read-only SQLite SELECT against the manufacturing database.

    Use this for any aggregation, ranking, cross-table, or set-operation question
    that the pre-built tools do not cover. Prefer the curated tools for common
    lookups; reach for run_sql for novel or complex questions.

    Full schema:
      machines(code TEXT, name TEXT, type TEXT)
        -- type values are Turkish: Ram, Kurutma, Yıkama, Kalite Kontrol, etc.
      products(code TEXT, "group" TEXT)
      routes(bom_code TEXT, product_code TEXT, version INTEGER)
      route_steps(bom_code TEXT, sequence INTEGER, machine_code TEXT,
                  cycle_time_seconds REAL, min_batch_qty REAL)
      parameters(id INTEGER, product_code TEXT, bom_code TEXT,
                 machine_code TEXT, sequence INTEGER,
                 key TEXT, value REAL, value_text TEXT, unit TEXT)
        -- key and value_text are Turkish; value holds numeric readings.
        -- Join parameters→machines on machine_code to filter by machine type.

    Only SELECT and WITH (CTE) queries are accepted. Returns up to `limit` rows.
    If the query has a SQL error it is returned in the `error` field — correct
    and retry.
    """
    q = query.strip().rstrip(";")
    upper = q.upper()

    if not upper.startswith(("SELECT", "WITH")):
        return {"error": "Only SELECT/WITH queries are allowed."}

    for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
               "ATTACH", "DETACH", "PRAGMA", "REPLACE", "VACUUM"):
        if kw in upper:
            return {"error": f"Query contains forbidden keyword: {kw}"}

    if ";" in q:
        return {"error": "Multiple statements are not allowed."}

    try:
        with _conn(db_path) as c:
            c.execute("PRAGMA query_only = ON")
            rows = _rows(c.execute(f"SELECT * FROM ({q}) LIMIT ?", (limit,)))
        return {"row_count": len(rows), "truncated": len(rows) == limit, "rows": rows}
    except Exception as e:
        return {"error": f"SQL error: {e}"}


# tool schema

TOOLS = [
    {
        "name": "count_summary",
        "description": "Return overall counts of machines, products, routes, and parameters in the dataset. Use this for orientation questions like 'how big is the dataset' or 'what do we have here'.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_machines",
        "description": "List machines in the factory. Optionally filter by machine type using a case-insensitive substring (e.g. 'Ram', 'washing', 'kalite').",
        "input_schema": {
            "type": "object",
            "properties": {
                "type_filter": {"type": "string", "description": "Optional type substring."}
            },
            "required": [],
        },
    },
    {
        "name": "list_machine_types",
        "description": "List distinct machine types with how many machines fall under each.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_products",
        "description": "List products, optionally filtered by group. Use when the user wants to browse products.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Optional product group."},
                "limit": {"type": "integer", "description": "Max rows, default 50."},
            },
            "required": [],
        },
    },
    {
        "name": "search_products",
        "description": "Case-insensitive substring search on product code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "Max rows, default 20."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_route",
        "description": "Get the full manufacturing route (BOM + ordered steps) for a product. Returns every step with sequence, machine, cycle time, and min batch.",
        "input_schema": {
            "type": "object",
            "properties": {"product_code": {"type": "string"}},
            "required": ["product_code"],
        },
    },
    {
        "name": "find_products_by_machine",
        "description": "List products whose route includes a specific machine (by machine code).",
        "input_schema": {
            "type": "object",
            "properties": {
                "machine_code": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["machine_code"],
        },
    },
    {
        "name": "find_products_by_machine_type",
        "description": "List products whose route includes any machine of a given type (substring, case-insensitive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "machine_type": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["machine_type"],
        },
    },
    {
        "name": "aggregate_cycle_time",
        "description": "Aggregate cycle time statistics (count, sum, avg, min, max in seconds). Optionally scope to a product or a machine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_code": {"type": "string"},
                "machine_code": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "rank_machines_by_product_count",
        "description": (
            "Rank all machines by the number of distinct products whose route includes them. "
            "Use this for questions like 'which machines handle the most products', "
            "'busiest machines', or 'most used machines'. Returns machine_code, "
            "machine_type, and product_count, sorted descending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "How many machines to return, default 20."},
            },
            "required": [],
        },
    },
    {
        "name": "longest_routes",
        "description": "Return products with the most steps in their route, descending.",
        "input_schema": {
            "type": "object",
            "properties": {"top_n": {"type": "integer", "description": "Default 10."}},
            "required": [],
        },
    },
    {
        "name": "get_step_parameters",
        "description": "All parameters set for one step of a product's route. Sequence is the step number (1-based).",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_code": {"type": "string"},
                "sequence": {"type": "integer"},
            },
            "required": ["product_code", "sequence"],
        },
    },
    {
        "name": "find_parameter",
        "description": "Find parameter values for a product whose key matches a substring (e.g. 'temperature', 'sıcaklık', 'hız', 'speed').",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_code": {"type": "string"},
                "key_substring": {"type": "string"},
            },
            "required": ["product_code", "key_substring"],
        },
    },
    {
        "name": "list_parameter_keys",
        "description": (
            "Dataset-wide survey of parameter keys ordered by frequency. "
            "Always returns total_distinct_keys so you know if the result is partial. "
            "Use only for broad orientation questions about the whole dataset. "
            "For machine-specific or product-specific parameter questions use run_sql."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"top_n": {"type": "integer", "description": "Default 30."}},
            "required": [],
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Execute a read-only SQLite SELECT against the manufacturing database. "
            "Use this for any aggregation, ranking, filtering, or cross-table question "
            "that the pre-built tools do not cover cleanly — for example: all parameter "
            "keys for a specific machine type, average temperature settings across "
            "products, products sharing a particular parameter value, etc. "
            "Prefer the curated tools for simple lookups; use run_sql for everything else. "
            "Schema: "
            "machines(code, name, type) — type is Turkish (Ram, Kurutma, Yıkama…); "
            "products(code, \"group\"); "
            "routes(bom_code, product_code, version); "
            "route_steps(bom_code, sequence, machine_code, cycle_time_seconds, min_batch_qty); "
            "parameters(id, product_code, bom_code, machine_code, sequence, key, value, value_text, unit) "
            "— key/value_text are Turkish, value is numeric. "
            "If the query returns an error, read it and retry with a corrected query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A SQLite SELECT or WITH statement."},
                "limit": {"type": "integer", "description": "Max rows to return, default 500."},
            },
            "required": ["query"],
        },
    },
]


TOOL_FUNCTIONS = {
    "count_summary": count_summary,
    "list_machines": list_machines,
    "rank_machines_by_product_count": rank_machines_by_product_count,
    "list_machine_types": list_machine_types,
    "list_products": list_products,
    "search_products": search_products,
    "get_route": get_route,
    "find_products_by_machine": find_products_by_machine,
    "find_products_by_machine_type": find_products_by_machine_type,
    "aggregate_cycle_time": aggregate_cycle_time,
    "longest_routes": longest_routes,
    "get_step_parameters": get_step_parameters,
    "find_parameter": find_parameter,
    "list_parameter_keys": list_parameter_keys,
    "run_sql": run_sql,
}


def dispatch(name: str, args: dict, db_path: Optional[Path] = None):
    """Run a tool call by name. Raises if name unknown."""
    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {name}")
    fn = TOOL_FUNCTIONS[name]
    return fn(**args, db_path=db_path) if db_path else fn(**args)
