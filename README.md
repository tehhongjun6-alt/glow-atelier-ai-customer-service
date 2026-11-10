# AI Customer Service

一个企业 AI 客服 Agent 的作品集 Demo，服务的公司是美容中心 **Glow Atelier 光研美学**。
顾客可以通过**网页聊天**或 **Gmail** 联系客服。AI 会用 **RAG 知识库**回答问题，通过 **Google Calendar** 处理预约，遇到处理不了的问题就**转给人工后台**。

---

## 功能

| # | 功能 | 说明 |
|---|------|------|
| 1 | RAG 知识库 | 价格表、产品、FAQ 转成向量存进 Supabase（pgvector） |
| 2 | 智能问答 | 顾客问价格、问产品时，Agent 先查知识库再回答 |
| 3 | 预约 | 连接 Google Calendar：新建、取消、更改时间 |
| 4 | Gmail | 预约成功、改期、取消时自动发确认信；自动读取顾客来信并回复 |
| 5 | 转人工 | 聊天或邮件里遇到 AI 处理不了的问题，建立工单交给人工后台 |

### 转人工工单状态

```
processing（待处理）  →  accepted（已接受）  →  resolved（处理完毕）
```

### 语言

- 用**中文**回答顾客。
- 要能听懂马来西亚常见的中英混合说法（例如"这个 package 几钱？""可以 book 明天吗？"）。
- 产品名这类专有名词保留英文。

---

## 技术栈

| 部分 | 技术 |
|------|------|
| 后端 | Python + FastAPI |
| AI Agent（回答模型） | Gemini 3.5 Flash-Lite（`gemini-3.5-flash-lite`） |
| 向量模型（Embedding） | Gemini embedding |
| 数据库 | Supabase（Postgres + pgvector） |
| 预约 | Google Calendar API |
| 邮件 | Gmail API |
| 前端 | React（Vite + TypeScript） |

---

## 系统架构

```
           顾客
        ┌───┴────┐
     网页聊天     Gmail
        │        │
        ▼        ▼
   ┌──────────────────────┐
   │  FastAPI 后端         │
   │  ┌────────────────┐  │
   │  │  Gemini Agent   │  │
   │  └───────┬────────┘  │
   │   ┌──────┼──────┬────────┐
   │   ▼      ▼      ▼        ▼
   │  RAG  Calendar Gmail  转人工
   └───┬──────┬──────┬───────┬─┘
       ▼      ▼      ▼       ▼
   Supabase Google  Gmail  Supabase
  (向量)  Calendar  API   (工单)
```

---

## 数据库（Supabase）

| 表 | 存什么 |
|----|--------|
| `documents` | 知识库的文字块和向量 |
| `bookings` | 预约记录（顾客、时间、状态、Google Calendar 活动 ID） |
| `handoffs` | 转人工工单（来源：聊天或邮件、内容、状态） |

---

## 项目结构

```
AI Customer Service/
├── knowledge_base/glow_atelier.md   ← 公司资料（501 行，切成 69 块）
├── RAG-blueprint.md                 ← RAG 系统的完整说明（每一步的决定、坑、成绩、面试问答）
├── backend/                         ← Python 后端
│   ├── api.py                       ← FastAPI：给前端调用的 API
│   ├── chat.py                      ← 在终端和客服聊天
│   ├── ingest.py                    ← 知识库导入 Supabase
│   ├── email_worker.py              ← 自动读信、回信（Gmail）
│   ├── admin.py                     ← 转人工后台（终端版）
│   ├── rag/                         ← 切块、向量、存储、检索（向量 + 关键词 + RRF）
│   ├── agent/                       ← Gemini Agent：prompt、工具、两段式确认
│   ├── booking/                     ← 预约规则、预约功能、通知信
│   ├── handoff/                     ← 转人工工单
│   ├── integrations/                ← Google 登录、Calendar、Gmail
│   ├── evals/                       ← 检索评估、回答评估
│   ├── tests/                       ← 自动测试（pytest，69 项，离线）
│   └── sql/                         ← Supabase 建表 SQL（001–004）
└── frontend/                        ← React（Vite + TypeScript）
    └── src/components/              ← 聊天、知识库、预约日历、转人工后台
```

---

## 怎么运行

**后端 API**（项目最外层）：
```
.venv\Scripts\python -m uvicorn api:app --app-dir backend --port 8000
```
打开 http://localhost:8000/docs 可以直接试每个 API。

**前端**（另开一个终端，项目最外层）：
```
npm --prefix frontend install
npm --prefix frontend run dev
```
打开 http://localhost:5173

