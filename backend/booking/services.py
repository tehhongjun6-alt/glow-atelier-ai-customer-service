"""可以预约的服务和时长

预约系统需要"结构化"的资料（每个服务要几分钟），不能每次靠 RAG 从文字里猜。
时长来自 knowledge_base/glow_atelier.md；资料里没写时长的，用标了 ⚠️ 的预设值。
"""

SERVICES = {
    # 脸部护理
    "经典深层清洁护理": 60,
    "Aqua Peel 小气泡清洁护理": 50,
    "水光补湿护理": 75,
    "痘痘肌净化护理": 75,
    "敏感肌修护护理": 60,
    "维他命 C 亮肤护理": 75,
    "抗衰紧致护理": 90,
    "RF 射频紧致护理": 60,
    "男士护理": 60,
    "眼部护理": 30,
    # 睫毛与眉毛
    "睫毛嫁接（自然款）": 90,
    "睫毛嫁接（浓密款）": 120,
    "睫毛补接": 60,              # ⚠️ 资料没写时长
    "睫毛自然卷翘": 60,
    "修眉造型": 20,
    "修眉加染眉": 40,
    "眉毛定型": 60,
    "半永久雾眉": 150,
    # 身体护理
    "芳疗全身按摩（60 分钟）": 60,
    "芳疗全身按摩（90 分钟）": 90,
    "肩颈舒缓按摩": 45,
    "孕妇按摩": 60,
    "身体去角质": 45,
    # 脱毛
    "蜜蜡脱毛": 30,              # ⚠️ 资料没写时长，按部位不同
    "IPL 光子脱毛": 30,          # ⚠️ 资料没写时长，按部位不同
    # 美甲
    "基础手部修甲": 30,
    "凝胶美甲（单色）": 60,
    "凝胶美甲（设计款）": 75,
    "基础脚部修甲": 45,
    "凝胶脚甲（单色）": 60,
    "卸凝胶甲": 20,
}


def find_service(name):
    """找服务的正式名称。完全一样最好；不然找"互相包含"的（例如 "雾眉" → "半永久雾眉"）。"""
    if name in SERVICES:
        return name
    matches = [s for s in SERVICES if name in s or s in name]
    return matches[0] if len(matches) == 1 else None


# ---------- 怀孕（知识库「怀孕可以做哪些护理？」「孕妇按摩」） ----------
# 预约时代码会检查，不只靠 AI 记得：这是有健康风险的规则。
# 可以做的项目 → 最少要怀孕几周（0 = 没有限制）
PREGNANCY_ALLOWED = {
    "经典深层清洁护理": 0,
    "Aqua Peel 小气泡清洁护理": 0,
    "水光补湿护理": 0,
    "敏感肌修护护理": 0,
    "眼部护理": 0,
    "孕妇按摩": 16,                 # 要怀孕 4 个月（16 周）以上
    "蜜蜡脱毛": 0,
    "基础手部修甲": 0,
    "凝胶美甲（单色）": 0,
    "凝胶美甲（设计款）": 0,
    "基础脚部修甲": 0,
    "凝胶脚甲（单色）": 0,
    "卸凝胶甲": 0,
}
# 资料明确写"怀孕期间不能做"的项目
PREGNANCY_FORBIDDEN = {
    "RF 射频紧致护理",
    "IPL 光子脱毛",
    "半永久雾眉",
    "维他命 C 亮肤护理",
    "痘痘肌净化护理",
    "芳疗全身按摩（60 分钟）",
    "芳疗全身按摩（90 分钟）",
}
# 其他项目资料没写 → 保守处理：不让 AI 自己决定，请顾客先联系门市确认


def pregnancy_problem(service, weeks):
    """孕妇能不能订这项服务。可以就返回 None，不行就返回原因（中文）。"""
    if not weeks:
        return None
    if service in PREGNANCY_FORBIDDEN:
        return f"怀孕期间不能做「{service}」"
    if service not in PREGNANCY_ALLOWED:
        return f"资料没有写明怀孕期间能不能做「{service}」，请先联系门市同事确认"
    if weeks < PREGNANCY_ALLOWED[service]:
        return f"「{service}」要怀孕 {PREGNANCY_ALLOWED[service]} 周以上才能做，目前是 {weeks} 周"
    return None


# ---------- 美容师（知识库「美容师团队介绍」「指定美容师」） ----------
# 名字 → (其他叫法, 可以接预约的星期几；0 = 星期一)
OPEN_DAYS = {1, 2, 3, 4, 5, 6}
BEAUTICIANS = {
    "林嘉欣": ({"jessica", "jessica lim", "嘉欣", "老板娘", "创办人"}, {1, 3, 5}),   # 只限星期二、四、六
    "陈美玲": ({"melissa", "melissa tan", "美玲"}, OPEN_DAYS),
    "黄思敏": ({"vivian", "vivian wong", "思敏"}, OPEN_DAYS),
    "Nur Aisyah": ({"aisyah", "nur"}, OPEN_DAYS),
    "Priya Devi": ({"priya"}, OPEN_DAYS),
    "张雅婷": ({"tiffany", "tiffany teoh", "雅婷"}, OPEN_DAYS),
}


def find_beautician(name):
    """顾客说的名字 → 正式名字；对不上就返回 None。"""
    key = name.strip().lower()
    for official, (aliases, _) in BEAUTICIANS.items():
        if key == official.lower() or key in aliases:
            return official
    return None


def beautician_works_on(official, weekday):
    return weekday in BEAUTICIANS[official][1]


# ---------- 谁会做哪些服务（排班用） ----------
# ⚠️ 知识库只写了每位美容师的"专长"，没写清楚每个人能做哪些服务。
#    下面是按专长推出来的假设，门市可以照实际情况修改。
FACIAL_TEAM = ["陈美玲", "张雅婷", "林嘉欣"]
SERVICE_STAFF = {
    **{s: FACIAL_TEAM for s in ["经典深层清洁护理", "Aqua Peel 小气泡清洁护理", "水光补湿护理", "痘痘肌净化护理",
                               "敏感肌修护护理", "维他命 C 亮肤护理", "男士护理", "眼部护理"]},
    "抗衰紧致护理": ["林嘉欣", "陈美玲"],
    "RF 射频紧致护理": ["林嘉欣", "陈美玲"],
    **{s: ["Nur Aisyah"] for s in ["睫毛嫁接（自然款）", "睫毛嫁接（浓密款）", "睫毛补接", "睫毛自然卷翘",
                                   "修眉造型", "修眉加染眉", "眉毛定型", "半永久雾眉"]},
    **{s: ["黄思敏", "张雅婷"] for s in ["芳疗全身按摩（60 分钟）", "芳疗全身按摩（90 分钟）", "肩颈舒缓按摩",
                                     "孕妇按摩", "身体去角质"]},
    "IPL 光子脱毛": ["张雅婷"],
    **{s: ["Priya Devi"] for s in ["蜜蜡脱毛", "基础手部修甲", "凝胶美甲（单色）", "凝胶美甲（设计款）",
                                   "基础脚部修甲", "凝胶脚甲（单色）", "卸凝胶甲"]},
}
# 特殊房间：身体去角质要用有淋浴间的房间（知识库：只有 2 间）
SHOWER_SERVICES = {"身体去角质"}
SHOWER_ROOMS = 2
