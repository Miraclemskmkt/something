import asyncio
import logging
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
from crawler.parser import effective_status, is_year_eligible
from crawler.service import refresh_ended_status, run_crawl
from crawler.submit_notice import SubmitNoticeError, list_submit_targets, submit_notice_link
from crawler.university_config import UNIVERSITY_TARGETS
from crawler.coverage import filter_targets_for_phase, get_covered_slots
from database import get_db, init_db
from models import Announcement, CrawlLog
from scheduler import start_scheduler, stop_scheduler
from double_first_class_api import get_double_first_class
from institutions import get_institutions
from schemas import (
    AnnouncementOut,
    CrawlResult,
    InstitutionsOut,
    PendingOut,
    StatsOut,
    SubmitCollegeOption,
    SubmitNoticeIn,
    SubmitNoticeOut,
)
from tier_filter import VALID_TIERS, filter_items_by_tier, filter_targets_by_tier, universities_in_tier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"


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


async def _background_crawl(board: str, tier: str | None = None) -> None:
    lock = get_lock(board, tier)
    if lock.locked():
        return
    async with lock:
        mark_started(board, tier)
        try:
            result = await run_crawl(board, tier=tier)
            mark_finished(board, result, tier)
        except Exception as e:
            logger.error("Background crawl failed (%s, %s): %s", board, tier, e)
            mark_failed(board, str(e), tier)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


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
    source: str | None = Query(None, description="学院官网 | 微信公众号"),
    tier: str | None = Query(None, description="985 | 211 | 双一流"),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    tier = _normalize_tier(tier)
    refresh_ended_status(db)
    q = db.query(Announcement)
    if college_type:
        q = q.filter(Announcement.college_type == college_type)
    if source:
        q = q.filter(Announcement.source == source)
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
    return _filter_by_effective_status(items, status)


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
    event = "预推免" if board == PRE_ADMISSION else "夏令营"
    return [
        PendingOut(
            id=r.id,
            university=r.university,
            college=r.college,
            college_type=r.college_type,
            status="pending",
            title=pending_title(r.university, r.college, board),
            event_type=event,
            search_count=r.search_count,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@app.get("/api/stats", response_model=StatsOut)
def get_stats(
    board: str | None = Query(None, description="summer_camp | pre_admission"),
    tier: str | None = Query(None, description="985 | 211 | 双一流"),
    db: Session = Depends(get_db),
):
    tier = _normalize_tier(tier)
    refresh_ended_status(db)
    all_items = _filter_items(db.query(Announcement).all(), board, tier)
    log_q = db.query(CrawlLog).filter(CrawlLog.status == "success")
    if board:
        log_q = log_q.filter(CrawlLog.board == board)
    last_log = log_q.order_by(CrawlLog.finished_at.desc()).first()

    universities = set(a.university for a in all_items if a.university != "未知")
    pending_rows = list_pending(db, board, tier=tier) if board else []
    status_pairs = _items_with_status(all_items)

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
        official_count=sum(1 for a in all_items if a.source == "学院官网"),
        wechat_count=sum(1 for a in all_items if a.source == "微信公众号"),
    )


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
    if pending == 0:
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
    asyncio.create_task(_background_crawl(board, tier))
    label = "夏令营" if board == SUMMER_CAMP else "预推免"
    return CrawlResult(
        board=board,
        tier=tier,
        found=0,
        new=0,
        updated=0,
        skipped=0,
        message=f"已开始检索{label} · {tier}（待检索 {pending} 个学院）",
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
        announcement=AnnouncementOut.model_validate(ann),
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
        return FileResponse(f, media_type="text/css")
    from fastapi import HTTPException
    raise HTTPException(status_code=404)


@app.get("/app.js")
async def app_js():
    f = STATIC_DIR / "app.js"
    if f.exists():
        return FileResponse(f, media_type="application/javascript")
    from fastapi import HTTPException
    raise HTTPException(status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