**自动测试**（在 `backend` 资料夹里，离线、免费，约 1 秒）：
```
python -m pytest -v
```
69 项测试，检查代码里的硬护栏：营业规则、预约名额、手机号码核对、日历失败回滚、两段式确认、转人工状态顺序、电邮安全检查。
数据库、Google Calendar、Gmail 都换成假的（`backend/tests/conftest.py`），不会动到真的资料；时间固定在 2026-11-10 星期二 09:00，每次结果都一样。改代码后跑一次，就知道有没有改坏。

**Agent 情境评估**（在 `backend` 资料夹里，真的 Gemini、假的数据库／日历／Gmail，约 12 分钟）：
```
python -m evals.agent_eval --fresh
```
18 个情境（预约、改期／取消、转人工、陷阱），关键的 4 个各跑 3 次，共 26 次：**26/26 通过**。报告在 `backend/evals/report_agent.md`。

**自动回信**（需要时才开，在 `backend` 资料夹里）：
```
python email_worker.py
```

**前端有什么**：
- 左边：顾客聊天视窗。每个回答下面列出 AI 的处理过程（查了什么、用了哪些资料、订了什么）
- 右边「知识库」：完整的公司资料和目录；点 AI 回答下面的资料名称，会跳到那一段并标亮
- 右边「预约日历」：接下来 14 天的预约，和 Google Calendar 同步
- 右边「转人工后台」：工单清单、进度、转人工前的对话；可以按「接受工单」「标记处理完毕」

---

## 开发步骤

- [x] **第 1 步：知识库内容**：Glow Atelier 光研美学（美容中心），资料在 `knowledge_base/glow_atelier.md`
- [x] **第 2 步：Supabase + RAG**：混合检索（向量 + 中文关键词 + RRF），检索评估 Hit@5 = 1.00（见下方"RAG 对照表"）
- [x] **第 3 步：聊天 Agent**：Gemini 自己决定何时查知识库（Agentic RAG），9 组回答测试全部通过（`backend/evals/answer_results.md`）
- [x] **第 4 步：Google Calendar**：查空档、预约、改期、取消；同步 Supabase `bookings` 表和专用日历；两段式确认由代码强制
- [x] **第 5 步：Gmail**：预约／改期／取消后自动寄通知信；`email_worker.py` 每分钟检查 +glow 别名的新信并由 AI 回复（6 道安全检查），两项都实测通过
- [x] **第 6 步：转人工后台**：AI 在投诉／过敏／退款／要求真人等情况建立工单（附摘要和完整对话）；状态 processing → accepted → resolved 由代码强制顺序；电邮对话转人工后 AI 不再自动回复；终端后台 `admin.py`
- [x] **第 7 步（前端）**：React 网页：聊天 + 知识库（可点击跳到 AI 引用的段落）+ 预约日历 + 转人工后台；FastAPI 提供 API
- [ ] **第 7 步（部署）**：上线给面试官一个网址

---

## 设计决定记录

