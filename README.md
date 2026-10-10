# SweaterCo General Manager's Co-Pilot

NUS DSS5105 Capstone Project - Track 1.

SweaterCo Co-Pilot 是一个面向快反服装厂总经理的 AI 助手。系统使用课程提供的订单、生产和外协车间数据检索事实、计算指标、发现风险并评估新订单，同时为用户可见的结论保留 Evidence 和结构化 trace。

## 当前进展

| 模块 | 状态 | 已实现内容 |
|---|---|---|
| A1 Retrieval | 已完成 | 订单检索、条件搜索、生产记录检索、外协车间检索、Evidence 溯源 |
| A2 Judgement | 已完成 | 订单状态、生产正常性、风险发现、Morning Briefing、接单可行性 |
| B1 Agent Orchestration | 已完成 | Intent 路由、缺参检查、四条 A1 到 A2 pipeline、确定性回复 |
| B2 OpenAI Integration | 已完成 | OpenAI Responses API、严格 JSON Schema、中英文 Intent Parsing、安全降级 |
| C Streamlit UI | 待接入 | 当前只有可启动的临时页面，还没有连接 `AgentService` |

当前 B 层支持英文和中文自然语言交互，以英文为默认语言。85 项自动化测试已通过，六种真实 OpenAI API 用例已完成端到端验证。

## 已支持的用户请求

| 功能 | 英文示例 | 中文示例 |
|---|---|---|
| 订单状态 | `How is order ORD-057?` | `ORD-057 现在怎么样？` |
| 生产正常性 | `Is Assembly operating normally?` | `Assembly 最近生产正常吗？` |
| Morning Briefing | `Give me the top 3 briefing items.` | `给我今天最重要的 5 个风险。` |
| 接单可行性 | `Can we accept 800 TOPS by 2026-04-25?` | `800 件 TOPS 在 2026-04-25 前能交吗？` |

如果请求缺少订单号、工序、产品类别、数量或交期，系统会返回 clarification，而不是猜测缺失信息。未知请求和模型调用失败会安全降级为 `unknown`。

## 系统架构

```text
English or Chinese user text
  -> app/agent/service.py                Natural-language entry point
  -> IntentParser                        LLM intent and slot extraction
  -> OpenAILLMClient                     Responses API and strict JSON Schema
  -> IntentDecision                      intent, language, slots, missing slots
  -> AgentOrchestrator                   deterministic routing and validation
  -> A1 Retrieval tools                  source facts and Evidence
  -> A2 Judgement and Integration        business rules and calculated results
  -> bilingual Response Builder          deterministic English or Chinese reply
  -> AgentReply                          text, evidence, warnings, assumptions, metadata
  -> future Streamlit UI
```

### A B C 分工

```text
app/ui/       C：展示 chat、briefing、warning、assumptions、Evidence 和 trace
app/agent/    B：理解问题、检查参数、选择 pipeline、调用工具、组织回复
app/tools/    A：确定性读取、计算、风险判断、可行性评估和 Evidence 溯源
data/         课程提供的 orders、production log 和 workshops
```

核心原则：LLM 只负责理解用户语言、识别 Intent 和抽取 slots。CSV 数据、检索结果和 A2 计算结果不会发送给模型。订单状态、产量、平均值、风险分和可行性均由 `app/tools/` 的确定性代码计算，最终回复由本地中英文模板生成。

## 本地运行

需要 Python 3.10 或更高版本。以下命令适用于 Windows PowerShell，并应在项目根目录执行。

### 1 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` 会同时安装应用依赖、pytest 和 Ruff。应用依赖包括 Streamlit、pandas、python-dotenv 和 OpenAI Python SDK。

### 2 配置 OpenAI API Key

