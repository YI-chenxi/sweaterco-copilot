# SweaterCo B Agent 第二轮：OpenAI LLM 接入报告

## 1. 报告信息

- 开发分支：`feat/b-agent-orchestration`
- 第一轮提交：`731f04c feat: add deterministic B agent orchestration`
- A1+A2 基线提交：`741110c feat: add A2 judgement tools and integration`
- 报告日期：2026-10-10
- 当前阶段：第二轮，自然语言理解与中英文回复接入
- 默认交互语言：英文；识别到中文输入时返回中文
- 默认模型：`gpt-6-luna`，可通过环境变量覆盖

## 2. 本轮结论

第二轮已经完成一个可替换、可测试的 LLM 接入层。系统现在能够接收英文或中文自然语言，由 OpenAI Responses API 将输入解析为严格结构化的 Intent，再复用第一轮已经验证的 B 层编排器调用 A1 Retrieval 和 A2 Judgement，最后返回与用户语言一致的确定性回复。

本轮最重要的边界是：LLM 只理解语言和抽取参数，不负责工厂事实查询、风险计算、产能计算或最终业务判断。因此，接入模型不会改变 A1/A2 的规则，也不会让模型绕开现有工具“猜答案”。

当前数据流如下：

```text
English / Chinese user text
          ↓
AgentService.handle_text
          ↓
IntentParser.parse
          ↓
StructuredLLMClient
  ├─ OpenAILLMClient（真实 API）
  └─ MockLLMClient（测试 / 离线开发）
          ↓
严格 JSON Schema 输出
          ↓
IntentDecision + ResponseLanguage + slots
          ↓
AgentOrchestrator
          ↓
A1 Retrieval → A2 Judgement / Integration
          ↓
双语 Response Builder
          ↓
AgentReply（text + evidence + warnings + assumptions + metadata）
```

## 3. OpenAI 接入设计

### 3.1 API 与结构化输出

实现使用 OpenAI Responses API，并通过 `text.format` 提交 `strict: true` 的 JSON Schema。这样可以让模型输出固定字段，而不是让下游代码解析一段自由文本。

