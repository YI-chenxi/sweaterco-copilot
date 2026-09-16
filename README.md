# Track 1 - General Manager's Co-Pilot

一个面向快反服装厂总经理的运营副驾驶。系统从本地结构化数据中检索事实、计算指标、发现风险，并以可追溯的方式回答问题和提出行动建议。

## 团队边界

- `app/tools/`（A）：确定性数据检索、计算、normal/risk/anomaly、可行性与溯源工具。工具不得调用 LLM。
- `app/agent/`（B）：理解问题、选择和调用工具、澄清歧义、维护多轮上下文、生成只基于工具结果的回答。
- `app/ui/`（C）：Streamlit briefing、chat、证据查看、watch、确认和审计界面。
- `tests/`（全组）：按模块放单元测试；端到端和评测资产后续补充。

`data/` 是课程提供的只读源数据；本地运行生成的 watches、audit log 等放在被忽略的 `runtime_data/`。

## 本地启动

需要 Python 3.10+。在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
streamlit run app/ui/streamlit_app.py
```

检查代码和测试：

```powershell
python -m ruff check .
python -m pytest
```

## 协作约定

- 每项功能使用独立分支和小而清晰的提交；合并前运行 ruff 与 pytest。
- 任何面向用户的数值都必须来自 `app/tools/` 的结果，并附带可追溯证据；LLM 不做算术。
- 对工具接口的改动必须同步更新 `app/agent/contracts.py`、相关测试和 README。
- 默认 `MODEL_PROVIDER=mock`，不会要求或读取真实 API key；接入真实模型前，团队需要共同确定供应商、成本和密钥管理方式。
