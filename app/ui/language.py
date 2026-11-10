"""One shared frontend locale: ui_language is en or zh.

The sidebar owns the control; Quick Workflows and display adapters read it.
Historical backend replies and task state are never translated in place.
"""

from collections.abc import Mapping
from typing import Any

HISTORICAL_COMPARISON = "Recorded production performance versus the established baseline."

ZH = {
    "Instant Briefing": "即时晨报",
    "Transcribing recording...": "正在转写录音…",
    "Review transcription before sending": "请检查并编辑识别文字，再发送",
    "Send reviewed text": "发送检查后的文字",
    "Discard transcription": "丢弃识别文字",
    "Re-record": "重新录制",
    "Cancel re-record": "取消重录",
    "Microphone help": "麦克风帮助",
    "Use the microphone to start and stop recording, then submit it for transcription.": (
        "点击麦克风开始和结束录音，然后提交录音进行转写。"
    ),
    (
        "If the microphone is blocked, allow access in your browser’s site settings. "
        "You can still type."
    ): "如果麦克风被阻止，请在浏览器的网站设置中允许访问。你仍可输入文字。",
    "Use the microphone below. A successful recording will replace this draft.": (
        "请使用下方的麦克风。新录音成功转写后会替换此草稿。"
    ),
    "New recordings are added to this draft unless you choose Re-record.": (
        "新录音会追加到草稿。若要替换草稿，请选择重新录制。"
    ),
    "Use the microphone below to record again.": "请使用下方的麦克风重新录制。",
    "The recording is empty. Please record again.": "录音为空，请重新录制。",
    "The recording could not be read. Please record again.": "无法读取录音，请重新录制。",
    "Recording exceeds 5 MiB. Please make it shorter.": "录音超过 5 MiB，请缩短录制时间。",
    "Recording exceeds 60 seconds. Please make it shorter.": "录音超过 60 秒，请缩短录制时间。",
    "No clear speech was found. Please try again.": "未识别到有效语音，请重试。",
    "Transcription timed out. Please try again.": "语音转写超时，请重试。",
    "Transcription is unavailable. You can still type.": "语音转写暂不可用，仍可输入文字。",
    "Daily mode": "日常模式",
    "Eye comfort mode": "护眼模式",
    "Night mode": "黑夜模式",
    "Review the details, then run the analysis.": "确认参数后，即可运行分析。",
    "Your factory, at a glance.": "工厂情况，一目了然。",
    "AI Configure": "AI 配置",
    "Configured": "已配置",
    "Not configured": "未配置",
    "I’m thinking…": "正在思考…",
    "AI Chat needs a model key. Check AI Configure or use Quick Workflows.": (
        "AI 聊天需要配置模型，请检查 AI 配置或使用快捷工作流。"
    ),
    "Some content could not be translated. Check the model connection and retry.": (
        "部分内容暂时无法翻译，请检查模型连接后重试。"
    ),
    "Retry translation": "重试翻译",
    "Follow answers while scrolling": "滚动时自动追随回答",
    "View Evidence selects an answer. Following resumes on your next chat scroll.": (
        "查看证据只切换回答；下次主动滚动聊天时继续追随。"
    ),
    "Business Assessment": "业务评估",
    "Supporting Details": "支持详情",
    "Show full analysis": "展开完整分析",
    "Hide full analysis": "收起完整分析",
    "Copy": "复制",
    "Use the copy icon to copy the complete original response.": "点击复制图标，复制完整原始回答。",
    "Order Status": "订单状态",
    "Production Health": "生产健康",
    "Morning Briefing": "运营晨报",
    "Check Feasibility": "接单可行性",
    "Order status": "订单状态",
    "Production health": "生产健康",
    "Morning briefing": "运营晨报",
    "Order feasibility": "接单可行性",
    "QUICK WORKFLOWS": "快捷工作流",
    "Choose a task to open its parameter dialog": "选择任务，在弹窗中填写参数",
    "Collapse": "收起",
    "Expand": "展开",
    "4 business tasks": "四项业务任务",
    "Open the four structured factory workflows": "打开四个工厂业务工作流",
    "Open workflow parameters in a dialog": "打开工作流参数弹窗",
    "INTERPRETED REQUEST": "工作流请求",
    "Review explicit fields before running": "执行前请确认参数",
    "Structured task helper": "业务任务助手",
    (
        "This mode uses deterministic A1/A2 business tools; it does not claim "
        "to understand free text. Results appear in the same conversation."
    ): ("此模式通过明确参数调用 A1/A2 业务工具，结果将出现在当前会话中。"),
    "Order ID": "订单编号",
    "Production stage": "生产工序",
    "Priority findings": "重点问题数量",
    "Category": "产品类别",
    "Quantity (pieces)": "数量（件）",
    "Required delivery date": "要求交货日期",
    "Run analysis →": "运行分析 →",
    "Checking factory data and business rules...": "正在检查工厂数据和业务规则…",
    "Invalid fields. Check the input parameters.": "参数无效，请检查输入内容。",
    "Unable to run the workflow. Please retry or contact your project maintainer.": (
        "暂时无法运行工作流，请重试或联系项目维护者。"
    ),
    "Close helper": "关闭参数区",
    "Structured workflow": "快捷工作流",
    "Structured workflow · deterministic tools": "快捷工作流 · 业务规则工具",
    "Enter an order ID, e.g. ORD-057": "输入订单编号，例如 ORD-057",
    "Order ID is required.": "请输入订单编号。",
    "KEY METRICS": "关键指标",
    "Order": "订单",
    "Assessment": "评估结果",
    "Days to due": "距交期天数",
    "Days since activity": "距最近活动天数",
    "Stage": "工序",
    "Baseline / day": "历史基准／日",
    "Recent / day": "近期产量／日",
    "Deviation": "偏差",
    "Estimate": "可行性评估",
    "Production days": "可用生产天数",
    "On track": "正常",
    "At risk": "存在风险",
    "Needs attention": "需要关注",
    "Outside normal range": "超出正常范围",
    "Feasible with internal capacity": "内部产能可支持",
    "Conditionally feasible": "有条件可行",
    "Not currently feasible": "目前不可行",
    "▣ Order details": "▣ 订单详情",
    "Business assessment": "业务评估",
    "Days to due (snapshot)": "距交期天数（快照）",
    "Derived order-status facts; no per-order completion percentage is available.": (
        "基于订单记录的计算结果；当前没有单笔订单的完成百分比。"
    ),
    "Historical baseline": "历史基准",
    "Recent output": "近期产量",
    "Deviation (%)": "偏差（%）",
    "▥ Historical reference": "▥ 历史参考",
    HISTORICAL_COMPARISON: "记录的生产表现与既定历史基准对比。",
    "▥ Capacity analysis": "▥ 产能分析",
    "Estimated internal capacity margins from deterministic business rules (pieces).": (
        "按既定业务规则估算的内部剩余产能（件）。"
    ),
    "Capacity margin": "剩余产能",
    "▣ External workshops & lead time": "▣ 外部车间与交期",
    "Workshop estimates only; not live supplier confirmations or materials stock.": (
        "仅为车间估算，不代表供应商实时确认或物料库存。"
    ),
    "name": "车间名称",
    "delay_days": "延迟天数",
    "nominal_capacity": "名义产能",
    "quality_adjusted_capacity": "质量调整后产能",
    "can_cover_new_order_nominally": "名义产能能否覆盖新订单",
    "ANALYSIS DETAILS": "分析详情",
    "Evidence & context": "证据与背景",
    (
        "Once you run a real analysis, source records, warnings, assumptions "
        "and relevant business details will appear here."
    ): ("执行分析后，来源记录、风险提醒、假设及相关业务详情将显示在这里。"),
    "Latest result": "最近结果",
    "Production check": "生产检查",
    "Order assessment": "接单评估",
    "General question": "一般问题",
    "Analysis": "分析",
    "Supporting Evidence": "支持证据",
    "records": "条记录",
    "Source": "来源",
    "Reference": "记录标识",
    "Detail": "详情",
    "Sources: ": "来源：",
    "View source rows": "查看来源记录",
    "Export full evidence CSV": "导出完整证据 CSV",
    "No source rows returned for this response.": "此回答未返回来源记录。",
    "Risk Warnings": "风险提醒",
    "Key Assumptions": "关键假设",
    "View details": "查看详情",
    "Technical trace": "技术追踪",
    "Result": "结果",
    "Request": "任务",
    "Answer": "回答",
    "Selected": "已选中",
    "Selected answer": "当前查看",
    "View Evidence": "查看证据",
    "No evidence was provided for this answer.": "此回答未提供证据。",
    "Hide analysis panel": "收起分析面板",
    "Hide": "收起",
    "Show analysis panel": "展开分析面板",
    "Show only this answer's evidence, warnings and assumptions": (
        "查看这条回答自己的证据、风险提醒和假设"
    ),
    "AI Chat · contextual tasks": "AI 聊天 · 上下文任务",
    "Business analysis": "业务分析",
    "Data interpretation in the selected language": "中文数据解读（原始回答保持不变）",
    "Original assumptions (unchanged)": "原始假设（保持不变）",
    "Original response (unchanged)": "原始回答（保持不变）",
}


