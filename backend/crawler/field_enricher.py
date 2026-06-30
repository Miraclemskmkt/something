"""四字段补全：规则底牌 → LLM 噪音分类 → LLM 主力提取 → 正则兜底。"""

from __future__ import annotations



import asyncio

import logging



import httpx

from sqlalchemy.orm import Session



from config import settings

from crawler.llm_classifier import classify_notice_relevance

from crawler.llm_enrich_state import clear_manual, needs_manual, record_failure, record_success

from crawler.llm_extractor import (

    LlmExtractResult,

    merge_llm_then_regex,

    warmup_llm,

)

from crawler.llm_cache import add_few_shot_from_manual, compute_field_confidence

from crawler.manual_preserves import (

    MANUAL_KEYS,

    MANUAL_SOURCE,

    is_manual_announcement,

    prefer_preserve_on_update,

)

from crawler.notice_content import gather_notice_content_bundle

from crawler.noise_filter import passes_title_filter

from crawler.parser import (

    ParsedAnnouncement,

    core_times_complete,

    merge_times_into_item,

)

from models import Announcement



logger = logging.getLogger(__name__)



FAILURE_LABELS = {

    "timeout": "LLM 超时",

    "bad_json": "返回非 JSON",

    "empty_fields": "返回空字段",

    "no_content": "正文缺失",

    "pdf_garble": "PDF 乱码",

    "disabled": "LLM 未启用",

    "llm_noise": "LLM 判定非招生通知",

}





def all_fields_complete(item: ParsedAnnouncement | Announcement) -> bool:

    return bool(

        core_times_complete(item)

        and getattr(item, "event_format", None)

    )





def summary_has_extended(summary: str | None) -> bool:

    return "---扩展信息---" in (summary or "")





def _apply_fields(target: Announcement, fields: dict[str, str | None]) -> bool:

    changed = False

    for key in ("publish_date", "deadline", "event_time", "event_format"):

        val = fields.get(key)

        if val and getattr(target, key) != val:

            setattr(target, key, val)

            changed = True

    return changed





def _apply_llm_result(

    item: ParsedAnnouncement,

    result: LlmExtractResult,

    *,

    ann_id: int | None = None,

) -> bool:

    if result.fields:

        wrote = False
        fields = dict(result.fields)
        extended = fields.pop("_extended", None)

        for k, v in fields.items():

            if v and not getattr(item, k, None):

                setattr(item, k, v)

                wrote = True

        if extended and isinstance(extended, dict):
            from crawler.llm_extractor import append_extended_to_summary
            item.summary = append_extended_to_summary(item.summary, extended)
            wrote = True

        if wrote or result.from_cache:

            from crawler.source_labels import normalize_source_on_enrich

            item.source = normalize_source_on_enrich(item.source)

            if ann_id:

                record_success(ann_id)

            return wrote or result.from_cache

        if ann_id and result.failure_type == "success":

            record_success(ann_id)

        return False



    if ann_id and result.failure_type and result.failure_type not in ("disabled",):

        record_failure(ann_id, result.failure_type)

    return False





