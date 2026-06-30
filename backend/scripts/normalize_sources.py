"""将数据库中历史 source 字段规范为六大来源标签。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.source_labels import canonical_source, normalize_source_for_storage
from database import SessionLocal, init_db
from models import Announcement


def normalize_all() -> int:
    init_db()
    db = SessionLocal()
    changed = 0
    try:
        for ann in db.query(Announcement).order_by(Announcement.id).all():
            canon = normalize_source_for_storage(ann.source)
            if ann.source != canon:
                print(f"id={ann.id}: {ann.source!r} -> {canon!r}")
                ann.source = canon
                changed += 1
        if changed:
            db.commit()
    finally:
        db.close()
    return changed


if __name__ == "__main__":
    n = normalize_all()
    print(f"已规范化 {n} 条来源标签")