SESSION_NOTE = "Session chat history is not saved to GitHub."
WELCOME_NOTE = "Ask naturally, or choose a workflow backed by factory data."
INPUT_PLACEHOLDER = "Ask about orders, production, risks or feasibility..."
MODEL_NOTE = "AI chat uses the model. Quick Workflows need no API key."
REQUEST_RESET_NOTE = "Please start fresh and submit up to two requests."

ZH.update(
    {
        "New conversation": "新建会话",
        "MAIN NAVIGATION": "主要导航",
        "AI Co-Pilot": "AI 运营助手",
        "Dashboards": "数据看板",
        "Data Hub": "数据中心",
        "Reports": "报告",
        "Integrations": "集成",
        "Planned": "待开发",
        "RECENT CHATS": "最近会话",
        "WORKSPACE": "工作空间",
        "Factory snapshot: ": "工厂数据快照：",
        "Simulated CSV data": "模拟 CSV 数据",
        "AI Chat configured": "AI 聊天已配置",
        "AI Chat needs model key": "AI 聊天尚未配置模型密钥",
        "Structured workflows available": "快捷工作流可用",
        "AI Chat setup": "AI 聊天设置",
        SESSION_NOTE: "会话记录仅在当前浏览器会话中保留，不会保存到 GitHub。",
        "Configure the model locally, then restart the app.": "请在本机配置模型后重新启动应用。",
        "How can I help with your factory today?": "今天需要我帮您了解哪些工厂情况？",
        WELCOME_NOTE: "可以直接提问，或选择工作流，根据工厂数据获取答案。",
        "FACTORY DATA SNAPSHOT": "工厂数据快照",
        "SIMULATED · NOT LIVE": "模拟数据 · 非实时",
        "Simulated factory data date": "模拟工厂数据日期",
        "FACTORY SNAPSHOT": "工厂数据快照",
        "DECISION SUPPORT ONLY": "仅供决策参考",
        "NOT LIVE FACTORY TELEMETRY": "非实时工厂监测数据",
        "ASK THE CO-PILOT": "向 AI 助手提问",
        "One natural-language chat input": "自然语言提问",
        INPUT_PLACEHOLDER: "询问订单、生产、风险或接单可行性…",
        MODEL_NOTE: "AI 聊天需要模型；快捷工作流无需 API 密钥。",
        "Send →": "发送 →",
        "Enter a question first.": "请先输入问题。",
        "Configure AI Chat or use Quick Workflows.": "请配置 AI 聊天，或使用上方快捷工作流。",
        "Agent is checking factory tools...": "正在查询工厂业务工具…",
        "Requests waiting for details": "等待补充信息的请求",
        "Start a fresh request": "开始新请求",
        "Still needed": "还需补充",
        "Needs clarification": "需要澄清",
        "Order check": "订单查询",
        "New-order assessment": "新订单评估",
        "product category": "产品类别",
        "order number": "订单编号",
        "order quantity": "订单数量",
        "delivery date": "交货日期",
        "production stage": "生产工序",
        "number of briefing items": "晨报条目数量",
        REQUEST_RESET_NOTE: "请开始新请求，并优先提交最多两项任务。",
    }
)


def shared_language(state: Mapping[str, Any]) -> str:
    if "ui_language" in state:
        return "zh" if state["ui_language"] == "zh" else "en"
    return "zh" if state.get("reply_language") in {"zh", "中文"} else "en"


def translate(text: str, language: str = "en") -> str:
    return ZH.get(text, text) if language == "zh" else text
