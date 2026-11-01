"""Build a self-contained HTML inventory dashboard from the SQLite views.

No server and no CDN: data is embedded as JSON and the charts are drawn as
inline SVG by a small script, so the file opens offline in any browser.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"


def gather(con: sqlite3.Connection) -> dict:
    weekly = pd.read_sql(
        """SELECT store_id, week_start, sales_usd, spoilage_usd, spoilage_pct_of_sales,
                  stockout_rate_pct, lost_sales_usd
           FROM v_store_week ORDER BY week_start""",
        con,
    )
    # drop partial first/last weeks
    days = pd.read_sql("SELECT strftime('%Y-%W', date) yw, COUNT(DISTINCT date) n FROM daily_inventory GROUP BY yw", con)
    full = set(pd.read_sql("SELECT year_week, MIN(week_start) ws FROM v_store_week GROUP BY year_week", con)
               .merge(days, left_on="year_week", right_on="yw").query("n == 7").ws)
    weekly = weekly[weekly.week_start.isin(full)]
    chain = weekly.groupby("week_start").spoilage_pct_of_sales.median().round(2)

    cats = pd.read_sql(
        """SELECT store_id, category, ROUND(SUM(spoilage_usd), 0) AS spoilage_usd,
                  ROUND(SUM(sales_usd), 0) AS sales_usd
           FROM v_daily WHERE date >= date((SELECT MAX(date) FROM daily_inventory), '-90 day')
           AND is_perishable GROUP BY store_id, category""",
        con,
    )
    dos = pd.read_sql(
        """SELECT store_id, product_name, category, stockout_days_28, avg_daily_sold, lost_sales_usd_28
           FROM v_stockout_hotspots ORDER BY lost_sales_usd_28 DESC""",
        con,
    )
    stores = pd.read_sql("SELECT store_id, city, perishable_deliveries_per_week FROM stores ORDER BY store_id", con)
    asof = pd.read_sql("SELECT MAX(date) d FROM daily_inventory", con).d[0]
    return {
        "asof": asof,
        "stores": stores.to_dict("records"),
        "weekly": weekly.round(2).to_dict("records"),
        "chainMedian": [{"week_start": k, "pct": float(v)} for k, v in chain.items()],
        "categories": cats.to_dict("records"),
        "supply": dos.fillna(0).to_dict("records"),
    }


def write(con: sqlite3.Connection, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(gather(con), separators=(",", ":")))
    path.write_text(html)
    return path
