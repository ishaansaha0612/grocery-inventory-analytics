"""Write simulated tables to SQLite and create the reporting views."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .simulate import Tables

ROOT = Path(__file__).resolve().parents[1]


def build(tables: Tables, db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    tables.stores.to_sql("stores", con, index=False)
    tables.products.to_sql("products", con, index=False)
    daily = tables.daily.copy()
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    daily.to_sql("daily_inventory", con, index=False)
    con.execute("CREATE INDEX ix_daily_store_date ON daily_inventory(store_id, date)")
    con.execute("CREATE INDEX ix_daily_date ON daily_inventory(date)")
    con.executescript((ROOT / "sql" / "views.sql").read_text())
    con.commit()
    return con
