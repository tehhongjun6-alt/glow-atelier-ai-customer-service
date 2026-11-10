-- ============================================================
-- 第 6 步：转人工工单
-- 已在 Supabase 运行过（migration：create_handoffs_table）。
--
-- 状态只能照顺序往前走：
--   processing（后台收到，等同事接手） → accepted（同事按"接受"） → resolved（同事按"处理完毕"）
-- 顺序由 backend/handoff/handoffs.py 用代码检查。
-- ============================================================

create table public.handoffs (
  id               bigint generated always as identity primary key,
  ticket_code      text not null unique,          -- 给顾客的工单编号，例如 HO-7K3M9Q
  channel          text not null check (channel in ('chat', 'email')),   -- 从哪里来
  reason           text not null check (reason in (
                     'complaint',      -- 投诉、不满意
                     'medical',        -- 过敏、皮肤不适等身体状况
                     'refund',         -- 退款、配套、储值纠纷
                     'request_human',  -- 顾客要求真人
                     'cannot_answer',  -- 资料里没有、AI 答不了
                     'other')),
  summary          text not null,                  -- AI 写的摘要：同事一眼看懂发生什么事
  customer_name    text,
  contact          text not null,                  -- 联系方式：电话或电邮
  email_thread_id  text,                           -- 电邮渠道：对应的 Gmail 对话（转人工后 AI 不再自动回这个对话）
  booking_code     text,                           -- 相关的预约（如果有）
  conversation     jsonb not null default '[]'::jsonb,   -- 转人工前的完整对话，同事接手时不用再问一次
  status           text not null default 'processing'
                     check (status in ('processing', 'accepted', 'resolved')),
  assigned_to      text,                           -- 哪位同事接手
  staff_note       text,                           -- 同事的处理记录
  created_at       timestamptz not null default now(),
  accepted_at      timestamptz,
  resolved_at      timestamptz
);

-- 顾客资料，一样只给后端读写
alter table public.handoffs enable row level security;

-- 后台列表：按状态、时间排
create index handoffs_status_idx on public.handoffs (status, created_at);
-- 电邮渠道：查某个对话有没有未处理完的工单
create index handoffs_thread_idx on public.handoffs (email_thread_id) where email_thread_id is not null;
