from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "camp.db"
engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(*, sync_coverage: bool = False):
    from models import Announcement, CollegePending, CrawlCoverage, CrawlLog  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    _cleanup_stale_crawl_logs()
    if sync_coverage:
        _sync_coverage_cache()


def _sync_coverage_cache():
    """启动时同步覆盖缓存，确保待定/已收录学院下次检索可跳过。"""
    from crawler.coverage import sync_coverage_from_announcements, sync_coverage_from_pending, sync_result_coverage_from_notice

    db = SessionLocal()
    try:
        sync_coverage_from_announcements(db)
        sync_coverage_from_pending(db)
        sync_result_coverage_from_notice(db)
        _refresh_announcement_status(db)
    finally:
        db.close()


def _refresh_announcement_status(db) -> None:
    from crawler.service import refresh_ended_status

    refresh_ended_status(db)


def _cleanup_stale_crawl_logs():
    """启动时清理异常中断的 running 日志。"""
    import sqlalchemy as sa
    from models import CrawlLog

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE crawl_logs SET status='error', message='上次检索异常中断' "
                "WHERE status='running'"
            )
        )


def _migrate_sqlite():
    """为已有 SQLite 库补全新增列（create_all 不会 ALTER 旧表）。"""
    import sqlalchemy as sa

    migrations = {
        "crawl_logs": {
            "board": "VARCHAR(50)",
            "skipped_count": "INTEGER DEFAULT 0",
        },
        "announcements": {
            "event_time": "VARCHAR(200)",
            "event_format": "VARCHAR(20)",
        },
        "college_pending": {
            "pending_kind": "VARCHAR(30) DEFAULT 'not_published'",
            "domain_status": "VARCHAR(20)",
            "next_check_at": "DATETIME",
        },
    }
    with engine.begin() as conn:
        for table, columns in migrations.items():
            existing = {
                row[1]
                for row in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
            } if conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).fetchone() else set()
            if not existing:
                continue
            for col, col_type in columns.items():
                if col not in existing:
                    conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