复制本地环境模板：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
MODEL_PROVIDER=openai
OPENAI_MODEL=gpt-6-luna
OPENAI_API_KEY=your_own_api_key_here
OPENAI_TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
```

`.env` 已被 `.gitignore` 排除。不要将真实 API Key 写入 `.env.example`、Python 文件、测试、日志或 Git commit。每位组员应使用自己的本地 `.env`。

安全确认配置是否加载成功：

```powershell
python -c "from app.agent.settings import LLMSettings; s=LLMSettings.from_env(); print(s.provider, s.model, bool(s.api_key))"
git check-ignore -v .env
```

预期第一条命令输出类似 `openai gpt-6-luna True`，但不会显示 Key 内容。

### 3 运行自然语言 Agent

当前推荐直接调用 `AgentService` 验证后端：

```powershell
python -c "from app.agent.service import AgentService; s=AgentService.from_env(); print(s.handle_text('How is order ORD-057?').text)"
```

中文示例：

```powershell
python -c "from app.agent.service import AgentService; s=AgentService.from_env(); print(s.handle_text('Assembly 最近生产正常吗？').text)"
```

这些命令会调用 OpenAI API 解析 Intent，并产生少量 API 用量。工厂数据查询和业务判断仍在本地完成。

### 4 运行自动化检查

```powershell
python -m pytest
python -m ruff check .
git diff --check
```

当前基线：

```text
pytest: 85 passed
ruff: All checks passed
```

### 5 启动当前 Streamlit 页面

```powershell
streamlit run app/ui/streamlit_app.py
```

当前页面只是临时集成壳。页面可以启动并接收输入，但尚未调用 `AgentService`，因此不会返回真实 Agent 结果。后端功能应先使用上一节的命令验证。

## OpenAI 调用路径

```text
.env
  -> python-dotenv
  -> LLMSettings.from_env
  -> create_llm_client
  -> OpenAILLMClient
  -> client.responses.create
