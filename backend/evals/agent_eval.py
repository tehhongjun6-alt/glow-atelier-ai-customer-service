"""Agent 情境评估：真的 Gemini，假的数据库／日历／Gmail

要测的是"模型的判断"：该不该查空档、日期算对没有、有没有等顾客确认、该不该转人工。
所以只有 Gemini 是真的；其他全部换成 tests/conftest.py 的假环境：
- 不会写进真的 Google Calendar、Supabase，也不会寄信
- 时间固定在 2026-11-10 星期二 09:00，"明天"永远是 11-11，评分才能写死
- 每个情境开始时都是干净的假数据库（需要的话先放入 setup 的预约）
知识库检索（RAG）还是用真的 Supabase，只读不写。

避开 Gemini 免费额度限制：
- 每次调用之间至少隔 MIN_SECONDS_BETWEEN_CALLS 秒（约每分钟 12 次）
- 遇到 429／503 会等一下再试（agent.py 本来就有）
- 每跑完一次就存档，中断了重跑会接续；今天额度用完，明天接着跑

用法（在 backend 资料夹里）：
    python -m evals.agent_eval            接续上次
    python -m evals.agent_eval --fresh    删掉旧结果重跑
    python -m evals.agent_eval --only B1 H4   只跑某几个情境
"""

import argparse
import json
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import agent.agent as agent_module
from agent.agent import FALLBACK_ANSWER, MODEL, CustomerServiceAgent
from booking import bookings, notifications, rules
from booking.services import SERVICES
from handoff import handoffs
from tests.conftest import NOW, FakeSupabase

EVAL_DIR = Path(__file__).resolve().parent
SCENARIOS_FILE = EVAL_DIR / "agent_scenarios.json"
RESULTS_FILE = EVAL_DIR / "results" / "agent" / "runs.jsonl"
REPORT_FILE = EVAL_DIR / "report_agent.md"
MIN_SECONDS_BETWEEN_CALLS = 5.0


# ---------- 控制速度：包住 Gemini 的调用 ----------
_last_call = [0.0]
_real_generate = agent_module.client.models.generate_content
call_count = [0]


def _paced_generate(*args, **kwargs):
    wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    call_count[0] += 1
    return _real_generate(*args, **kwargs)


# ---------- 假环境 ----------
class FakeWorld:
    """一个情境一个世界：假数据库 + 假日历 + 假寄信。"""

    def __init__(self, setup):
        self.db = FakeSupabase()
        self.calendar = []
        self.emails = []
        for b in setup or []:
            start = rules.parse_datetime(b["date"], b["time"])
            end = start + timedelta(minutes=SERVICES[b["service"]])
            self.db.tables["bookings"].add({
                "booking_code": b["booking_code"], "customer_name": "陈小明", "phone": "0128886666",
                "email": "chen@example.com", "service": b["service"], "beautician": None, "notes": None,
                "start_at": start.isoformat(), "end_at": end.isoformat(), "google_event_id": "event-setup",
            })

    def install(self):
        bookings.supabase = self.db
        handoffs.supabase = self.db
        bookings.google_calendar.create_event = lambda row: (self.calendar.append(("create", row["booking_code"])) or "event-new")
        bookings.google_calendar.update_event_time = lambda eid, s, e: self.calendar.append(("update", eid))
        bookings.google_calendar.delete_event = lambda eid: self.calendar.append(("delete", eid))
        notifications.send_for = lambda kind, result: (self.emails.append(kind) or (True, None))

    def bookings(self, status="confirmed"):
        return [r for r in self.db.tables["bookings"].rows if r["status"] == status]

    def handoffs(self):
        return self.db.tables["handoffs"].rows


def install_globals():
    rules.now = lambda: NOW
    agent_module.client.models.generate_content = _paced_generate


