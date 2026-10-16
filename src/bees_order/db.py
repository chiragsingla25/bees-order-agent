"""SQLite catalog, stock and orders (spec FR-1–FR-3, R15, R17)."""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from bees_order.catalog import CATALOG, SEED_STOCK
from bees_order.schemas import Cart, OrderResult, Product

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY, name TEXT NOT NULL, brand TEXT NOT NULL, line TEXT NOT NULL,
    container TEXT NOT NULL, size_ml INTEGER NOT NULL, variant TEXT,
    pack_type TEXT NOT NULL, units_per_pack INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL, aliases TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK (stock >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    items TEXT NOT NULL, total_cents INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def init_db(path: str = ":memory:") -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def seed(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    """Replace the catalog with the seed data and reset stock. Orders are kept."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM products")
    conn.executemany(
        "INSERT INTO products VALUES (:sku, :name, :brand, :line, :container, :size_ml, "
        ":variant, :pack_type, :units_per_pack, :unit_price_cents, :aliases, :stock)",
        [{**p, "aliases": json.dumps(p["aliases"])} for p in CATALOG],
    )
    _set_last_reset(conn, now or _now())
    conn.execute("COMMIT")


def reset_stock(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany("UPDATE products SET stock = ? WHERE sku = ?",
                     [(units, sku) for sku, units in SEED_STOCK.items()])
    _set_last_reset(conn, now or _now())
    conn.execute("COMMIT")


def maybe_reset(conn: sqlite3.Connection, now: datetime | None = None,
                interval_h: float | None = None) -> bool:
    """R17: reseed stock if the reset interval has elapsed. Returns True if it reset."""
    now = now or _now()
    if interval_h is None:
        interval_h = float(os.getenv("RESET_INTERVAL_H", "2"))
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_reset'").fetchone()
    if row and now - datetime.fromisoformat(row["value"]) <= timedelta(hours=interval_h):
        return False
    reset_stock(conn, now)
    return True


def last_reset(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_reset'").fetchone()
    return datetime.fromisoformat(row["value"]) if row else None


def _set_last_reset(conn: sqlite3.Connection, when: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_reset', ?)", (when.isoformat(),))


def get_products(conn: sqlite3.Connection) -> dict[str, Product]:
    rows = conn.execute("SELECT * FROM products ORDER BY rowid").fetchall()
    return {r["sku"]: Product(**{**dict(r), "aliases": json.loads(r["aliases"])}) for r in rows}


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