async def _classify_and_extract(

    item: ParsedAnnouncement,

    *,

    ann_id: int | None = None,

    force_llm: bool = False,

    client: httpx.AsyncClient | None = None,

) -> LlmExtractResult | None:

    """

    完整 LLM 管线：

    1. 规则 title_filter（底牌，标题+摘要）

    2. 抓取正文 bundle

    3. LLM YES/NO 分类

    4. LLM 主力提取 + 正则兜底

    """

    title = item.title or ""

    summary = item.summary or ""

    already_complete = all_fields_complete(item)



    if not already_complete and not passes_title_filter(title, summary, url=item.url or ""):

        item.llm_rejected = True

        logger.info("规则底牌拒绝: %s", title[:50])

        return LlmExtractResult(failure_type="llm_noise", detail="规则过滤")



    bundle = await gather_notice_content_bundle(

        item.url or "", client=client, title=title,

    )

    content = bundle.text

    if content and (not item.summary or len(item.summary or "") < 80):

        item.summary = content[:500]

    if (not content or len(content.strip()) < 40) and item.summary:
        fallback = item.summary.split("---扩展信息---")[0].strip()
        if len(fallback) >= 40:
            content = fallback

    if not content or len(content.strip()) < 40:

        return LlmExtractResult(failure_type="no_content", detail="正文过短")



    if settings.llm_classify_enabled and not already_complete:

        clf = classify_notice_relevance(

            title,

            content,

            college_type=item.college_type or "law",

            url=item.url or "",

        )

        if not clf.relevant and clf.failure_type == "success":

            item.llm_rejected = True

            return LlmExtractResult(failure_type="llm_noise", detail=clf.raw[:80])



    table_for_llm = bundle.table_html or bundle.table_text

    if settings.llm_extract_first and settings.llm_enabled:

        result = merge_llm_then_regex(

            item,

            content,

            url=item.url or "",

            table_text=table_for_llm,

            html=bundle.raw_html or None,

        )

        _apply_llm_result(item, result, ann_id=ann_id)

        return result



    merge_times_into_item(item, content, bundle.raw_html or None)

    if settings.llm_enabled and (force_llm or not all_fields_complete(item)):

        from crawler.llm_extractor import call_llm_extract

        result = call_llm_extract(

            title, content,

            url=item.url or "",

            table_text=table_for_llm,

            garbled=bundle.garbled,

        )

        _apply_llm_result(item, result, ann_id=ann_id)

        if not all_fields_complete(item):

            merge_times_into_item(item, content, bundle.raw_html or None)

        return result



    return None





async def enrich_parsed_item(

    item: ParsedAnnouncement,

    *,

    client: httpx.AsyncClient | None = None,

    force_llm: bool = False,

    ann_id: int | None = None,

    skip_if_needs_manual: bool = True,

) -> ParsedAnnouncement:

    if ann_id and skip_if_needs_manual and needs_manual(ann_id) and not force_llm:

        return item



    if all_fields_complete(item) and not force_llm:

        return item



    await _classify_and_extract(

        item, ann_id=ann_id, force_llm=force_llm, client=client,

    )

    return item





async def enrich_db_announcement(

    ann: Announcement,

    *,

    client: httpx.AsyncClient | None = None,

    force_llm: bool = False,

    skip_if_needs_manual: bool = True,

    extended_only: bool = False,

) -> tuple[bool, LlmExtractResult | None]:

    if is_manual_announcement(ann) and not extended_only:

        return False, None

    if extended_only:

        if not all_fields_complete(ann) or summary_has_extended(ann.summary):

            return False, None

        force_llm = True

    elif all_fields_complete(ann) and not force_llm:

        return False, None

    if skip_if_needs_manual and needs_manual(ann.id) and not force_llm:

        return False, None



    item = ParsedAnnouncement(

        title=ann.title,

        url=ann.url,

        university=ann.university,

        college=ann.college,

        college_type=ann.college_type,

        publish_date=ann.publish_date,

        deadline=ann.deadline,

        event_time=ann.event_time,

        event_format=ann.event_format,

        summary=ann.summary,

        source=ann.source,

    )

    result = await _classify_and_extract(

        item, ann_id=ann.id, force_llm=force_llm, client=client,

    )

    if item.llm_rejected:

        return False, result



    probe = ParsedAnnouncement(

        title=ann.title, url=ann.url,

        publish_date=item.publish_date,

        deadline=item.deadline,

        event_time=item.event_time,

        event_format=item.event_format,

    )

    prefer_preserve_on_update(ann, probe)

    changed = _apply_fields(ann, {

        "publish_date": item.publish_date,

        "deadline": item.deadline,

        "event_time": item.event_time,

        "event_format": item.event_format,

    })

    if item.summary and (

        not ann.summary

        or len(item.summary) > len(ann.summary or "")

        or (extended_only and item.summary != ann.summary)

    ):

        ann.summary = item.summary

        changed = True

    return changed, result