官方参考：

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
- [Model selection](https://developers.openai.com/api/docs/guides/model-selection)

结构化结果包含：

- `intent`
- `language`
- `order_id`
- `stage`
- `category`
- `pieces`
- `requested_delivery_date`
- `top_n`
- `missing_slots`

当前支持的 Intent：

1. `order_status`
2. `production_normality`
3. `morning_briefing`
4. `order_feasibility`
5. `unknown`

### 3.2 为什么 LLM 只做 Intent Parsing

这种边界保留了第一轮的确定性：

- 用户原始消息和意图提取提示词会发送给 OpenAI。
- CSV 数据、A1 检索结果和 A2 计算结果不会发送给 OpenAI。
- 订单状态、风险分、正常性、交付可行性仍由项目代码计算。
- 最终回复由本地双语模板生成，模型不能修改结构化业务结论。
- 模型失败时安全降级为 `unknown`，不会自动编造业务回答。

### 3.3 中英文策略

- 英文是默认语言。
- LLM 在结构化结果中返回 `en` 或 `zh`。
- 如果 LLM 调用或解析失败，本地代码通过中文字符检测选择降级回复语言。
- 订单号、工序和产品类别统一规范为大写。
- A2 原始 assumptions 原样保留在 metadata 中；面向用户的 assumptions 使用本地中英文版本。

## 4. 新增文件与函数

### 4.1 `app/agent/prompts.py`

集中管理模型提示词和 JSON Schema，避免 API 调用代码与业务规则混在一起。

- `INTENT_SYSTEM_PROMPT`
  - 告诉模型它只负责中英文意图识别和 slot 抽取。
  - 明确禁止回答业务问题、计算指标、猜测工厂事实或调用工具。
  - 描述四个受支持的 Intent、缺失参数规则、大小写标准化规则及中英文示例。
- `SLOT_NAMES`
  - 维护所有允许的 slot 名称，供 schema 与 payload 校验共用。
- `INTENT_RESPONSE_SCHEMA`
  - 定义严格的 JSON 输出结构。
  - 所有字段均要求出现；可选值使用 `null`，未知字段被拒绝。

### 4.2 `app/agent/llm_client.py`

提供可替换的结构化 LLM 访问层。

- `LLMClientError`
  - 统一表示模型请求失败、响应未完成、输出为空或 JSON 无效。
- `StructuredLLMClient`
  - Protocol 接口；Intent Parser 只依赖该最小接口，不依赖特定供应商 SDK。
- `MockLLMClient.__init__`
  - 接收一组排队的结构化响应或异常，用于离线测试。
- `MockLLMClient.generate_structured`
  - 记录 prompt/schema 调用并返回下一条模拟响应。
  - 没有配置响应时明确抛出 `LLMClientError`。
- `OpenAILLMClient.__init__`
  - 接收模型名、API Key、超时和可注入 client。
  - 延迟导入 OpenAI SDK，缺少依赖时给出明确错误。
  - 允许测试注入 fake client，不需要真实网络请求。
- `OpenAILLMClient.generate_structured`
  - 调用 `client.responses.create(...)`。
  - 使用严格 JSON Schema 请求结构化 Intent。
  - 检查完成状态、空输出、JSON 格式和顶层对象类型。
- `create_llm_client`
  - 根据 `LLMSettings.provider` 创建 OpenAI 或 Mock client。
  - OpenAI 模式没有 API Key 时立即失败，避免请求阶段才暴露配置问题。

### 4.3 `app/agent/intent_parser.py`

负责把模型 payload 转换为项目内部的 `IntentDecision`。

- `detect_response_language`
  - 在模型失败时用确定性字符检测选择中文或英文。
- `decision_from_payload`
  - 校验 Intent 和语言枚举。
  - 清理并标准化 `order_id`、`stage`、`category`。
  - 将 `pieces`、`top_n` 安全转换为整数。
  - 只接受项目声明过的 `missing_slots`。
  - 对 `unknown` 清空 slots，防止无效数据继续流入工具层。
- `IntentParser.__init__`
  - 注入任意符合 `StructuredLLMClient` 的实现。
- `IntentParser.parse`
  - 处理空输入、调用结构化模型、执行 payload 转换。
  - 模型或解析失败时记录 warning 并返回安全的 `unknown`。

### 4.4 `app/agent/service.py`

提供自然语言入口，连接配置、LLM Parser 和第一轮 Orchestrator。

- `AgentService.__init__`
  - 注入 `IntentParser` 和 `AgentOrchestrator`，便于独立测试。
- `AgentService.from_env`
  - 从环境变量读取 LLM 配置。
  - 创建 provider client、Intent Parser 和 Orchestrator。
  - 是本地应用或未来 UI 接入时推荐使用的工厂方法。
- `AgentService.handle_text`
  - 接收一条中英文消息。
  - 完成 Intent Parsing、工具编排和回复生成。
  - 将最终语言写入 reply metadata。

### 4.5 `tests/test_llm_client.py`

验证 LLM adapter，不访问真实网络。

- `FakeResponses.create`：捕获 Responses API 参数并返回假响应。
- `FakeOpenAI`：提供与 SDK 相似的 `responses` 接口。
- `test_mock_client_records_prompts_and_returns_a_copy`：验证 Mock 调用记录与结果隔离。
- `test_openai_client_uses_responses_api_strict_json_schema`：验证 Responses API、模型名和 strict schema 的调用形状。
- `test_openai_client_rejects_incomplete_responses`：验证未完成响应会被拒绝。
- `test_openai_client_rejects_invalid_json`：验证无效 JSON 会被拒绝。

### 4.6 `tests/test_intent_parser.py`

验证中英文 Intent、slot 清理和错误降级。

- `payload`：生成测试用完整 payload。
- 英文订单状态解析测试。
- 中文生产正常性解析测试。
- 可行性缺失 category 测试。
- 非法 Intent 降级测试。
- 模型失败后的中文 fallback 测试。
- 空输入不调用模型测试。

### 4.7 `tests/test_agent_service.py`

使用 Mock LLM 配合真实 A1/A2 pipeline 做自然语言端到端测试。

- `payload`：生成结构化模型测试响应。
- `service_for`：组合 Mock Parser 与真实 Orchestrator。
- 英文订单查询完整链路测试。
- 中文生产正常性完整链路测试。
- 英文缺失参数 clarification 测试。
- 中文可行性及 assumptions 保留测试。

### 4.8 `tests/test_agent_settings.py`

验证 LLM 环境配置。

- OpenAI 环境变量读取测试。
- 未知 provider 拒绝测试。
- OpenAI 模式缺少 API Key 测试。
- Mock 模式无需 API Key 测试。

### 4.9 `tests/test_response_builder.py`

验证中英文确定性模板的日期边界表达。

- 英文今天到期和今天活动使用自然表达，不出现 `0 days ago`。
- 中文今天到期和今天活动使用“今天”表达。

## 5. 修改的现有文件与函数

### 5.1 `requirements.txt`

- 新增 `openai>=2,<3`。
- 本轮在项目虚拟环境中实际安装并验证的版本为 `openai 2.54.0`。

### 5.2 `.env.example`

新增本地 LLM 配置模板：

- `MODEL_PROVIDER`
- `OPENAI_MODEL`
- `OPENAI_API_KEY`
- `OPENAI_TIMEOUT_SECONDS`
- `LOG_LEVEL`

`.env.example` 只保存字段名和安全默认值，不包含任何真实密钥。

### 5.3 `app/agent/settings.py`

保留原有 `AgentSettings`，新增：

- `LLMSettings`
  - 保存 provider、model、API Key 和超时。
- `LLMSettings.__post_init__`
  - 校验 provider 只能是 `openai` 或 `mock`。
  - 校验模型名非空、超时为正数。
- `LLMSettings.from_env`
  - 可选加载本地 `.env`。
  - 读取并清理环境变量。

### 5.4 `app/agent/intents.py`

- 新增 `ResponseLanguage`
  - `ENGLISH = "en"`
  - `CHINESE = "zh"`
- `IntentDecision`
  - 新增 `language` 字段，默认值为英文，保持旧调用兼容。

原有 `required_slots_for` 和 `find_missing_slots` 逻辑保持不变。

### 5.5 `app/agent/orchestrator.py`

- `AgentOrchestrator.handle`
  - 继续执行 Intent 路由和必填 slot 检查。
  - 现在把 `decision.language` 传给所有回复构造函数。
- `_handle_order_status`
  - 调用现有订单 pipeline，并按请求语言组织回复。
- `_handle_production_normality`
  - 调用现有生产正常性 pipeline，并按请求语言组织回复。
- `_handle_morning_briefing`
  - 调用风险 briefing pipeline，并按请求语言组织标题和条目。
- `_handle_order_feasibility`
  - 调用可行性 pipeline，并区分本地化 assumptions 与 A2 原始 assumptions。

工具注册、A1/A2 调用方式和异常边界未改变。

### 5.6 `app/agent/response_builder.py`

该文件扩展为确定性的双语回复层。

- `SLOT_LABELS`
  - 保存缺失参数在英文和中文中的可读名称。
- `ASSUMPTION_TRANSLATIONS`
  - 为已知 A2 assumptions 提供英文用户版本。
- `_warning_suffix`
  - 将 warnings 附加为中文或英文提示。
  - 英文展示会翻译已知的 A2 中文校验信息，但 `AgentReply.warnings` 仍保留原文。
- `_localized_source_message`
  - 翻译已知 A2 validation message，仅影响用户可见文字，不修改源警告。
- `_format_days_to_due`
  - 正确描述逾期、今天到期和剩余天数。
- `_format_days_since_activity`
  - 正确描述今天、一天前和多天前，避免 `0 days ago` 等生硬表达。
- `build_clarification_reply`
  - 按语言列出缺失 slots，并生成补充信息问题。
- `build_unknown_intent_reply`
  - 用中英文说明当前支持的四类请求。
- `build_failure_reply`
  - 将工具失败转换为安全回复，不虚构结果。
- `build_order_status_reply`
  - 输出订单状态、工序、完成率、截止日期和 activity 信息。
- `build_production_normality_reply`
  - 输出阶段是否正常、平均产量、activity、发现项和 warnings。
- `_finding_line`
  - 将结构化 finding 转换为双语单行描述。
- `build_morning_briefing_reply`
  - 输出排好序的重点项目，并保持 evidence/metadata。
- `_localized_assumptions`
  - 根据回复语言生成面向用户的 assumption 列表。
- `build_feasibility_reply`
  - 输出 `FEASIBLE`、`CONDITIONAL` 或 `NOT_FEASIBLE` 结果。
  - 本地化 assumptions 放入 `AgentReply.assumptions`。
  - A2 原文放入 `metadata.source_assumptions`，保证审计信息不丢失。

### 5.7 `tests/test_agent_orchestration.py`

- 更新 feasibility 断言：
  - 验证用户可见 assumptions 是英文版本。
  - 验证 A2 原始 assumptions 仍保存在 metadata 中。

## 6. 配置和运行方式

### 6.1 安装依赖

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 6.2 创建本地配置

```powershell
Copy-Item .env.example .env
```

然后只在本地 `.env` 中填写：

```dotenv
MODEL_PROVIDER=openai
OPENAI_MODEL=gpt-6-luna
OPENAI_API_KEY=your_api_key_here
OPENAI_TIMEOUT_SECONDS=30
```

不要把 `.env` 或 API Key 提交到 Git。

### 6.3 最小调用示例

```python
from app.agent.service import AgentService

service = AgentService.from_env()

english_reply = service.handle_text("How is ORD-057?")
print(english_reply.text)

chinese_reply = service.handle_text("Assembly 最近正常吗？")
print(chinese_reply.text)
```

### 6.4 离线模式

自动化测试和离线开发可设置：

```dotenv
MODEL_PROVIDER=mock
```

`MockLLMClient` 需要由测试或调用方提供预设结构化响应。它不会自行理解自然语言，因此不适合作为真实用户入口。

## 7. 验证结果

本轮完成后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

结果：

- `85 passed`
- Ruff：`All checks passed!`
- `git diff --check`：无 whitespace error；只显示 Windows 的 LF/CRLF 转换提示
- OpenAI SDK 调用形状已通过 fake client 单元测试
- 英文订单、中文生产正常性、英文 Morning Briefing 和英文/中文可行性链路已使用 Mock LLM + 真实 A1/A2 工具完成离线冒烟验证

### 7.1 真实 OpenAI API 验证

2026-10-10 使用本地 `.env` 中配置的 API Key 和 `gpt-6-luna` 完成了六次真实 Responses API 请求。验证脚本只输出模型名、Intent、语言、业务状态、回复和 evidence 数量，没有读取或打印 API Key。

| 用例 | 模型解析结果 | 业务链路结果 |
|---|---|---|
| `How is order ORD-057?` | `order_status`, `en` | `NORMAL`；返回 1 条 evidence |
| `Assembly 最近生产正常吗？` | `production_normality`, `zh` | `ABNORMAL`；返回中文说明和 90 条 evidence |
| `Give me the top 3 items for the morning briefing.` | `morning_briefing`, `en`, `top_n=3` | 正确返回 3 个排序后的风险项和 394 条 evidence |
| `我们能接 800 件 TOPS，并在 2026-04-25 前交付吗？` | `order_feasibility`, `zh` | `CONDITIONAL`；返回中文 assumptions 和 400 条 evidence |
| `Can we accept 500 pieces by 2026-04-25?` | `order_feasibility`, `en`，缺少 `category` | 正确返回英文 clarification；没有调用业务 pipeline |
| `你好，请介绍一下你自己。` | `unknown`, `zh` | 正确返回中文能力边界说明；没有调用业务 pipeline |

六个在线用例全部符合预期，证明以下链路能够真实运行：

```text
OpenAI Responses API
  → strict JSON Schema Intent
  → bilingual payload validation
  → deterministic B orchestration
  → real A1 Retrieval / A2 Judgement
  → bilingual AgentReply
```

## 8. 安全性与代码卫生

- 未修改 `app/tools/` 下 A1/A2 组员代码。
- 未修改 CSV 数据。
- 未提交或打印 API Key。
- OpenAI client 与业务编排通过 Protocol 隔离。
- 模型错误不会进入业务 pipeline，而是安全降级。
- 工具输出仍保留 evidence、warnings、assumptions 和 metadata。
- 本报告与第二轮代码一同提交到 `feat/b-agent-orchestration` 分支；Word 交接文档仅保留在本地，不进入 Git。

## 9. 当前限制

1. 当前是一问一答，不会自动合并多轮对话中的缺失 slots。
2. LLM 只做 Intent Parsing；最终回复使用确定性模板，而不是 LLM 润色。
3. 当前还没有 CLI、Web UI 或聊天界面入口。
4. 中文/英文之外的语言会默认走英文回复。
5. 用户使用含糊产品名时，系统不会猜测 category，而会要求补充。
6. 当前没有持久化 API latency、token usage 或成本统计。

## 10. 建议的下一步

建议按以下顺序继续：

1. 增加一个简单 CLI 或 API endpoint，让 `AgentService.handle_text` 成为可直接交互的入口。
2. 增加 session state，用于多轮补齐 `missing_slots`。
3. 增加调用日志与 token/latency 统计，但必须排除 API Key 和敏感信息。
4. 根据真实输入样本建立更大的中英文 Intent 评测集，再决定是否调整模型或 prompt。
5. 创建 Pull Request，由组员复核 Agent 与 Streamlit UI 的接口边界。
