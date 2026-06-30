import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from crawler.boards import PRE_ADMISSION, SUMMER_CAMP, matches_board
from crawler.pending import list_pending, pending_title
from crawler.crawl_state import (
    get_lock,
    is_running,
    mark_failed,
    mark_finished,
    mark_started,
    status_payload,
)
from crawler.source_labels import (
    SOURCE_CATEGORIES,
    canonical_source,
    count_by_source,
    is_official_source,
    is_wechat_source,
    source_display,
    source_matches_filter,
)
from crawler.parser import effective_status, is_year_eligible
from crawler.service import run_crawl
from crawler.submit_notice import SubmitNoticeError, list_submit_targets, submit_notice_link
from crawler.university_config import UNIVERSITY_TARGETS
from crawler.coverage import filter_targets_for_phase, get_covered_slots
from database import get_db, init_db, _sync_coverage_cache
from models import Announcement, CrawlLog
from scheduler import start_scheduler, stop_scheduler
from double_first_class_api import get_double_first_class
from institutions import get_institutions
from schemas import (
    AnnouncementOut,
    BoardOut,
    CrawlResult,
    InstitutionsOut,
    PendingOut,
    StatsOut,
    SubmitCollegeOption,
    SubmitNoticeIn,
    SubmitNoticeOut,
    IncompleteAnnouncementOut,
    FieldsPatchIn,
    LlmEnrichResult,
)
from tier_filter import VALID_TIERS, filter_items_by_tier, filter_targets_by_tier, universities_in_tier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"
_bg_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-sync")


def _normalize_tier(tier: str | None) -> str | None:
    if not tier or tier not in VALID_TIERS:
        return None
    return tier


def _filter_items(items: list, board: str | None, tier: str | None = None) -> list:
    eligible = [a for a in items if is_year_eligible(a.title, a.publish_date, a.deadline)]
    if board:
        eligible = [a for a in eligible if matches_board(a, board)]
    if tier:
        eligible = filter_items_by_tier(eligible, tier)
    return eligible


def _items_with_status(items: list) -> list[tuple]:
    """附加按截止时间计算后的有效状态。"""
    return [(a, effective_status(a.status, a.deadline)) for a in items]


def _filter_by_effective_status(items: list, status: str | None) -> list:
    if not status:
        return items
    return [a for a, st in _items_with_status(items) if st == status]


def _sort_active_by_deadline(items: list, board: str | None, status: str | None) -> list:
    """夏令营「进行中」按截止填报时间从早到晚。"""
    if board != SUMMER_CAMP or status != "active":
        return items
    from datetime import datetime

    from crawler.parser import parse_datetime_value

    def _key(a):
        if not a.deadline:
            return (1, datetime.max)
        dt = parse_datetime_value(a.deadline)
        return (0, dt if dt else datetime.max)

    return sorted(items, key=_key)


def _filter_by_source(items: list, source: str | None) -> list:
    if not source:
        return items
    return [a for a in items if source_matches_filter(a.source, source)]


def _to_announcement_out(ann: Announcement) -> AnnouncementOut:
    cat = canonical_source(ann.source)
    return AnnouncementOut(
        id=ann.id,
        title=ann.title,
        url=ann.url,
        university=ann.university,
        college=ann.college,
        college_type=ann.college_type,
        status=effective_status(ann.status, ann.deadline),
        event_type=ann.event_type,
        publish_date=ann.publish_date,
        deadline=ann.deadline,
        event_time=ann.event_time,
        event_format=ann.event_format,
        source=source_display(ann.source),
        source_category=cat,
        summary=ann.summary,
        updated_at=ann.updated_at,
    )


