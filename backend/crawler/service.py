import asyncio

import logging

from datetime import datetime



import httpx

from sqlalchemy.orm import Session



from config import settings

from crawler.boards import BOARD_LABELS, PRE_ADMISSION, SUMMER_CAMP

from crawler.coverage import (
    clear_tier_board_coverage,
    _db_write_lock,
    filter_targets_for_phase,
    filter_wechat_targets,
    get_covered_slots,
    mark_coverage,
    mark_target_searched,
    sync_coverage_from_announcements,
    sync_coverage_from_pending,
    sync_result_coverage_from_notice,
)
from crawler.boards import matches_board
from crawler.parser import core_times_complete
from crawler.crawl_state import mark_progress
from crawler.pending import clear_college_pending, clear_pending_for_announcement, mark_college_pending

from crawler.fetcher import create_http_client, fetch_page
from crawler.detail_enricher import enrich_announcement, infer_board_from_item
from crawler.parser import ParsedAnnouncement, is_valid_announcement_url, is_year_eligible, parse_news_list
from crawler.searcher import _search_one_target_timed as search_one_target

from crawler.wechat import crawl_wechat_for_targets

from crawler.university_config import UNIVERSITY_TARGETS

from database import SessionLocal

from models import Announcement, AnnouncementStatus, CrawlLog



logger = logging.getLogger(__name__)


class IncrementalSaver:
    """每凑满 batch_size 条通知写入数据库，便于前端实时看到结果。"""

    def __init__(
        self,
        db: Session,
        log: CrawlLog,
        board: str,
        tier: str | None = None,
        batch_size: int | None = None,
    ):
        self.db = db
        self.log = log
        self.board = board
        self.tier = tier
        self.batch_size = batch_size or settings.search_save_batch_size
        self._pending: list[ParsedAnnouncement] = []
        self.total_new = 0
        self.total_updated = 0
        self.total_found = 0
        self._flush_count = 0

    def on_batch(self, items: list[ParsedAnnouncement]) -> None:
        self._pending.extend(items)
        while len(self._pending) >= self.batch_size:
            self._flush(self._pending[: self.batch_size])
            self._pending = self._pending[self.batch_size :]

    def flush_now(self) -> None:
        """每个学院检索完成后立即入库，不等待凑满批次。"""
        if self._pending:
            self._flush(self._pending)
            self._pending = []

    def finish(self) -> tuple[int, int]:
        if self._pending:
            self._flush(self._pending)
            self._pending = []
        return self.total_new, self.total_updated

    def _flush(self, chunk: list[ParsedAnnouncement]) -> None:
        if not chunk:
            return
        new, upd = save_announcements(self.db, chunk)
        self._flush_count += 1
        if self._flush_count % settings.coverage_sync_every_n_flushes == 0:
            sync_coverage_from_announcements(self.db)
        self.total_new += new
        self.total_updated += upd
        self.total_found += len(chunk)
        label = BOARD_LABELS.get(self.board, self.board)
        self.log.found_count = self.total_found
        self.log.new_count = self.total_new
        self.log.updated_count = self.total_updated
        self.log.message = (
            f"{label}：已入库 {self.total_found} 条，新增 {self.total_new}，"
            f"更新 {self.total_updated}（检索进行中…）"
        )
        self.db.commit()
        mark_progress(self.board, self.log.message, self.tier)
        logger.info("Incremental save: %d items, new=%d", len(chunk), new)


