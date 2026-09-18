# SweaterCo General Manager's Co-Pilot

NUS DSS5105 Capstone Project - Track 1.

这是一个面向快反服装厂总经理的 AI 助手。系统以课程提供的工厂数据为基础检索事实、计算指标和发现风险，并尽量让每条用户可见的结论能够回溯到数据来源。

## 当前状态

项目目前是可运行的协作骨架，而不是完整产品。

- 已有：数据、Python 环境配置、Streamlit 临时页面、A/B/C 模块边界、工具标准返回格式、一个订单查询示例工具和自动化测试。
- 尚未完成：晨间简报、真实 LLM 调用与编排、多轮对话、风险/异常检测、可行性评估、watch、确认式行动和审计日志。

## 运行项目

需要 Python 3.10 或更高版本。在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
streamlit run app/ui/streamlit_app.py
```

当前页面是暂时的集成壳；可以输入问题，但在 Agent 接入前不会给出真实答案。

运行质量检查：

```powershell
python -m ruff check .
python -m pytest
```

## 系统架构

```text
用户
  -> app/ui/                  C：展示 briefing、chat、证据、确认和审计记录
  -> app/agent/               B：理解问题、澄清歧义、选择工具、管理多轮上下文、组织回答
  -> app/tools/               A：确定性读取、计算、风险判断、可行性评估和溯源
  -> data/                    课程提供的 orders、production log、workshops
```

**核心原则：** LLM 负责理解和表达；订单数、产量、平均值、比较、风险判断和可行性估算由 `app/tools/` 的确定性工具完成。这样每一个对用户展示的数值都有相应证据。

## 目录与文件说明

```text
.
├─ app/
│  ├─ agent/                  # B 组：Agent 编排层
│  │  └─ contracts.py          # A/B/C 共享的数据和工具接口
│  ├─ tools/                  # A 组：所有确定性业务工具
│  │  └─ order_lookup.py       # 示例：按订单号查询订单
│  └─ ui/                     # C 组：Streamlit 界面
│     └─ streamlit_app.py      # 当前可启动的临时应用入口
├─ data/                      # 课程提供的源数据；建议保持原样
│  ├─ orders.csv              # 120 个客户订单
│  ├─ production_log.csv      # 90 天、4 个工序的生产记录
│  ├─ workshops.csv           # 8 家外协车间资料
│  └─ data_dictionary.md      # 所有字段及业务含义
├─ evaluation/                # 30+ 题评测集、ground truth、结果和 failure analysis
├─ notebooks/                 # 探索性分析；正式应用逻辑不要只留在 notebook 中
├─ tests/                     # 自动化测试，目录结构应尽量对应 app/
├─ .env.example               # 本地环境变量模板；不含密钥
├─ .gitignore                 # 排除虚拟环境、密钥、缓存和本地运行数据
├─ requirements.txt           # 应用运行依赖
├─ requirements-dev.txt       # 测试和 lint 所需的开发依赖
└─ pyproject.toml             # pytest 与 ruff 的统一规则
```

## A/B/C 分工详情

### A：Data & Intelligence Tools

`app/tools/` 用于放置业务逻辑。为便于 A、B、C 三组协作，建议每个工具具备以下特征：

1. 输入明确，例如 `order_id`、`days_threshold` 或 `due_date`。
2. 只做确定性检索或计算，不调用 LLM。
3. 返回 `ToolResult`，包含 `data`、`evidence` 和必要的 `warnings`。
4. 对不支持、缺失或不可回答的请求给出明确说明，而不推测。
5. 为正常、边界和失败情况添加测试。

[app/tools/order_lookup.py](app/tools/order_lookup.py) 是一个初步的sample。它按订单号读取 `orders.csv`，返回原始记录，并以订单 ID 作为行级证据。

### B：LLM Agent & System Integration

在 `app/agent/` 中实现：意图识别、工具选择、澄清问题、多轮对话状态和基于工具结果的回答生成。

[app/agent/contracts.py](app/agent/contracts.py) 是当前的跨模块边界；如需调整，建议同步通知相关模块负责人：

- `Evidence`：支撑一个结论的来源，例如 `orders.csv` 中的 `ORD-057`。
- `ToolResult`：A 工具返回的数据、证据和警告。
- `Tool`：工具的统一最小协议：`name`、`description` 和 `execute()`。
- `AgentReply`：B 交给 C 的文本、证据，以及是否需要用户澄清。

新增真实模型时，可以集中在一个独立的 LLM client/adapter 中读取 `.env`，这样 API key、模型调用和 prompt 不会分散在工具与 UI 中。

### C：Product / UI / Actions

`app/ui/` 用于实现 Streamlit 页面。UI 主要展示事实、证据和工具建议，无需复制计算逻辑。对于创建提醒、写订单备注或发送消息等有副作用的操作，建议提供预览、用户确认和审计记录。

## 协作流程

1. 可以从最新 `main` 创建功能分支，例如 `feat/risk-tool`。
2. 功能与相关测试通常放在同一个小提交中，方便他人审阅。
3. 提交前可运行 `python -m ruff check .` 和 `python -m pytest`，来检查代码集成问题。
4. 加入提交前缀，例如 `feat:`、`fix:`、`test:`、`docs:` 或 `chore:`，有助于查看项目历史。
5. 合并前建议检查 `.env`、`.venv`、API key 和本地运行产物没有被加入提交。

## 建议的下一步

1. A 组先把订单检索、生产数据查询、normal/risk/anomaly、feasibility 和 trace 做成独立工具。
2. B 组基于 `contracts.py` 实现工具注册、问题澄清和一个不接 LLM 也可测试的编排流程。
3. C 组将 `streamlit_app.py` 扩展为 briefing、聊天和证据面板，并接入 B 的 `AgentReply`。
4. 尽早在 `evaluation/` 定义 30+ 道有 ground truth 的题目，而不是等功能完成后再补。
