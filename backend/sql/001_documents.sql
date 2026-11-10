-- ============================================================
-- 第 2 步：RAG 知识库的数据表
-- 这份 SQL 已经在 Supabase 运行过（两个 migration：
--   create_documents_table、create_match_documents_function）。
-- 留在这里是为了记录数据库长什么样；以后换新的 Supabase 项目，
-- 把整份贴进 SQL Editor 运行一次就能重建。
-- ============================================================


-- ---------- Part 1：开启向量功能 ----------
-- Postgres 本身不认识"向量"，要装 pgvector 扩展才能存向量、算相似度。
create extension if not exists vector with schema extensions;


-- ---------- Part 2：知识库表 ----------
-- 每一行 = 知识库里的一块（一个 ## 标题）
create table public.documents (
  id          bigint generated always as identity primary key,  -- 编号：数据库自动生成 1, 2, 3……
  source      text not null,                     -- 来自哪个文件，例如 glow_atelier.md
  category    text,                              -- 大分类，例如 "脸部护理（Facial）"
  title       text not null,                     -- ## 标题
  content     text not null,                     -- 这一块的完整内容
  embedding   extensions.vector(768) not null,   -- Gemini 生成的 768 维向量
  created_at  timestamptz not null default now() -- 建立时间，不填就自动用现在的时间
);

-- 开启 RLS（行级安全），但不加任何规则 = 前端的公开 key 读不到。
-- 只有后端用 Secret key（service_role）才能读写。
alter table public.documents enable row level security;

-- 向量索引（HNSW）：资料变多时，相似度搜索仍然很快。
-- vector_cosine_ops = 用"余弦距离"来比较向量。
create index documents_embedding_idx
  on public.documents using hnsw (embedding extensions.vector_cosine_ops);

-- 普通索引：重新导入时要"删掉某个文件的旧资料"，按 source 找会更快。
create index documents_source_idx on public.documents (source);


-- ---------- Part 3：检索函数 ----------
-- 传入"问题的向量"，返回最相似的几块。
-- Python 里这样调用：supabase.rpc("match_documents", {...})
create or replace function public.match_documents (
  query_embedding extensions.vector(768),  -- 问题的向量
  match_count     int   default 5,         -- 最多返回几块
  min_similarity  float default 0.5        -- 相似度低于这个分数的不要
)
returns table (
  id         bigint,
  category   text,
  title      text,
  content    text,
  similarity float
)
language sql
stable                                   -- 只读不写，Postgres 可以优化
set search_path = public, extensions     -- 固定搜索路径（安全建议）
as $$
  select
    d.id,
    d.category,
    d.title,
    d.content,
    -- <=> 是余弦"距离"（0 = 完全一样）。1 - 距离 = 相似度（1 = 完全一样）
    1 - (d.embedding <=> query_embedding) as similarity
  from public.documents d
  where 1 - (d.embedding <=> query_embedding) >= min_similarity
  order by d.embedding <=> query_embedding   -- 距离越小越前面
  limit match_count;
$$;

-- 只让后端（service_role）调用，前端和未登录的人都不能调用。
revoke execute on function public.match_documents(extensions.vector, int, float)
  from public, anon, authenticated;
