"""任务 2.3：切块（Chunking）

把一份 Markdown 知识库切成很多小块：
- 每个 `## 标题` 是一块
- 每块记下自己属于哪个 `# 大分类`
- 分隔线 `---` 不算内容
"""

import re
from pathlib import Path

# 用来去掉大分类前面的中文编号："一、公司介绍" → "公司介绍"
CATEGORY_NUMBER = re.compile(r"^[一二三四五六七八九十]+、\s*")


def chunk_markdown(text, source):
    chunks = []      # 存所有切好的块
    category = ""    # 目前的大分类（# 标题）
    title = ""       # 目前这一块的标题（## 标题）
    lines = []       # 目前这一块的内容，一行一行存

    def save_current():
        """把手上这一块存进 chunks（手上没有块就什么都不做）。"""
        if title:
            chunks.append({
                "source": source,
                "category": category,
                "title": title,
                "content": "\n".join(lines).strip(),
            })

    for line in text.splitlines():

        if line.startswith("## "):
            # 遇到新的 ## 标题：先存上一块，再开始新的一块
            save_current()
            title = line[3:].strip()
            lines = [line]

        elif line.startswith("# "):
            # 遇到新的 # 大分类：也要先存上一块！
            # 不然上一块会被算进新的分类，还会把后面的内容接进去（这就是"陷阱"）
            save_current()
            title = ""
            lines = []
            category = CATEGORY_NUMBER.sub("", line[2:].strip())

        elif line.strip() == "---":
            # 分隔线只是排版用的，不是内容
            continue

        elif title:
            lines.append(line)

    # 文件读完了，最后一块还在手上，要存起来
    save_current()

    return chunks


def chunk_file(path):
    """读一个 .md 文件并切块。"""
    path = Path(path)
    return chunk_markdown(path.read_text(encoding="utf-8"), path.name)


# ---------- 测试：直接运行这个文件时才会执行 ----------
if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent.parent.parent
    path = project_dir / "knowledge_base" / "glow_atelier.md"

    chunks = chunk_file(path)
    lengths = [len(c["content"]) for c in chunks]

    print(f"📄 {path.name}")
    print(f"切成 {len(chunks)} 块，每块 {min(lengths)}–{max(lengths)} 字，平均 {sum(lengths) // len(lengths)} 字\n")
    for i, c in enumerate(chunks, 1):
        print(f"{i:>2}. [{c['category']}] {c['title']}（{len(c['content'])} 字）")

    # 把切块结果存成 JSON，方便在 VS Code 里打开检查
    import json
    output_path = project_dir / "backend" / "output" / "chunks.json"
    output_path.parent.mkdir(exist_ok=True)          # 没有 output 资料夹就建一个
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"\n已存到 {output_path}")