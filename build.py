"""Copy data/picks.json into site/data.json with an updated timestamp."""
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "data", "picks.json"), encoding="utf-8") as f:
    rows = json.load(f)

with open(os.path.join(HERE, "site", "data.json"), "w", encoding="utf-8") as f:
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "rows": rows}, f, separators=(",", ":"))

graded = [r for r in rows if r.get("r")]
print(f"{len(rows)} rows, {len(graded)} graded -> site/data.json")
