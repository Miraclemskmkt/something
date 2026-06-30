from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/camp.db"
    crawl_interval_minutes: int = 30
    scheduler_enabled: bool = False
    request_timeout: int = 15
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    max_concurrent_requests: int = 12
    min_notice_year: int = 2026

    # 快速抓取模式：更高并发、更短间隔、跳过非必要补全（默认开启）
    crawl_fast_mode: bool = True

    # 官网抓取：重试与抖动，降低触发 WAF 概率
    fetch_retry_count: int = 1
    fetch_retry_delay: float = 0.5
    fetch_jitter_max: float = 0.0
    official_request_delay: float = 0.0

    # 爬取模式：search=全网关键词检索 | official=学院官网列表 | both=两者并用
    crawl_mode: str = "search"
    # 检索策略：broad=泛搜(学校+学院+关键词) | site=site:域名限定
    search_strategy: str = "broad"
    search_engine: str = "bing"
    search_request_delay: float = 0.4
    search_max_concurrent: int = 12
    search_max_per_target: int = 1
    search_max_domains: int = 1
    search_compact_keywords: bool = False
    search_save_batch_size: int = 10
    broad_search_max_queries: int = 2
    broad_search_pdf: bool = False

    # 通知列表自动探测
    notice_list_probe_enabled: bool = True
    notice_probe_max_paths: int = 4
    notice_probe_parallel_paths: int = 3

    # 聚焦爬虫（末级兜底）
    focus_crawler_enabled: bool = True
    focus_crawler_max_sec: int = 5
    focus_crawler_depth: int = 2
    focus_crawler_max_requests: int = 5
    focus_crawler_delay: float = 0.0

    # 单学院预算与超时（典型 ≤10s，硬熔断 45s）
    http_connect_timeout: float = 4.0
    http_read_timeout: float = 4.0
    http_head_timeout: float = 3.0
    college_max_requests: int = 15
    college_total_timeout_sec: int = 45
    college_search_budget_sec: int = 30
    college_slow_threshold_sec: float = 12.0
    college_fast_target_sec: float = 10.0
    college_max_detail_fetches: int = 2
    search_query_timeout_sec: int = 5
    search_cache_ttl_sec: int = 7200
    list_page_cache_ttl_sec: int = 86400
    crawl_college_concurrency: int = 24

    # 微信公众号通道
    wechat_enabled: bool = False  # 总开关（含 crawl 流程中的微信逻辑）
    wechat_sogou_enabled: bool = False  # 搜狗搜索（默认关闭，易触发验证码）
    sogou_cookie: str = ""
    sogou_cdp_url: str = ""  # 如 http://127.0.0.1:9222，复用已登录浏览器
    wechat_request_delay: float = 6.0
    wechat_max_concurrent: int = 1
    wechat_compact_keywords: bool = True
    wechat_pending_only: bool = True
    wechat_sogou_daily_limit: int = 50
    wechat_sogou_weekly_limit: int = 500
    wechat_sogou_queries_per_college: int = 1
    wechat_snapshot_enabled: bool = True
    wechat_min_text_len: int = 100
    # 保研类网站聚合（保研论坛等，零微信验证码）
    baoyan_sites_enabled: bool = True
    baoyan_sites_delay: float = 1.5
    baoyan_sites_max_threads: int = 60
    baoyan_sites_interval_hours: int = 2
    baoyan_sites_list_pages: int = 3
    forum_radar_search_timeout_sec: float = 5.0

    # 保研汇总公众号 RSS（零搜狗请求）
    wechat_rss_enabled: bool = False
    wechat_rss_interval_hours: int = 6

    # Playwright 无头浏览器（应对 JS 反爬/WAF），默认关闭
    playwright_enabled: bool = False
    playwright_timeout: int = 20
    playwright_wait_ms: int = 1500

    # 检索与补全：超时与限量
    fetch_page_timeout: int = 18
    enrich_timeout_sec: int = 35
    enrich_timeout_light_sec: int = 6
    enrich_timeout_full_sec: int = 55
    search_target_timeout_sec: int = 45
    search_verify_max_results: int = 2
    bing_fallback_max_queries: int = 2
    enrich_resolve_max_candidates: int = 4
    official_list_max_items: int = 3
    coverage_sync_every_n_flushes: int = 8

    # PDF 通知正文解析
    pdf_enabled: bool = True
    pdf_max_bytes: int = 8_000_000

    # LLM 四字段结构化抽取（OpenAI 兼容 / Ollama）
    llm_enabled: bool = False
    llm_provider: str = "openai"  # openai | ollama
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_sec: int = 180
    llm_max_concurrent: int = 1
    llm_on_save: bool = True
    llm_only_incomplete: bool = True
    llm_content_max_chars: int = 3000
    llm_max_failures: int = 2
    llm_keep_alive: str = "-1"  # Ollama 模型常驻内存
    # LLM 噪音分类（正文抓取后第二道门；规则黑白名单仍为底牌）
    llm_classify_enabled: bool = True
    llm_classify_model: str = "qwen2.5:1.5b"
    llm_classify_timeout_sec: int = 45
    llm_forum_classify_enabled: bool = True  # 论坛帖入库前 1.5b 分类
    llm_link_extract_enabled: bool = True  # 7b 从帖内提取官方链接
    llm_enrich_all_enabled: bool = True  # 扩展字段（条件/专业/材料等）
    # LLM 主力提取（正则仅兜底）
    llm_extract_first: bool = True
    llm_cache_enabled: bool = True
    # 扫描件 PDF 多模态（需 Ollama 视觉模型 + pymupdf，默认关）
    llm_multimodal_enabled: bool = False
    llm_multimodal_model: str = "qwen2.5vl:3b"

    class Config:
        env_prefix = "CAMP_"
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