def _build_stats(db: Session, board: str | None, tier: str | None) -> StatsOut:
    all_items = _filter_items(db.query(Announcement).all(), board, tier)
    log_q = db.query(CrawlLog).filter(CrawlLog.status == "success")
    if board:
        log_q = log_q.filter(CrawlLog.board == board)
    last_log = log_q.order_by(CrawlLog.finished_at.desc()).first()

    universities = set(a.university for a in all_items if a.university != "未知")
    pending_rows = list_pending(db, board, tier=tier) if board else []
    status_pairs = _items_with_status(all_items)
    src_counts = count_by_source([a.source for a in all_items])

    return StatsOut(
        total=len(all_items),
        active=sum(1 for _, st in status_pairs if st == "active"),
        ended=sum(1 for _, st in status_pairs if st == "ended"),
        excellent_list=sum(1 for _, st in status_pairs if st == "excellent_list"),
        pending=len(pending_rows),
        law=sum(1 for a in all_items if a.college_type == "law"),
        foreign_lang=sum(1 for a in all_items if a.college_type == "foreign_lang"),
        last_crawl=last_log.finished_at if last_log else None,
        universities_count=len(universities),
        official_count=src_counts.get("学院官网", 0),
        wechat_count=src_counts.get("微信公众号", 0),
        source_counts=src_counts,
    )


