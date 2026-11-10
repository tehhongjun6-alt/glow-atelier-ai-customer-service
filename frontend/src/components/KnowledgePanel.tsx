import { Children, isValidElement, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Chunk } from "../api";

// 把标题的文字变成 id，例如 "半永久雾眉（Ombre Powder Brows）" → "kb-半永久雾眉（Ombre Powder Brows）"
function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}
const idFor = (title: string) => "kb-" + title.trim();

type Section = { title: string; children: string[] };

function buildToc(markdown: string): Section[] {
  const toc: Section[] = [];
  for (const line of markdown.split("\n")) {
    if (line.startsWith("## ")) toc.at(-1)?.children.push(line.slice(3).trim());
    else if (line.startsWith("# ")) toc.push({ title: line.slice(2).trim(), children: [] });
  }
  return toc.filter((s) => s.children.length > 0);
}

export default function KnowledgePanel({ cited }: { cited: { title: string; nonce: number } | null }) {
  const [markdown, setMarkdown] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const docRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .knowledge()
      .then((k) => {
        setMarkdown(k.markdown);
        setChunks(k.chunks);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const toc = useMemo(() => buildToc(markdown), [markdown]);
  const lines = markdown ? markdown.trimEnd().split("\n").length : 0;

  // 跳到某一段，并标亮"这一段"（从 ## 标题到下一个标题之前）
  function jumpTo(title: string, highlight: boolean) {
    const doc = docRef.current;
    const heading = doc?.querySelector<HTMLElement>(`[id="${CSS.escape(idFor(title))}"]`);
    if (!doc || !heading) return;
    doc.querySelectorAll(".is-cited").forEach((el) => el.classList.remove("is-cited"));
    if (highlight) {
      let el: Element | null = heading;
      while (el) {
        el.classList.add("is-cited");
        el = el.nextElementSibling;
        if (!el || /^(H1|H2|HR)$/.test(el.tagName)) break;
      }
    }
    heading.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  useEffect(() => {
    if (cited && markdown) requestAnimationFrame(() => jumpTo(cited.title, true));
  }, [cited, markdown]);

  const heading =
    (Tag: "h1" | "h2") =>
    ({ children }: { children?: ReactNode }) => {
      const title = textOf(Children.toArray(children));
      return <Tag id={idFor(title)}>{children}</Tag>;
    };

  const q = filter.trim().toLowerCase();
  const visibleToc = toc
    .map((s) => ({ ...s, children: s.children.filter((c) => !q || c.toLowerCase().includes(q)) }))
    .filter((s) => s.children.length > 0);

  if (error) return <p className="panel-error">知识库载入失败：{error}</p>;
  if (!markdown) return <p className="panel-loading">正在载入知识库……</p>;

  return (
    <div className="kb">
      <aside className="kb-toc" aria-label="目录">
        <p className="kb-stats">
          <strong>{chunks.length}</strong> 块资料，原文 {lines} 行。每个 ## 标题是一块，AI 检索时一次拿最相关的 5 块。
        </p>
        <label className="sr-only" htmlFor="kb-filter">筛选目录</label>
        <input
          id="kb-filter"
          className="kb-filter"
          placeholder="找一段资料，例如 雾眉"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <nav>
          {visibleToc.map((section) => (
            <div key={section.title} className="toc-group">
              <p className="toc-category">{section.title}</p>
              <ul>
                {section.children.map((title) => (
                  <li key={title}>
                    <button
                      className={cited?.title === title ? "toc-link is-current" : "toc-link"}
                      onClick={() => jumpTo(title, false)}
                    >
                      {title}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {visibleToc.length === 0 && <p className="toc-empty">没有符合「{filter}」的段落</p>}
        </nav>
      </aside>

      <article className="kb-doc" ref={docRef}>
        {cited && (
          <p className="cited-banner">
            标亮的是 AI 刚才读到的「{cited.title}」
          </p>
        )}
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ h1: heading("h1"), h2: heading("h2") }}>
          {markdown}
        </ReactMarkdown>
      </article>
    </div>
  );
}
