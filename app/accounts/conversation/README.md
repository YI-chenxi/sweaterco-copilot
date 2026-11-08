# Account Person 4 状态恢复与后端集成

本模块属于账号与持久聊天系统本轮 Person 4，不是上一轮 Confirmed Actions。
独立交付分支：feat/account-person4-state-recovery。代码只修改 P4 自有目录。

## 当前版本

已按实际交付适配：
- P1 feat/account-auth：f5dd3d2fcc5cda2f38c16b8f50acb6f92d9871b6。
- P2 feat/account-sessions：e67aa3c78c593facc4338fc880fcbc2050d911a1。
- P3 feat/account-storage：0d2f90f0bf839a84b1bb87c72a0bd08b7f96203c。

已实现状态和回复 JSON 编解码、P2 可信身份适配、P3 事务适配、账号组合服务，以及恢复/隔离/去重/异常测试。真实数据库和部署网页验收尚未执行；本轮先完成离线验证。

## 文件

| 文件 | 用途 |
| --- | --- |
| codec.py | 完整保存和恢复现有 ChatSession、AgentReply，版本为 1 |
| identity_adapter.py | 验证 P2 bearer token 及工作区权限，映射安全错误 |
| postgres_adapter.py | 使用 P3 Repository 和现有表，补齐一致快照、执行领取和事务边界 |
| runtime.py | P5 可调用的注册、登录、退出、个人线程及持久对话入口 |
| service.py | 每次请求绑定可信身份，恢复状态、执行 Agent、确认保存 |
| contracts.py | P4 内部适配类型，不替换 P1/P2/P3 的公共接口 |
| INTEGRATION_HANDOFF.md | 部署配置、前端接入和后续验收 |
| PROPOSED_INTERFACES.md | 内部契约及与真实 P3 接口的差异 |

没有改动主 Streamlit 页面、共享依赖文件或 P3 迁移。依赖由 P1/P3 分支提供：argon2-cffi 和 psycopg[binary]。只应用 P3 的 001、002 迁移，不执行 P2 独立 schema.sql，也不自行将数据库 TEXT 类型改成 UUID 类型。

## 统一入口

~~~python
from app.accounts.conversation import build_account_backend

backend = build_account_backend(agent=existing_agent, secrets=st.secrets)

registered = backend.register(username, password)  # 注册不自动登录
login = backend.login(username, password)
# P5 将 login.cookie 交给安全 Cookie 适配器，不放入普通 session_state 或日志。
token = cookie_value_from_approved_browser_adapter
current = backend.current_user(token)
threads = backend.list_threads(token)
thread = backend.create_thread(token, "New conversation")
opened = backend.open_thread(token, thread.thread_id)
saved = backend.handle_turn(token, thread.thread_id, user_text, request_uuid)
current_history = backend.open_thread(token, thread.thread_id)
~~~

其他方法：
- rename_thread(token, thread_id, title)
- delete_thread(token, thread_id)
- logout(token)：先完成服务端撤销，再返回删除 Cookie 的指令。

除 Cookie 以外，UI 不提供 user_id/workspace_id 作为权限凭据。私人工作区采用 P3 的既定映射 workspace_id = user_id，且通过 P2 与数据库重新校验。数据库 TEXT 字段保存 P1 返回的 UUID 字符串，现有三者可兼容，不需为此新增迁移。

## 保存和恢复行为

完整保留 state、active_task_ids、global_issues、next_task_number；回复保留原文、证据、警告、假设、澄清问题和 metadata。恢复不调用工具、不重跑已完成任务，也不扩展现有 Agent 理解能力。

P3 begin_turn 会先保存一条 user 消息；因此读取允许完整轮次后有一条显式标记的待处理消息。opened.pending 为 None，或包含 request_id 和 processing/failed。failed 表示需要核实，不能当作可以随便重试。

P3 原接口的新请求与已存在请求都可能返回 processing。P4 在短数据库事务中锁定线程、检查请求及 revision，只有刚创建的请求才能执行 Agent；同一请求或另一请求的未解决状态不会再次执行。LLM 运行期间没有持有连接或事务。

完成时委托 P3 complete_turn 原子提交回复和状态。登录会话及工作区在该事务内再次校验。P3 内部的 commit/close 通过借用连接适配交给 P4 外层事务统一处理，P3 的写入逻辑不重写。

重放使用旧请求的原始回复及不可变完成回执，不重跑模型。P3 未保存每个历史轮次的完整旧状态，因此重放不返回旧 ChatSession；UI 重新加载最新状态。002 之前缺少有效完成回执的旧请求明确要求核实。

保存失败或结果不明时保留 processing/failed；不会自动解锁、重试工具或改请求 ID 绕过。P3 的 failed 状态用于这一保守阻止行为，本轮没有添加自动恢复或人工核对界面。

## 验证

~~~powershell
python -m pytest -q tests/accounts/conversation
~~~

P4 个人分支不包含别人的模块：涉及真实 P1/P2/P3 的测试在依赖缺失时会跳过。
联合验证在独立本地集成分支上合入三个精确提交后运行。
真实 P1/P2 服务的离线测试使用内存 Repository；P4/P3 SQL 适配测试使用脚本化 DB-API 替身。这些不等于真实 PostgreSQL 持久化验证。

test_postgres_live.py 提供独立临时 schema 的真实 PostgreSQL 验收，默认不连接数据库、不读取生产 DATABASE_URL。需要专用测试配置并显式开启，见交接说明。本次用户选择先完成离线工作。

详细实际测试结果记录在 INTEGRATION_HANDOFF.md。旧版 108/645 的结果不能当作本次集成结果，也不能与各人的分支测试数相加。

## 边界

本模块不设置浏览器 Cookie、不修改页面，不提供密码找回，不迁移主动任务业务数据。刷新/重开浏览器的登录与聊天恢复还需要 P5 完成 Cookie 和页面接入后验收。

编解码白名单支持 JSON 基础值、有限浮点数、tuple、date/datetime 和既有四类枚举。未知版本/对象明确报错，不静默清空。单个文档上限 8 MiB，用户输入上限 32,000 字符。
