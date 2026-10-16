"""Phase 1 criteria: seed contents, R15 (commit_order) and R17 (maybe_reset)."""

import threading
from datetime import timedelta

from bees_order import db
from bees_order.cart import build_cart
from bees_order.schemas import ResolvedItem

from .conftest import T0


def cart_for(products, *pairs):
    return build_cart([ResolvedItem(product_text=s, quantity=q, unit="pack", sku=s)
                       for s, q in pairs], products)


def stock(conn, sku):
    return conn.execute("SELECT stock FROM products WHERE sku = ?", (sku,)).fetchone()[0]


def test_seed_contents(products):
    assert len(products) >= 28
    assert {"BRA-CHP-LT350", "BRA-CHP-LTA473", "GUA-LT350", "GUA-PET2L"} <= products.keys()
    assert any(p.stock == 0 for p in products.values())
    assert any(0 < p.stock < 10 * p.units_per_pack for p in products.values())
    for p in products.values():
        assert p.unit_price_cents > 0 and p.units_per_pack > 0 and p.aliases is not None


def test_r15_approve_decrements_stock_and_writes_one_order(conn, products):
    cart = cart_for(products, ("BRA-CHP-LT350", 3), ("GUA-PET2L", 1))
    result = db.commit_order(conn, cart)
    assert result.status == "approved" and result.order_id == 1
    assert result.total_cents == cart.total_cents
    assert stock(conn, "BRA-CHP-LT350") == 600 - 36
    assert stock(conn, "GUA-PET2L") == 24 - 6
    assert db.order_count(conn) == 1


def test_r15_stock_changed_writes_nothing(conn, products):
    cart = cart_for(products, ("STE-LN330", 5), ("SKL-LT350", 1))
    conn.execute("UPDATE products SET stock = 24 WHERE sku = 'STE-LN330'")  # another buyer
    result = db.commit_order(conn, cart)
    assert result.status == "stock_changed"
    assert stock(conn, "STE-LN330") == 24
    assert stock(conn, "SKL-LT350") == 1200  # earlier line not half-applied
    assert db.order_count(conn) == 0


def test_r15_only_billable_lines_are_committed(conn, products):
    cart = cart_for(products, ("SPA-LT350", 2), ("BOH-LT350", 10))  # unavailable + short
    result = db.commit_order(conn, cart)
    assert result.status == "approved"
    assert stock(conn, "BOH-LT350") == 50 - 48
    assert stock(conn, "SPA-LT350") == 0


def test_r15_empty_cart_writes_nothing(conn, products):
    assert db.commit_order(conn, cart_for(products, ("SPA-LT350", 1))).status == "empty"
    assert db.order_count(conn) == 0


def test_r15_concurrent_approvals_never_oversell(tmp_path):
    path = str(tmp_path / "bees.db")
    setup = db.init_db(path)
    db.seed(setup, now=T0)
    products = db.get_products(setup)
    results = []

    def approve():
        c = db.init_db(path)
        c.execute("PRAGMA busy_timeout = 5000")
        results.append(db.commit_order(c, cart_for(products, ("STE-LN330", 4))).status)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["approved", "stock_changed"]  # 6 packs in stock, 2 × 4 asked
    assert stock(setup, "STE-LN330") == 144 - 96


def test_r17_maybe_reset_only_after_interval_and_keeps_orders(conn, products):
    db.commit_order(conn, cart_for(products, ("SKL-LT350", 2)))
    assert not db.maybe_reset(conn, now=T0 + timedelta(hours=1), interval_h=2)
    assert stock(conn, "SKL-LT350") == 1200 - 24
    assert db.maybe_reset(conn, now=T0 + timedelta(hours=2, minutes=1), interval_h=2)
    assert stock(conn, "SKL-LT350") == 1200
    assert db.order_count(conn) == 1
    assert db.last_reset(conn) == T0 + timedelta(hours=2, minutes=1)
    assert not db.maybe_reset(conn, now=T0 + timedelta(hours=3), interval_h=2)


def test_r17_reset_on_fresh_db_without_meta():
    c = db.init_db(":memory:")
    assert db.maybe_reset(c, now=T0, interval_h=2)  # no last_reset row → resets
