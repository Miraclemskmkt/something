"""法学院 / 外国语学院通知页注册表（基于权威学院子站 + 已知通知直链）。"""

from dataclasses import dataclass

from official_sites import OFFICIAL_COLLEGE_SITES, derive_news_urls


@dataclass
class CollegeRegistryEntry:
    university: str
    college: str
    college_type: str
    news_urls: list[str]
    base_url: str = ""
    province: str = ""
    tags: list[str] | None = None
    source: str = "official"  # official | manual


# 已验证可用的学院通知页（优先于路径猜测）
MANUAL_URLS: dict[tuple[str, str], list[str]] = {
    ("北京大学", "law"): [
        "https://law.pku.edu.cn/xwzx/tzgg/index.htm",
        "https://law.pku.edu.cn/xwzx/xyxw/index.htm",
    ],
    ("北京大学", "foreign_lang"): ["https://sfl.pku.edu.cn/xwzx/tzgg/index.htm"],
    ("清华大学", "law"): ["https://www.law.tsinghua.edu.cn/xwzx/tzgg/index.htm"],
    ("清华大学", "foreign_lang"): ["https://www.dfll.tsinghua.edu.cn/xwzx/tzgg/index.htm"],
    ("中国人民大学", "law"): ["https://law.ruc.edu.cn/xwzx/tzgg/index.htm"],
    ("中国人民大学", "foreign_lang"): ["https://fl.ruc.edu.cn/xwzx/tzgg/index.htm"],
    ("北京师范大学", "law"): ["https://law.bnu.edu.cn/xwzx/tzgg/index.htm"],
    ("北京师范大学", "foreign_lang"): ["https://sfl.bnu.edu.cn/xwzx/tzgg/index.htm"],
    ("对外经济贸易大学", "law"): ["https://law.uibe.edu.cn/xwzx/tzgg/index.htm"],
    ("对外经济贸易大学", "foreign_lang"): ["https://sfs.uibe.edu.cn/xwzx/tzgg/index.htm"],
    ("北京外国语大学", "foreign_lang"): [
        "https://graduate.bfsu.edu.cn/tzgg/list.htm",
        "https://sei.bfsu.edu.cn/xwzx/tzgg/index.htm",
    ],
    ("中央财经大学", "law"): ["https://law.cufe.edu.cn/xwzx/tzgg/index.htm"],
    ("中国政法大学", "law"): ["https://law.cupl.edu.cn/xwzx/tzgg/index.htm"],
    ("中国政法大学", "foreign_lang"): ["https://wyxy.cupl.edu.cn/xwzx/tzgg/index.htm"],
    ("复旦大学", "law"): ["https://law.fudan.edu.cn/882/list.htm"],
    ("复旦大学", "foreign_lang"): ["https://dfll.fudan.edu.cn/882/list.htm"],
    ("上海交通大学", "law"): ["https://law.sjtu.edu.cn/Data/List/tzgg"],
    ("上海交通大学", "foreign_lang"): ["https://sfl.sjtu.edu.cn/Data/List/tzgg"],
    ("华东师范大学", "law"): ["https://law.ecnu.edu.cn/xwzx/tzgg/index.htm"],
    ("华东师范大学", "foreign_lang"): ["https://wyxy.ecnu.edu.cn/xwzx/tzgg/index.htm"],
    ("南京大学", "law"): ["https://law.nju.edu.cn/882/list.htm"],
    ("南京大学", "foreign_lang"): ["https://wyx.nju.edu.cn/882/list.htm"],
    ("浙江大学", "law"): ["https://law.zju.edu.cn/882/list.htm"],
    ("浙江大学", "foreign_lang"): ["https://sfl.zju.edu.cn/882/list.htm"],
    ("武汉大学", "law"): ["https://law.whu.edu.cn/xwzx/tzgg/index.htm"],
    ("武汉大学", "foreign_lang"): ["https://sfl.whu.edu.cn/xwzx/tzgg/index.htm"],
    ("华中科技大学", "law"): ["https://law.hust.edu.cn/xwzx/tzgg/index.htm"],
    ("华中科技大学", "foreign_lang"): ["https://sfl.hust.edu.cn/xwzx/tzgg/index.htm"],
    ("中南财经政法大学", "law"): ["https://law.zuel.edu.cn/xwzx/tzgg/index.htm"],
    ("中南财经政法大学", "foreign_lang"): ["https://sfl.zuel.edu.cn/xwzx/tzgg/index.htm"],
    ("中山大学", "law"): ["https://law.sysu.edu.cn/xwzx/tzgg/index.htm"],
    ("中山大学", "foreign_lang"): ["https://sfl.sysu.edu.cn/xwzx/tzgg/index.htm"],
    ("厦门大学", "law"): ["https://law.xmu.edu.cn/xwzx/tzgg/index.htm"],
    ("厦门大学", "foreign_lang"): ["https://sfl.xmu.edu.cn/xwzx/tzgg/index.htm"],
    ("山东大学", "law"): ["https://law.sdu.edu.cn/xwzx/tzgg/index.htm"],
    ("山东大学", "foreign_lang"): ["https://www.sfl.sdu.edu.cn/xwzx/tzgg/index.htm"],
    ("四川大学", "law"): ["https://law.scu.edu.cn/xwzx/tzgg/index.htm"],
    ("四川大学", "foreign_lang"): ["https://flc.scu.edu.cn/xwzx/tzgg/index.htm"],
    ("吉林大学", "law"): ["https://law.jlu.edu.cn/xwzx/tzgg/index.htm"],
    ("吉林大学", "foreign_lang"): ["https://foreign.jlu.edu.cn/xwzx/tzgg/index.htm"],
    ("南开大学", "law"): ["https://law.nankai.edu.cn/xwzx/tzgg/index.htm"],
    ("南开大学", "foreign_lang"): ["https://sfl.nankai.edu.cn/xwzx/tzgg/index.htm"],
    ("天津大学", "law"): ["https://law.tju.edu.cn/xwzx/tzgg/index.htm"],
    ("天津大学", "foreign_lang"): ["https://sfl.tju.edu.cn/xwzx/tzgg/index.htm"],
    ("西安交通大学", "law"): ["https://law.xjtu.edu.cn/xwzx/tzgg/index.htm"],
    ("西安交通大学", "foreign_lang"): ["https://sfl.xjtu.edu.cn/xwzx/tzgg/index.htm"],
    ("大连海事大学", "law"): ["https://law.dlmu.edu.cn/xwzx/tzgg/index.htm"],
    ("大连海事大学", "foreign_lang"): ["https://sfl.dlmu.edu.cn/xwzx/tzgg/index.htm"],
    ("中国海洋大学", "law"): ["https://law.ouc.edu.cn/xwzx/tzgg/index.htm"],
    ("中国海洋大学", "foreign_lang"): ["https://sfl.ouc.edu.cn/xwzx/tzgg/index.htm"],
    ("苏州大学", "law"): ["https://law.suda.edu.cn/xwzx/tzgg/index.htm"],
    ("苏州大学", "foreign_lang"): ["https://sfl.suda.edu.cn/xwzx/tzgg/index.htm"],
    ("南京师范大学", "law"): ["https://law.njnu.edu.cn/xwzx/tzgg/index.htm"],
    ("南京师范大学", "foreign_lang"): ["https://wyxy.njnu.edu.cn/xwzx/tzgg/index.htm"],
    ("湖南大学", "law"): ["https://law.hnu.edu.cn/xwzx/tzgg/index.htm"],
    ("湖南大学", "foreign_lang"): ["https://wyxy.hnu.edu.cn/xwzx/tzgg/index.htm"],
    ("重庆大学", "law"): ["https://law.cqu.edu.cn/xwzx/tzgg/index.htm"],
    ("重庆大学", "foreign_lang"): ["https://sfl.cqu.edu.cn/xwzx/tzgg/index.htm"],
    ("兰州大学", "law"): ["https://law.lzu.edu.cn/xwzx/tzgg/index.htm"],
    ("兰州大学", "foreign_lang"): ["https://sfl.lzu.edu.cn/xwzx/tzgg/index.htm"],
    ("郑州大学", "law"): ["https://law.zzu.edu.cn/xwzx/tzgg/index.htm"],
    ("郑州大学", "foreign_lang"): ["https://www.sfl.zzu.edu.cn/xwzx/tzgg/index.htm"],
    ("云南大学", "law"): ["https://law.ynu.edu.cn/xwzx/tzgg/index.htm"],
    ("云南大学", "foreign_lang"): ["https://wyxy.ynu.edu.cn/xwzx/tzgg/index.htm"],
    ("安徽大学", "law"): ["https://law.ahu.edu.cn/xwzx/tzgg/index.htm"],
    ("安徽大学", "foreign_lang"): ["https://sfl.ahu.edu.cn/xwzx/tzgg/index.htm"],
    ("南昌大学", "law"): ["https://law.ncu.edu.cn/xwzx/tzgg/index.htm"],
    ("南昌大学", "foreign_lang"): ["https://wyxy.ncu.edu.cn/xwzx/tzgg/index.htm"],
    ("福州大学", "law"): ["https://law.fzu.edu.cn/xwzx/tzgg/index.htm"],
    ("福州大学", "foreign_lang"): ["https://sfl.fzu.edu.cn/xwzx/tzgg/index.htm"],
    ("中国传媒大学", "foreign_lang"): ["https://sfl.cuc.edu.cn/xwzx/tzgg/index.htm"],
    ("中国传媒大学", "law"): ["https://law.cuc.edu.cn/xwzx/tzgg/index.htm"],
    ("中央民族大学", "foreign_lang"): ["https://sfl.muc.edu.cn/xwzx/tzgg/index.htm"],
    ("中央民族大学", "law"): ["https://law.muc.edu.cn/xwzx/tzgg/index.htm"],
    ("外交学院", "law"): ["https://law.cfau.edu.cn/xwzx/tzgg/index.htm"],
    ("外交学院", "foreign_lang"): ["https://english.cfau.edu.cn/xwzx/tzgg/index.htm"],
    ("上海外国语大学", "foreign_lang"): ["https://gs.shisu.edu.cn/tzgg/list.htm"],
    ("上海外国语大学", "law"): ["https://law.shisu.edu.cn/xwzx/tzgg/index.htm"],
}


def build_registry_entries() -> list[CollegeRegistryEntry]:
    entries: list[CollegeRegistryEntry] = []
    for site in OFFICIAL_COLLEGE_SITES:
        key = (site.university, site.college_type)
        if key in MANUAL_URLS:
            urls = list(dict.fromkeys(MANUAL_URLS[key] + derive_news_urls(site.homepage)))
            src = "manual"
        else:
            urls = derive_news_urls(site.homepage)
            src = "official"
        entries.append(CollegeRegistryEntry(
            university=site.university,
            college=site.college,
            college_type=site.college_type,
            news_urls=urls,
            base_url=site.homepage,
            province=site.province,
            tags=site.tags,
            source=src,
        ))
    return entries


REGISTRY_ENTRIES = build_registry_entries()
