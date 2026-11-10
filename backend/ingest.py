"""导入知识库：读文件 → 切块 → 做向量 → 存进 Supabase

用法（在 backend 资料夹里）：
    python ingest.py

知识库内容改了之后，重新运行一次就会更新。
"""

from pathlib import Path

from rag.chunker import chunk_file
from rag.embeddings import chunk_to_text, embed_documents
from rag.store import replace_source

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"


def main():
    files = sorted(KNOWLEDGE_BASE_DIR.glob("*.md"))
    print(f"在 knowledge_base 找到 {len(files)} 个文件\n")

    for path in files:
        print(f"📄 {path.name}")

        chunks = chunk_file(path)
        print(f"   1. 切块：{len(chunks)} 块")

        vectors = embed_documents([chunk_to_text(c) for c in chunks])
        print(f"   2. 向量：{len(vectors)} 个，每个 {len(vectors[0])} 维")

        count = replace_source(path.name, chunks, vectors)
        print(f"   3. 写入 Supabase：{count} 行 ✅\n")

    print("导入完成！")


if __name__ == "__main__":
    main()
