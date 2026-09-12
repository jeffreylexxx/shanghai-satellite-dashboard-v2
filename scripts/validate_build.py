import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "public" / "data" / "latest.json").read_text(encoding="utf-8"))
assert data["schema_version"] == 2
assert len(data["frames"]) == 288
assert len(data["catalog"]) >= 10_000
assert data["daily"]["unique_satellites"] == len(data["catalog"])
assert data["daily"]["pass_count"] == len(data["passes"])
assert data["daily"]["starlink_unique"] == sum(1 for v in data["catalog"].values() if v[1] == "Starlink")
html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
assert "__SKY_DATA__" not in html and "window.SKY_DATA" in html
assert (ROOT / "public" / "assets" / "satellite-category-sprite.png").stat().st_size > 100_000
print(json.dumps({"status":"ok","frames":len(data["frames"]),"catalog":len(data["catalog"]),"passes":len(data["passes"])}))
