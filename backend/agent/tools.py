"""Agent 可以使用的工具

- 第 3 步：search_knowledge_base（查知识库）
- 第 4 步：预约
    只读：check_availability、find_bookings
    要确认：request_booking、request_cancel、request_reschedule → confirm_action（见 agent/actions.py）
- 第 6 步：request_human_handoff（转人工，建立工单）

每个工具要有两样东西：
1. 声明（FunctionDeclaration）：告诉模型"有这个工具、它做什么、要传什么参数"
2. 实作（Python 函数）：模型决定调用时，真正执行的代码
   第一个参数 agent：这位顾客的 Agent（agent.gate 是两段式确认；转人工要用对话记录和渠道）
   返回 (给模型看的结果, 给 trace 记录的简短说明)
"""

from google.genai import types

from booking import bookings
from booking.services import SERVICES
from handoff import handoffs
from rag.search import hybrid_search

TOP_K = 5   # 交给模型几块资料（2.7 评估：Hit@5 = 1.00）


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": required}


DATE = {"type": "string", "description": "日期，格式 YYYY-MM-DD（吉隆坡时间）"}
TIME = {"type": "string", "description": "开始时间，24 小时制 HH:MM，例如 15:00"}
PHONE = {"type": "string", "description": "顾客的手机号码"}
CODE = {"type": "string", "description": "预约编号，例如 GA-7K3M9Q"}
SERVICE = {"type": "string", "description": "服务名称，必须是这些之一：" + "、".join(SERVICES)}
CONFIRM_NOTE = "这个工具只检查和暂存，不会真的执行；要把摘要给顾客确认，顾客下一则讯息同意后再调用 confirm_action。"


# ---------- 1. 声明：模型看到的工具说明 ----------
DECLARATIONS = [
    types.FunctionDeclaration(
        name="search_knowledge_base",
        description=(
            "搜索 Glow Atelier 光研美学的知识库，包括服务项目、价格、配套与优惠、"
            "预约规则、营业时间、地址、付款方式、常见问题等。"
            "回答任何和 Glow Atelier 有关的问题之前，都要先用这个工具查资料。"
        ),
        parameters_json_schema=_schema({
            "query": {
                "type": "string",
                "description": "搜索句。可以把顾客的口语改写得更清楚，例如'雾眉 价格'、'星期一 营业时间'。",
            }
        }, ["query"]),
    ),
    types.FunctionDeclaration(
        name="check_availability",
        description="查某一天某项服务还有哪些时间可以预约。顾客想预约或改期时，先用这个查空档。",
        parameters_json_schema=_schema({"service": SERVICE, "date": DATE}, ["service", "date"]),
    ),
    types.FunctionDeclaration(
        name="find_bookings",
        description="用手机号码查顾客接下来的预约。顾客想取消或改期、但不记得预约编号时使用。",
        parameters_json_schema=_schema({"phone": PHONE}, ["phone"]),
    ),
    types.FunctionDeclaration(
        name="request_booking",
        description="准备一笔新预约（检查规则和名额）。" + CONFIRM_NOTE,
        parameters_json_schema=_schema({
            "customer_name": {"type": "string", "description": "顾客全名"},
            "phone": PHONE,
            "email": {"type": "string", "description": "顾客电邮，用来寄确认信"},
            "service": SERVICE,
            "date": DATE,
            "time": TIME,
            "beautician": {"type": "string", "description": "指定的美容师名字（顾客没指定就不要填）"},
            "notes": {"type": "string", "description": "备注，例如过敏、怀孕周数、第一次来（没有就不要填）"},
        }, ["customer_name", "phone", "email", "service", "date", "time"]),
    ),
    types.FunctionDeclaration(
        name="request_cancel",
        description="准备取消一笔预约（核对预约编号和手机号码）。" + CONFIRM_NOTE,
        parameters_json_schema=_schema({"booking_code": CODE, "phone": PHONE}, ["booking_code", "phone"]),
    ),
    types.FunctionDeclaration(
        name="request_reschedule",
        description="准备把预约改到新的日期和时间（核对资料、检查新时段）。" + CONFIRM_NOTE,
        parameters_json_schema=_schema(
            {"booking_code": CODE, "phone": PHONE, "new_date": DATE, "new_time": TIME},
            ["booking_code", "phone", "new_date", "new_time"],
        ),
    ),
    types.FunctionDeclaration(
        name="request_human_handoff",
        description=(
            "把顾客转给门市同事（真人）处理，会在后台建立一张工单。"
            "用于：顾客要求真人、投诉或不满意、护理后过敏或身体不适、退款或配套纠纷、"
            "资料里查不到而顾客需要答案、或工具做不到的要求（例如新娘／团体预约）。"
        ),
        parameters_json_schema=_schema({
            "reason": {
                "type": "string",
                "enum": ["complaint", "medical", "refund", "request_human", "cannot_answer", "other"],
                "description": "complaint=投诉不满意；medical=过敏或身体不适；refund=退款配套纠纷；request_human=顾客要求真人；cannot_answer=资料里没有；other=其他",
            },
            "summary": {"type": "string", "description": "给同事看的摘要：顾客是谁、发生什么事、想要什么、已经做了什么（2–4 句）"},
            "contact": {"type": "string", "description": "顾客的电话或电邮（同事要用来联络顾客）。电邮渠道可以不填，会自动用寄件人地址"},
            "customer_name": {"type": "string", "description": "顾客名字（知道的话）"},
            "booking_code": {"type": "string", "description": "相关的预约编号（有的话）"},
        }, ["reason", "summary"]),
    ),
    types.FunctionDeclaration(
        name="confirm_action",
        description="顾客已经在最新一则讯息明确同意之后，执行刚才暂存的预约／取消／改期。",
        parameters_json_schema=_schema({}, []),
    ),
]

