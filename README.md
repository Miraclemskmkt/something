# 保研夏令营检索平台

聚合检索全国高校**法学院**（国际法、法律等）和**外国语学院**（英语、翻译、文学等）的夏令营、预推免通知，按状态分类展示，并提供直达官网链接。

## 功能

- **实时检索更新**：每 30 分钟自动爬取各高校官网 + 搜索引擎
- **分类展示**：
  - 进行中 — 正在报名的夏令营/预推免
  - 已结束 — 报名截止或活动已结束
  - 优营名单 — 已发布优秀营员名单的通知
- **学院筛选**：法学院 / 外国语学院
- **活动类型**：夏令营 / 预推免 / 优营名单
- **院校名录**：全国 177 所院校、284 个法学院/外国语学院，按七大地区分类展示
- **直达链接**：每条通知链接至学校官方发布页面

## 快速启动

### Windows

双击 `start.bat`，或在命令行运行：

```bat
start.bat
```

### 手动启动

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

浏览器访问：**http://127.0.0.1:8000**

## 数据来源

| 来源 | 说明 |
|------|------|
| **学院官网** | 80+ 所重点院校法学院/外国语学院通知公告页 |
| **微信公众号** | 通过搜狗微信检索各学院官方公众号发布的夏令营/预推免/优营文章 |
| **自动更新** | 后台定时任务，默认每 30 分钟执行一次 |

## 配置微信公众号

编辑 `backend/crawler/university_config.py`：

1. 在 `WECHAT_NAME_OVERRIDES` 中添加公众号名称映射（当公众号名与「学校+学院」不一致时）
2. 或在 `UniversityTarget` 中直接设置 `wechat_name="公众号名称"`

```python
UniversityTarget("北京大学", "法学院", "law", [...], "https://...", wechat_name="北大法学"),
```

## 配置

环境变量（可选）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CAMP_CRAWL_INTERVAL_MINUTES` | 30 | 自动爬取间隔（分钟） |
| `CAMP_REQUEST_TIMEOUT` | 15 | 请求超时（秒） |

## 添加更多学校

编辑 `backend/crawler/university_config.py`，在 `UNIVERSITY_TARGETS` 列表中添加：

```python
UniversityTarget("学校名", "学院名", "law", [
    "https://学院官网/tzgg/index.htm",
], "https://学院官网"),
```

`college_type` 可选 `"law"` 或 `"foreign_lang"`。

## 技术栈

- 后端：FastAPI + SQLAlchemy + SQLite
- 爬虫：httpx + BeautifulSoup + APScheduler
- 前端：原生 HTML/CSS/JS

## 说明

- 学院官网直连 + 微信公众号文章链接（`mp.weixin.qq.com`）
- 搜狗微信检索可能触发验证码，届时微信来源会暂时减少
- 本工具仅供信息聚合参考，请以各高校官方通知为准
