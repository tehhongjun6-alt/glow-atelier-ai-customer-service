-- ============================================================
-- 第 4 步：预约记录表
-- 已在 Supabase 运行过（migration：create_bookings_table）。
--
-- 为什么 Google Calendar 之外还要一张表？
-- - Calendar 负责"给人看"：门市同事打开日历就看得到当天的预约
-- - 数据库负责"给程序用"：查顾客的预约、验证手机号码、算时段还剩几个位、
--   之后给转人工后台和前端使用
-- 两边用 google_event_id 对应。
-- ============================================================

create table public.bookings (
  id               bigint generated always as identity primary key,
  booking_code     text not null unique,         -- 给顾客的预约编号，例如 GA-7K3M9Q
  customer_name    text not null,
  phone            text not null,                -- 只存数字，例如 0123862016
  email            text not null,                -- 第 5 步用来发确认信
  service          text not null,                -- 服务名称（booking/services.py 里的名字）
  beautician       text,                         -- 指定的美容师（可以不填）
  notes            text,                         -- 备注，例如过敏、怀孕周数
  start_at         timestamptz not null,
  end_at           timestamptz not null,
  status           text not null default 'confirmed' check (status in ('confirmed', 'cancelled')),
  google_event_id  text,                         -- 对应 Google Calendar 的活动
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  check (end_at > start_at)
);

-- 和 documents 一样：开 RLS 不加规则，只有后端的 Secret key 能读写（顾客资料更要保护）
alter table public.bookings enable row level security;

-- 查"某个时段有几个预约"（只看有效的预约）
create index bookings_time_idx on public.bookings (start_at, end_at) where status = 'confirmed';
-- 用手机号码找顾客的预约
create index bookings_phone_idx on public.bookings (phone);
