"""One command: simulate, build SQLite, weekly Excel report, spoilage analysis, dashboard."""
import json
from pathlib import Path

from src.simulate import simulate
from src.database import build
from src import weekly_report, spoilage_analysis, dashboard

SEED = 42
out = Path("outputs"); out.mkdir(exist_ok=True)

tables = simulate(SEED)
con = build(tables, out / "grocery.db")
weekly_report.write(con, out / "weekly_store_report.xlsx")
result = spoilage_analysis.run(con, SEED, out)
dashboard.write(con, out / "dashboard.html")
(out / "spoilage_findings.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
print("\nOpen outputs/dashboard.html in a browser. Weekly report: outputs/weekly_store_report.xlsx")
