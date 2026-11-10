"""任务 2.5：存储

把切好的块和向量写进 Supabase 的 documents 表。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# 读取 backend/.env 里的 SUPABASE_URL 和 SUPABASE_SECRET_KEY
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Secret key 可以绕过 RLS，所以只能在后端使用
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY"))


def replace_source(source, chunks, vectors):
    """把一个文件的块存进数据库。

    先删掉这个文件的旧资料，再写入新的。
    这样知识库改了内容、重新导入时，不会出现重复或过时的块。
    """
    # 1. 删掉旧的（例如 source = "glow_atelier.md" 的所有行）
    supabase.table("documents").delete().eq("source", source).execute()

    # 2. 把每一块和它的向量配成一行
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append({
            "source": chunk["source"],
            "category": chunk["category"],
            "title": chunk["title"],
            "content": chunk["content"],
            "embedding": vector,
        })

    # 3. 一次全部写进去
    supabase.table("documents").insert(rows).execute()
    return len(rows)
