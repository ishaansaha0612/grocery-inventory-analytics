"""Automated weekly store report (replaces a manual Excel build).

Pulls last week's KPIs from SQLite with SQL and writes a formatted Excel
workbook: a summary sheet with flags, a 12-week trend sheet, and a stockout
watchlist of items that ran out 4+ days in the last 28.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F2A37")
FLAG_FILL = PatternFill("solid", fgColor="FBE3D6")


def last_full_week(con) -> str:
    """Most recent week that has all 7 days of data."""
    return pd.read_sql(
        """SELECT strftime('%Y-%W', date) AS yw
           FROM (SELECT DISTINCT date FROM daily_inventory)
           GROUP BY yw HAVING COUNT(*) = 7
           ORDER BY yw DESC LIMIT 1""",
        con,
    ).iloc[0, 0]


def build_frames(con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    week = last_full_week(con)
    summary = pd.read_sql(
        """
        WITH cur AS (SELECT * FROM v_store_week WHERE year_week = :w),
        prior AS (
            SELECT store_id, AVG(spoilage_pct_of_sales) AS avg_spoil_pct
            FROM v_store_week
            WHERE year_week < :w AND year_week >= (
                SELECT MIN(year_week) FROM (
                    SELECT DISTINCT year_week FROM v_store_week
                    WHERE year_week < :w ORDER BY year_week DESC LIMIT 8))
            GROUP BY store_id)
        SELECT s.store_id, s.city, cur.sales_usd, cur.spoilage_usd,
               cur.spoilage_pct_of_sales, ROUND(prior.avg_spoil_pct, 2) AS spoilage_pct_8wk_avg,
               cur.stockout_rate_pct, cur.lost_sales_usd
        FROM cur JOIN stores s USING (store_id) JOIN prior USING (store_id)
        ORDER BY cur.spoilage_pct_of_sales DESC
        """,
        con,
        params={"w": week},
    )
    chain_median = summary.spoilage_pct_of_sales.median()
    summary["flag"] = ""
    summary.loc[summary.spoilage_pct_of_sales > 2 * chain_median, "flag"] = "Spoilage > 2x chain median"
    summary.loc[summary.stockout_rate_pct > 8, "flag"] = (summary.flag + "; High stockouts").str.strip("; ")

    trend = pd.read_sql(
        """SELECT year_week, week_start, store_id, spoilage_pct_of_sales
           FROM v_store_week WHERE year_week <= :w ORDER BY year_week""",
        con, params={"w": week},
    )
    trend = trend.pivot(index="week_start", columns="store_id", values="spoilage_pct_of_sales").tail(12)

    watch = pd.read_sql(
        """SELECT store_id, product_name, category, stockout_days_28,
                  avg_daily_sold, lost_sales_usd_28
           FROM v_stockout_hotspots
           WHERE stockout_days_28 >= 4
           ORDER BY lost_sales_usd_28 DESC""",
        con,
    )
    return {"week": week, "Summary": summary, "Spoilage trend (12 wk)": trend, "Stockout watchlist": watch}


def write(con: sqlite3.Connection, path: str | Path) -> Path:
    frames = build_frames(con)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for name in ["Summary", "Spoilage trend (12 wk)", "Stockout watchlist"]:
            df = frames[name]
            df.to_excel(xl, sheet_name=name, index=name.startswith("Spoilage"), startrow=2)
    wb = load_workbook(path)
    for ws in wb.worksheets:
        ws["A1"] = f"{ws.title}: week {frames['week']}"
        ws["A1"].font = Font(bold=True, size=14)
        for cell in ws[3]:
            cell.fill = HEADER_FILL
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for i, col in enumerate(ws.iter_cols(min_row=3), start=1):
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 34)
        ws.freeze_panes = "A4"
    ws = wb["Summary"]
    last = ws.max_row
    for col in ["C", "D", "H"]:
        for r in range(4, last + 1):
            ws[f"{col}{r}"].number_format = '"$"#,##0'
    ws.conditional_formatting.add(f"I4:I{last}", CellIsRule(operator="notEqual", formula=['""'], fill=FLAG_FILL))
    wb.save(path)
    return path
