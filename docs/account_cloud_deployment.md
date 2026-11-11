# SweaterCo 云端账号接入与验收

本版本复用现有注册登录、会话授权、PostgreSQL 存储和个人聊天界面。
新增入口 `app/cloud_app.py` 使用 Streamlit 的 `st.App` 挂载同域 HTTP 路由，
由服务器设置和清除 `HttpOnly; Secure; SameSite=Lax; Path=/` 的 Cookie。
已有普通页面入口 `app/ui/streamlit_app.py` 继续可用。

## 已完成的验证

- 全量回归：1595 项通过，14 项需要数据库环境的检查跳过。
- 新增的真实 PostgreSQL 联调另行通过，使用独立临时测试区验证 Cookie 登录、
  新服务实例恢复聊天和 Agent 状态、两账号隔离及退出撤销。
- 本机真实浏览器通过登录、订单查询保存、刷新恢复、退出后刷新及换号隔离验证。
  浏览器测试使用一次性账号；目标 Streamlit Cloud 仍需完成下方验收。

## 部署负责人操作

1. 在 Streamlit Community Cloud 新建账号验收应用，先保留现有公开演示应用。
   仓库选 `YI-chenxi/sweaterco-copilot`，分支选
   `feat/account-cloud-session-integration`，Main file path 填
   `app/cloud_app.py`，Python 选 3.12。沿用项目依赖中的 Streamlit 版本要求。
2. 在该应用的 Secrets 中保留现有模型配置，并配置下面三项。
   `DATABASE_URL` 用 P3 已配置的连接信息，验收优先使用独立测试数据库；
   确认已应用 P3 的 001、002 迁移。已有稳定 `ACCOUNT_SECRET` 请继续使用；
   首次配置由负责人生成高熵秘密，UTF-8 至少 32 字节。
   `ACCOUNT_PUBLIC_URL` 填本次部署的实际 HTTPS 域名，不含路径。

   ```toml
   DATABASE_URL = "替换为服务端数据库连接配置"
   ACCOUNT_SECRET = "替换为稳定的服务端秘密"
   ACCOUNT_PUBLIC_URL = "https://替换为实际应用子域名.streamlit.app"
   ```

3. 部署后用无痕窗口打开。应先显示账号登录页，完成注册和登录后
   才进入个人工作区。部署新的入口需要选择新文件；仅重启使用旧入口的应用
   不会开启真实账号模式。设置缺失时，账号入口保持关闭。
4. 依次完成下面的线上验收，再决定是否把正式应用切换到新入口。

## 线上验收

1. 用验收账号 A 登录，执行一次 Quick Workflow 或发一条实际聊天，
   确认 Recent Chats 有记录。刷新页面，确认仍登录并可打开同一条聊天。
   重新登录也应恢复原回复、证据和待补参数。
2. 在另一个无痕会话用验收账号 B 登录，确认看不到 A 的聊天。
   A 退出后刷新应回到登录页；原登录凭证已撤销。再次登录 A，聊天仍在。
3. 在可安排的测试时段重启验收应用，确认有效 Cookie 对应的会话及聊天可以恢复。
   如需检查浏览器 Cookie，只核对名称及安全属性，不复制 Cookie 值。
4. 若出现“无法建立浏览器登录状态”，先核对应用分支、入口和
   `ACCOUNT_PUBLIC_URL`。若配置正确，检查同域
   `sweaterco-auth/session` 的 POST 请求是否抵达应用，
   浏览器收到的 Cookie 是否在下一次请求送回服务器。
   提供状态码和脱敏日志即可。若托管代理拦截自定义路由或 Cookie，
   需要部署负责人确认平台支持，或使用可保留该路由及 Cookie 的 HTTPS 部署环境。

## 接入边界

- 浏览器只转送短期、单次使用的交接编号。服务器在第二次 HTTP 请求
  确认 Cookie 已生效，才把登录状态交给原账号界面。原有后端继续校验
  会话过期、撤销和聊天归属。退出先撤销服务端会话，再清浏览器 Cookie。
- 交接状态只在当前服务进程短暂保存；刷新或重启后，已有 Cookie 可通过
  PostgreSQL 会话恢复。部署使用单个 Streamlit 服务进程。
  浏览器 Cookie 使用 P2 登录时给出的有效期；到期后需要重新登录。
- 公开站点的托管代理行为需要按上面的实际网页步骤验证。
  本地 HTTP 和浏览器测试不能证明目标 Cloud 环境已启用这项功能。
- P2 的会话校验、P3 的数据库结构、P4 的对话恢复和 P5 的界面继续复用。
  本次没有增加数据库迁移，也不改变主动任务的调度和存储方式。
