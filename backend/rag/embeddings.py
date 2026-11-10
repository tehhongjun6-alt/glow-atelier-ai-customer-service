"""任务 2.4：向量（Embedding）

用 Gemini 把文字变成向量（768 个数字）。
意思相近的文字，向量也会很接近，所以"雾眉几钱？"和"半永久雾眉 RM 880"
用字不同，向量却很像，这就是 RAG 能"听懂"问题的原因。

两种用法，要配对：
- embed_documents()：知识库的内容用（task_type = RETRIEVAL_DOCUMENT）
- embed_query()：顾客的问题用（task_type = RETRIEVAL_QUERY）
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# 读取 backend/.env 里的 GEMINI_API_KEY
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = "gemini-embedding-001"
DIMENSIONS = 768        # 一定要和 Supabase 里的 vector(768) 一样
BATCH_SIZE = 50         # 一次最多送几段给 Gemini

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _embed(texts, task_type):
    """调用 Gemini，把一组文字变成一组向量。"""
    result = client.models.embed_content(
        model=MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=DIMENSIONS,
        ),
    )
    return [list(e.values) for e in result.embeddings]


def embed_documents(texts):
    """把知识库的很多段文字变成向量。分批送，避免一次送太多被拒绝。"""
    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        vectors.extend(_embed(batch, "RETRIEVAL_DOCUMENT"))
    return vectors


def embed_query(text):
    """把顾客的一个问题变成向量。"""
    return _embed([text], "RETRIEVAL_QUERY")[0]


def chunk_to_text(chunk):
    """一块要拿去做向量的文字：前面加上大分类，让向量带更多上下文。"""
    return f"{chunk['category']}\n{chunk['content']}"


def cosine_similarity(a, b):
    """余弦相似度：1 = 意思完全一样，0 = 完全无关。
    （这里自己算一次是为了理解原理；正式检索时由 Supabase 来算。）"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b)


# ---------- 测试：直接运行这个文件时才会执行 ----------
if __name__ == "__main__":
    from rag.chunker import chunk_file

    project_dir = Path(__file__).resolve().parent.parent.parent
    chunks = chunk_file(project_dir / "knowledge_base" / "glow_atelier.md")

    # 1. 把 69 块全部变成向量
    print(f"把 {len(chunks)} 块送去 Gemini 做向量……")
    vectors = embed_documents([chunk_to_text(c) for c in chunks])
    print(f"✅ 得到 {len(vectors)} 个向量，每个 {len(vectors[0])} 个数字")
    print(f"   第 1 块的前 5 个数字：{[round(x, 4) for x in vectors[0][:5]]}\n")

    # 2. 用几个问题测试：自己算相似度，看最接近的是哪几块
    questions = [
        "雾眉几钱？",
        "我有 pimple 很多，应该做什么 facial？",
        "can book 星期一吗？",
    ]
    for q in questions:
        q_vec = embed_query(q)
        scores = [(cosine_similarity(q_vec, v), c["title"]) for v, c in zip(vectors, chunks)]
        scores.sort(reverse=True)
        print(f"❓ {q}")
        for score, title in scores[:3]:
            print(f"   {score:.3f}  {title}")
        print()
