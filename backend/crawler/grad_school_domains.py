"""研究生院 / 招生网域名，用于 site: 检索与 Bing 兜底。"""
from urllib.parse import urlparse

# 已知研究生院门户（启发式 yz.{root} 无法覆盖的）
GRAD_SCHOOL_OVERRIDES: dict[str, list[str]] = {
    "北京大学": ["yz.china.pku.edu.cn", "admission.pku.edu.cn"],
    "中国人民大学": ["pgs.ruc.edu.cn"],
    "清华大学": ["yz.tsinghua.edu.cn"],
    "北京航空航天大学": ["yzb.buaa.edu.cn"],
    "北京师范大学": ["yz.bnu.edu.cn"],
    "北京外国语大学": ["graduate.bfsu.edu.cn"],
    "对外经济贸易大学": ["yz.uibe.edu.cn"],
    "复旦大学": ["gsao.fudan.edu.cn", "yz.fudan.edu.cn"],
    "上海交通大学": ["yzb.sjtu.edu.cn", "yz.sjtu.edu.cn"],
    "华东师范大学": ["yjszs.ecnu.edu.cn"],
    "南京大学": ["yzb.nju.edu.cn"],
    "东南大学": ["yzb.seu.edu.cn"],
    "浙江大学": ["yjsy.zju.edu.cn", "grs.zju.edu.cn"],
    "厦门大学": ["zs.xmu.edu.cn"],
    "山东大学": ["www.yz.sdu.edu.cn"],
    "武汉大学": ["yz.whu.edu.cn"],
    "华中科技大学": ["gszs.hust.edu.cn"],
    "中南财经政法大学": ["yzb.zuel.edu.cn"],
    "中山大学": ["graduate.sysu.edu.cn"],
    "四川大学": ["yz.scu.edu.cn"],
    "重庆大学": ["yz.cqu.edu.cn"],
    "西安交通大学": ["yz.xjtu.edu.cn"],
    "西北政法大学": ["yjsy.nwupl.edu.cn"],
    "中国政法大学": ["yz.china-cupl.edu.cn"],
    "上海外国语大学": ["gs.shisu.edu.cn"],
    "天津大学": ["yzb.tju.edu.cn"],
    "南开大学": ["yzb.nankai.edu.cn"],
    "吉林大学": ["yjsy.jlu.edu.cn"],
    "哈尔滨工业大学": ["yzb.hit.edu.cn"],
    "大连海事大学": ["yz.dlmu.edu.cn"],
    "东北师范大学": ["yjsy.nenu.edu.cn"],
    "湖南大学": ["gra.hnu.edu.cn"],
    "中南大学": ["gra.csu.edu.cn"],
    "华南理工大学": ["yz.scut.edu.cn"],
    "暨南大学": ["yz.jnu.edu.cn"],
    "西南政法大学": ["yjsy.swupl.edu.cn"],
    "西南财经大学": ["yz.swufe.edu.cn"],
    "兰州大学": ["yz.lzu.edu.cn"],
}

GRAD_HOST_PREFIXES = (
    "yz", "yzb", "graduate", "gs", "yjsy", "yjs", "yjszs", "zsb",
    "admission", "postgraduate", "gra", "gsao", "pgs",
)


def grad_domain_candidates(university: str, root: str = "") -> list[str]:
    """返回某校可能的研究生院域名列表。"""
    domains: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        host = host.lower().replace("www.", "")
        if host and host not in seen:
            seen.add(host)
            domains.append(host)

    for host in GRAD_SCHOOL_OVERRIDES.get(university, []):
        add(host)
    if root:
        for prefix in GRAD_HOST_PREFIXES:
            add(f"{prefix}.{root}")
    return domains


def get_search_domains(target) -> list[str]:
    """检索用域名：学院子域 → 学校根域 → 研究生院域名。"""
    from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS

    domains: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        host = host.lower().replace("www.", "")
        if host and host not in seen:
            seen.add(host)
            domains.append(host)

    if target.base_url:
        add(urlparse(target.base_url).netloc)
    root = UNIVERSITY_ROOT_DOMAINS.get(target.university, "")
    if root:
        add(root)
    for g in grad_domain_candidates(target.university, root):
        add(g)
    return domains


def bing_site_queries(university: str, college: str, keyword: str, root: str = "") -> list[str]:
    """为 Bing 兜底生成 site: 查询列表。"""
    if not root:
        from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS
        root = UNIVERSITY_ROOT_DOMAINS.get(university, "")

    queries: list[str] = []
    seen: set[str] = set()
    short = (keyword or "夏令营 2026").strip()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    add(f"{university} {college} {short} site:edu.cn")
    for host in grad_domain_candidates(university, root)[:4]:
        add(f"site:{host} {university} {college} {short}")
    if root:
        add(f"site:{root} {university} {college} {short}")
    return queries[:8]