TOOLS = [types.Tool(function_declarations=DECLARATIONS)]


# ---------- 2. 实作：真正执行的代码 ----------
def search_knowledge_base(agent, query):
    """用混合检索找前 5 块，整理成有编号的资料给模型读（蓝图第 ⑧ 关）。"""
    rows = hybrid_search(query, top_k=TOP_K)
    if not rows:
        return {"found": False, "documents": "知识库里没有找到相关资料。"}, []

    blocks = []
    for i, r in enumerate(rows, 1):
        blocks.append(f"[资料{i}] 标题：{r['title']}（分类：{r['category']}）\n{r['content']}")

    titles = [r["title"] for r in rows]
    return {"found": True, "documents": "\n\n".join(blocks)}, titles


def _short(result):
    """trace 里显示的一行说明。"""
    if not result.get("success"):
        return [f"❌ {result.get('error')}"]
    if result.get("status") == "waiting_for_customer_confirmation":
        return ["⏸️ 已暂存，等顾客确认"]
    if "booking" in result:
        b = result["booking"]
        return [f"✅ {result.get('action', '')} {b['booking_code']} {b['service']} {b['date']} {b['time']} ({b['status']})"]
    if "available_times" in result:
        return [f"✅ {result['date']} 有 {len(result['available_times'])} 个时段"]
    return [f"✅ 找到 {len(result.get('bookings', []))} 笔预约"]


def check_availability(agent, service, date):
    result = bookings.check_availability(service, date)
    return result, _short(result)


def find_bookings(agent, phone):
    result = bookings.find_bookings(phone)
    return result, _short(result)


def request_booking(agent, **kwargs):
    row, error = bookings.prepare_booking(**kwargs)
    result = error or agent.gate.stage("booking", row, bookings.describe(row))
    return result, _short(result)


def request_cancel(agent, booking_code, phone):
    row, error = bookings.prepare_cancel(booking_code, phone)
    if error:
        return error, _short(error)
    summary = {**bookings.describe(row), "within_24_hours": bookings.rules.hours_until(
        bookings._to_local(row["start_at"])) < 24}
    result = agent.gate.stage("cancel", row, summary)
    return result, _short(result)


def request_reschedule(agent, booking_code, phone, new_date, new_time):
    plan, error = bookings.prepare_reschedule(booking_code, phone, new_date, new_time)
    if error:
        return error, _short(error)
    new = bookings.describe({**plan["row"], "start_at": plan["start_at"], "end_at": plan["end_at"]})
    summary = {"old": bookings.describe(plan["row"]), "new": new,
               "within_24_hours": bookings.rules.hours_until(bookings._to_local(plan["row"]["start_at"])) < 24}
    result = agent.gate.stage("reschedule", plan, summary)
    return result, _short(result)


def confirm_action(agent):
    result = agent.gate.confirm()
    return result, _short(result)


def request_human_handoff(agent, reason, summary, contact=None, customer_name=None, booking_code=None):
    """第 6 步：建立转人工工单。顾客自己要求转人工，不需要两段式确认（不会改动顾客的资料）。"""
    contact = contact or agent.customer_email
    if not contact:
        result = {"success": False, "error": "还没有顾客的联系方式。先问顾客的电话或电邮，再转人工。"}
        return result, [f"❌ {result['error']}"]
    result = handoffs.create_handoff(
        channel=agent.channel, reason=reason, summary=summary, contact=contact,
        customer_name=customer_name, booking_code=booking_code,
        email_thread_id=agent.email_thread_id, conversation=agent.transcript(),
    )
    if not result["success"]:
        return result, [f"❌ {result['error']}"]
    h = result["handoff"]
    return result, [f"🙋 工单 {h['ticket_code']}（{h['reason_name']}，{h['status_name']}）"]


# 工具名称 → Python 函数
TOOL_FUNCTIONS = {
    "search_knowledge_base": search_knowledge_base,
    "check_availability": check_availability,
    "find_bookings": find_bookings,
    "request_booking": request_booking,
    "request_cancel": request_cancel,
    "request_reschedule": request_reschedule,
    "confirm_action": confirm_action,
    "request_human_handoff": request_human_handoff,
}
