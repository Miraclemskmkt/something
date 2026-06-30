"""对字段不全通知：聚合正文 + LLM 结构化抽取。"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from config import settings
from crawler.field_enricher import enrich_incomplete_batch

logger = logging.getLogger(__name__)


async def fix_incomplete_announcements(db: Session) -> int:
    if not settings.llm_enabled:
        logger.info("LLM 未启用，跳过字段补全（可配置 CAMP_LLM_ENABLED=true）")
        return 0
    processed, completed = await enrich_incomplete_batch(db, force_llm=True)
    return completed
