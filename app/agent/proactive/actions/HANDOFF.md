# Person 4 确认执行模块交接

开发分支：feat/confirmed-actions
开发基线：integration/intent-enhancement 的 fec149e
日期：2026-11-03

## 本次完成内容

本次是新的主动任务功能中的 P4 确认执行模块，不替换上一次的澄清与参数校验。

已实现：

- 操作提案：保存动作类型、目标、参数、创建者、工作区、有效期及幂等键；提出操作本身不调用写操作。
- 确认、拒绝与过期：确认绑定一个明确提案 UUID；拒绝、过期和状态冲突均阻止写操作。
- 执行前再次检查权限与目标版本；不使用固定工厂快照日期作为确认时钟。
- SQLite 保存提案和审计记录，重开数据库、独立 Python 进程均可读取记录。
- 原子领取执行权；重复确认、并发确认或执行后保存失败不会再次调用执行器。
- 可注入服务执行器、存储、身份、权限策略、时钟和请求标识，不依赖其他成员未完成分支。

原有 clarification.py、clarification_adapter.py、共享契约、A1/A2、Agent 入口和 Streamlit UI 均未修改。

## 交接接口

导入路径：app.agent.proactive.actions

ActionService 提供：
1. propose_action(workspace_id, command)
2. confirm_action(workspace_id, proposal_id)
3. reject_action(workspace_id, proposal_id)
4. get_action_history(workspace_id)

前三个返回约定的 ServiceResult 字典。历史返回审计事件列表；无权限或存储失败时抛出带安全错误代码的 ActionError，不返回假成功空列表。

状态沿用 pending_confirmation / executed / failed / rejected / expired。
内部领取标记不扩展共享枚举。默认提案有效期 15 分钟，可通过构造参数调整，最终产品策略由团队确认。

## 各人需要怎样接入

P1：只传规范化的业务命令，不能把一句“好的”自行绑定到不明确提案。参数不完整先澄清。确认与拒绝使用明确 proposal_id。
P2：模块验收测试位于 tests/test_confirmed_actions.py，可复用这些输入输出补充跨模块用例。
P3：提供晨报创建、修改、取消的业务适配器及完整参数校验。
P5：提供监控创建、取消的业务适配器及完整参数校验。当前 V1 没有 update_watch，未接入前明确拒绝这一操作。
集成/UI：从可信登录状态提供用户身份和权限策略；保存和展示明确提案；接入确认/拒绝按钮、错误处理和历史查看。

## 业务适配器必须满足的约定

每个适配器有只读 validate() 与真正写入的 execute()，具体签名见 README 和 models.py。

- validate() 校验业务参数、目标存在及状态；修改/取消必须返回可比较的目标版本。
- execute() 接收 idempotency_key 和 expected_version，在业务写入事务中再次比较版本并记录幂等键。
- 不要将未提供幂等支持的普通 CRUD 方法直接包装后宣称跨服务“严格只执行一次”。

现有 P3/P5 文档签名尚未表达版本检查和幂等参数，需要在集成时与其存储适配器统一；本次没有私自修改公共契约。

## 测试结果和复验

- P4 专属测试：76 passed。
- 全项目回归：613 passed，原有 537 项加 P4 新增 76 项。
- P4 Python 文件 Ruff 检查与格式检查通过。
- 无需 API 的离线演示通过：确认前零执行，确认及重复确认合计一次，拒绝不执行。
- 包含两个数据库连接的并发验证、独立进程持久化、权限撤销、目标变化、到期边界、事务回滚和执行结果保存失败。
- 所有执行器均为 Mock/模拟动作；没有真实模型调用，也没有实际创建晨报或监控任务。

在本分支目录、使用项目虚拟环境运行：

~~~powershell
python -m app.agent.proactive.actions.demo
python -m pytest -q tests/test_confirmed_actions.py
python -m pytest -q
python -m ruff check app/agent/proactive/actions tests/test_confirmed_actions.py
python -m ruff format --check app/agent/proactive/actions tests/test_confirmed_actions.py
git diff --check
~~~

## 当前限制与后续集成

本次只完成 P4 独立模块；真实 P3/P5 适配、前端按钮、登录授权和云端共享数据库仍需集成。

本地 SQLite 的跨进程持久化，不等于 Streamlit Community Cloud 本地文件永久保存。部署前由团队选定共享持久化存储并替换 Repository。

若执行过程中进程中断或结果不确定，requires_reconciliation 为 true；系统保留领取记录，不自动重试。需要先核对业务幂等回执或实际目标状态。本版未提供管理员强制解锁功能。

过期状态在确认或拒绝时落库；历史查询保持只读，不主动扫描所有过期提案。

当前保存在本地独立分支，未上传 GitHub、未修改公共分支。审阅后提交 PR 到 integration/intent-enhancement，由团队审核后合并。
