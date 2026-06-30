"""学校/学院检索用全称与简称映射，用于泛搜拼接。"""

# 学校简称（检索召回）
UNIVERSITY_SHORT: dict[str, list[str]] = {
    "北京大学": ["北大"],
    "清华大学": ["清华"],
    "中国人民大学": ["人大"],
    "复旦大学": ["复旦"],
    "武汉大学": ["武大"],
    "南京大学": ["南大"],
    "浙江大学": ["浙大"],
    "上海交通大学": ["上交", "上海交大"],
    "中山大学": ["中大"],
    "厦门大学": ["厦大"],
    "四川大学": ["川大"],
    "南开大学": ["南开"],
    "天津大学": ["天大"],
    "同济大学": ["同济"],
    "华东师范大学": ["华师大", "华东师大"],
    "北京师范大学": ["北师大"],
    "中国政法大学": ["法大"],
    "对外经济贸易大学": ["贸大", "对外经贸"],
    "北京外国语大学": ["北外"],
    "上海外国语大学": ["上外"],
    "西安交通大学": ["西交大"],
    "哈尔滨工业大学": ["哈工大"],
    "哈尔滨工程大学": ["哈工程"],
    "电子科技大学": ["电子科大", "成电"],
    "兰州大学": ["兰大"],
    "河南大学": ["河大"],
    "宁波大学": ["宁大"],
    "湘潭大学": ["湘大"],
    "山西大学": ["山大"],
    "首都师范大学": ["首师大"],
    "黑龙江大学": ["黑大"],
    "外交学院": ["外院"],
}

# 学院简称
COLLEGE_SHORT: dict[str, list[str]] = {
    "法学院": ["法学", "法律"],
    "外国语学院": ["外院", "外语"],
    "外语学院": ["外院", "外语"],
    "外国语言文学学院": ["外院", "外语"],
    "外文学院": ["外文", "外语"],
    "英语系": ["英语"],
    "英语学院": ["英语"],
    "国际法系": ["国法", "法学"],
    "凯原法学院": ["法学", "凯原"],
    "人文社会科学学院": ["人文", "法学"],
    "政法学院": ["政法", "法学"],
}


def search_name_variants(university: str, college: str) -> list[tuple[str, str]]:
    """返回 (学校名, 学院名) 组合，优先全称。"""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def add(u: str, c: str) -> None:
        key = (u.strip(), c.strip())
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            out.append(key)

    add(university, college)
    for us in UNIVERSITY_SHORT.get(university, []):
        add(us, college)
    for cs in COLLEGE_SHORT.get(college, []):
        add(university, cs)
    for us in UNIVERSITY_SHORT.get(university, [])[:1]:
        for cs in COLLEGE_SHORT.get(college, [])[:1]:
            add(us, cs)
    return out
