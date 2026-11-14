-- 006：把 Supabase Auth 帐号设成「员工」，才能登录后台（staff_auth.py 会检查 role）
--
-- 步骤（在 Supabase 后台做）：
-- 1. Authentication → Users → Add user → Create new user
--    建 2 个帐号（例如前台 Amy、店长 Jessica），勾选 Auto Confirm User，密码自己设
-- 2. Authentication → Sign In / Providers → 关掉「Allow new users to sign up」
--    （不让陌生人自己注册帐号）
-- 3. 把下面的邮箱换成你刚建的两个帐号，然后在 SQL Editor 执行这个文件
--
-- 为什么用 app_metadata？用户自己改不了 app_metadata（只有后台能改），
-- 所以不能自己把自己升级成员工。

update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role": "staff"}'::jsonb,
    raw_user_meta_data = coalesce(raw_user_meta_data, '{}'::jsonb) || '{"name": "前台 Amy"}'::jsonb
where email = 'amy@换成你的邮箱.com';

update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role": "admin"}'::jsonb,
    raw_user_meta_data = coalesce(raw_user_meta_data, '{}'::jsonb) || '{"name": "店长 Jessica"}'::jsonb
where email = 'jessica@换成你的邮箱.com';

-- 检查：应该看到两行，role 是 staff / admin
select email, raw_app_meta_data ->> 'role' as role, raw_user_meta_data ->> 'name' as name
from auth.users
where raw_app_meta_data ->> 'role' in ('staff', 'admin');
