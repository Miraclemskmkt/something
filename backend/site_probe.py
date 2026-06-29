"""加载学院子站探测结果。"""

import json
from pathlib import Path

PROBE_FILE = Path(__file__).resolve().parent / "data" / "official_sites_probe.json"


def load_probe_map() -> dict[tuple[str, str, str], dict]:
    if not PROBE_FILE.exists():
        return {}
    data = json.loads(PROBE_FILE.read_text(encoding="utf-8"))
    return {
        (d["university"], d["college"], d["college_type"]): d
        for d in data.get("details", [])
    }


def load_probe_summary() -> dict:
    if not PROBE_FILE.exists():
        return {}
    return json.loads(PROBE_FILE.read_text(encoding="utf-8"))
