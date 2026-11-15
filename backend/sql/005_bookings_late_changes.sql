-- ============================================================
-- 24 小时内改期的次数（migration：bookings_late_changes）
-- 知识库：24 小时内每个预约可以免费改一次；第二次会被当作取消。
-- 代码（booking/bookings.py）用这个栏位检查，第二次就挡下并请顾客联系门市。
-- ============================================================
alter table public.bookings add column if not exists late_changes int not null default 0;