```

当前使用 Responses API 和 `strict: true` JSON Schema。模型输出固定包含：

- `intent`
- `language`
- `order_id`
- `stage`
- `category`
- `pieces`
- `requested_delivery_date`
- `top_n`
- `missing_slots`

没有 API Key 或不希望产生费用时，测试可以注入 `MockLLMClient`。`MODEL_PROVIDER=mock` 不会自动理解用户自然语言，因此不应作为真实 UI 的默认运行方式。

## B Agent 文件说明

| 文件 | 作用 |
|---|---|
| `app/agent/contracts.py` | A、B、C 共用的 `Evidence`、`ToolResult`、`Tool` 和 `AgentReply` 契约 |
| `app/agent/intents.py` | Intent、ResponseLanguage、必填 slots 和 `IntentDecision` |
| `app/agent/settings.py` | `AgentSettings` 与环境变量驱动的 `LLMSettings` |
| `app/agent/tool_registry.py` | 集中创建和持有 A1 Retrieval tools |
| `app/agent/orchestrator.py` | 缺参检查、Intent 路由和四条确定性业务 pipeline |
| `app/agent/response_builder.py` | 中英文确定性回复、warning 和 assumption 展示 |
| `app/agent/prompts.py` | 双语 Intent Parsing prompt 与严格 JSON Schema |
| `app/agent/llm_client.py` | OpenAI adapter、Mock adapter 和统一异常 |
| `app/agent/intent_parser.py` | 模型 payload 校验、slot 标准化和失败降级 |
| `app/agent/service.py` | 自然语言总入口；未来 Streamlit 应调用这里 |

`AgentReply` 向 UI 提供：

- `text`
- `evidence`
- `warnings`
- `assumptions`
- `needs_clarification`
- `suggested_question`
- `metadata`

如需修改这些共享字段，应同步通知 A、B、C 模块负责人。

## 项目目录

```text
.
├─ app/
│  ├─ agent/                         # B：LLM adapter、Intent Parser 和 Agent 编排
│  ├─ tools/                         # A：Retrieval、Judgement、风险和可行性工具
│  │  ├─ a2_integration.py           # A1 Retrieval 到 A2 的集成层
│  │  ├─ order_lookup.py             # 按订单号检索
│  │  ├─ order_search_tool.py        # 按条件搜索订单
│  │  ├─ production_retrieval_tool.py
│  │  ├─ workshop_retrieval_tool.py
│  │  ├─ order_status/
│  │  ├─ production_normality/
│  │  ├─ production_risks/
│  │  └─ order_feasibility/
│  └─ ui/
│     └─ streamlit_app.py            # C：当前临时 Streamlit 壳
├─ data/
│  ├─ orders.csv
│  ├─ production_log.csv
│  ├─ workshops.csv
│  └─ data_dictionary.md
├─ docs/
│  ├─ b_agent_first_iteration_report.md
│  ├─ b_agent_llm_integration_report.md
│  └─ tool_specifications.md
├─ evaluation/                       # 后续中英文 ground-truth evaluation set
├─ notebooks/                        # 探索性分析
├─ tests/                            # 85 项自动化测试
├─ .env.example                     # 本地配置模板，不含密钥
├─ requirements.txt                 # 应用依赖
├─ requirements-dev.txt             # 应用、测试和 lint 依赖
└─ pyproject.toml                   # pytest 与 Ruff 配置
```

## Streamlit 下一阶段

UI 接入时应复用 `AgentService.from_env()` 和 `AgentService.handle_text()`，不要在 `streamlit_app.py` 中复制 OpenAI client、Intent 路由或业务计算。

建议的 Streamlit MVP：

1. 使用 `st.cache_resource` 创建一次 `AgentService`。
2. 使用 `st.session_state.messages` 保存当前聊天历史。
3. 使用 `st.chat_input` 和 `st.chat_message` 展示中英文对话。
4. 首屏展示 `AgentReply.text`。
5. 分开展示 `warnings`、`assumptions` 和 `suggested_question`。
6. 默认只展示少量直接 Evidence，完整 Evidence 放入折叠区、分页或下载文件。
7. Sidebar 只显示 provider、model 和连接状态，不显示 API Key。
8. 对 Key 缺失、网络失败和业务 validation failure 提供不同的安全提示。

真实测试中，生产正常性、Morning Briefing 和可行性评估可能携带 90 到 400 条 Evidence。UI 不应默认全部展开。下一步建议将 Evidence 分为：

```text
response_evidence    直接支撑当前回答的精选证据
full_evidence        完整审计和复现记录
```

## 当前限制

- 当前是一问一答，不会自动合并多轮对话中的缺失 slots。
- Streamlit 尚未连接 B 层。
- 最终回答由确定性模板生成，尚未使用 LLM 润色。
- 尚未持久化 latency、token usage、成本和 audit log。
- 尚未建立 30 题以上的中英文 ground-truth evaluation set。
- Watch、提醒和有副作用的 Actions 尚未实现。

## 协作流程

1. 开始工作前运行 `git status`，不要覆盖组员的本地改动。
2. 从最新远端分支创建自己的功能分支。
3. 功能代码、相关测试和文档放在同一个小提交中。
4. 提交前运行 pytest、Ruff 和 `git diff --check`。
5. 使用 `feat:`、`fix:`、`test:`、`docs:` 或 `chore:` 等提交前缀。
6. 合并前确认 `.env`、`.venv`、API Key 和本地生成文件没有进入提交。
7. 修改 `contracts.py` 或 `AgentReply` 字段前同步 A、B、C 负责人。

## 后续优先级

1. 将 `AgentService` 接入 Streamlit chat UI。
2. 在 UI 中展示 clarification、warnings、assumptions 和 Evidence 摘要。
3. 增加多轮 slot filling 和 session state。
4. 将 Evidence 分为 response evidence 与 full evidence。
5. 建立 30 题以上的中英文评测集和 failure analysis。
6. 增加不记录敏感信息的 latency、token usage 和 audit log。
7. 为提醒、备注和消息等 Actions 增加预览、用户确认和审计记录。

## 开发报告

- [第一轮确定性 Agent 编排](docs/b_agent_first_iteration_report.md)
- [第二轮 OpenAI LLM 接入与真实 API 验证](docs/b_agent_llm_integration_report.md)