| 决定 | 原因 |
|------|------|
| 后端用 Python | 求职方向是 AI 相关职位，Python 是 AI 领域最常用的语言 |
| 数据库用 Supabase | 一个服务同时提供关系表和向量检索（pgvector） |
| Gmail 先用定时检查（Polling），之后升级成即时推送（Pub/Sub） | 先在本机把流程做通；部署后再改成即时推送 |
| 知识库按 `##` 标题切块 | 每块是一个完整的服务或问题，检索结果更准确 |
| 中文关键词检索用 PGroonga（Bigram 分词） | Postgres 自带全文检索按空格分词，中文搜不到 |
| 关键词检索过滤停用词（中文＋英文＋ah/lah）和常见词（出现在超过 15% 的块里） | 第一版"没有""星期"这类常见词造成杂讯，把正确答案挤到第 6 名 |
| 预约规则用代码检查，不交给 AI | AI 可能算错日期或忘记星期一休息；代码不会（`booking/rules.py`） |
| 不可逆的动作用"两段式确认"，由代码强制 | 测试时模型跳过了确认、直接下单；改成 request → 顾客下一则讯息同意 → confirm，同一轮内确认会被拒绝（`agent/actions.py`） |
| 预约同时存 Supabase 和 Google Calendar | 日历给门市同事看；数据库给程序用（查预约、核对电话、算名额）。先写数据库再写日历，日历失败就回滚 |
| 改期／取消要核对手机号码 | 避免别人拿到预约编号就乱取消 |
| 每次调用都附上"现在时间" | 模型不知道今天几号，"明天""下星期三"会算错 |
| Gemini 429／503 自动等待重试 | 免费方案每分钟 15 次；新模型常客满 |
| Gmail 用个人邮箱的 +glow 别名（`tehhongjun6+glow@gmail.com`） | 不用另开账号；寄到别名的信会保留 Delivered-To，程序只处理这些信 |
| 自动回信有 6 道安全检查 | 在个人邮箱里运行（500+ 封未读），绝不能碰到真实邮件：只处理 +glow、只处理启动后的新信、跳过自己寄的、跳过 no-reply／退信／电子报／自动回复、处理过就加标签、每轮最多 5 封 |
| 先 dry-run 再真的寄 | `--dry-run` 只显示会回什么，不寄信、不加标签 |
| 通知信用固定模板，不让 AI 写 | 预约通知要准确、格式一致；AI 只负责回答顾客来信 |
| 通知信 Reply-To 设成 +glow 地址 | 顾客直接按"回复"，信就会进到 AI 处理的地址 |
| 转人工的状态只能照顺序走（processing → accepted → resolved） | 代码检查，不能跳步、不能倒退；更新时加上"原本状态"条件，两位同事同时按也不会出错 |
| 工单附上 AI 写的摘要和完整对话 | 同事接手时不用再问顾客一次 |
| 没有联系方式就不能转人工 | 工具直接拒绝；测试时 AI 第一次真的想不问电话就转，被代码挡下后才去问 |
| 电邮对话转人工后，AI 不再自动回 | 避免 AI 和同事在同一个对话里各说各的 |
| 护栏用 pytest 自动测试，数据库／日历／Gmail 换成假的 | 不花钱、不连网络、结果固定，改代码后一键确认没改坏；也证明测试不会污染真的数据库 |
| Agent 情境评估只让 Gemini 是真的 | 测的是模型的判断；数据库、日历、Gmail 用假的，不污染资料、时间固定所以评分能写死；每次调用间隔 5 秒、每跑完一次存档，避开免费额度限制 |
| 暂时不加 Reranker | 评估显示 Hit@5 已经是 1.00，正确答案都在前 5 名；资料只有 69 块 |
| 不用相似度门槛挡"没答案"的题 | 评估显示有答案和没答案两组的分数重叠（0.646 vs 0.662），改由第 3 步的规则处理 |

---

## RAG 对照表（照 RAG 蓝图 10 关）

> 完整说明（每个任务做了什么、为什么、踩过的坑、成绩、面试问答）见 [RAG-blueprint.md](RAG-blueprint.md)。

| 关卡 | 这个项目的选择 | 文件／位置 | 状态 |
|---|---|---|---|
| ① 资料来源 | 公司资料手册 1 份（Markdown，约 1.4 万字） | `knowledge_base/glow_atelier.md` | ✅ |
| ② 解析与清理 | Markdown 直接读；去掉分隔线 `---` | `backend/rag/chunker.py` | ✅ |
| ③ 文档切片 | 按 `##` 标题切，69 块，每块 93–398 字；记录大分类 | `backend/rag/chunker.py` | ✅ |
| ④ Embedding | `gemini-embedding-001`，768 维；资料／问题用不同 task_type；做向量前加大分类 | `backend/rag/embeddings.py` | ✅ |
| ⑤ 向量数据库 | Supabase pgvector + HNSW；RLS 开启；重新导入先删再写 | `backend/sql/001_documents.sql`、`backend/rag/store.py` | ✅ |
| ⑥ 相似度检索 | 混合检索：向量 + PGroonga 中文关键词，各取 20 名 | `backend/sql/002_hybrid_search.sql` | ✅ |
| ⑦ 重排序 | RRF（k = 60，同分同名次）；没有 reranker；没有门槛 | 同上 | ✅ |
| ⑧ 放进上下文 | 前 5 块，每块编号 [资料1]… 附标题和分类；每次约 2,800 输入 tokens | `backend/agent/tools.py` | ✅ |
| ⑨ 生成答案 | Gemini 3.5 Flash-Lite + 工具调用；8 条规则（只根据资料、没有就说没有、医疗转述规定、范围外拒答）；附参考标题 | `backend/agent/` | ✅ |
| ⑩ 评估 | 检索：30 题（20 dev + 10 holdout，含 5 题没答案），Hit@5 = 1.00、MRR 0.94–1.00。回答：9 组 4 种题型（基本、复杂、范围外、模型自己知道），全部通过，引用都来自检索结果 | `backend/evals/` | ✅ |

---

## 需要的账号

做到哪一步再申请哪个：

- [x] Supabase 项目（第 2 步）
- [x] Gemini API key（已有）
- [x] Google Cloud 项目 `glow-atelier-ai`：已开通 Calendar API、Gmail API，OAuth 桌面应用凭证在 `backend/secrets/`（测试状态，授权约 7 天过期，过期就删掉 `token.json` 重新登录）