class TargetRecorder:
    """每个学院检索完成后立即写入：有通知入库，无通知进待定榜。"""

    def __init__(
        self,
        db: Session,
        board: str,
        phase: str,
        saver: IncrementalSaver | None = None,
    ):
        self.db = db
        self.board = board
        self.phase = phase
        self.saver = saver
        self.searched = 0
        self.pending_count = 0
        self.notice_count = 0

    def on_target_complete(self, target, items: list[ParsedAnnouncement]) -> None:
        with _db_write_lock:
            self.searched += 1
            if items:
                self.notice_count += 1
                if self.saver:
                    self.saver.on_batch(items)
                    self.saver.flush_now()
                if self.phase == "notice":
                    clear_college_pending(self.db, target, self.board, self.phase)
            elif self.phase == "notice":
                mark_college_pending(self.db, target, self.board, self.phase)
                self.pending_count += 1

            mark_target_searched(self.db, target, self.board, self.phase)

            if self.saver and self.saver.log:
                label = BOARD_LABELS.get(self.board, self.board)
                self.saver.log.message = (
                    f"{label}：已检索 {self.searched} 个学院，"
                    f"通知 {self.notice_count} 所，待定 {self.pending_count} 所…"
                )
                self.db.commit()
                mark_progress(self.board, self.saver.log.message, self.saver.tier)
            else:
                self.db.commit()