def _static_file(path: Path, media_type: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


async def _background_crawl(board: str, tier: str | None = None, refresh: bool = False) -> None:
    lock = get_lock(board, tier)
    if lock.locked():
        return
    async with lock:
        mark_started(board, tier)
        try:
            result = await run_crawl(board, tier=tier, refresh=refresh)
            mark_finished(board, result, tier)
        except Exception as e:
            logger.error("Background crawl failed (%s, %s): %s", board, tier, e)
            mark_failed(board, str(e), tier)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from scripts.normalize_sources import normalize_all
    try:
        n = normalize_all()
        if n:
            logger.info("来源标签规范化: 更新 %d 条", n)
    except Exception as e:
        logger.debug("来源标签规范化跳过: %s", e)
    _bg_executor.submit(_sync_coverage_cache)
    start_scheduler()
    yield
    stop_scheduler()
    _bg_executor.shutdown(wait=False)


app = FastAPI(
    title="保研夏令营检索平台",
    description="法学院 & 外国语学院 夏令营/预推免信息聚合",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/announcements", response_model=list[AnnouncementOut])
def list_announcements(
    board: str | None = Query(None, description="summer_camp | pre_admission"),
    status: str | None = Query(None, description="active | ended | excellent_list"),
    college_type: str | None = Query(None, description="law | foreign_lang"),
    source: str | None = Query(None, description="学院官网 | 微信公众号 | 保研论坛 | 全网检索 | 用户提交 | 用户补全"),
    tier: str | None = Query(None, description="985 | 211 | 双一流"),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    tier = _normalize_tier(tier)
    q = db.query(Announcement)
    if college_type:
        q = q.filter(Announcement.college_type == college_type)
    if tier:
        q = q.filter(Announcement.university.in_(universities_in_tier(tier)))
    if search:
        q = q.filter(
            Announcement.title.contains(search)
            | Announcement.university.contains(search)
            | Announcement.college.contains(search)
        )
    items = q.order_by(Announcement.updated_at.desc()).all()
    items = _filter_items(items, board, tier)
    items = _filter_by_source(items, source)
    items = _filter_by_effective_status(items, status)
    items = _sort_active_by_deadline(items, board, status)
    return [_to_announcement_out(a) for a in items]


@app.get("/api/pending", response_model=list[PendingOut])
def list_pending_colleges(
    board: str = Query(..., description="summer_camp | pre_admission"),
    college_type: str | None = Query(None, description="law | foreign_lang"),
    tier: str | None = Query(None, description="985 | 211 | 双一流"),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if board not in (SUMMER_CAMP, PRE_ADMISSION):
        board = SUMMER_CAMP
    tier = _normalize_tier(tier)
    rows = list_pending(db, board, college_type, search, tier=tier)
    from crawler.pending_kinds import KIND_LABELS

    event = "预推免" if board == PRE_ADMISSION else "夏令营"
    return [
        PendingOut(
            id=r.id,
            university=r.university,
            college=r.college,
            college_type=r.college_type,
            status="pending",
            pending_kind=getattr(r, "pending_kind", None) or "not_published",
            pending_kind_label=KIND_LABELS.get(getattr(r, "pending_kind", None) or "not_published", "暂未发布"),
            domain_status=getattr(r, "domain_status", None),
            next_check_at=getattr(r, "next_check_at", None),
            title=pending_title(r.university, r.college, board),
            event_type=event,
            search_count=r.search_count,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@app.get("/api/board", response_model=BoardOut)
def get_board(
    board: str = Query(..., description="summer_camp | pre_admission"),
    tier: str | None = Query(None, description="985 | 211 | 双一流"),
    status: str | None = Query(None, description="active | ended | excellent_list | pending"),
    college_type: str | None = Query(None, description="law | foreign_lang"),
    source: str | None = Query(None, description="学院官网 | 微信公众号 | 保研论坛 | 全网检索 | 用户提交 | 用户补全"),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """一次返回统计与列表，减少首屏往返。"""
    if board not in (SUMMER_CAMP, PRE_ADMISSION):
        board = SUMMER_CAMP
    tier = _normalize_tier(tier)
    stats = _build_stats(db, board, tier)

    if status == "pending":
        from crawler.pending_kinds import KIND_LABELS

        rows = list_pending(db, board, college_type, search, tier=tier)
        event = "预推免" if board == PRE_ADMISSION else "夏令营"
        items = [
            PendingOut(
                id=r.id,
                university=r.university,
                college=r.college,
                college_type=r.college_type,
                status="pending",
                pending_kind=getattr(r, "pending_kind", None) or "not_published",
                pending_kind_label=KIND_LABELS.get(getattr(r, "pending_kind", None) or "not_published", "暂未发布"),
                domain_status=getattr(r, "domain_status", None),
                next_check_at=getattr(r, "next_check_at", None),
                title=pending_title(r.university, r.college, board),
                event_type=event,
                search_count=r.search_count,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
        return BoardOut(stats=stats, items=items)

    q = db.query(Announcement)
    if college_type:
        q = q.filter(Announcement.college_type == college_type)
    if tier:
        q = q.filter(Announcement.university.in_(universities_in_tier(tier)))
    if search:
        q = q.filter(
            Announcement.title.contains(search)
            | Announcement.university.contains(search)
            | Announcement.college.contains(search)
        )
    items = q.order_by(Announcement.updated_at.desc()).all()
    items = _filter_items(items, board, tier)
    items = _filter_by_source(items, source)
    items = _filter_by_effective_status(items, status)
    items = _sort_active_by_deadline(items, board, status)
    return BoardOut(stats=stats, items=[_to_announcement_out(a) for a in items])


@app.get("/api/stats", response_model=StatsOut)
def get_stats(
    board: str | None = Query(None, description="summer_camp | pre_admission"),
    tier: str | None = Query(None, description="985 | 211 | 双一流"),
    db: Session = Depends(get_db),
):
    tier = _normalize_tier(tier)
    return _build_stats(db, board, tier)


@app.get("/api/institutions", response_model=InstitutionsOut)
def list_institutions(
    college_type: str | None = Query(None, description="law | foreign_lang"),
    region: str | None = Query(None, description="华北|东北|华东|华中|华南|西南|西北"),
    tag: str | None = Query(None, description="985 | 211 | 双一流"),
    search: str | None = Query(None),
):
    return get_institutions(college_type, region, search, tag)


@app.get("/api/double-first-class")
def list_double_first_class(
    college_type: str | None = Query(None),
    region: str | None = Query(None),
    tag: str | None = Query(None, description="985 | 211 | 双一流"),
    search: str | None = Query(None),
):
    return get_double_first_class(college_type, region, search, tag)


def _uncovered_target_count(db: Session, board: str, tier: str) -> int:
    pool = filter_targets_by_tier(UNIVERSITY_TARGETS, tier)
    covered = get_covered_slots(db, board)
    total = 0
    for phase in ("notice", "result"):
        total += len(filter_targets_for_phase(pool, covered, board, phase))
    return total


@app.post("/api/crawl", response_model=CrawlResult)
async def trigger_crawl(
    board: str = Query(..., description="summer_camp | pre_admission"),
    tier: str = Query(..., description="985 | 211 | 双一流"),
    refresh: bool = Query(
        False,
        description="true=清除该分层通知槽缓存并重新全网检索+补全字段",
    ),
    db: Session = Depends(get_db),
):
    if board not in (SUMMER_CAMP, PRE_ADMISSION):
        board = SUMMER_CAMP
    tier = _normalize_tier(tier)
    if not tier:
        return CrawlResult(
            board=board,
            tier="",
            found=0,
            new=0,
            updated=0,
            skipped=0,
            message="请指定分层：985、211 或 双一流",
        )
    if is_running(board, tier):
        return CrawlResult(
            board=board,
            tier=tier,
            found=0,
            new=0,
            updated=0,
            skipped=0,
            message=f"{tier} 院校正在检索中，请稍候",
        )
    pending = _uncovered_target_count(db, board, tier)
    if not refresh and pending == 0:
        label = "夏令营" if board == SUMMER_CAMP else "预推免"
        return CrawlResult(
            board=board,
            tier=tier,
            found=0,
            new=0,
            updated=0,
            skipped=0,
            message=f"{label} · {tier} 已全部检索完毕，正在显示数据库缓存",
        )
    asyncio.create_task(_background_crawl(board, tier, refresh=refresh))
    label = "夏令营" if board == SUMMER_CAMP else "预推免"
    if refresh:
        pool = filter_targets_by_tier(UNIVERSITY_TARGETS, tier)
        msg = (
            f"已开始刷新{label} · {tier}（重检 {len(pool)} 个学院，"
            "含详情补全）"
        )
    else:
        msg = f"已开始检索{label} · {tier}（待检索 {pending} 个学院）"
    return CrawlResult(
        board=board,
        tier=tier,
        found=0,
        new=0,
        updated=0,
        skipped=0,
        message=msg,
    )


@app.get("/api/submit/colleges", response_model=list[SubmitCollegeOption])
def submit_college_options():
    """用户提交链接时可选择的学院列表。"""
    return list_submit_targets()


@app.post("/api/submit-notice", response_model=SubmitNoticeOut)
async def submit_notice(body: SubmitNoticeIn, db: Session = Depends(get_db)):
    """提交学院官方通知链接：先校验来源，再抓取解析入库。"""
    try:
        ann, is_new = await submit_notice_link(
            url=body.url,
            university=body.university,
            college=body.college,
            board=body.board,
            db=db,
        )
    except SubmitNoticeError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": str(e)}) from e
    except Exception as e:
        logger.exception("Submit notice failed: %s", e)
        raise HTTPException(status_code=500, detail={"code": "error", "message": "处理失败，请稍后重试"}) from e

    verb = "已新增" if is_new else "已更新"
    return SubmitNoticeOut(
        ok=True,
        message=f"官方链接校验通过，通知{verb}：{ann.university} - {ann.college}",
        is_new=is_new,
        announcement=_to_announcement_out(ann),
    )


@app.get("/api/incomplete", response_model=list[IncompleteAnnouncementOut])
def list_incomplete_announcements(db: Session = Depends(get_db)):
    from crawler.field_enricher import all_fields_complete
    from crawler.llm_enrich_state import get_record

    rows = db.query(Announcement).order_by(Announcement.id).all()
    out: list[IncompleteAnnouncementOut] = []
    for a in rows:
        missing = []
        if not a.publish_date:
            missing.append("开放提交")
        if not a.deadline:
            missing.append("截止提交")
        if not a.event_time:
            missing.append("举办时间")
        if not a.event_format:
            missing.append("举办形式")
        if all_fields_complete(a):
            continue
        st = get_record(a.id)
        out.append(IncompleteAnnouncementOut(
            id=a.id,
            university=a.university,
            college=a.college,
            college_type=a.college_type,
            title=a.title,
            url=a.url,
            publish_date=a.publish_date,
            deadline=a.deadline,
            event_time=a.event_time,
            event_format=a.event_format,
            missing=missing,
            fields_complete=False,
            source=source_display(a.source),
            needs_manual=st["needs_manual"],
            llm_fail_count=st["fail_count"],
            last_llm_failure=st["last_failure"],
        ))
    out.sort(key=lambda x: (not x.needs_manual, x.id))
    return out


@app.patch("/api/announcements/{ann_id}/fields", response_model=AnnouncementOut)
def patch_announcement_fields(
    ann_id: int,
    body: FieldsPatchIn,
    db: Session = Depends(get_db),
):
    from crawler.field_enricher import mark_manual_fields

    ann = mark_manual_fields(
        db, ann_id,
        publish_date=body.publish_date,
        deadline=body.deadline,
        event_time=body.event_time,
        event_format=body.event_format,
        summary=body.summary,
        url=body.url,
    )
    if not ann:
        raise HTTPException(status_code=404, detail="通知不存在")
    return _to_announcement_out(ann)


@app.post("/api/announcements/{ann_id}/llm-enrich", response_model=LlmEnrichResult)
async def llm_enrich_one(ann_id: int, db: Session = Depends(get_db)):
    from config import settings
    from crawler.field_enricher import all_fields_complete, enrich_db_announcement
    from crawler.fetcher import create_http_client

    if not settings.llm_enabled:
        raise HTTPException(
            status_code=400,
            detail="LLM 未启用，请在 .env 配置 CAMP_LLM_ENABLED=true 及 API Key",
        )
    ann = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="通知不存在")
    from crawler.llm_enrich_state import needs_manual as llm_needs_manual
    if llm_needs_manual(ann_id):
        raise HTTPException(status_code=400, detail="该通知 LLM 已失败 2 次，请人工补全")
    async with create_http_client() as client:
        changed, _ = await enrich_db_announcement(
            ann, client=client, force_llm=True, skip_if_needs_manual=False,
        )
    db.commit()
    db.refresh(ann)
    return LlmEnrichResult(
        ok=True,
        message="LLM 补全完成",
        fields_complete=all_fields_complete(ann),
        announcement=_to_announcement_out(ann),
    )


@app.post("/api/announcements/llm-enrich-batch")
async def llm_enrich_batch(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from config import settings
    from crawler.field_enricher import enrich_incomplete_batch

    if not settings.llm_enabled:
        raise HTTPException(status_code=400, detail="LLM 未启用")
    processed, completed = await enrich_incomplete_batch(db, limit=limit, force_llm=True)
    return {"processed": processed, "fields_complete": completed}


@app.get("/api/ops-health")
def get_ops_health(db: Session = Depends(get_db)):
    from crawler.ops_health import build_ops_health
    return build_ops_health(db)


@app.get("/api/missing-extended")
def list_missing_extended(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from crawler.ops_health import list_missing_extended
    return list_missing_extended(db, limit=limit)


@app.post("/api/announcements/{ann_id}/extended-enrich", response_model=LlmEnrichResult)
async def extended_enrich_one(ann_id: int, db: Session = Depends(get_db)):
    from config import settings
    from crawler.field_enricher import all_fields_complete, enrich_db_announcement, summary_has_extended
    from crawler.fetcher import create_http_client

    if not settings.llm_enabled:
        raise HTTPException(status_code=400, detail="LLM 未启用")
    settings.llm_enrich_all_enabled = True
    ann = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="通知不存在")
    if not all_fields_complete(ann):
        raise HTTPException(status_code=400, detail="四字段未齐全，请先在上方补全")
    if summary_has_extended(ann.summary):
        return LlmEnrichResult(
            ok=True,
            message="已有扩展信息",
            fields_complete=True,
            announcement=_to_announcement_out(ann),
        )
    async with create_http_client() as client:
        changed, _ = await enrich_db_announcement(
            ann, client=client, force_llm=True, extended_only=True,
        )
    db.commit()
    db.refresh(ann)
    return LlmEnrichResult(
        ok=changed or summary_has_extended(ann.summary),
        message="扩展字段已写入" if summary_has_extended(ann.summary) else "未能抽取扩展字段，请粘贴正文后重试",
        fields_complete=all_fields_complete(ann),
        announcement=_to_announcement_out(ann),
    )


@app.get("/api/crawl/status")
def crawl_status(
    board: str = Query(..., description="summer_camp | pre_admission"),
    tier: str = Query(..., description="985 | 211 | 双一流"),
):
    if board not in (SUMMER_CAMP, PRE_ADMISSION):
        board = SUMMER_CAMP
    tier = _normalize_tier(tier) or tier
    return status_payload(board, tier)


@app.get("/")
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "保研夏令营检索平台 API 运行中，请访问 /docs"}


@app.get("/style.css")
async def style_css():
    f = STATIC_DIR / "style.css"
    if f.exists():
        return _static_file(f, "text/css")
    raise HTTPException(status_code=404)


@app.get("/app.js")
async def app_js():
    f = STATIC_DIR / "app.js"
    if f.exists():
        return _static_file(f, "application/javascript")
    raise HTTPException(status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