# ---------- 跑一个情境 ----------
def run_scenario(scenario):
    world = FakeWorld(scenario.get("setup"))
    world.install()
    bot = CustomerServiceAgent(channel="chat")
    turns = []
    for message in scenario["turns"]:
        result = bot.chat(message)
        turns.append({
            "message": message,
            "answer": result["answer"],
            "trace": result["trace"],
            "handoffs_after": len(world.handoffs()),
        })
    state = {
        "bookings_confirmed": len(world.bookings()),
        "bookings": {r["booking_code"]: bookings.describe(r) for r in world.db.tables["bookings"].rows},
        "handoffs": [{"reason": h["reason"], "contact": h["contact"]} for h in world.handoffs()],
        "calendar": world.calendar,
        "emails": world.emails,
    }
    return turns, state


# ---------- 评分：全部用代码检查 ----------
def calls(turns):
    return [step for t in turns for step in t["trace"]]


def grade(scenario, turns, state):
    c = scenario["checks"]
    steps = calls(turns)
    names = [s["tool"] for s in steps]
    answers = [t["answer"] for t in turns]
    failures = []

    if any(s["tool"] == "error" for s in steps) or FALLBACK_ANSWER in answers:
        failures.append("Gemini 出错（429／503 或没有回答），这次不算数")

    for tool in c.get("called", []):
        if tool not in names:
            failures.append(f"应该调用 {tool}，但没有")
    for tool in c.get("not_called", []):
        if tool in names:
            failures.append(f"不应该调用 {tool}，但调用了")

    for tool, expected in c.get("args", {}).items():
        made = [s["args"] for s in steps if s["tool"] == tool]
        ok = any(all(str(a.get(k, "")).find(v) != -1 for k, v in expected.items()) for a in made)
        if made and not ok:
            failures.append(f"{tool} 的参数不对：期望 {expected}，实际 {made}")

    if "bookings" in c and state["bookings_confirmed"] != c["bookings"]:
        failures.append(f"有效预约应该是 {c['bookings']} 笔，实际 {state['bookings_confirmed']} 笔")

    for code, expected in c.get("booking_state", {}).items():
        actual = state["bookings"].get(code, {})
        diff = {k: actual.get(k) for k, v in expected.items() if actual.get(k) != v}
        if diff:
            failures.append(f"{code} 应该是 {expected}，实际 {diff}")

    if "handoffs" in c and len(state["handoffs"]) != c["handoffs"]:
        failures.append(f"工单应该是 {c['handoffs']} 张，实际 {len(state['handoffs'])} 张")
    if "handoff_reason" in c and state["handoffs"]:
        reason = state["handoffs"][0]["reason"]
        if reason not in c["handoff_reason"]:
            failures.append(f"工单原因应该是 {c['handoff_reason']} 之一，实际 {reason}")
    if "handoffs_after_turn_1" in c and turns[0]["handoffs_after"] != c["handoffs_after_turn_1"]:
        failures.append("还没拿到联系方式，第一轮就开了工单")

    final = answers[-1]
    if c.get("answer_any") and not any(w in final for w in c["answer_any"]):
        failures.append(f"最后的回答应该提到 {c['answer_any']} 其中一个")
    if c.get("answer_any_turn") and not any(w in a for a in answers for w in c["answer_any_turn"]):
        failures.append(f"对话中应该提到 {c['answer_any_turn']}")
    for word in c.get("answer_none", []):
        if word in final:
            failures.append(f"最后的回答不应该出现「{word}」")

    invalid = failures and failures[0].startswith("Gemini 出错")
    return failures, invalid


# ---------- 存档、接续 ----------
def load_runs():
    if not RESULTS_FILE.exists():
        return []
    return [json.loads(line) for line in RESULTS_FILE.read_text(encoding="utf-8").splitlines() if line]


def save_run(run):
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")


