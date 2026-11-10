"""任务 2.6：检索

三种检索方法：
- vector_search()：向量检索（理解意思）
- keyword_search()：关键词检索（对准字词）
- hybrid_search()：混合检索（两种一起用，再用 RRF 合并排名）

真正的比较和排序都在 Supabase 的 SQL 函数里完成，
Python 只负责把问题送过去、把结果拿回来。
"""

from rag.embeddings import embed_query
from rag.store import supabase


def vector_search(question, top_k=5, embedding=None):
    """2.6a 向量检索：把问题变成向量，找出意思最接近的几块。

    embedding：已经算好的问题向量（评估时用，避免同一题重复调用 Gemini）。
    """
    response = supabase.rpc("match_documents", {
        "query_embedding": embedding or embed_query(question),
        "match_count": top_k,
        "min_similarity": 0.0,
    }).execute()
    return response.data


def keyword_search(question, top_k=5):
    """2.6b 关键词检索：用 PGroonga 把问题切成词（中文按两个字一组），
    找出包含最多这些词的块。"""
    response = supabase.rpc("keyword_search", {
        "query_text": question,
        "match_count": top_k,
    }).execute()
    return response.data


def hybrid_search(question, top_k=5, candidates=20, embedding=None):
    """2.6c 混合检索：向量和关键词各取前 20 名，用 RRF 合并成一份排名。

    RRF 分数 = 1/(60 + 向量名次) + 1/(60 + 关键词名次)
    两边都排得前面的块，分数最高。
    """
    response = supabase.rpc("hybrid_search", {
        "query_text": question,
        "query_embedding": embedding or embed_query(question),
        "match_count": top_k,
        "candidate_count": candidates,
    }).execute()
    return response.data


# ---------- 测试：比较三种检索方法 ----------
if __name__ == "__main__":
    questions = [
        "雾眉几钱？",
        "我有 pimple 很多，应该做什么 facial？",
        "can book 星期一吗？",
        "有没有 student discount？",
        "Lash Lift 可以维持多久？",
    ]

    for q in questions:
        print(f"\n❓ {q}")

        print("   【向量】", " | ".join(r["title"] for r in vector_search(q, 3)))
        print("   【关键词】", " | ".join(r["title"] for r in keyword_search(q, 3)))

        print("   【混合 RRF】")
        for r in hybrid_search(q, 3):
            v = r["vector_rank"] or "-"
            k = r["keyword_rank"] or "-"
            print(f"      {r['rrf_score']:.4f}  {r['title']}（向量第 {v} 名，关键词第 {k} 名）")
