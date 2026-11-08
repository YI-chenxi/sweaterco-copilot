# Account Person 4 集成交接

## 当前交付

Person 4 已从仅有 Fake 的独立模块升级为适配实际 P1/P2/P3 的后端组合代码。
本次用户决定先完成离线验证，真实 Supabase 与部署浏览器验收留待连接配置补齐后进行。

正式个人分支：feat/account-person4-state-recovery。
本地验证分支：test/account-p4-adapters-20261108，仅用于合入依赖、运行测试，不是团队正式交付分支。
没有推送、创建 PR 或修改公共分支，也没有修改前端主文件或云端数据库。

精确依赖：
| 模块 | 分支 | 提交 |
| --- | --- | --- |
| P1 | feat/account-auth | f5dd3d2fcc5cda2f38c16b8f50acb6f92d9871b6 |
| P2 | feat/account-sessions | e67aa3c78c593facc4338fc880fcbc2050d911a1 |
| P3 | feat/account-storage | 0d2f90f0bf839a84b1bb87c72a0bd08b7f96203c |

只合入 P4 分支而没有上述模块时，编解码及独立测试仍可运行，生产组合服务会报 CONFIGURATION_ERROR，不会假装连接成功。

## 这次修改解决什么

1. 使用真实 P2 resolve_current_user 获取身份，并检查工作区权限，不接收前端指定身份。
2. 使用真实 P1 注册和凭据验证；验证成功后才请求 P2 创建登录会话。
3. 私人工作区采用 P3 触发器规定的 workspace_id = user_id，并由 P2 和数据库复核。
4. 适配 P3 begin_turn 提前保存用户消息的行为，暴露 pending 状态，不把处理中当作成功。
5. 在短事务中检查“新请求/已有请求”和当前 revision，只有新取得的请求执行 Agent。
6. P3 complete_turn 的回复、状态和完成回执纳入同一外层事务，不重写其业务 SQL。
7. 原请求重试返回原回复；较新状态不被旧回执覆盖。缺失旧完成回执时要求核实。
8. 退出或过期后拒绝后续请求；写入事务内再次检查登录会话和工作区。
9. 状态损坏、数据库失败、结果未知明确报错；保留 processing/failed，不盲目重跑。
10. 给 P5 一套账号和聊天调用入口，避免前端直接拼接多个 Repository。

## P5 初始化

先确保 P1、P2、P3 和 P4 都已进入同一审核后的代码基线，并安装合并后的依赖：
- argon2-cffi 来自 P1 requirements.txt。
- psycopg[binary] 来自 P3 requirements.txt。
- pytest 用于开发测试。

主页面取得已有 Agent 后：

~~~python
import streamlit as st
from app.accounts.conversation import build_account_backend

backend = build_account_backend(
    agent=existing_agent,
    secrets=st.secrets,
)
~~~

runtime.py 会将 st.secrets["DATABASE_URL"] 传入 P3 create_connection_factory，
再组合 P1/P2/P3 的真实 PostgreSQL Repository。后端不直接依赖 Streamlit，因此测试可注入普通字典。

需要两个服务端配置键：

~~~toml
DATABASE_URL = "<P3 已提供的 PostgreSQL 连接地址>"
ACCOUNT_SECRET = "<稳定、随机且至少 32 字节的账号服务密钥>"
~~~

这只是格式示例，不含真实密码。ACCOUNT_SECRET 已在用户电脑的仓库外配置文件中生成；
P3/部署者随后填好 DATABASE_URL。部署时将同一 ACCOUNT_SECRET 通过安全方式加入 Cloud Secrets。
不要每次启动重新生成，否则原登录令牌不能在重启后继续验证。P4 从这一个密钥派生用途分离的登录限流与会话密钥。

不要提交实际 secrets.toml、连接字符串、密码或 Cookie。初始化和普通错误提示都不会打印这些值。
只应用 P3 001/002；不要执行 P2 的独立 schema.sql。不要在每次应用启动时自动运行迁移。

## P5 账号流程

注册：

~~~python
user = backend.register(username, password)
# 成功表示账号已保存，不自动登录。
~~~

登录：

~~~python
login = backend.login(username, password)
# login.cookie 是安全 Cookie 的指令，交给 P5 经批准的浏览器适配器应用。
# login.user_id / username / workspace_id / session_id 是安全身份元数据。
~~~

login.cookie.value 是一次性返回的敏感 bearer token；不要把整个 login 对象存入普通
session_state、数据库聊天、日志、URL 或页面调试输出。LoginResult 的 repr 隐藏 Cookie，
但自行序列化它仍可能包含 token。不得通过前端 JavaScript 冒充 HttpOnly Cookie。

读取浏览器 Cookie 后：

~~~python
token = approved_cookie_adapter.get_cookie_value()
current_user = backend.current_user(token)
~~~

cookie_name 默认 sweaterco_session。P2 指令含 HttpOnly、Secure、SameSite=Lax 和 Path=/；
P5 仍需验证实际部署是否能安全应用这些指令。后端不会自动替浏览器设置 Cookie。

退出：

~~~python
deletion = backend.logout(token)
# 服务端撤销成功后，浏览器按 deletion 删除同名同属性 Cookie。
# 清空当前用户的消息、状态、选中证据、翻译缓存与待处理请求。
~~~

若后端撤销失败，显示明确错误，不将界面“已退出”视为服务端已经撤销。
跨账号切换与多标签页退出也必须在真实网页验证。

## P5 个人线程与发送

