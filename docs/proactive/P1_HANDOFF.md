# P1 自然语言解析交接说明

**交接日期：**2026-11-05
**开发分支：**`feat/proactive-intent-parser`
**起点：**`integration/intent-enhancement` 的 `fec149e`
**状态：**独立模块实现和测试完成；尚未接入 Agent/UI。仓库原有的未跟踪交接文档未修改。

## 1. 目标与责任边界

P1 将中英文用户请求转换为 `ProactiveCommand`，供集成层决定后续路由。解析器回答的是“用户要对哪类主动任务做什么、明确给出了哪些参数、还缺什么信息”。它不检索业务事实，不计算可行性，不创建任务，不写数据库，不授权，不确认或执行动作，也不发送通知。

依据：`docs/proactive/INTERFACE_CONTRACT.md` 和 `docs/proactive/DEVELOPMENT_PLAN.md`。此次只新增独立模块，未修改共享的 `AgentService`、`turn_service.py`、`intents.py`、现有增强解析器或 Streamlit UI。

## 2. 交付文件

| 文件 | 用途 |
| --- | --- |
| `app/agent/proactive/understanding/parser.py` | `ProactiveCommand` 数据类、模型调用、参数规范化、目标校验和安全降级 |
| `app/agent/proactive/understanding/prompts.py` | 主动指令的提示词与结构化输出格式 |
| `app/agent/proactive/understanding/__init__.py` | 对外导出解析器和命令类型 |
| `app/agent/proactive/understanding/README.md` | 简明工具契约、调用方法和边界 |
| `tests/proactive/test_understanding.py` | 10 个离线测试，覆盖主要解析及安全边界 |

## 3. 对外调用与输出

```python
from app.agent.proactive.understanding import ProactiveCommandParser

parser = ProactiveCommandParser(llm_client)
command = parser.parse(
    "从2026年11月6日开始每3天早上8点生成晨报",
    context={"briefings": [], "watches": [], "proposals": []},
)
payload = command.to_dict()
```

`llm_client` 必须实现现有 `StructuredLLMClient.generate_structured()` 接口。`context` 可省略；若提供，当前格式是三个键：`briefings`、`watches`、`proposals`，各自为含 `id` 字符串的对象列表。传给模型的上下文仅含这些 ID，不含完整业务记录。调用方必须先按 `workspace_id` 和权限过滤上下文；P1 不承担授权。

上述示例的预期输出：

```json
{
  "command_type": "create",
  "task_type": "briefing",
  "parameters": {
    "interval_days": 3,
    "local_time": "08:00",
    "start_date": "2026-11-06",
    "timezone": "Asia/Singapore"
  },
  "target_id": null,
  "missing_fields": [],
  "language": "zh",
  "reference_text": null
}
```

`command_type` 取值为 `create / update / cancel / list / get / confirm / reject / unknown`。`task_type` 为 `briefing / watch / null`；确认、拒绝和未知命令使用 `null`。`missing_fields` 非空时，集成层应先追问，不能创建提案或执行。`reference_text` 当前始终为 `null`，避免将可能含秘密的原文复制进审计记录。

## 4. 当前识别规则

### 定时简报

识别定时创建、修改、取消、列表和详情请求。创建时要求 `interval_days`、`local_time`、`start_date`；时区默认 `Asia/Singapore`。间隔必须为正整数，时间为 24 小时 `HH:MM`，开始日期为明确的 ISO 日期。更新只保留用户本轮要改的字段，不自动覆盖原有时区。“现在给我一份晨报”归现有即时业务对话，不能创建定时任务。

### 持续监控

识别创建、取消、列表和详情请求。当前条件仅允许 `no_progress_by_deadline` 与 `status_equals`。创建时要求 `order_id`、`condition_type`、`check_interval_minutes`；前一种条件还要求 `deadline_at`，后一种还要求 `expected_status`。安全格式变体 `ORD058` 可规范为 `ORD-058`；`ODR058` 之类疑似拼错不会静默修正。截止时间须有明确日期和时间，带时区的模型输出转为 UTC。现有 V1 `WatchService` 没有更新方法，因此 `update watch` 返回 `unknown`。

### 确认与目标定位

`confirm` 和 `reject` 必须指向 `context.proposals` 中的提案。目标 ID 必须为 UUID，与 P4 接口一致。若有多个候选，用户消息必须明确包含目标 ID；只说“Yes”不会选一个。修改、取消和详情同样要求目标 UUID 属于相应上下文。确认还需要肯定措辞；否定措辞不能被模型误判成确认。P1 只解析确认意图，真正的权限复核、过期检查、幂等执行和审计由 P4 负责。

### 其他请求

即时订单查询、可行性分析及“为什么”追问留给现有业务对话。生成催办草稿、发送消息、添加订单备注尚无已批准的 V1 动作工具契约，不能伪装成已支持的主动命令。单次只返回一个命令；多命令拆分尚未定义。

## 5. 校验和失败行为

模型用于意图和参数提取；代码再次检查命令类型、任务类型、参数白名单、正整数、日期时间、订单号、条件枚举和目标 ID。无效字段不进入 `parameters`。缺字段列入 `missing_fields`。空输入、模型异常或无效结构返回 `unknown`，不会调用任何业务工具。

当前校验仍有明确边界：复杂口语中的数字与状态值主要依赖模型抽取，测试只覆盖部分来源核对；相对日期（例如“周四”）不自动落到某个具体时刻，需要澄清为明确日期和时间。解析器不验证订单是否真实存在，也不判断监控条件是否真正触发。

## 6. 集成建议

1. 集成入口先区分主动命令与现有即时业务请求，不能让定时请求误落入普通晨报执行。
2. `missing_fields` 非空或 `command_type=unknown` 时只澄清或说明不支持；不要调用变更工具。
3. `create / update / cancel` 进入 P4 `ActionService.propose_action()`；用户确认后由 P4 执行。`confirm / reject` 传给 P4 的对应方法。`list / get` 为只读查询。
4. P3/P5 接收的参数须继续做服务端校验。解析成功不代表授权通过，也不代表目标仍存在。
5. 集成人员在共享文件中实现路由；P1 分支不直接改共享 Agent/UI 文件。

## 7. 需要团队决定的接口问题

| 问题 | 当前处理 | 需要确认 |
| --- | --- | --- |
| 上下文 ID 的来源与格式 | 接收三个 ID 列表 | 谁按工作区、用户和状态筛选；提案 ID 是否暴露给用户 |
| `get` 的服务语义 | 可解析命令 | 简报历史和监控详情分别由哪个服务方法提供；当前 P5 接口无 `get_watch` |
| 监控修改 | 返回 `unknown` | P5 是否增加 `update_watch`；若增加，先更新契约 |
| 不支持的动作 | 返回 `unknown` | 订单备注、催办消息发送等工具的动作名、参数、权限及影响范围 |
| 多命令请求 | 当前仅单命令 | 是否扩展 P1 输出结构，还是由集成层拆分 |
| 解析问题的表达 | 主要使用 `missing_fields` | 是否需增加歧义/不支持原因错误码和用户可读澄清文案 |

## 8. 验证记录与复现

在当前本地分支验证：P1 测试 **10 passed**；项目完整回归 **547 passed**；Ruff 检查通过。测试使用 `MockLLMClient` 的预设输出，没有调用在线模型，因此不能据此声称真实模型解析准确率。P2 仍需开展独立评估；集成后还需端到端测试。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\proactive\test_understanding.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app\agent\proactive\understanding tests\proactive\test_understanding.py
```

合并前请附上本交接说明、测试结果和上述待决定事项，并由团队审核接口与集成顺序。
