from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AnnouncementOut(BaseModel):
    id: int
    title: str
    url: str
    university: str
    college: str
    college_type: str
    status: str
    event_type: str
    publish_date: Optional[str] = None
    deadline: Optional[str] = None
    event_time: Optional[str] = None
    event_format: Optional[str] = None
    source: str
    source_category: str = ""
    summary: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    total: int
    active: int
    ended: int
    excellent_list: int
    pending: int = 0
    law: int
    foreign_lang: int
    last_crawl: Optional[datetime] = None
    universities_count: int
    official_count: int = 0
    wechat_count: int = 0
    source_counts: dict[str, int] = {}


class PendingOut(BaseModel):
    id: int
    university: str
    college: str
    college_type: str
    status: str = "pending"
    pending_kind: str = "not_published"
    pending_kind_label: str = "暂未发布"
    domain_status: Optional[str] = None
    next_check_at: Optional[datetime] = None
    title: str
    event_type: str
    source: str = "检索记录"
    search_count: int = 1
    updated_at: datetime

    class Config:
        from_attributes = True


class BoardOut(BaseModel):
    stats: StatsOut
    items: list[AnnouncementOut | PendingOut]


class CrawlResult(BaseModel):
    board: str = ""
    tier: str = ""
    found: int
    new: int
    updated: int
    skipped: int = 0
    message: str


class SubmitCollegeOption(BaseModel):
    university: str
    college: str
    college_type: str
    label: str


class SubmitNoticeIn(BaseModel):
    url: str
    university: str
    college: str
    board: str = "summer_camp"


class SubmitNoticeOut(BaseModel):
    ok: bool
    message: str
    is_new: bool = False
    announcement: AnnouncementOut | None = None


class IncompleteAnnouncementOut(BaseModel):
    id: int
    university: str
    college: str
    college_type: str
    title: str
    url: str
    publish_date: Optional[str] = None
    deadline: Optional[str] = None
    event_time: Optional[str] = None
    event_format: Optional[str] = None
    missing: list[str]
    fields_complete: bool = False
    source: str = ""
    needs_manual: bool = False
    llm_fail_count: int = 0
    last_llm_failure: Optional[str] = None


class FieldsPatchIn(BaseModel):
    publish_date: Optional[str] = None
    deadline: Optional[str] = None
    event_time: Optional[str] = None
    event_format: Optional[str] = None
    summary: Optional[str] = None
    url: Optional[str] = None


class LlmEnrichResult(BaseModel):
    ok: bool
    message: str
    fields_complete: bool = False
    announcement: AnnouncementOut | None = None


class InstitutionItem(BaseModel):
    university: str
    college: str
    college_type: str
    province: str
    region: str
    tags: list[str]
    monitored: bool
    homepage: str = ""
    homepage_ok: bool | None = None
    notice_ok: bool | None = None
    note: str = ""


class ProvinceBlock(BaseModel):
    province: str
    count: int
    institutions: list[InstitutionItem]


class RegionBlock(BaseModel):
    region: str
    count: int
    provinces: list[ProvinceBlock]


class InstitutionsOut(BaseModel):
    summary: dict
    regions: list[RegionBlock]
    region_list: list[str]
