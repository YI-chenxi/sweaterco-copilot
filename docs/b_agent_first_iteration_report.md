# SweaterCo B Agent 第一轮开发报告

## 1. 报告信息

- 开发分支：`feat/b-agent-orchestration`
- A1+A2 基线：`origin/feat/a2-judgement-tools`
- 基线提交：`741110caa8233c745d1a1c3cc40df7c3290df58e`
- 报告日期：2026-10-10
- 当前阶段：第一轮确定性 Agent 编排

## 2. 阶段结论

第一轮 B 层已经完成。项目现在具备一个不依赖 LLM 的确定性 Agent 编排核心，可以接收结构化 Intent，检查必填参数，调用现有 A1 Retrieval 和 A2 Judgement pipeline，并返回包含 Evidence、warnings、assumptions 和结构化 metadata 的 `AgentReply`。

本轮没有修改任何 `app/tools/` 文件，没有改变 A1/A2 的数据读取、判断阈值、风险评分或可行性计算规则。

当前数据流如下：

```text
IntentDecision
  ↓
检查必填 slots
  ├─ 缺失 → clarification AgentReply
  └─ 完整
       ↓
选择业务 handler
       ↓
调用 A1 Retrieval
       ↓
调用 A2 Integration / Judgement
       ↓
模板化 Response Builder
       ↓
AgentReply
```

第一轮已接通四条真实业务 pipeline：

1. 订单状态
2. 生产正常性
3. Morning Briefing
4. 接单可行性

## 3. 设计原则

本轮开发遵循以下原则：

1. B 层只负责编排，不重新读取 CSV。
2. B 层不重新计算日期差、平均产量、风险分或产能。
3. A1 Retrieval 和 A2 Judgement 继续作为业务事实与计算的 source of truth。
4. 缺少输入时返回 clarification，不让 Agent 猜测。
5. 工具无法判断时返回明确失败，不让 LLM 用常识补齐工厂事实。
6. `CONDITIONAL`、`ABNORMAL`、`AT_RISK` 等结构化结论必须原样保留。
7. Evidence、warnings、assumptions 和 rule version 必须传递到 Agent 输出。
8. 第一轮不接入 LLM，先确保编排逻辑可单独测试。

## 4. 修改的现有文件

### 4.1 `app/agent/contracts.py`

对现有 `AgentReply` 做了向后兼容扩充。

新增字段：

- `warnings`：保存工具警告和业务边界信息。
- `assumptions`：保存 Feasibility 使用的业务假设。
- `metadata`：保存 Intent、执行状态、rule version 和完整结构化结果。

当前结构：

```python
@dataclass
class AgentReply:
    text: str
    evidence: list[Evidence] = field(default_factory=list)
    needs_clarification: bool = False
    suggested_question: str | None = None
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

所有新增字段均有默认值，因此组员原有的 `AgentReply(text="...")` 调用方式保持兼容。

## 5. 新增应用文件

### 5.1 `app/agent/intents.py`

该文件定义 B 层支持的 Intent 和 slot contract。

#### `Intent`

第一轮支持：

```text
ORDER_STATUS
PRODUCTION_NORMALITY
MORNING_BRIEFING
ORDER_FEASIBILITY
UNKNOWN
```

`Intent` 同时继承 `str` 和 `Enum`，方便后续用于 JSON、日志和 LLM structured output。

#### `REQUIRED_SLOTS`

定义每个 Intent 的必填参数：

| Intent | 必填参数 |
|---|---|
| `ORDER_STATUS` | `order_id` |
| `PRODUCTION_NORMALITY` | `stage` |
| `MORNING_BRIEFING` | 无 |
| `ORDER_FEASIBILITY` | `category`, `pieces`, `requested_delivery_date` |

#### `IntentDecision`

表示执行工具前的结构化路由结果：

```python
IntentDecision(
    intent=Intent.ORDER_STATUS,
    slots={"order_id": "ORD-057"},
    missing_slots=(),
)
```

当前由测试或调用方直接构造；后续可以由 LLM intent parser 生成。

#### `required_slots_for(intent)`

返回指定 Intent 所要求的 slot 集合。

#### `find_missing_slots(decision)`

合并以下两类缺失信息：

- Router 明确声明的 `missing_slots`
- 根据实际 slot 内容自动识别的缺失值

空字符串、全空格字符串和 `None` 都会被视为缺失。返回结果排序固定，便于测试和稳定展示。

### 5.2 `app/agent/settings.py`

该文件集中管理 B 层运行配置。

#### `AgentSettings`

当前配置：

```python
reference_date = date(2026, 4, 1)
briefing_top_n = 5
```

`reference_date` 固定使用课程数据集的 reference date，不会误用电脑当前日期。

#### `AgentSettings.__post_init__()`

验证 `briefing_top_n` 必须大于零，防止无效配置进入 Discovery pipeline。

### 5.3 `app/agent/tool_registry.py`

该文件负责集中创建和持有已有 A1 Retrieval tools。

#### `AgentToolRegistry`

注册以下工具：

- `OrderLookupTool`
- `OrderSearchTool`
- `ProductionRetrievalTool`
- `WorkshopRetrievalTool`

Orchestrator 不需要在不同 handler 中重复创建工具。Registry 也构成依赖注入边界，使测试或未来运行环境可以传入替代实现。

#### `AgentToolRegistry.from_project_data()`

使用项目默认 CSV 路径创建完整 Registry：

```python
registry = AgentToolRegistry.from_project_data()
```

B 层本身不读取 CSV，实际读取仍由 A1 tools 负责。

### 5.4 `app/agent/response_builder.py`

该文件把结构化工具结果转换为经理可读的确定性回答。它不进行新的业务判断，只表达已有结果。

#### `_warning_suffix(warnings)`

将一个或多个 warning 添加到回答末尾，避免 warning 被静默丢弃。

#### `_format_days_to_due(days_to_due)`

把日期差转换为自然语言：

```text
-3 → 交期已过去 3 天
 0 → 今天到期
 5 → 距离交期还有 5 天
