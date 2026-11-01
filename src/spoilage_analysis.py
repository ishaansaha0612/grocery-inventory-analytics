"""Find where spoilage money is lost, why, and what fixing it is worth.

Steps:
1. Annualize perishable spoilage $ per store from SQL.
2. Flag outlier stores (robust z-score on spoilage % of sales).
3. Test the delivery-frequency hypothesis: compare 2x vs 3x weekly stores.
4. Estimate savings with a counterfactual: re-run the identical demand with
   every store on 3 deliveries a week, and measure spoilage AND stockouts
   so the fix isn't just trading waste for empty shelves.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .simulate import simulate

EXTRA_DELIVERY_COST = 175  # assumed $ per added delivery
INK, MUTED, ACCENT, WARN = "#1f2a37", "#8a94a3", "#2a6fdb", "#d9622b"

STORE_SQL = """
SELECT v.store_id, s.city, s.perishable_deliveries_per_week AS deliveries_per_week,
       SUM(v.sales_usd)    AS sales_usd,
       SUM(v.spoilage_usd) AS spoilage_usd,
       AVG(v.stockout)     AS stockout_rate,
       COUNT(DISTINCT v.date) AS days
FROM v_daily v JOIN stores s USING (store_id)
WHERE v.is_perishable
GROUP BY v.store_id
"""


def store_table(con) -> pd.DataFrame:
    df = pd.read_sql(STORE_SQL, con)
    years = df.days / 365.0
    df["spoilage_usd_per_year"] = df.spoilage_usd / years
    df["spoilage_pct_of_sales"] = 100 * df.spoilage_usd / df.sales_usd
    med = df.spoilage_pct_of_sales.median()
    mad = (df.spoilage_pct_of_sales - med).abs().median()
    df["robust_z"] = 0.6745 * (df.spoilage_pct_of_sales - med) / mad
    df["outlier"] = df.robust_z > 3.5
    return df.sort_values("spoilage_pct_of_sales", ascending=False)


def category_table(con, stores: list[str]) -> pd.DataFrame:
    q = f"""
    SELECT category,
           SUM(CASE WHEN store_id IN ({','.join('?' * len(stores))}) THEN spoilage_usd END) /
           SUM(CASE WHEN store_id IN ({','.join('?' * len(stores))}) THEN sales_usd END) * 100 AS outlier_pct,
           SUM(CASE WHEN store_id NOT IN ({','.join('?' * len(stores))}) THEN spoilage_usd END) /
           SUM(CASE WHEN store_id NOT IN ({','.join('?' * len(stores))}) THEN sales_usd END) * 100 AS others_pct
    FROM v_daily WHERE is_perishable GROUP BY category ORDER BY outlier_pct DESC
    """
    return pd.read_sql(q, con, params=stores * 4)


def counterfactual(seed: int, stores: list[str]) -> pd.DataFrame:
    """Same demand, every store on 3 deliveries/week."""
    fixed = simulate(seed, twice_weekly=set())
    d = fixed.daily.merge(fixed.products, on="product_id")
    d = d[(d.shelf_life_days < 60) & d.store_id.isin(stores)]
    d["spoilage_usd"] = d.spoiled_units * d.unit_cost
    years = d.date.nunique() / 365.0
    return d.groupby("store_id").agg(
        spoilage_usd_per_year=("spoilage_usd", lambda x: x.sum() / years),
        stockout_rate=("stockout", "mean"),
    )


def plot(stores: pd.DataFrame, path: Path):
    fig, ax = plt.subplots(figsize=(9, 4.6))
    s = stores.sort_values("spoilage_pct_of_sales")
    colors = [WARN if o else ACCENT for o in s.outlier]
    ax.barh(s.store_id + " · " + s.city, s.spoilage_pct_of_sales, color=colors)
    med = stores.spoilage_pct_of_sales.median()
    ax.axvline(med, color=INK, ls="--", lw=1)
    ax.text(med, len(s) - 0.4, " chain median", fontsize=8, color=INK, va="bottom")
    for i, (v, d) in enumerate(zip(s.spoilage_pct_of_sales, s.deliveries_per_week)):
        ax.text(v + 0.05, i, f"{v:.1f}%  ({d}x/wk)", va="center", fontsize=8, color=INK)
    ax.set_xlabel("Perishable spoilage, % of perishable sales", color=INK)
    ax.set_title("Two stores lose 4-5x more to spoilage, and both get only 2 deliveries a week",
                 loc="left", color=INK, fontsize=11)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.set_xlim(0, s.spoilage_pct_of_sales.max() * 1.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(con: sqlite3.Connection, seed: int, out: Path) -> dict:
    stores = store_table(con)
    outliers = stores.loc[stores.outlier, "store_id"].tolist()
    cats = category_table(con, outliers)
    by_freq = stores.groupby(stores.deliveries_per_week.astype(str) + "x/week").spoilage_pct_of_sales.mean()

    cf = counterfactual(seed, outliers)
    now = stores.set_index("store_id").loc[outliers, ["spoilage_usd_per_year", "stockout_rate"]]
    savings = pd.DataFrame({
        "spoilage_now": now.spoilage_usd_per_year,
        "spoilage_with_3x": cf.spoilage_usd_per_year,
        "stockout_now_pct": 100 * now.stockout_rate,
        "stockout_with_3x_pct": 100 * cf.stockout_rate,
    })
    savings["gross_savings"] = savings.spoilage_now - savings.spoilage_with_3x
    # A third weekly truck isn't free: subtract the extra delivery cost
    savings["extra_delivery_cost"] = EXTRA_DELIVERY_COST * 52
    savings["annual_savings"] = savings.gross_savings - savings.extra_delivery_cost
    plot(stores, out / "spoilage_by_store.png")
    stores.round(3).to_csv(out / "spoilage_by_store.csv", index=False)
    savings.round(2).to_csv(out / "savings_estimate.csv")

    return {
        "outlier_stores": outliers,
        "outlier_cities": stores.set_index("store_id").loc[outliers, "city"].tolist(),
        "chain_median_spoilage_pct": round(float(stores.spoilage_pct_of_sales.median()), 2),
        "outlier_spoilage_pct": stores.set_index("store_id").loc[outliers, "spoilage_pct_of_sales"].round(2).to_dict(),
        "spoilage_pct_by_delivery_frequency": by_freq.round(2).to_dict(),
        "worst_categories_in_outliers": cats.head(3).round(2).to_dict("records"),
        "annual_savings_usd": round(float(savings.annual_savings.sum()), 0),
        "assumed_cost_per_extra_delivery": EXTRA_DELIVERY_COST,
        "savings_by_store": savings.round(1).to_dict("index"),
        "months_analyzed": round(float(stores.days.iloc[0] / 30.44), 1),
    }
