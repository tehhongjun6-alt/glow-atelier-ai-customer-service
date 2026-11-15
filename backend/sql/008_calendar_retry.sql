-- ============================================================
-- 日历自动补做：Google Calendar 失败时，先记下来，背景程序每分钟自动再试
--
-- 以前：
-- - 新预约写日历失败 → 把数据库那笔删掉，告诉顾客"系统忙"（顾客订不到）
-- - 取消／改期写日历失败 → 只写 log，要员工手动去改日历（常常没人发现）
-- 现在：
-- - 数据库是"真正的资料"，日历只是"给人看的副本"
-- - 日历失败 → 预约照样成功，这一笔标成"日历待补做"（calendar_pending = true）
-- - 背景程序（booking/calendar_sync.py）每分钟找出待补做的预约，照数据库的样子补到日历
-- - 试了 10 次还不行 → log 记 ERROR，交给工程师
-- ============================================================

alter table public.bookings
  add column if not exists calendar_pending  boolean not null default false,   -- 日历还没跟上数据库
  add column if not exists calendar_attempts int     not null default 0,       -- 已经补做失败几次
  add column if not exists calendar_error    text;                             -- 最后一次失败的原因（给工程师看）

-- 背景程序每分钟查"待补做"的预约；平常几乎都是 false，用部分索引只记 true 的那几笔
create index if not exists bookings_calendar_pending_idx
  on public.bookings (id) where calendar_pending;