```

#### `build_clarification_reply(decision, missing_slots)`

当 slot 不完整时生成澄清回答，例如：

```text
请补充以下信息：产品类别、期望交付日期。
```

同时设置：

```python
needs_clarification = True
suggested_question = ...
metadata["missing_slots"] = [...]
```

#### `build_unknown_intent_reply()`

无法识别支持的 Intent 时返回安全澄清，不会随意选择工具。

#### `build_failure_reply(intent, message)`

把 A2 的 `ValueError` 或输入类型错误转换为明确失败回答。错误不会被包装成成功，也不会自动补充缺失事实。

#### `build_order_status_reply(result)`

生成订单状态回答并保留：

- `AT_RISK / NORMAL`
- days to due
- days since activity
- labels
- rule version
- Evidence
- warnings
- 完整 structured result

未知订单会返回 warning，不会猜测相似订单。

#### `build_production_normality_reply(result)`

生成生产正常性回答并展示：

- 工序
- `NORMAL / WATCH / ABNORMAL`
- 历史基准
- 最近表现
- 偏差百分比
- 方向
- rule version

回答不会添加工具没有提供的根因解释。

#### `_finding_line(index, finding)`

将一个 Discovery finding 转换为晨报条目。订单 finding 展示交期和活动情况；生产 finding 展示产出偏差。

#### `build_morning_briefing_reply(result)`

将已排序的 Top-N findings 转换成编号晨报，只描述 finding 标题、风险分、交期或停滞指标，以及生产偏差，不推断根因。

#### `build_feasibility_reply(result)`

生成接单评估回答并保留：

- `SUPPORTED / CONDITIONAL / NOT_SUPPORTED`
- 工作日
- 内部负产能余量
- 具备名义覆盖能力的外协数量
- 全部 assumptions
- warnings
- Evidence
- rule version
- structured result

其中 `CONDITIONAL` 会原样保留，不会转换为肯定承诺。

### 5.5 `app/agent/orchestrator.py`

该文件是第一轮 B 层的核心入口。

#### `AgentOrchestrator.__init__(registry, settings)`

接收：

- `AgentToolRegistry`
- `AgentSettings`

未传入时自动使用项目默认 tools 和 `2026-04-01` reference date。内部建立 Intent 到 handler 的映射。

#### `AgentOrchestrator.handle(decision)`

处理顺序：

1. 检查是否为 `UNKNOWN`
2. 计算缺失 slots
3. 缺失则生成 clarification
4. 选择 handler
5. 执行业务 pipeline
6. 捕获 `TypeError` 和 `ValueError`
7. 返回 `AgentReply`

没有使用过度宽泛的异常捕获，因此编程错误和未知数据契约错误仍会被测试及时发现。

#### `_handle_order_status(slots)`

调用链：

```text
OrderLookupTool
  → judge_order_from_retrieval
  → build_order_status_reply
```

`today` 由 `AgentSettings.reference_date` 注入。

#### `_handle_production_normality(slots)`

调用链：

```text
ProductionRetrievalTool
  → judge_production_from_retrieval
  → build_production_normality_reply
```

工序名称统一转换为大写。

#### `_handle_morning_briefing(slots)`

调用链：

```text
OrderSearchTool + ProductionRetrievalTool
  → discover_factory_risks
  → build_morning_briefing_reply
```

支持可选 `top_n`；未提供时使用配置中的 5。布尔值、非整数、零和负数都会被拒绝。

#### `_handle_order_feasibility(slots)`

调用链：

```text
OrderSearchTool
+ ProductionRetrievalTool
+ WorkshopRetrievalTool
  → estimate_feasibility_from_retrieval
  → build_feasibility_reply
