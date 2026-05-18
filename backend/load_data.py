"""
Load seed_data.json into a SQLite database.

Usage:
    python load_data.py path/to/seed_data.json [path/to/output.db]

If output path is omitted, writes to ./manufacturing.db next to this file.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
DROP TABLE IF EXISTS parameters;
DROP TABLE IF EXISTS route_steps;
DROP TABLE IF EXISTS routes;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS machines;

CREATE TABLE machines (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL
);

CREATE TABLE products (
    code  TEXT PRIMARY KEY,
    "group" TEXT
);

CREATE TABLE routes (
    bom_code     TEXT PRIMARY KEY,
    product_code TEXT NOT NULL REFERENCES products(code),
    version      INTEGER NOT NULL
);

CREATE TABLE route_steps (
    bom_code           TEXT    NOT NULL REFERENCES routes(bom_code),
    sequence           INTEGER NOT NULL,
    machine_code       TEXT    NOT NULL REFERENCES machines(code),
    cycle_time_seconds REAL,
    min_batch_qty      REAL,
    PRIMARY KEY (bom_code, sequence)
);

CREATE TABLE parameters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code TEXT    NOT NULL,
    bom_code     TEXT    NOT NULL,
    machine_code TEXT    NOT NULL,
    sequence     INTEGER NOT NULL,
    key          TEXT    NOT NULL,
    value        REAL,
    value_text   TEXT,
    unit         TEXT
);

CREATE INDEX idx_routes_product       ON routes(product_code);
CREATE INDEX idx_steps_machine        ON route_steps(machine_code);
CREATE INDEX idx_params_product       ON parameters(product_code);
CREATE INDEX idx_params_product_step  ON parameters(product_code, sequence);
CREATE INDEX idx_params_machine       ON parameters(machine_code);
CREATE INDEX idx_params_key           ON parameters(key);
"""


def load(seed_path: Path, db_path: Path) -> None:
    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Machines
    conn.executemany(
        "INSERT INTO machines (code, name, type) VALUES (?, ?, ?)",
        [(m["code"], m["name"], m["type"]) for m in data["machines"]],
    )

    # Products
    conn.executemany(
        'INSERT INTO products (code, "group") VALUES (?, ?)',
        [(p["code"], p.get("group")) for p in data["products"]],
    )

    # Routes + steps
    route_rows = []
    step_rows = []
    for r in data["routes"]:
        route_rows.append((r["bom_code"], r["product_code"], r["version"]))
        for s in r["steps"]:
            step_rows.append(
                (
                    r["bom_code"],
                    s["sequence"],
                    s["machine_code"],
                    s.get("cycle_time_seconds"),
                    s.get("min_batch_qty"),
                )
            )

    conn.executemany(
        "INSERT INTO routes (bom_code, product_code, version) VALUES (?, ?, ?)",
        route_rows,
    )
    conn.executemany(
        "INSERT INTO route_steps "
        "(bom_code, sequence, machine_code, cycle_time_seconds, min_batch_qty) "
        "VALUES (?, ?, ?, ?, ?)",
        step_rows,
    )

    # Parameters
    param_rows = [
        (
            p["product_code"],
            p["bom_code"],
            p["machine_code"],
            p["sequence"],
            p["key"],
            p.get("value"),
            p.get("value_text"),
            p.get("unit"),
        )
        for p in data["parameters"]
    ]
    conn.executemany(
        "INSERT INTO parameters "
        "(product_code, bom_code, machine_code, sequence, key, value, value_text, unit) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        param_rows,
    )

    conn.commit()

    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ["machines", "products", "routes", "route_steps", "parameters"]
    }
    conn.close()

    print(f"Wrote {db_path}")
    for t, n in counts.items():
        print(f"  {t:14s} {n:>8,}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    seed = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else (
        Path(__file__).parent / "manufacturing.db"
    )
    load(seed, out)
