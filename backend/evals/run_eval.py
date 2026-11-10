"""任务 2.7：检索评估（RAG 蓝图第 ⑩ 关）

只考"检索"，不考"回答"：
- 只用 Gemini embedding + Supabase，不花聊天模型的额度
- 答错时分得出是"找错"（检索）还是"写错"（回答，第 3 步再考）

指标（只算"资料里有答案"的题）：
- Hit@K：正确的块有没有出现在前 K 名（K = 1、3、5）。expected 里任何一块出现就算
- MRR：正确的块排第几名的倒数取平均。第 1 名 = 1，第 2 名 = 0.5，没找到 = 0
- Full@5：need_all=true 的题，expected 里每一块都要在前 5 名才算（需要两块资料才答得完整的题）

题库：
- questions.json        第一批 30 题（基础题）
- questions_hard.json   第二批 30 题（难题，有 difficulty 和 need_all）

用法（在 backend 资料夹里）：
    python -m evals.run_eval                                   考第一批，接续上次
    python -m evals.run_eval --fresh                           考第一批，删掉旧结果重考
    python -m evals.run_eval --questions questions_hard.json --fresh   只考难题
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from rag.chunker import chunk_file
from rag.embeddings import embed_query
from rag.search import hybrid_search, vector_search

EVAL_DIR = Path(__file__).resolve().parent
KNOWLEDGE_FILE = EVAL_DIR.parent.parent / "knowledge_base" / "glow_atelier.md"
CACHE_FILE = EVAL_DIR / "cache" / "embeddings.json"

CANDIDATES = 20   # 每种方法看前 20 名（算 MRR 用）
DIFFICULTY_ORDER = ["medium", "hard", "very_hard"]


def paths_for(questions_name):
    """第一批题沿用原本的位置；其他题库各自一个结果资料夹和报告。"""
    stem = Path(questions_name).stem
    if stem == "questions":
        return EVAL_DIR / "results", EVAL_DIR / "report.md"
    suffix = stem.removeprefix("questions_")
    return EVAL_DIR / "results" / suffix, EVAL_DIR / f"report_{suffix}.md"


# ---------- 要比较的检索方法 ----------
def run_vector(question, embedding):
    rows = vector_search(question, top_k=CANDIDATES, embedding=embedding)
    return [r["title"] for r in rows], (rows[0]["similarity"] if rows else None)


def run_hybrid(question, embedding):
    rows = hybrid_search(question, top_k=CANDIDATES, candidates=CANDIDATES, embedding=embedding)
    return [r["title"] for r in rows], None


CONFIGS = {
    "vector": run_vector,   # 只用向量
    "hybrid": run_hybrid,   # 向量 + 关键词 + RRF
}


# ---------- 考试前检查题库：expected 的标题打错，会被误算成答错 ----------
def validate(questions):
    titles = {c["title"] for c in chunk_file(KNOWLEDGE_FILE)}
    problems = [f"{q['id']}：找不到标题「{t}」" for q in questions for t in q["expected"] if t not in titles]
    ids = [q["id"] for q in questions]
    problems += [f"题号重复：{i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if problems:
        raise SystemExit("题库有问题，先修好再考：\n  " + "\n  ".join(problems))


# ---------- 向量缓存：同一题只调用一次 Gemini ----------
def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def get_embedding(question, cache):
    if question not in cache:
        cache[question] = embed_query(question)
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    return cache[question]


# ---------- 考试：每题考完马上存档，中断了可以接续 ----------
def load_done(results_dir, config):
    path = results_dir / f"{config}.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {r["id"]: r for r in rows}


def run_config(config, questions, cache, results_dir):
    done = load_done(results_dir, config)
    path = results_dir / f"{config}.jsonl"
    todo = [q for q in questions if q["id"] not in done]
    print(f"[{config}] 已考 {len(done)} 题，这次要考 {len(todo)} 题")

    for q in todo:
        embedding = get_embedding(q["question"], cache)
        titles, top1_similarity = CONFIGS[config](q["question"], embedding)

        # 每一块正确资料各排第几名（没进前 20 名 = None）
        ranks = {t: (titles.index(t) + 1 if t in titles else None) for t in q["expected"]}
        found = [r for r in ranks.values() if r]
        rank = min(found) if found else None   # 第一个出现的正确块

        result = {**q, "rank": rank, "ranks": ranks, "top_titles": titles[:5], "top1_similarity": top1_similarity}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        done[q["id"]] = result

    return [done[q["id"]] for q in questions]


# ---------- 算分 ----------
def metrics(results):
    answerable = [r for r in results if r["expected"]]
    n = len(answerable)
    if n == 0:
        return None

    def hit(k):
        return sum(1 for r in answerable if r["rank"] and r["rank"] <= k) / n

    mrr = sum(1 / r["rank"] for r in answerable if r["rank"]) / n
    return {"n": n, "hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "mrr": mrr}


def full_at_5(r):
    return all(rank and rank <= 5 for rank in r["ranks"].values())


def metric_table(title, groups, all_results):
    lines = [f"## {title}", "", "| 组别 | 方法 | 题数 | Hit@1 | Hit@3 | Hit@5 | MRR |", "|---|---|---|---|---|---|---|"]
    for group_name, keep in groups:
        for config, results in all_results.items():
            m = metrics([r for r in results if keep(r)])
            if m:
                lines.append(f"| {group_name} | {config} | {m['n']} | {m['hit@1']:.2f} | {m['hit@3']:.2f} | "
                             f"{m['hit@5']:.2f} | {m['mrr']:.2f} |")
    return lines + [""]


def build_report(all_results, questions_name):
    sample = next(iter(all_results.values()))
    lines = [f"# 检索评估报告（题库：{questions_name}）", ""]
    lines += ["只算资料里有答案的题。Hit@K = 正确的块在前 K 名的比例；MRR = 正确的块名次倒数的平均。", ""]

    lines += metric_table("按题组（dev／holdout）", [(s, lambda r, s=s: r["split"] == s) for s in ["dev", "holdout"]], all_results)

    levels = [d for d in DIFFICULTY_ORDER if any(r.get("difficulty") == d for r in sample)]
    if levels:
        lines += metric_table("按难度", [(d, lambda r, d=d: r.get("difficulty") == d) for d in levels], all_results)

    need_all = [r for r in sample if r.get("need_all")]
    if need_all:
        lines += ["## 需要两块以上资料的题（Full@5：每一块都要进前 5 名）", "",
                  "| 方法 | 题号 | 问题 | 每块资料的名次 | Full@5 |", "|---|---|---|---|---|"]
        for config, results in all_results.items():
            for r in (x for x in results if x.get("need_all")):
                ranks = "、".join(f"{t}：{rank or '没进前 20'}" for t, rank in r["ranks"].items())
                lines.append(f"| {config} | {r['id']} | {r['question']} | {ranks} | {'✅' if full_at_5(r) else '❌'} |")
        lines.append("")

    lines += ["## 错题（正确的块没排第 1 名）", "", "| 方法 | 题号 | 难度 | 问题 | 正确答案排第几 | 实际前 3 名 |", "|---|---|---|---|---|---|"]
    for config, results in all_results.items():
        for r in results:
            if r["expected"] and r["rank"] != 1:
                rank = r["rank"] or f"前 {CANDIDATES} 名都没有"
                lines.append(f"| {config} | {r['id']} | {r.get('difficulty', '-')} | {r['question']} | {rank} | "
                             f"{' / '.join(r['top_titles'][:3])} |")
    lines.append("")

    # 门槛参考：向量检索第 1 名的相似度，有答案 vs 没答案（蓝图第 ⑦ 关）
    vec = all_results.get("vector", [])
    has = [r["top1_similarity"] for r in vec if r["expected"]]
    no = [r["top1_similarity"] for r in vec if not r["expected"]]
    if has and no:
        lines += ["## 门槛参考：向量检索第 1 名的相似度", "",
                  "| 组别 | 题数 | 最低 | 平均 | 最高 |", "|---|---|---|---|---|",
                  f"| 资料里有答案 | {len(has)} | {min(has):.3f} | {sum(has)/len(has):.3f} | {max(has):.3f} |",
                  f"| 资料里没答案 | {len(no)} | {min(no):.3f} | {sum(no)/len(no):.3f} | {max(no):.3f} |", ""]
        lines += ["没答案那组的**最高分**如果比有答案那组的**最低分**还低，两组之间就可以放一个门槛；"
                  "如果两组重叠，表示单靠相似度挡不住，要靠重排或第 ⑨ 关的规则。", ""]
        lines += ["| 题号 | 问题 | 第 1 名 | 相似度 |", "|---|---|---|---|"]
        for r in sorted(vec, key=lambda r: r["top1_similarity"]):
            tag = "" if r["expected"] else "（没答案）"
            lines.append(f"| {r['id']} | {r['question']}{tag} | {r['top_titles'][0]} | {r['top1_similarity']:.3f} |")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default="questions.json", help="题库文件（放在 evals 资料夹里）")
    parser.add_argument("--fresh", action="store_true", help="删掉这个题库的旧结果，全部重考")
    args = parser.parse_args()

    questions = json.loads((EVAL_DIR / args.questions).read_text(encoding="utf-8"))["questions"]
    validate(questions)

    results_dir, report_file = paths_for(args.questions)
    if args.fresh:
        for config in CONFIGS:   # 只删这个题库的结果，不会删到别的题库
            (results_dir / f"{config}.jsonl").unlink(missing_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    cache = load_cache()
    all_results = {config: run_config(config, questions, cache, results_dir) for config in CONFIGS}

    report = build_report(all_results, args.questions)
    report_file.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"报告已存到 {report_file}")


if __name__ == "__main__":
    main()
