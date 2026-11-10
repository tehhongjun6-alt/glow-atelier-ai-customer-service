-- ============================================================
-- 任务 2.6b + 2.6c：关键词检索（PGroonga）+ 混合检索（RRF）
-- 已在 Supabase 运行过（migrations：enable_pgroonga_keyword_index、
-- create_keyword_and_hybrid_search）。要先运行 001_documents.sql。
-- ============================================================


-- ---------- 2.6b 关键词检索 ----------

-- Postgres 自带的全文检索按"空格"分词，中文句子没有空格，所以搜不到。
-- PGroonga 支持中文：把中文按"两个字一组"（Bigram）切词，例如 星期一 → 星期、期一
create extension if not exists pgroonga with schema extensions;

-- 在 content 栏位建 PGroonga 全文索引
create index documents_content_pgroonga_idx
  on public.documents using pgroonga (content extensions.pgroonga_text_full_text_search_ops_v2);

-- 关键词检索函数：把问题切成词，去掉太常见的词，找出包含最多这些词的块
--
-- 【杂讯修正（v2）】第一版发现："有没有 student discount？" 唯一命中的词是"没有"，
-- 它出现在 13 块里，于是关键词检索返回 13 块不相关的资料，在 RRF 里把正确答案挤到第 6 名。
-- 修正方法（两种一起用）：
--   1. 停用词表：问句里常见、没有区分度的词（什么、没有、可以……）
--   2. 文档频率过滤：一个词如果出现在超过 15% 的块里（69 块中超过 10 块），就太常见了，不拿来搜
--      （和 BM25 里 IDF 的概念一样：越常见的词，越没有区分度）
-- 如果过滤后一个词都不剩，关键词检索就返回空，混合检索会自动只看向量的结果。
--
-- 【英文停用词（v3）】难题评估发现："can I bring my baby along? no one can jaga her"
-- 里的 "no" 命中了"缺席（No-show）"，把正确答案"可以带小孩来吗？"挤到第 3 名。
-- v2 的停用词表只有中文，v3 加上英文常用词（no、can、my、the……）和马来西亚口语语气词（ah、lah、leh）。
-- 对应的 migration：keyword_search_english_stopwords
create or replace function public.keyword_search (
  query_text    text,
  match_count   int   default 5,
  max_doc_ratio float default 0.15   -- 出现在超过这个比例的块里的词，不拿来搜
)
returns table (
  id       bigint,
  category text,
  title    text,
  content  text,
  score    float   -- 命中几个词（越多越相关）
)
language sql
stable
set search_path = public, extensions
set enable_seqscan = off   -- 强制使用 PGroonga 索引，pgroonga_score 才有分数
as $$
  -- 1. 把问题切成词。"can book 星期一吗？" → can, book, 星期, 期一, 一吗
  --    去掉单个字（例如"吗"）和标点，它们太常见，没有区分度
  with raw_terms as (
    select distinct t->>'value' as word
    from unnest(pgroonga_tokenize(query_text,
                                  'tokenizer', 'TokenBigram',
                                  'normalizer', 'NormalizerAuto')) t
    where char_length(t->>'value') >= 2
      and (t->>'value') ~ '[[:alnum:]]'
  ),
  -- 2. 去掉停用词，以及出现在太多块里的词（例如"星期"出现在 22 块，"期一"只有 8 块 → 留下"期一"）
  terms as (
    select coalesce(array_agg(r.word), '{}') as words
    from raw_terms r
    where r.word <> all (array[
            -- 中文问句常用词
            '什么','没有','有没','可以','怎么','怎样','多少','哪里','哪个','请问','应该','我们','你们','我想','是不','不是',
            -- 英文常用词（PGroonga 会先转成小写）
            'no','not','can','could','will','would','should','do','does','did','is','are','am','was','be','been',
            'the','an','and','or','but','if','so','to','of','in','on','at','for','with','from','by','about','as',
            'it','its','this','that','these','those','there','here','what','which','who','how','when','where','why',
            'me','my','you','your','we','our','us','he','she','her','him','his','they','them','their',
            'one','any','some','all','have','has','had','get','got','want','need','please','pls','ok','okay',
            'just','also','then','than','too','very','day',
            -- 马来西亚口语语气词
            'ah','lah','leh','lor','meh'])
      and (select count(*) from documents d where d.content &@ r.word)
          <= (select count(*) from documents) * max_doc_ratio
  )
  -- 3. &@| = "包含其中任何一个词"；pgroonga_score = 命中了几个词
  select d.id, d.category, d.title, d.content,
         pgroonga_score(d.tableoid, d.ctid)::float as score
  from documents d
  where d.content &@| (select words from terms)
  order by score desc, d.id
  limit match_count;
$$;


-- ---------- 2.6c 混合检索（RRF） ----------

-- 向量和关键词各取前 candidate_count 名，用 RRF 合并：
--   RRF 分数 = 1/(rrf_k + 向量名次) + 1/(rrf_k + 关键词名次)
-- 只出现在一边的块，另一边算 0 分
create or replace function public.hybrid_search (
  query_text      text,
  query_embedding extensions.vector(768),
  match_count     int default 5,
  candidate_count int default 20,
  rrf_k           int default 60
)
returns table (
  id           bigint,
  category     text,
  title        text,
  content      text,
  vector_rank  bigint,   -- 在向量检索里排第几（null = 没进前 candidate_count 名）
  keyword_rank bigint,   -- 在关键词检索里排第几
  rrf_score    float
)
language sql
stable
set search_path = public, extensions
as $$
  with vector_results as (          -- 向量检索的前 N 名
    select d.id,
           row_number() over (order by d.embedding <=> query_embedding) as rank
    from documents d
    order by d.embedding <=> query_embedding
    limit candidate_count
  ),
  keyword_results as (              -- 关键词检索的前 N 名
    -- 【同分修正（v2）】用 rank() 而不是 row_number()：同分的块名次相同。
    -- 第一版用 row_number()，同样只命中 1 个词的块会按编号排出第 1、2、3 名，
    -- 等于随便给其中一块加分。
    select k.id,
           rank() over (order by k.score desc) as rank
    from keyword_search(query_text, candidate_count) k
  ),
  fused as (                        -- 合并：full outer join 让只出现在一边的也保留
    select coalesce(v.id, k.id) as id,
           v.rank as vector_rank,
           k.rank as keyword_rank,
           coalesce(1.0 / (rrf_k + v.rank), 0)
             + coalesce(1.0 / (rrf_k + k.rank), 0) as rrf_score
    from vector_results v
    full outer join keyword_results k on v.id = k.id
  )
  select d.id, d.category, d.title, d.content,
         f.vector_rank, f.keyword_rank, f.rrf_score::float
  from fused f
  join documents d on d.id = f.id
  order by f.rrf_score desc, f.vector_rank nulls last   -- RRF 同分时，向量排名靠前的优先
  limit match_count;
$$;

-- 只让后端（service_role）调用
revoke execute on function public.keyword_search(text, int, float) from public, anon, authenticated;
revoke execute on function public.hybrid_search(text, extensions.vector, int, int, int) from public, anon, authenticated;