# ---------- 报告 ----------
def build_report(scenarios, runs):
    by_id = defaultdict(list)
    for r in runs:
        if not r["invalid"]:
            by_id[r["id"]].append(r)

    lines = [f"# Agent 情境评估报告（模型：{MODEL}）", "",
             "真的 Gemini + 假的数据库／日历／Gmail；现在时间固定为 2026-11-10 星期二 09:00。全部用代码评分。", ""]

    lines += ["## 按类别", "", "| 类别 | 通过 / 运行次数 | 通过率 |", "|---|---|---|"]
    cats = defaultdict(lambda: [0, 0])
    for s in scenarios:
        for r in by_id[s["id"]]:
            cats[s["category"]][1] += 1
            cats[s["category"]][0] += r["passed"]
    total_pass = sum(v[0] for v in cats.values())
    total = sum(v[1] for v in cats.values())
    for cat, (p, n) in cats.items():
        lines.append(f"| {cat} | {p} / {n} | {p / n:.0%} |")
    if total:
        lines.append(f"| **全部** | **{total_pass} / {total}** | **{total_pass / total:.0%}** |")
    lines.append("")

    lines += ["## 每个情境", "", "| 情境 | 类别 | 测什么 | 结果 |", "|---|---|---|---|"]
    for s in scenarios:
        rs = by_id[s["id"]]
        if not rs:
            lines.append(f"| {s['id']} | {s['category']} | {s['title']} | 还没跑 |")
            continue
        p = sum(r["passed"] for r in rs)
        mark = "✅" if p == len(rs) else ("❌" if p == 0 else "⚠️")
        lines.append(f"| {s['id']} | {s['category']} | {s['title']} | {mark} {p}/{len(rs)} |")
    lines.append("")

    failed = [r for r in runs if not r["passed"] and not r["invalid"]]
    if failed:
        lines += ["## 没通过的运行（逐一检查：是模型的问题，还是题目写得太严？）", ""]
        for r in failed:
            lines += [f"### {r['id']}（第 {r['attempt']} 次）", ""]
            lines += [f"- ❌ {f}" for f in r["failures"]]
            lines.append("")
            for t in r["turns"]:
                lines.append(f"**顾客：** {t['message']}")
                for s in t["trace"]:
                    lines.append(f"> 🔧 `{s['tool']}` {json.dumps(s['args'], ensure_ascii=False)} → {'；'.join(s['results'][:2])}")
                lines += ["", f"**AI：** {t['answer'][:600]}", ""]
    invalid = [r for r in runs if r["invalid"]]
    if invalid:
        lines += [f"另外有 {len(invalid)} 次因为 Gemini 出错不算数（重跑会自动补上）。", ""]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="删掉旧结果重跑")
    parser.add_argument("--only", nargs="*", help="只跑这几个情境，例如 B1 H4")
    args = parser.parse_args()

    scenarios = json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))["scenarios"]
    if args.fresh:
        RESULTS_FILE.unlink(missing_ok=True)
    runs = load_runs()
    done = {(r["id"], r["attempt"]) for r in runs if not r["invalid"]}
    install_globals()

    todo = [(s, n) for s in scenarios for n in range(1, s.get("repeat", 1) + 1)
            if (s["id"], n) not in done and (not args.only or s["id"] in args.only)]
    print(f"要跑 {len(todo)} 次（已完成 {len(done)} 次）；每次调用 Gemini 至少间隔 {MIN_SECONDS_BETWEEN_CALLS:.0f} 秒")

    started = time.time()
    for i, (s, attempt) in enumerate(todo, 1):
        turns, state = run_scenario(s)
        failures, invalid = grade(s, turns, state)
        run = {"id": s["id"], "attempt": attempt, "passed": not failures, "invalid": bool(invalid),
               "failures": failures, "turns": turns, "state": state}
        save_run(run)
        runs = [r for r in runs if not (r["id"] == s["id"] and r["attempt"] == attempt)] + [run]
        mark = "⏭️ 不算数" if invalid else ("✅" if not failures else "❌ " + "；".join(failures))
        print(f"[{i}/{len(todo)}] {s['id']} 第 {attempt} 次：{mark}   （累计调用 {call_count[0]} 次，{time.time() - started:.0f} 秒）",
              flush=True)

    REPORT_FILE.write_text(build_report(scenarios, runs), encoding="utf-8")
    print(f"\n报告已存到 {REPORT_FILE}")


if __name__ == "__main__":
    main()