async def classify_incomplete_one(

    ann: Announcement,

    *,

    client: httpx.AsyncClient,

) -> dict:

    if all_fields_complete(ann):

        return {"id": ann.id, "type": "already_complete", "title": ann.title[:60]}



    if needs_manual(ann.id):

        from crawler.llm_enrich_state import get_record

        rec = get_record(ann.id)

        return {

            "id": ann.id,

            "type": "needs_manual",

            "title": ann.title[:60],

            "fail_count": rec["fail_count"],

            "last_failure": rec["last_failure"],

        }



    bundle = await gather_notice_content_bundle(

        ann.url, client=client, title=ann.title or "",

    )

    content = bundle.text

    if not content or len(content.strip()) < 60:

        return {"id": ann.id, "type": "no_content", "title": ann.title[:60]}



    if settings.llm_classify_enabled:

        clf = classify_notice_relevance(

            ann.title or "", content,

            college_type=ann.college_type or "law",

            url=ann.url or "",

            auto_learn=False,

        )

        if not clf.relevant and clf.failure_type == "success":

            return {

                "id": ann.id,

                "type": "llm_noise",

                "title": ann.title[:60],

                "detail": clf.raw[:80],

            }



    item = ParsedAnnouncement(

        title=ann.title, url=ann.url,

        publish_date=ann.publish_date, deadline=ann.deadline,

        event_time=ann.event_time, event_format=ann.event_format,

    )

    result = merge_llm_then_regex(

        item, content,

        url=ann.url,

        table_text=bundle.table_html or bundle.table_text,

        html=bundle.raw_html or None,

    )

    missing = [

        k for k in ("publish_date", "deadline", "event_time", "event_format")

        if not getattr(item, k)

    ]

    if result.failure_type == "success" and not missing:

        return {

            "id": ann.id,

            "type": "llm_ok_pending_apply",

            "title": ann.title[:60],

            "fields": {

                "publish_date": item.publish_date,

                "deadline": item.deadline,

                "event_time": item.event_time,

                "event_format": item.event_format,

            },

            "confidence": result.confidence,

        }

    return {

        "id": ann.id,

        "type": result.failure_type or "unknown",

        "title": ann.title[:60],

        "detail": result.detail,

        "still_missing": missing,

        "confidence": result.confidence,

    }





async def classify_incomplete_batch(db: Session) -> dict[str, list[dict]]:

    rows = [

        a for a in db.query(Announcement).order_by(Announcement.id).all()

        if not all_fields_complete(a) and not is_manual_announcement(a)

    ]

    buckets: dict[str, list[dict]] = {}

    from crawler.fetcher import create_http_client



    async with create_http_client() as client:

        for ann in rows:

            info = await classify_incomplete_one(ann, client=client)

            key = info["type"]

            buckets.setdefault(key, []).append(info)

    return buckets





async def enrich_incomplete_batch(

    db: Session,

    *,

    limit: int | None = None,

    force_llm: bool = True,

    skip_needs_manual: bool = True,

) -> tuple[int, int]:

    rows = [

        a for a in db.query(Announcement).order_by(Announcement.id).all()

        if not all_fields_complete(a) and not is_manual_announcement(a)

    ]

    if skip_needs_manual:

        rows = [a for a in rows if not needs_manual(a.id)]

    if limit:

        rows = rows[:limit]



    if not rows:

        return 0, 0



    warmup_llm()



    sem = asyncio.Semaphore(max(1, settings.llm_max_concurrent))

    complete_after = 0

    rejected = 0



    from crawler.fetcher import create_http_client



    async with create_http_client() as client:

        async def one(ann: Announcement) -> bool:

            nonlocal rejected

            async with sem:

                try:

                    changed, result = await asyncio.wait_for(

                        enrich_db_announcement(

                            ann, client=client, force_llm=force_llm,

                            skip_if_needs_manual=skip_needs_manual,

                        ),

                        timeout=settings.llm_timeout_sec + 60,

                    )

                    if result and result.failure_type == "llm_noise":

                        db.delete(ann)

                        rejected += 1

                        return False

                    if changed and all_fields_complete(ann):

                        return True

                except Exception as e:

                    logger.debug("补全失败 id=%s: %s", ann.id, e)

                    record_failure(ann.id, "timeout")

                return False



        results = await asyncio.gather(*[one(a) for a in rows])

        complete_after = sum(1 for r in results if r)



    db.commit()

    logger.info(

        "LLM 管线: %d 条处理, %d 条四字段齐全, %d 条 LLM 拒收",

        len(rows), complete_after, rejected,

    )

    return len(rows), complete_after





