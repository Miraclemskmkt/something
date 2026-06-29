from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class CollegeType(str, Enum):
    LAW = "law"
    FOREIGN_LANG = "foreign_lang"


class AnnouncementStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"
    EXCELLENT_LIST = "excellent_list"


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    university: Mapped[str] = mapped_column(String(200), nullable=False)
    college: Mapped[str] = mapped_column(String(200), nullable=False)
    college_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=AnnouncementStatus.ACTIVE.value)
    event_type: Mapped[str] = mapped_column(String(50), default="夏令营")
    publish_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_time: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="学院官网")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_college_type_status", "college_type", "status"),
        Index("ix_university", "university"),
    )


class CrawlCoverage(Base):
    """记录某校某学院某类通知已收录，后续检索跳过。"""

    __tablename__ = "crawl_coverage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    university: Mapped[str] = mapped_column(String(200), nullable=False)
    college: Mapped[str] = mapped_column(String(200), nullable=False)
    college_type: Mapped[str] = mapped_column(String(50), nullable=False)
    slot: Mapped[str] = mapped_column(String(50), nullable=False)
    announcement_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_coverage_unique", "university", "college", "college_type", "slot", unique=True),
    )


class CollegePending(Base):
    """已检索但未发现夏令营/预推免通知的学院（待定榜）。"""

    __tablename__ = "college_pending"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    university: Mapped[str] = mapped_column(String(200), nullable=False)
    college: Mapped[str] = mapped_column(String(200), nullable=False)
    college_type: Mapped[str] = mapped_column(String(50), nullable=False)
    slot: Mapped[str] = mapped_column(String(50), nullable=False)
    search_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "ix_pending_unique",
            "university", "college", "college_type", "slot",
            unique=True,
        ),
    )


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    board: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="running")
    found_count: Mapped[int] = mapped_column(default=0)
    new_count: Mapped[int] = mapped_column(default=0)
    updated_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