async def crawl_university(
    target,
    board: str,
    phase: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[ParsedAnnouncement]:
    from official_sites import derive_news_urls

    candidate_urls = list(dict.fromkeys(
        [u for u in target.news_urls if u]
        + (derive_news_urls(target.base_url) if target.base_url else [])
    ))[:4]

    async def _load(news_url: str) -> tuple[str, str | None]:
        html = await fetch_page(
            news_url, client=client, allow_playwright=False, fast=True,
        )
        return news_url, html

    html: str | None = None
    news_url: str | None = None
    batch_size = 3
    for i in range(0, len(candidate_urls), batch_size):
        batch = candidate_urls[i:i + batch_size]
        pairs = await asyncio.gather(*[_load(u) for u in batch])
        for u, h in pairs:
            if h and len(h) >= 500:
                html, news_url = h, u
                break
        if html:
            break

    if not html or not news_url:
        return []

    base = target.base_url or news_url
    items = parse_news_list(
        html, base, target.college_type,
        board=board, phase=phase,
    )

    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    max_items = settings.official_list_max_items

    async def enrich_one(item: ParsedAnnouncement) -> None:
        await enrich_announcement(
            item, board=board, phase=phase, client=client, mode="crawl",
        )

    enrich_sem = asyncio.Semaphore(4)
    pending_enrich: list[ParsedAnnouncement] = []

    from crawler.source_labels import OFFICIAL_LABEL

    for item in items[:max_items]:
        item.source = OFFICIAL_LABEL
        item.university = target.university
        item.college = target.college
        item.college_type = target.college_type
        pending_enrich.append(item)

    async def enrich_with_sem(it: ParsedAnnouncement) -> None:
        async with enrich_sem:
            await enrich_one(it)

    await asyncio.gather(*[enrich_with_sem(it) for it in pending_enrich])

    for item in pending_enrich:
        if item.llm_rejected:
            continue
        if item.url not in seen:
            seen.add(item.url)
            results.append(item)

    return results





async def crawl_board_phase(
    targets: list,
    board: str,
    phase: str,
    db: Session,
    saver: IncrementalSaver | None = None,
) -> list[ParsedAnnouncement]:
    all_items: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    official_hits: set[tuple[str, str, str]] = set()

    if not targets:
        return all_items

    crawl_mode = settings.crawl_mode.lower()
    use_search = crawl_mode in ("search", "both")
    use_official = crawl_mode in ("official", "both")
    recorder = TargetRecorder(db, board, phase, saver)

    sem = asyncio.Semaphore(settings.max_concurrent_requests)
    search_sem = asyncio.Semaphore(settings.search_max_concurrent)

    async with create_http_client() as http_client:

        async def process_target(target):
            items: list[ParsedAnnouncement] = []
            local_seen: set[str] = set()

            if use_search:
                try:
                    from crawler.multi_source import discover_for_target

                    found = await asyncio.wait_for(
                        discover_for_target(
                            target, board, phase, http_client, search_sem, sem,
                        ),
                        timeout=settings.search_target_timeout_sec,
                    )
                    for item in found:
                        if item.url not in local_seen:
                            local_seen.add(item.url)
                            items.append(item)
                except asyncio.TimeoutError:
                    logger.warning(
                        "学院检索超时 (%ds): %s %s",
                        settings.search_target_timeout_sec,
                        target.university,
                        target.college,
                    )
                except Exception as e:
                    logger.warning("Multi-source error %s %s: %s", target.university, target.college, e)

            need_official = (
                use_official
                and settings.search_strategy != "broad"
                and not items
            )
            if need_official:
                try:
                    async with sem:
                        found = await crawl_university(
                            target, board, phase, client=http_client,
                        )
                    for item in found:
                        if item.url not in local_seen:
                            local_seen.add(item.url)
                            items.append(item)
                except Exception as e:
                    logger.warning("Official crawl error %s: %s", target.university, e)

            recorder.on_target_complete(target, items)
            return items

        chunk = settings.crawl_college_concurrency
        results: list = []
        for i in range(0, len(targets), chunk):
            batch = targets[i: i + chunk]
            part = await asyncio.gather(
                *[process_target(t) for t in batch],
                return_exceptions=True,
            )
            results.extend(part)

    for result in results:
        if isinstance(result, Exception):
            logger.warning("Target process error: %s", result)
            continue
        for item in result:
            if item.url not in seen:
                seen.add(item.url)
                all_items.append(item)
                official_hits.add((item.university, item.college, item.college_type))

    if settings.wechat_sogou_enabled:
        wechat_targets = filter_wechat_targets(db, targets, board, phase, official_hits)
        if wechat_targets:
            try:
                wechat_items = await crawl_wechat_for_targets(wechat_targets, board, phase)
                wechat_new: list[ParsedAnnouncement] = []
                for item in wechat_items:
                    if item.llm_rejected:
                        continue
                    if item.url not in seen:
                        seen.add(item.url)
                        all_items.append(item)
                        wechat_new.append(item)
                if saver and wechat_new:
                    saver.on_batch(wechat_new)
            except Exception as e:
                logger.warning("WeChat crawl error: %s", e)

    if settings.wechat_rss_enabled and phase == "notice":
        try:
            from crawler.wechat_rss import crawl_wechat_rss
            rss_items = await crawl_wechat_rss(board=board, phase=phase)
            rss_new: list[ParsedAnnouncement] = []
            for item in rss_items:
                if item.url not in seen:
                    seen.add(item.url)
                    all_items.append(item)
                    rss_new.append(item)
            if saver and rss_new:
                saver.on_batch(rss_new)
        except Exception as e:
            logger.warning("WeChat RSS error: %s", e)
    elif use_search and not use_official:
        logger.info(
            "%s-%s：全网检索 %d 个学院，有通知 %d 所，待定 %d 所",
            board, phase, recorder.searched, recorder.notice_count, recorder.pending_count,
        )

    return all_items





def purge_old_notices(db: Session) -> int:

    from crawler.manual_preserves import is_manual_announcement

    count = 0

    for item in db.query(Announcement).all():

        if is_manual_announcement(item):
            continue

        if not is_year_eligible(item.title, item.publish_date, item.deadline):

            db.delete(item)

            count += 1

    db.commit()

    return count





def purge_invalid_records(db: Session) -> int:

    from crawler.manual_preserves import is_manual_announcement

    count = 0

    for item in db.query(Announcement).all():

        if is_manual_announcement(item):
            continue

        if not is_valid_announcement_url(item.url):

            db.delete(item)

            count += 1
            continue

        from crawler.noise_filter import title_college_mismatch, title_filter
        from crawler.detail_enricher import infer_board_from_item

        if title_college_mismatch(item.title, item.university or "", item.college or ""):

            db.delete(item)

            count += 1
            continue

        board = infer_board_from_item(item)
        ok, _ = title_filter(
            item.title or "",
            item.summary or "",
            board=board,
            phase="notice",
            url=item.url or "",
        )
        if not ok:

            db.delete(item)

            count += 1

    db.commit()

    return count





def save_announcements(db: Session, items: list[ParsedAnnouncement]) -> tuple[int, int]:
    from crawler.source_labels import WECHAT_LABEL, normalize_source_for_storage

    new_count = 0
    updated_count = 0

    from crawler.manual_preserves import prefer_preserve_on_update

    url_to_target = {}
    for t in UNIVERSITY_TARGETS:
        for u in t.news_urls:
            domain = u.split("/")[2] if "//" in u else ""
            url_to_target[domain] = t

    def find_existing(item: ParsedAnnouncement) -> Announcement | None:
        existing = db.query(Announcement).filter(Announcement.url == item.url).first()
        if existing:
            return existing
        orig = item.original_url
        if orig and orig != item.url:
            existing = db.query(Announcement).filter(Announcement.url == orig).first()
            if existing:
                return existing
        if item.university and item.college and item.title:
            prefix = item.title[:24]
            for cand in db.query(Announcement).filter(
                Announcement.university == item.university,
                Announcement.college == item.college,
            ).all():
                if prefix and (prefix in cand.title or cand.title[:24] in item.title):
                    return cand
        return None

    for item in items:
        if getattr(item, "llm_rejected", False):
            continue
        if not is_valid_announcement_url(item.url):
            continue
        if not is_year_eligible(item.title, item.publish_date, item.deadline):
            continue
        from crawler.noise_filter import should_accept_notice

        board_guess = infer_board_from_item(item) or SUMMER_CAMP
        if not should_accept_notice(
            item.title, summary=item.summary or "", board=board_guess, url=item.url or "",
        ):
            continue

        existing = find_existing(item)

        university = item.university or "未知"
        college = item.college or "未知"
        college_type = item.college_type or "law"

        if university == "未知":
            for domain, tgt in url_to_target.items():
                if domain in item.url:
                    university = tgt.university
                    college = tgt.college
                    college_type = tgt.college_type
                    break
            else:
                if any(k in item.title for k in ["外国语", "外语", "英语", "翻译", "文学", "语言"]):
                    college_type = "foreign_lang"
                    college = "外国语学院"
                elif any(k in item.title for k in ["法", "法律", "法学", "国际法"]):
                    college_type = "law"
                    college = "法学院"
                for tgt in UNIVERSITY_TARGETS:
                    if tgt.university in item.title:
                        university = tgt.university
                        college = tgt.college
                        college_type = tgt.college_type
                        break

        from crawler.noise_filter import title_college_mismatch

        if title_college_mismatch(item.title, university, college):
            continue

        from crawler.wechat_article import is_weixin_url
        from crawler.source_labels import (
            OFFICIAL_LABEL,
            WECHAT_LABEL,
            normalize_source_for_storage,
        )

        item.source = normalize_source_for_storage(item.source)
        if is_weixin_url(item.url or "") and item.source == OFFICIAL_LABEL:
            item.source = WECHAT_LABEL

        if existing:
            prefer_preserve_on_update(existing, item)
            changed = False

            if item.url != existing.url:
                conflict = db.query(Announcement).filter(
                    Announcement.url == item.url,
                    Announcement.id != existing.id,
                ).first()
                if conflict:
                    db.delete(conflict)
                    db.flush()
                existing.url = item.url
                changed = True

            for field, val in [
                ("status", item.status),
                ("title", item.title),
                ("source", item.source),
            ]:
                if val and getattr(existing, field) != val:
                    setattr(existing, field, val)
                    changed = True

            if item.deadline and existing.deadline != item.deadline:
                existing.deadline = item.deadline
                changed = True
            if item.event_time and existing.event_time != item.event_time:
                existing.event_time = item.event_time
                changed = True
            if item.event_format and existing.event_format != item.event_format:
                existing.event_format = item.event_format
                changed = True
            if item.publish_date and existing.publish_date != item.publish_date:
                existing.publish_date = item.publish_date
                changed = True
            if item.summary and (
                not existing.summary or len(item.summary) > len(existing.summary or "")
            ):
                existing.summary = item.summary
                changed = True

            if changed:
                existing.updated_at = datetime.now()
                updated_count += 1

            mark_coverage(db, existing)
            clear_pending_for_announcement(db, existing)

        else:
            ann = Announcement(
                title=item.title,
                url=item.url,
                university=university,
                college=college,
                college_type=college_type,
                status=item.status,
                event_type=item.event_type,
                publish_date=item.publish_date,
                deadline=item.deadline,
                event_time=item.event_time,
                event_format=item.event_format,
                source=item.source,
                summary=item.summary,
            )
            db.add(ann)
            db.flush()
            mark_coverage(db, ann)
            clear_pending_for_announcement(db, ann)
            new_count += 1

    db.commit()
    return new_count, updated_count





def refresh_ended_status(db: Session) -> int:

    from crawler.parser import parse_datetime_value

    count = 0

    now = datetime.now()

    active_items = db.query(Announcement).filter(

        Announcement.status == AnnouncementStatus.ACTIVE.value

    ).all()

    for item in active_items:

        if item.deadline:

            dl = parse_datetime_value(item.deadline)

            if dl and dl < now:

                item.status = AnnouncementStatus.ENDED.value

                count += 1

    db.commit()

    return count





async def enrich_incomplete_in_tier(
    db: Session,
    board: str,
    tier: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    """补全库内已有通知的开放/截止/举办时间/形式（crawl 模式）。"""
    from tier_filter import universities_in_tier

    tier_unis = universities_in_tier(tier)
    candidates = [
        ann for ann in db.query(Announcement).filter(
            Announcement.university.in_(tier_unis),
        ).all()
        if matches_board(ann, board)
    ]
    to_fix: list[Announcement] = []
    for ann in candidates:
        probe = ParsedAnnouncement(
            title=ann.title,
            url=ann.url,
            publish_date=ann.publish_date,
            deadline=ann.deadline,
            event_time=ann.event_time,
            event_format=ann.event_format,
            university=ann.university,
            college=ann.college,
            college_type=ann.college_type,
        )
        if not core_times_complete(probe) or not probe.event_format:
            to_fix.append(ann)

    if not to_fix:
        return 0

    updated = 0
    sem = asyncio.Semaphore(4)

    async def _one(ann: Announcement, http_client: httpx.AsyncClient) -> None:
        nonlocal updated
        item = ParsedAnnouncement(
            title=ann.title,
            url=ann.url,
            publish_date=ann.publish_date,
            deadline=ann.deadline,
            event_time=ann.event_time,
            event_format=ann.event_format,
            university=ann.university,
            college=ann.college,
            college_type=ann.college_type,
            source=ann.source,
            summary=ann.summary,
        )
        async with sem:
            await enrich_announcement(
                item, board=board, phase="notice", client=http_client, mode="crawl",
            )
        changed = False
        for field in ("publish_date", "deadline", "event_time", "event_format", "title", "summary"):
            new_val = getattr(item, field)
            if new_val and getattr(ann, field) != new_val:
                setattr(ann, field, new_val)
                changed = True
        if item.url and item.url != ann.url:
            conflict = db.query(Announcement).filter(
                Announcement.url == item.url,
                Announcement.id != ann.id,
            ).first()
            if conflict:
                db.delete(conflict)
                db.flush()
            ann.url = item.url
            changed = True
        if changed:
            ann.updated_at = datetime.now()
            updated += 1

    if client is not None:
        await asyncio.gather(
            *[_one(a, client) for a in to_fix],
            return_exceptions=True,
        )
    else:
        async with create_http_client() as http_client:
            await asyncio.gather(
                *[_one(a, http_client) for a in to_fix],
                return_exceptions=True,
            )

    if updated:
        db.commit()
        mark_progress(
            board,
            f"已补全 {updated}/{len(to_fix)} 条通知的时间与形式字段…",
            tier,
        )
    return updated


async def run_crawl(
    board: str = SUMMER_CAMP,
    tier: str | None = None,
    *,
    refresh: bool = False,
) -> dict:

    if board not in (SUMMER_CAMP, PRE_ADMISSION):

        raise ValueError(f"Unknown board: {board}")

    from tier_filter import VALID_TIERS, filter_targets_by_tier, tier_label

    if tier and tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier: {tier}")

    target_pool = filter_targets_by_tier(UNIVERSITY_TARGETS, tier) if tier else UNIVERSITY_TARGETS
    tier_name = tier_label(tier) if tier else "全部"

    db = SessionLocal()

    log = CrawlLog(status="running", board=board)
    db.add(log)
    db.commit()

    label = BOARD_LABELS[board]
    total_skipped = 0
    saver = IncrementalSaver(db, log, board, tier=tier)
    phases: tuple[str, ...] = ("notice", "result")
    if refresh:
        phases = ("notice",)
        mark_progress(
            board,
            f"正在清除 {tier_name} 旧检索缓存并重新全网检索…",
            tier,
        )
        clear_tier_board_coverage(db, tier, board, phases=phases)

    try:
        purge_invalid_records(db)
        purge_old_notices(db)
        if not refresh:
            sync_coverage_from_announcements(db)
            sync_coverage_from_pending(db)
            sync_result_coverage_from_notice(db)
        covered = get_covered_slots(db, board)

        all_items: list[ParsedAnnouncement] = []
        for phase in phases:
            targets = filter_targets_for_phase(target_pool, covered, board, phase)
            from crawler.pending_schedule import filter_targets_by_schedule
            targets = filter_targets_by_schedule(db, targets, board, phase)
            skipped = len(target_pool) - len(targets)
            total_skipped += skipped
            logger.info(
                "%s-%s-%s: 跳过 %d 个已检索学院，待检索 %d 个",
                label, tier_name, phase, skipped, len(targets),
            )
            if not targets:
                continue
            mark_progress(
                board,
                f"{label}（{tier_name}）：正在检索 {len(targets)} 个学院（{phase}）…",
                tier,
            )
            items = await crawl_board_phase(targets, board, phase, db, saver=saver)
            all_items.extend(items)
            covered = get_covered_slots(db, board)

        enrich_updated = 0
        if refresh or saver.total_found:
            mark_progress(
                board,
                f"{label}（{tier_name}）：正在补全已有通知的时间与形式…",
                tier,
            )
            enrich_updated = await enrich_incomplete_in_tier(db, board, tier)
            if settings.llm_enabled:
                from crawler.field_enricher import enrich_incomplete_batch
                _, llm_done = await enrich_incomplete_batch(db, limit=50, force_llm=True)
                enrich_updated += llm_done

        new_count, updated_count = saver.finish()
        updated_count += enrich_updated
        sync_coverage_from_announcements(db)
        ended_count = refresh_ended_status(db)

        log.finished_at = datetime.now()
        log.status = "success"
        log.found_count = len(all_items)
        log.new_count = new_count
        log.updated_count = updated_count + ended_count
        log.skipped_count = total_skipped
        if refresh:
            log.message = (
                f"{label}（{tier_name}）刷新完成：发现 {len(all_items)} 条，"
                f"新增 {new_count}，更新 {updated_count + ended_count}"
                f"（含补全 {enrich_updated} 条）"
            )
        else:
            log.message = (
                f"{label}（{tier_name}）：发现 {len(all_items)} 条，新增 {new_count}，"
                f"更新 {updated_count + ended_count}，跳过 {total_skipped} 个已检索学院"
            )
        if (
            not refresh
            and not all_items
            and total_skipped > 0
            and total_skipped >= len(target_pool) * len(phases)
        ):
            log.message = (
                f"{label}（{tier_name}）：全部学院已检索完毕，"
                "点击「刷新检索」可重新抓取最新通知"
            )

        db.commit()

        logger.info(log.message)

        return {

            "board": board,

            "tier": tier or "",

            "found": len(all_items),

            "new": new_count,

            "updated": updated_count + ended_count,

            "skipped": total_skipped,

            "message": log.message,

        }

    except Exception as e:

        log.finished_at = datetime.now()

        log.status = "error"

        log.message = str(e)

        db.commit()

        logger.error("Crawl failed (%s): %s", board, e)

        raise

    finally:
        if settings.playwright_enabled:
            from crawler.playwright_fetcher import close_playwright
            await close_playwright()
        db.close()





async def run_crawl_all() -> None:

    await run_crawl(SUMMER_CAMP)

    await run_crawl(PRE_ADMISSION)