~~~python
threads = backend.list_threads(token)
thread = backend.create_thread(token, "New conversation")
backend.rename_thread(token, thread.thread_id, "Order review")
opened = backend.open_thread(token, thread.thread_id)

saved = backend.handle_turn(
    token,
    thread.thread_id,
    user_text,
    request_uuid,
)
latest = backend.open_thread(token, thread.thread_id)
# 删除有明确用户交互后调用：
backend.delete_thread(token, thread.thread_id)
~~~

thread_id 和 request_uuid 使用规范的小写 UUID 字符串。标题长度为 1–200 字符。
UI 创建一次发送请求时生成 request_uuid；同一次发送的网络重试保留原 ID 和原文本，
明确的新消息才生成新 ID。不能在重复 rerun 时不断更换请求编号。

数据库是长期来源。UI 不把本地线程序号当作数据库 ID，不自行修改 ChatSession。
saved 只在保存被确认后返回；即使 saved.replayed 为 True，也要重新加载 latest，
而不是用历史请求对应的旧状态覆盖现在线程。

opened.messages 每条包含 role/content，以及 assistant 的完整 reply，可复用当前
证据和结构化结果的展示组件。不要用展示翻译覆盖原始 reply。

opened.pending：
- None：没有未解决的预留请求。
- status = processing：保留一条处理中用户消息，不能再次执行。
- status = failed：结果需要核实，不是安全重试信号。

不能只删页面消息或改请求编号来绕过预留。当前没有自动核对/恢复界面；
需要恢复未决请求时由后端按实际结果核查，不自动解锁。

## 错误呈现

| 错误 | 页面处理 |
| --- | --- |
| CONFIGURATION_ERROR | 提示部署配置或依赖未接好，不显示密钥 |
| INVALID_CREDENTIALS | 统一提示用户名或密码错误 |
| INVALID_USERNAME / INVALID_PASSWORD | 展示账号规则错误 |
| USERNAME_TAKEN / RATE_LIMITED | 提示用户名不可用或稍后重试 |
| UNAUTHENTICATED / IDENTITY_CHANGED | 清理用户展示并要求有效登录 |
| FORBIDDEN / NOT_FOUND | 不展示其他用户资源，刷新个人列表 |
| CONFLICT | 重新读取当前线程，不自动执行 |
| REQUEST_IN_PROGRESS | 显示处理中/等待核对，禁止重复执行 |
| RECONCILIATION_REQUIRED | 显示结果待核实 |
| STORAGE_OUTCOME_UNKNOWN | 保存结果未知；同 ID 查询原结果，不能声明成功 |
| INVALID_STATE / UNSUPPORTED_SCHEMA | 保留原数据并报告，不静默清空 |
| AGENT_ERROR / STORAGE_ERROR | 显示安全失败提示，不打印原始异常 |

## 本次验证记录

- P4 personal branch: 134 passed, 3 integration modules skipped because peer modules are not merged there.
- Local combined branch with the exact P1/P2/P3 revisions: 741 passed, 13 skipped, 0 failures, 0 errors (full repository run).
- 13 skips: 7 P4 real PostgreSQL tests + 6 original P3 real PostgreSQL tests; no database connection was configured for this run.
- Ruff check/format passed for P4-owned Python files; Python 3.10 syntax parsing passed.
- The initial temporary-directory setup errors were corrected by configuring an existing D-drive parent and a fresh pytest base directory; no P3 implementation changes were needed.

Final full command: python -m pytest -q -p no:cacheprovider --basetemp <fresh-D-drive-path> --tb=short -rs


P4 的真实 PostgreSQL 测试为 test_postgres_live.py，七项覆盖：
重新登录后恢复澄清、两账号隔离及 CRUD、旧请求重放、并发不重复执行、
失败后保留待核实、真实提交后回执丢失、执行中退出登录阻止提交。

本次没有提供数据库连接，因此这七项未运行。P3 自有的六项真实 PostgreSQL 测试同样跳过。
P3 曾报告的 PostgreSQL 18 与 Supabase 结果是其交接记录，不当成本次 P4 实测结果。

离线结果包括真实 P1/P2 服务加内存 Repository、真实 P3 SQL 方法加脚本化 DB-API
测试替身。不能据此宣称真实数据库行锁、云端重启持久化或浏览器持续登录已经验收。

## 补齐连接后如何继续

用户电脑仓库外已有本地 secrets.toml，ACCOUNT_SECRET 已生成。
请 P3 只填入原有 DATABASE_URL，用户告诉 P4“已填好”即可继续，由 P4 安全读取。
不需要服务器 SSH 密码，也不要求在聊天发送数据库密码。

真实 PostgreSQL 测试显式读取 SWEATERCO_P4_TEST_DATABASE_URL，
并要求 SWEATERCO_P4_ALLOW_TEST_SCHEMA=1。不会自动使用生产 DATABASE_URL。
测试创建随机 p4_test_ 开头的独立 schema，只在其中应用原 P3 迁移、创建测试账号，
最后仅删除这个自建 schema，不修改现有业务表。使用有权限的测试连接并先确认测试范围。

~~~powershell
python -m pytest -q tests/accounts/conversation/test_postgres_live.py
~~~

随后由 P5 接页面、P2/P5 落实 Cookie，再在 Streamlit Cloud 验证：
两个账号隔离、刷新、关闭再打开、重新登录、服务重启、退出与多标签页失效，
以及恢复原任务后不重跑已完成工作。

P4 后端就绪不代表这些浏览器功能已经完成。正式发布和共享分支合并仍需团队审核。
