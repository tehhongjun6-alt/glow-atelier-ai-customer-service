# 隐私权政策｜Privacy Policy

**Glow Atelier 光研美学 AI 客服（作品集示范项目）**
最后更新：2026-11-11

> 这是一个**作品集示范项目**，Glow Atelier 光研美学的公司资料、服务和价格都是为了示范而设计的内容。
> **请不要在示范网站输入真实的个人资料**，测试时请使用假的姓名、电话和电邮。

---

## 1. 我们收集哪些资料

| 资料 | 什么时候收集 | 用途 |
|---|---|---|
| 聊天讯息 | 你在网站的聊天客服发问时 | 让 AI 回答问题、处理预约 |
| 姓名、电话、电邮 | 你预约、改期、取消，或要求转人工时 | 建立预约、寄送预约通知信、让同事联络你 |
| 寄到客服邮箱的电邮内容 | 你寄信给客服时 | 让 AI 回信，或转交同事处理 |
| 访客 IP 位址 | 每次发送聊天讯息时 | 只用来计算使用次数、防止滥用，不会长期保存 |

## 2. 资料存在哪里

- **Supabase**（数据库）：预约记录、转人工工单
- **Google Calendar**：预约活动。⚠️ 示范用的预约日历**设为公开**，预约时填写的姓名和服务项目任何人都看得到，所以请务必使用假资料
- **Gmail**：预约通知信和客服回信
- **Google Gemini API**：聊天讯息会送到 Gemini 产生回答

## 3. Google 使用者资料的使用方式

这个应用程序通过 Google OAuth 存取 **项目拥有者本人** 的 Google Calendar 和 Gmail，只用来：

- 在专用的「Glow Atelier 预约」日历新增、修改、删除预约活动
- 寄送预约通知信，读取并回复寄到客服专用地址的电邮

本应用程序**不会**存取其他 Google 用户的资料，也**不会**把 Google 使用者资料卖给第三方、用于广告，或用来训练 AI 模型。
本应用程序对 Google API 资料的使用遵守 [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)，包括其中的 Limited Use（有限使用）要求。

## 4. 资料保存与删除

- 聊天对话只暂存在服务器内存里，2 小时没有新讯息或服务器重启后就会清除
- 预约和工单资料会保存在数据库，直到项目拥有者删除
- 如果想查询或删除你在示范网站留下的资料，请发电邮到下方的联系邮箱

## 5. 联系方式

项目拥有者：Hong Jun Teh
电邮：tehhongjun6@gmail.com
GitHub：https://github.com/tehhongjun6-alt/glow-atelier-ai-customer-service

---

## English summary

This is a **portfolio demo**. The business "Glow Atelier" and its services are fictional demo content. **Please use fake names, phone numbers and emails when testing.**

- **Data collected**: chat messages; name, phone and email when booking or asking for a human; emails sent to the support address; visitor IP (rate limiting only).
- **Storage**: Supabase (bookings, handoff tickets), Google Calendar (the demo booking calendar is **public**), Gmail (notifications and replies), Google Gemini API (to generate answers).
- **Google user data**: the app accesses only the project owner's own Google Calendar and Gmail, solely to manage the demo booking calendar and to send/read/reply to support emails. It does not access other users' Google data, and does not sell, advertise with, or train AI models on Google user data. Use of Google API data complies with the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.
- **Retention**: chats are kept in memory only (cleared after 2 idle hours or on restart); bookings and tickets stay in the database until the owner deletes them.
- **Contact**: tehhongjun6@gmail.com
