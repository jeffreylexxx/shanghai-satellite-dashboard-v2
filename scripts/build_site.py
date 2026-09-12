import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
template = (ROOT / "src" / "index.template.html").read_text(encoding="utf-8")
data = json.loads((ROOT / "public" / "data" / "latest.json").read_text(encoding="utf-8"))
payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
if "__SKY_DATA__" not in template:
    raise RuntimeError("Template data token is missing")
(ROOT / "public" / "index.html").write_text(template.replace("__SKY_DATA__", payload), encoding="utf-8")
print("built public/index.html")
