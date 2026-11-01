-- Views the dashboard and weekly report read from.
-- Base tables: stores, products, daily_inventory (one row per store/product/day).

DROP VIEW IF EXISTS v_daily;
CREATE VIEW v_daily AS
SELECT
    d.store_id,
    d.product_id,
    p.category,
    d.date,
    strftime('%Y-%W', d.date)                 AS year_week,
    d.demand,
    d.sold,
    d.received,
    d.spoiled_units,
    d.on_hand_end,
    d.stockout,
    d.sold * p.unit_price                     AS sales_usd,
    d.spoiled_units * p.unit_cost             AS spoilage_usd,
    (d.demand - d.sold) * p.unit_price        AS lost_sales_usd,
    p.shelf_life_days < 60                    AS is_perishable
FROM daily_inventory d
JOIN products p USING (product_id);

-- Weekly KPIs per store (what the manual Excel report used to contain)
DROP VIEW IF EXISTS v_store_week;
CREATE VIEW v_store_week AS
SELECT
    store_id,
    year_week,
    MIN(date)                                              AS week_start,
    ROUND(SUM(sales_usd), 2)                               AS sales_usd,
    ROUND(SUM(spoilage_usd), 2)                            AS spoilage_usd,
    ROUND(100.0 * SUM(spoilage_usd) / NULLIF(SUM(sales_usd), 0), 2) AS spoilage_pct_of_sales,
    ROUND(SUM(lost_sales_usd), 2)                          AS lost_sales_usd,
    ROUND(100.0 * AVG(stockout), 2)                        AS stockout_rate_pct
FROM v_daily
GROUP BY store_id, year_week;

-- Stockout hotspots: items that ran out most often in the last 28 days,
-- with the sales that walked out the door as a result
DROP VIEW IF EXISTS v_stockout_hotspots;
CREATE VIEW v_stockout_hotspots AS
WITH latest AS (SELECT MAX(date) AS d FROM daily_inventory)
SELECT
    v.store_id,
    p.product_name,
    v.category,
    SUM(v.stockout)                    AS stockout_days_28,
    ROUND(AVG(v.sold), 1)              AS avg_daily_sold,
    ROUND(SUM(v.lost_sales_usd), 0)    AS lost_sales_usd_28
FROM v_daily v
JOIN products p USING (product_id)
JOIN latest ON v.date > date(latest.d, '-28 day')
GROUP BY v.store_id, v.product_id
HAVING SUM(v.stockout) > 0;
