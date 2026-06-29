from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/camp.db"
    crawl_interval_minutes: int = 30
    scheduler_enabled: bool = False
    request_timeout: int = 15
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    max_concurrent_requests: int = 8
    min_notice_year: int = 2026

    # 爬取模式：search=全网关键词检索 | official=学院官网列表 | both=两者并用
    crawl_mode: str = "search"
    search_engine: str = "bing"
    search_request_delay: float = 2.0
    search_max_concurrent: int = 3
    search_max_per_target: int = 2
    search_max_domains: int = 5
    search_compact_keywords: bool = True
    search_save_batch_size: int = 10

    # 微信公众号（搜狗）检索，默认关闭
    wechat_enabled: bool = False
    sogou_cookie: str = ""
    wechat_request_delay: float = 5.0
    wechat_max_concurrent: int = 1
    wechat_compact_keywords: bool = True

    # Playwright 无头浏览器（应对 JS 反爬/WAF），默认关闭
    playwright_enabled: bool = False
    playwright_timeout: int = 30
    playwright_wait_ms: int = 2500

    class Config:
        env_prefix = "CAMP_"
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