```

B 层额外验证订单件数：

- 接受正整数
- 可将合法整数字符串转换为整数
- 拒绝零和负数
- 拒绝非法字符串
- 显式拒绝 `True` 和 `False`

最后一点可以防止 Python 把 `True` 当作 1 件。

## 6. 新增测试文件

### 6.1 `tests/test_agent_intents.py`

包含 2 个测试：

1. 自动识别空字符串和不存在的 slot。
2. 保留上游 Router 明确声明的 missing slot。

### 6.2 `tests/test_agent_orchestration.py`

包含 9 个测试。

#### 真实订单状态

使用真实 `orders.csv` 查询 ORD-057，验证：

```text
judgement = NORMAL
rule_version = ORDER_RISK_V1
Evidence reference = ORD-057
```

#### 真实生产正常性

使用真实生产数据验证 Assembly：

```text
historical baseline = 774.9
recent performance = 425.5
deviation = -45.1%
judgement = ABNORMAL
```

#### 真实 Morning Briefing

验证返回 5 个 findings，第一项为 `ORDER:ORD-002`，并保留 Evidence。

#### 真实接单可行性

验证以下请求：

```text
800 件 TOPS
交期 2026-04-25
```

结果为 `CONDITIONAL`，工作日为 21，并保留 assumptions。

#### Clarification

缺少 category 和交期时：

- 不执行工具
- 返回 `needs_clarification=True`
- metadata 记录缺失字段
- 不生成 Evidence

#### 未知订单

ORD-999 返回 warning，不猜测订单，Evidence 为空。

#### 无效交期

请求日期早于 reference date 时返回明确失败，不生成虚假 feasibility。

#### 布尔件数

`pieces=True` 会被拒绝，不会错误地转换成 1 件。

#### Unknown Intent

无法确定用户意图时返回 clarification。

## 7. 验证结果

修改前：

```text
53 tests passed
```

修改后：

```text
64 tests passed
```

新增 11 个测试，所有原有测试继续通过。

质量检查：

```text
pytest: 64 passed
ruff: All checks passed
git diff --check: passed
```

真实数据冒烟结果：

| Pipeline | 结果 |
|---|---|
| ORD-057 状态 | `NORMAL` |
| Assembly 正常性 | `ABNORMAL`，偏差 `-45.1%` |
| Morning Briefing | 返回 Top 5，首项 `ORD-002` |
| 800 件 TOPS，2026-04-25 | `CONDITIONAL` |

## 8. 代码卫生与变更边界

本轮只修改或新增：

```text
app/agent/contracts.py
app/agent/intents.py
app/agent/settings.py
app/agent/tool_registry.py
app/agent/response_builder.py
app/agent/orchestrator.py
tests/test_agent_intents.py
tests/test_agent_orchestration.py
docs/b_agent_first_iteration_report.md
```

本轮没有修改：

```text
app/tools/
data/
app/ui/
```

因此：

- A1 Retrieval 未修改。
- A2 Judgement 未修改。
- A1 到 A2 integration 未修改。
- CSV 数据未修改。
- C 的 UI 未修改。
- 当前改动保留在 `feat/b-agent-orchestration` 工作区，尚未创建 Git commit。

## 9. 当前限制

以下内容尚未实现：

- 自然语言到 `IntentDecision` 的 LLM parser
- 多轮 slot filling
- 直接订单搜索和 workshop 查询 Intent
- Evidence 精简与 full trace 分层
- LLM 回答润色
- Streamlit 接入 Orchestrator
- 30 道以上的 ground-truth evaluation set
- Agent 日志和 audit trace

当前完整 Discovery 和 Feasibility 仍会携带数百条源级 Evidence。完整证据有利于审计，但下一阶段应区分：

```text
full_evidence
  完整审计记录

response_evidence
  直接支撑当前回答的精选证据
```

## 10. 下一阶段建议

建议第二轮按以下顺序推进：

1. 定义自然语言 intent parser 的 JSON contract。
2. 实现可替换的 LLM client adapter。
3. 将自然语言转换为 `IntentDecision`。
4. 实现多轮 clarification 和 slot merging。
5. 增加直接订单搜索与 workshop 查询。
6. 将 Evidence 分为 full trace 和 response evidence。
7. 增加 LLM faithfulness tests。
8. 建立 30 道以上的 ground-truth evaluation questions。
9. 最后将 Orchestrator 接入 Streamlit UI。

第一轮完成后的系统已经可以在不使用 LLM 的情况下稳定执行四条核心业务 pipeline。下一阶段的主要任务不是修改 A1/A2 计算逻辑，而是在保持结构化结果忠实性的前提下加入自然语言理解、多轮澄清和回答表达。
