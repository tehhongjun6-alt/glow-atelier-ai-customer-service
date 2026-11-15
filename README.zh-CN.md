# Glow Atelier AI 客服 Agent

[English](README.md) | **简体中文**

美容中心 **Glow Atelier 光研美学**（吉隆坡 SS2）的 AI 客服。
顾客可以通过**网页聊天**或 **Email** 问问题、预约、改期、取消；AI 查公司知识库回答，处理不了的交给**人工后台**。

> 作品集项目。重点不只是"会聊天"，而是：**重要规则由代码强制、每个功能都有测试和评估、出错有人知道**。

---

## 功能

### 顾客

| 功能 | 说明 |
|---|---|
| 智能问答 | 价格、服务、营业时间、护理须知……先查知识库再回答，附上参考资料；资料没有的会老实说没有 |
| 预约 | 查空档 → 收集资料 → 顾客确认后才写入；自动分配有空的美容师，写进 Google Calendar 并寄确认信 |
| 改期／取消 | 要核对预约编号 + 电话，再输入寄到电邮的 **OTP 验证码** |
| Email 客服 | 寄到公司信箱的信，AI 自动回复（网页上有开关，预设关闭） |
| 转人工 | 投诉、过敏、退款、要求真人等情况，开工单交给门市同事 |
| 语言 | 用中文回答，听得懂马来西亚常见的中英混合说法（"这个 package 几钱？""可以 book 明天吗？"） |

### 员工（要登录）

| 功能 | 说明 |
|---|---|
| 转人工后台 | 工单清单、AI 写的摘要和完整对话；接受 → 处理完毕 |
| 预约日历 | 接下来 30 天的预约，和 Google Calendar 同步 |
| 邮箱开关 | 打开／关闭 Email 自动回复 |
| Telegram 通知 | 新工单、系统出错，手机马上收到 |

---

## 技术重点

**RAG（检索增强生成）**
- 知识库切成 69 块，Gemini embedding（768 维）存进 Supabase pgvector
- **混合检索**：向量 + 中文关键词（PGroonga），用 RRF 合并排名
- 检索评估 60 题（50 题资料里有答案、10 题故意没有）：有答案的题 Hit@5 = 1.00

**Function calling（工具调用）**
- Gemini 自己决定用哪个工具：查知识库、查空档、查预约、预约、改期、取消、确认、转人工（共 8 个）
- 手动控制循环（不用 SDK 自动执行），每一步都看得到、记得下来

**护栏：重要规则由代码强制，不只靠 prompt**
- **两段式确认**：预约／改期／取消要顾客看过摘要、下一句同意才执行
- **OTP 验证码**：只存哈希值、10 分钟有效、最多 5 次
- **数据库防重复预约**：exclusion constraint，开多台服务器也不会把同一位美容师订两次
- **AI 说谎保护**：回信说"已转人工"却没开工单，寄出前由代码补开
- **防提示词注入**：垃圾邮件 AI 不读；密钥从不进 prompt；错误讯息不外泄内部细节

**可靠性**
- **模型自动换档**：免费额度用完或模型太忙，自动换下一个模型，额度恢复后自动回来
- **日历自动补做**：Google Calendar 失败不影响预约，背景每分钟补上
- **逐字显示（SSE）**：一边生成一边显示，60 秒没进度就提示

**品质与维运**
- **195 个自动测试**（pytest，离线、不连真的服务）+ **GitHub Actions** 每次 push 自动跑
- **35 个 Agent 情境评估**（真的 Gemini）：预约、转人工、陷阱题、隐私、提示词注入
- **Langfuse 追踪**：每一题的模型调用、工具、token 都看得到
- **Telegram 警报**：ERROR 自动通知（电话、电邮先遮蔽）；健康检查 `/api/health`

---

## 技术栈

| 部分 | 技术 |
|---|---|
| 后端 | Python 3.14 · FastAPI |
| AI 模型 | Gemini（gemini-3.6-flash 优先，自动换档） |
| Embedding | gemini-embedding-001（768 维） |
| 数据库 | Supabase：Postgres + pgvector + PGroonga |
| 员工登录 | Supabase Auth |
| 预约／Email | Google Calendar API · Gmail API |
| 前端 | React 19 · Vite · TypeScript |
| 可观测性 | Langfuse · Python logging · Telegram Bot |
| CI | GitHub Actions |

---

## 系统架构

```
              顾客                                员工
        ┌──────┴──────┐                    ┌──────┴──────┐
     网页聊天         Email               后台网页      Telegram
   （逐字显示）   （专用 Gmail）          （要登录）     （通知）
        │              │                     │             ▲
        ▼              ▼                     ▼             │
   ┌───────────────────────────────────────────────────────┴──┐
   │  FastAPI 后端                                             │
   │   ┌──────────────────────────────┐   背景工作：           │
   │   │ Gemini Agent（工具调用循环）  │   · Email 自动回复     │
   │   │ 护栏：两段式确认、OTP、规则   │   · 日历自动补做       │
   │   └──┬────────┬────────┬────────┬┘                        │
   │      ▼        ▼        ▼        ▼                         │
   │    RAG      预约     Email    转人工                      │
   └──────┬────────┬────────┬────────┬────────────────────────┘
          ▼        ▼        ▼        ▼
       Supabase  Google   Gmail   Supabase        Langfuse（追踪）
       （向量）  Calendar  API    （工单）
```

---

## 怎么运行

需要：Python 3.14、Node.js，以及 `backend/.env`（Gemini、Supabase、Google 的设定，不会上传）。

```bash
# 后端（项目最外层）
.venv\Scripts\python -m uvicorn api:app --app-dir backend --port 8010
# → http://localhost:8010/docs 可以直接试每个 API

# 前端（另开一个终端）
npm --prefix frontend install
npm --prefix frontend run dev
# → http://localhost:5180

# 自动测试（在 backend 资料夹里，离线，约 2 秒）
python -m pytest -q

# Agent 情境评估（在 backend 资料夹里，会用到 Gemini 额度）
python -m evals.agent_eval
```

---

## 更多细节

[RAG-blueprint.md](RAG-blueprint.md)：RAG 每一步的做法和原因、评估成绩、踩过的坑、**设计决定记录**、面试问答。
