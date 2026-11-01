"""Simulate 18 months of store-level grocery inventory with real FIFO spoilage.

Each store/product pair runs a daily loop:
  receive delivery -> sell oldest stock first -> throw out expired stock
  -> place an order-up-to replenishment for the next delivery day.

Two stores (S03 and S11) get perishable deliveries only twice a week, so they
must order bigger batches that expire before they sell. That is the problem
the spoilage analysis is meant to discover. All data is synthetic.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

START, END = "2025-01-01", "2026-06-30"
N_STORES = 14
TWICE_WEEKLY = {"S03", "S11"}

# category: (shelf life days, unit cost range, margin, base daily units range)
CATEGORIES = {
    "Produce": (5, (0.6, 2.5), 0.45, (20, 60)),
    "Bakery": (3, (1.5, 4.0), 0.55, (8, 25)),
    "Dairy": (12, (1.2, 4.5), 0.30, (15, 45)),
    "Meat & seafood": (4, (4.0, 12.0), 0.28, (6, 18)),
    "Deli": (6, (3.0, 8.0), 0.40, (5, 15)),
    "Pantry": (365, (1.0, 6.0), 0.35, (10, 30)),
}
PRODUCTS_PER_CATEGORY = {"Produce": 9, "Bakery": 5, "Dairy": 7, "Meat & seafood": 6, "Deli": 4, "Pantry": 9}
CITIES = ["Plano", "Frisco", "Richardson", "Garland", "McKinney", "Allen", "Irving",
          "Carrollton", "Mesquite", "Denton", "Lewisville", "Arlington", "Grapevine", "Rowlett"]


@dataclass
class Tables:
    stores: pd.DataFrame
    products: pd.DataFrame
    daily: pd.DataFrame  # store, product, date, demand, sold, received, spoiled_units, on_hand_end, stockout


def make_dims(rng) -> tuple[pd.DataFrame, pd.DataFrame]:
    stores = pd.DataFrame({
        "store_id": [f"S{i:02d}" for i in range(1, N_STORES + 1)],
        "city": CITIES[:N_STORES],
        "size_factor": rng.uniform(0.7, 1.4, N_STORES).round(2),
    })
    stores["perishable_deliveries_per_week"] = np.where(stores.store_id.isin(TWICE_WEEKLY), 2, 3)
    rows = []
    pid = 1
    for cat, (life, (clo, chi), margin, _) in CATEGORIES.items():
        for _ in range(PRODUCTS_PER_CATEGORY[cat]):
            cost = round(float(rng.uniform(clo, chi)), 2)
            rows.append((f"P{pid:03d}", cat, life, cost, round(cost * (1 + margin), 2)))
            pid += 1
    products = pd.DataFrame(rows, columns=["product_id", "category", "shelf_life_days", "unit_cost", "unit_price"])
    products["product_name"] = products.category + " item " + products.product_id.str[1:]
    return stores, products


def delivery_days(per_week: int, perishable: bool) -> set[int]:
    if not perishable:
        return {0}  # pantry: weekly on Monday
    return {0, 3} if per_week == 2 else {0, 2, 4}


def simulate_series(rng, dates, base, life, deliver_on, safety=1.15):
    """Run one store/product. Returns per-day arrays."""
    n = len(dates)
    dow = dates.dayofweek.values
    doy = dates.dayofyear.values
    season = 1 + 0.08 * np.sin(2 * np.pi * (doy - 30) / 365)
    weekday_mult = np.array([0.9, 0.85, 0.9, 0.95, 1.1, 1.3, 1.15])[dow]
    expected = base * season * weekday_mult
    demand = rng.poisson(expected)

    lots: deque[list] = deque()  # [units, expiry_index]
    pending = 0
    out = np.zeros((n, 5), dtype=np.int64)  # sold, received, spoiled, on_hand_end, stockout
    deliver_idx = [i for i in range(n) if dow[i] in deliver_on]
    next_delivery = {}
    for k, i in enumerate(deliver_idx):
        next_delivery[i] = deliver_idx[k + 1] if k + 1 < len(deliver_idx) else n
    # opening stock
    lots.append([int(expected[:7].sum()), min(life, n)])

    for i in range(n):
        received = 0
        if pending:
            lots.append([pending, i + life])
            received, pending = pending, 0
        need = int(demand[i])
        sold = 0
        while need and lots:
            take = min(need, lots[0][0])
            lots[0][0] -= take
            need -= take
            sold += take
            if lots[0][0] == 0:
                lots.popleft()
        spoiled = 0
        while lots and lots[0][1] <= i + 1:
            spoiled += lots.popleft()[0]
        on_hand = sum(l[0] for l in lots)
        if i in next_delivery:
            # order for delivery tomorrow covering until the following delivery
            horizon = slice(i + 1, min(next_delivery[i] + 1, n))
            target = expected[horizon].sum() * safety
            pending = max(0, int(round(target - on_hand)))
        out[i] = (sold, received, spoiled, on_hand, int(need > 0))
    return demand, out


def simulate(seed: int = 42, twice_weekly: set[str] | None = None) -> Tables:
    rng = np.random.default_rng(seed)
    stores, products = make_dims(rng)
    twice = TWICE_WEEKLY if twice_weekly is None else twice_weekly
    stores["perishable_deliveries_per_week"] = np.where(stores.store_id.isin(twice), 2, 3)
    dates = pd.date_range(START, END, freq="D")
    base_by_product = {
        r.product_id: rng.uniform(*CATEGORIES[r.category][3]) for r in products.itertuples()
    }
    frames = []
    for s in stores.itertuples():
        srng = np.random.default_rng([seed, int(s.store_id[1:])])
        for p in products.itertuples():
            perishable = p.shelf_life_days < 60
            days = delivery_days(s.perishable_deliveries_per_week, perishable)
            demand, out = simulate_series(srng, dates, base_by_product[p.product_id] * s.size_factor,
                                          p.shelf_life_days, days)
            frames.append(pd.DataFrame({
                "store_id": s.store_id, "product_id": p.product_id, "date": dates,
                "demand": demand, "sold": out[:, 0], "received": out[:, 1],
                "spoiled_units": out[:, 2], "on_hand_end": out[:, 3], "stockout": out[:, 4],
            }))
    daily = pd.concat(frames, ignore_index=True)
    return Tables(stores, products, daily)