async def enrich_extended_batch(

    db: Session,

    *,

    limit: int | None = None,

    ids: list[int] | None = None,

) -> tuple[int, int]:

    """四字段已齐全、尚未写入扩展信息块的通知，批量 LLM 扩充。"""

    if not settings.llm_enrich_all_enabled:

        return 0, 0

    if ids:

        rows = db.query(Announcement).filter(Announcement.id.in_(ids)).order_by(Announcement.id).all()

        rows = [a for a in rows if all_fields_complete(a) and not summary_has_extended(a.summary)]

    else:

        rows = [

            a for a in db.query(Announcement).order_by(Announcement.id).all()

            if all_fields_complete(a)

            and not summary_has_extended(a.summary)

            and not needs_manual(a.id)

        ]

    if limit:

        rows = rows[:limit]

    if not rows:

        return 0, 0

    warmup_llm()

    sem = asyncio.Semaphore(max(1, settings.llm_max_concurrent))

    enriched = 0

    from crawler.fetcher import create_http_client

    async with create_http_client() as client:

        async def one(ann: Announcement) -> bool:

            async with sem:

                try:

                    changed, _ = await asyncio.wait_for(

                        enrich_db_announcement(

                            ann, client=client, force_llm=True, extended_only=True,

                        ),

                        timeout=settings.llm_timeout_sec + 60,

                    )

                    return bool(changed)

                except Exception as e:

                    logger.debug("扩展字段补全失败 id=%s: %s", ann.id, e)

                return False

        results = await asyncio.gather(*[one(a) for a in rows])

        enriched = sum(1 for r in results if r)

    db.commit()

    logger.info("扩展字段补全: %d 条处理, %d 条写入", len(rows), enriched)

    return len(rows), enriched





def mark_manual_fields(

    db: Session,

    ann_id: int,

    *,

    publish_date: str | None = None,

    deadline: str | None = None,

    event_time: str | None = None,

    event_format: str | None = None,

    summary: str | None = None,

    url: str | None = None,

) -> Announcement | None:

    ann = db.query(Announcement).filter(Announcement.id == ann_id).first()

    if not ann:

        return None

    for key, val in (

        ("publish_date", publish_date),

        ("deadline", deadline),

        ("event_time", event_time),

        ("event_format", event_format),

    ):

        if val is not None and str(val).strip():

            setattr(ann, key, str(val).strip())

    if summary is not None and str(summary).strip():
        base = (ann.summary or "").split("---扩展信息---")[0].strip()
        pasted = str(summary).strip()
        if pasted != base:
            ann.summary = pasted

    if url is not None and str(url).strip().startswith("http"):
        ann.url = str(url).strip().split("#")[0]

    ann.source = MANUAL_SOURCE

    key = (ann.university, ann.college, ann.college_type)

    MANUAL_KEYS.add(key)

    clear_manual(ann_id)



    add_few_shot_from_manual(

        title=ann.title or "",

        content_snippet=ann.summary or "",

        fields={

            "publish_date": ann.publish_date,

            "deadline": ann.deadline,

            "event_time": ann.event_time,

            "event_format": ann.event_format,

        },

        university=ann.university,

        college=ann.college,

    )

    db.commit()

    db.refresh(ann)

    return ann

