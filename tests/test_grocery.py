import numpy as np
import pandas as pd
import pytest

from src.simulate import simulate_series, simulate
from src.database import build
from src import spoilage_analysis, weekly_report


def test_inventory_balance_holds_every_day():
    """Stock conservation: opening + received - sold - spoiled = closing."""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2025-01-01", periods=120)
    demand, out = simulate_series(rng, dates, base=20, life=4, deliver_on={0, 3})
    sold, received, spoiled, on_hand = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
    opening = np.concatenate([[on_hand[0] - received[0] + sold[0] + spoiled[0]], on_hand[:-1]])
    assert np.array_equal(opening + received - sold - spoiled, on_hand)
    assert (sold <= demand).all()


def test_fewer_deliveries_means_more_spoilage():
    dates = pd.date_range("2025-01-01", periods=180)
    _, two = simulate_series(np.random.default_rng(1), dates, 20, 4, {0, 3})
    _, three = simulate_series(np.random.default_rng(1), dates, 20, 4, {0, 2, 4})
    assert two[:, 2].sum() > 2 * three[:, 2].sum()


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    return build(simulate(42), tmp_path_factory.mktemp("db") / "g.db")


def test_analysis_finds_planted_stores(con, tmp_path):
    r = spoilage_analysis.run(con, 42, tmp_path)
    assert set(r["outlier_stores"]) == {"S03", "S11"}
    assert r["annual_savings_usd"] > 0


def test_weekly_report_builds(con, tmp_path):
    path = weekly_report.write(con, tmp_path / "r.xlsx")
    sheets = pd.read_excel(path, sheet_name=None, header=2)
    assert len(sheets["Summary"]) == 14
