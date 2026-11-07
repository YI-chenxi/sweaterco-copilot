# 账号与持久聊天系统：Person 4 独立模块

开发分支：feat/account-person4-state-recovery
起点：integration/intent-enhancement，fec149ef98896a9d834eca5906ba8eb71045a3c7。
本模块是本轮账号系统 Person 4 的工作，不是上一轮 Confirmed Actions 的 Person 4。

## 已实现的范围

1. codec.py：ChatSession / AgentReply 的版本化 JSON 保存、完整恢复和校验。
2. service.py：通过可替换的身份、存储接口，组织恢复、执行一次对话、保存和读取。
3. contracts.py：供 Person 2、3 对接的暂定接口；不是已经获得团队同意的最终公共接口。
4. tests/accounts/conversation：测试替身、异常/隔离测试、与现有 Agent 的本地业务工具验证。

恢复包含任务顺序、任务状态、意图和参数、缺失参数、语言、任务级问题、全局问题、active_task_ids 和 next_task_number。不会把已完成任务改回可执行状态。

回复包含原始正文、Evidence、warnings、assumptions、澄清问题和 metadata。保存完整回复，不从 UI 展示文本反推后台信息。重建历史不会调用模型或业务工具。

这里恢复的是现有 ChatSession 的任务上下文。保存历史消息不等于自动把全部历史文本加入模型提示；本模块没有改变原有 Agent 的理解能力。

## 接口用法

~~~python
from app.accounts.conversation import PersistentConversationService

service = PersistentConversationService(
    agent=existing_agent,
    repository=person3_repository_adapter,
    identity=person2_identity_adapter,
)

opened = service.open_thread(login_session_handle, thread_uuid)
saved = service.handle_turn(
    login_session_handle,
    thread_uuid,
    text=user_input,
    request_id=request_uuid,
)
# 保存成功或重放后，重新读取当前历史；不要用旧请求的状态覆盖较新的会话。
current = service.open_thread(login_session_handle, thread_uuid)
~~~

以上是未来集成示意。两个 adapter 当前没有生产实现；代码中不默认放行，也不会自动使用 Fake。

thread_uuid 和 request_uuid 使用规范小写 UUID 字符串。会话创建、列表、重命名、删除由 Person 3 提供，本模块只读取现有会话和保存新轮次。一次完成的轮次包含一条 user 和一条 assistant 消息，revision 加 1。

同一次发送发生网络重试时，必须沿用原 request_id 和原始文本。主动发起的新消息才生成新 request_id。

## 关键行为

- 每次读取/发送均从身份服务获取可信 user_id、workspace_id、session_id，并在执行/保存边界复核。不能信任页面或模型填写的 user_id。
- 先读取一致的历史和状态，再申请存储端原子预留；没有确认获得预留，不执行 Agent。
- 同一 request_id 的已完成请求返回原保存结果，不再执行；原请求完成后又发生新轮次，重放也不会回滚新状态。
- 未完成/结果不确定的请求会阻止同一会话的其他新请求，等待核对；不会换一个请求编号就绕过。
- 数据损坏、未知版本、身份无效、保存失败会明确报错，不静默清空状态。
- 保存 user 消息、assistant 完整回复、最新状态、revision 和去重回执，必须由真实仓库放在一个事务中完成。
- Agent 异常或执行后保存结果不明确时，不自动重试。即使异常标记保存失败，原预留也必须保留。
- 错误对象只提供固定错误码/安全提示，不直接返回模型、凭据或数据库原始异常。

这不能被表述为任意外部操作的 exactly-once 保证。真实数据库事务、请求预留、外部写操作去重及崩溃核对都必须接好后再做整体验证。

## Person 2、3、5 后续配合

| 负责人 | 需要提供或确认 |
| --- | --- |
| Person 2 | 身份服务适配：有效登录会话、过期/注销/撤销、工作区权限；不得通过用户可编辑字段建立身份 |
| Person 3 | 存储服务适配：一致快照、事务保存、所有权检查、revision 冲突、持久请求预留/去重、删除并发语义；确认是否新增请求记录表 |
| Person 4（本模块） | 双方正式接口确定后适配；新增状态类型时维护版本/迁移；配合真实数据库联调 |
| Person 5 | 页面发送与历史加载接入；传登录会话句柄和 UUID，保存完成再更新展示；处理失效登录、冲突与结果待核对状态 |

具体契约、错误码和联调验收见 PROPOSED_INTERFACES.md。如果现有 P2/P3 接口命名不同，增加适配层即可，不需要照搬本模块名字。

## 独立验证

新增模块测试：

~~~powershell
python -m pytest -q tests/accounts/conversation
~~~

需要项目原有依赖和 pytest，不需要真实 API Key、数据库或登录账号。测试中的 FakeIdentity / FakeRepository 仅用于验证；FakeRepository 是内存实现，不能证明真实磁盘、跨进程或云端重启持久化。

包括实际本地业务工具参与的测试：先完成 ORD-057 查询、保留 800 TOPS 缺交期任务；状态 JSON 保存/恢复后补交期，只执行剩余任务。模型输出使用 Mock；不是在线模型准确率测试。

独立测试：108 passed；全仓回归：645 passed（原有 537 项 + 新增 108 项）。Ruff 检查及 Python 3.10 语法检查通过。

## 尚未包含

注册/密码/登录 cookie、真实 PostgreSQL 迁移和仓库、登录页面和主界面改造、旧聊天导入、未知状态版本迁移、跨账号数据迁移、外部写操作执行、崩溃请求自动恢复。

日期支持 date / datetime；datetime 保留 ISO 时间、UTC 偏移及 fold，不保留命名时区对象的身份。metadata 仅允许 JSON 基础类型、元组、日期和本模块白名单枚举。未知对象显式报错，不转换为字符串或丢弃。单个 JSON 文档上限 8 MiB，用户输入上限 32,000 字符。
