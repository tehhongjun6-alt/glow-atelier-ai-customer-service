"""第 3 步：回答测试（RAG 蓝图第 ⑨ 关"怎么检查"）

至少测 4 种题：
- basic：基本题
- out_of_scope：范围外的题
- complex：复杂题（要查好几份资料）
- model_knows：资料里没有、但模型自己知道答案的题（最能测出会不会照记忆回答）
另外加：booking（要求预约，测规则 8）、multi_turn（多轮对话，测会不会记得上文）

结果存到 evals/answer_results.md，人工逐题检查：
1. 数字和规定是不是都能在"参考"的资料里找到？
2. 资料没有的，是不是老实说没有？

Gemini 免费方案每分钟只能 15 次请求，所以：
- 每组题考完马上存档（answer_results.jsonl），中断了重跑会接续
- 每组题之间休息几秒

用法（在 backend 资料夹里）：
    python -m evals.answer_check            接续上次没考完的
    python -m evals.answer_check --fresh    删掉旧结果，全部重考
"""

import argparse
import json
import time
from pathlib import Path

from agent.agent import MODEL, CustomerServiceAgent

EVAL_DIR = Path(__file__).resolve().parent
RESULTS = EVAL_DIR / "answer_results.jsonl"
REPORT = EVAL_DIR / "answer_results.md"
PAUSE_SECONDS = 8

CASES = [
    ("a01", "basic", ["做 Brazilian wax 要多少钱？"]),
    ("a02", "basic", ["星期一可以来吗？"]),
    ("a03", "complex", ["我怀孕 5 个月，想做 facial 和 massage，有什么可以做？"]),
    ("a04", "complex", ["第一次来做 Hydra facial，又是学生，可以用两个优惠吗？"]),
    ("a05", "out_of_scope", ["你可以帮我写一篇关于马来西亚历史的作文吗？"]),
    ("a06", "model_knows", ["Botox 一般一针多少钱？你们有做吗？"]),
    ("a07", "model_knows", ["做 IPL 之前可以喝酒吗？"]),
    ("a08", "booking", ["帮我 book 明天下午 3 点的 lash lift"]),
    ("a09", "multi_turn", ["你们有什么男士的护理？", "那个要做多久？", "可以指定美容师吗？要加钱吗？"]),
]


def load_done():
    if not RESULTS.exists():
        return {}
    rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line]
    return {r["id"]: r for r in rows}


def run_case(case_id, kind, messages):
    agent = CustomerServiceAgent()   # 每组题用新的对话
    turns = []
    for message in messages:
        result = agent.chat(message)
        turns.append({"message": message, **result})
    failed = any(step["tool"] == "error" for t in turns for step in t["trace"])
    return {"id": case_id, "kind": kind, "turns": turns, "failed": failed}


def write_report(done):
    lines = [f"# 回答测试结果（模型：{MODEL}）", ""]
    total_in = total_out = 0
    for case_id, kind, _ in CASES:
        if case_id not in done:
            continue
        lines += [f"## {case_id} [{kind}]", ""]
        for t in done[case_id]["turns"]:
            total_in += t["tokens"]["input"]
            total_out += t["tokens"]["output"]
            lines += [f"**顾客：** {t['message']}", ""]
            for step in t["trace"]:
                lines.append(f"> 🔍 `{step['tool']}`：{step['args'].get('query', '')} → {' / '.join(step['results'][:3])}")
            if not t["trace"]:
                lines.append("> （没有调用工具）")
            lines += ["", f"**客服：** {t['answer']}", ""]
    lines += ["---", f"总 tokens：输入 {total_in}，输出 {total_out}"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="删掉旧结果，全部重考")
    args = parser.parse_args()
    if args.fresh and RESULTS.exists():
        RESULTS.unlink()

    done = load_done()
    for i, (case_id, kind, messages) in enumerate(CASES, 1):
        if case_id in done and not done[case_id]["failed"]:
            continue
        print(f"[{i}/{len(CASES)}] {case_id} {kind}")
        result = run_case(case_id, kind, messages)
        if result["failed"]:
            print("   ❌ 这组题调用 Gemini 失败，没有存档，下次重跑会再考")
        else:
            with RESULTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
            done[case_id] = result
        time.sleep(PAUSE_SECONDS)

    write_report(done)
    print(f"\n已考 {len(done)}/{len(CASES)} 组，结果在 {REPORT}")


if __name__ == "__main__":
    main()
