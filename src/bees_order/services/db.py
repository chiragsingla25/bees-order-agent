"""SQLite: the catalog, live stock and orders (spec R15, R17).

The catalog itself is data, not code: data/products.csv (one row per SKU, starting stock
included) is loaded into SQLite on start-up and again on every stock reset.
"""

import csv
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bees_order.domain.models import Cart, OrderResult, Product

CATALOG_CSV = Path(__file__).resolve().parents[3] / "data" / "products.csv"

_PRODUCTS = """CREATE TABLE products (
    sku TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
    brand TEXT NOT NULL, line TEXT NOT NULL,
    container TEXT NOT NULL, size_ml INTEGER NOT NULL, variant TEXT,
    pack_type TEXT NOT NULL, units_per_pack INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL, aliases TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK (stock >= 0)
)"""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    items TEXT NOT NULL, total_cents INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def load_catalog(path: Path = CATALOG_CSV) -> list[dict]:
    """The CSV rows, typed. Aliases are "|"-separated; an empty variant means none."""
    with open(path, newline="", encoding="utf-8") as f:
        return [{**row, "size_ml": int(row["size_ml"]), "variant": row["variant"] or None,
                 "units_per_pack": int(row["units_per_pack"]),
                 "unit_price_cents": int(row["unit_price_cents"]), "stock": int(row["stock"])}
                for row in csv.DictReader(f)]


def init_db(path: str = ":memory:") -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def seed(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    """(Re)load the products table from data/products.csv, starting stock included. Used on
    start-up and for every stock reset (R17). Orders are kept."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DROP TABLE IF EXISTS products")  # so a new CSV column never breaks an old DB
    conn.execute(_PRODUCTS)
    conn.executemany(
        "INSERT INTO products VALUES (:sku, :name, :category, :brand, :line, :container, "
        ":size_ml, :variant, :pack_type, :units_per_pack, :unit_price_cents, :aliases, :stock)",
        load_catalog(),
    )
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_reset', ?)",
                 ((now or _now()).isoformat(),))
    conn.execute("COMMIT")


def maybe_reset(conn: sqlite3.Connection, now: datetime | None = None,
                interval_h: float | None = None) -> bool:
    """R17: reload stock if the reset interval has elapsed. Returns True if it did."""
    now = now or _now()
    if interval_h is None:
        interval_h = float(os.getenv("RESET_INTERVAL_H", "2"))
    last = last_reset(conn)
    if last and now - last <= timedelta(hours=interval_h):
        return False
    seed(conn, now)
    return True


def last_reset(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_reset'").fetchone()
    return datetime.fromisoformat(row["value"]) if row else None


def get_products(conn: sqlite3.Connection) -> dict[str, Product]:
    rows = conn.execute("SELECT * FROM products ORDER BY rowid").fetchall()
    return {r["sku"]: Product(**{**dict(r), "aliases": [a for a in r["aliases"].split("|") if a]})
            for r in rows}


def commit_order(conn: sqlite3.Connection, cart: Cart, now: datetime | None = None) -> OrderResult:
    """R15: one transaction — re-check stock, write the order, decrement stock."""
    lines = cart.billable
    if not lines:
        return OrderResult(status="empty")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for ln in lines:
            row = conn.execute("SELECT stock FROM products WHERE sku = ?", (ln.sku,)).fetchone()
            if row is None or row["stock"] < ln.units:
                conn.execute("ROLLBACK")
                return OrderResult(status="stock_changed")
        for ln in lines:
            conn.execute("UPDATE products SET stock = stock - ? WHERE sku = ?",
                         (ln.units, ln.sku))
        total = sum(ln.line_total_cents for ln in lines)
        items = [ln.model_dump(include={"sku", "name", "packs", "units", "unit_price_cents",
                                        "line_total_cents", "flag"}) for ln in lines]
        cur = conn.execute(
            "INSERT INTO orders (created_at, items, total_cents, status) "
            "VALUES (?, ?, ?, 'approved')",
            ((now or _now()).isoformat(), json.dumps(items), total),
        )
        conn.execute("COMMIT")
        return OrderResult(status="approved", order_id=cur.lastrowid, total_cents=total)
    except Exception:
        conn.execute("ROLLBACK")
        raise


def order_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
