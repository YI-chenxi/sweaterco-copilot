"""A1 Retrieval -> A2 Judgement / Discovery / Feasibility 集成层。

本模块不读取 CSV，也不重复解析 A1 已经标准化的 typed facts。
它只负责把 A1 ToolResult.data 转成 A2 业务函数需要的输入，并把
A1 evidence / warnings 原样向下游传递。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import date
from typing import Any, Protocol

from app.tools.order_feasibility import estimate_order_feasibility
from app.tools.order_feasibility.config import STAGES
from app.tools.order_status import judge_order_status
from app.tools.production_normality import judge_production_normality
from app.tools.production_risks import discover_production_risks


class RetrievalTool(Protocol):
    """A1 Retrieval Tool 的最小接口；避免 A2 依赖具体实现类。"""

    def execute(self, **kwargs: Any) -> Any: ...


def _result_like(source: Any, *, data: Any, evidence: list[Any], warnings: list[str]) -> Any:
    """沿用 A1 ToolResult 的实际类型，保持 data/evidence/warnings 契约不变。"""
    return source.__class__(data=data, evidence=evidence, warnings=warnings)


def _merge_evidence(results: Iterable[Any]) -> list[Any]:
    merged: list[Any] = []
    for result in results:
        merged.extend(getattr(result, "evidence", []) or [])
    return merged


def _merge_warnings(results: Iterable[Any]) -> list[str]:
    merged: list[str] = []
    for result in results:
        merged.extend(getattr(result, "warnings", []) or [])
    return merged


def judge_order_from_retrieval(order_result: Any, *, today: date | str) -> Any:
    """把 A1 单订单 Retrieval 结果交给 Tool 1，并保留 A1 Evidence。"""
    if not getattr(order_result, "data", None):
        return _result_like(
            order_result,
            data={},
            evidence=list(getattr(order_result, "evidence", []) or []),
            warnings=list(getattr(order_result, "warnings", []) or []),
        )

    payload = dict(order_result.data)
    # today 不属于 CSV 事实，由系统 reference date / runtime context 显式注入。
    payload["today"] = today
    judgement = judge_order_status(payload)
    return _result_like(
        order_result,
        data=judgement.to_dict(),
        evidence=list(getattr(order_result, "evidence", []) or []),
        warnings=list(getattr(order_result, "warnings", []) or []),
    )


def judge_production_from_retrieval(production_result: Any, *, stage: str) -> Any:
    """把 A1 production records 交给 Tool 2，并保留 A1 Evidence。"""
    records = (getattr(production_result, "data", {}) or {}).get("records", [])
    if not records:
        return _result_like(
            production_result,
            data={},
            evidence=list(getattr(production_result, "evidence", []) or []),
            warnings=list(getattr(production_result, "warnings", []) or []),
        )

    judgement = judge_production_normality(records, stage)
    return _result_like(
        production_result,
        data=asdict(judgement),
        evidence=list(getattr(production_result, "evidence", []) or []),
        warnings=list(getattr(production_result, "warnings", []) or []),
    )


def discover_factory_risks(
    *,
    order_search_tool: RetrievalTool,
    production_retrieval_tool: RetrievalTool,
    today: date | str,
    top_n: int = 5,
) -> Any:
    """全厂扫描：A1 Retrieval -> Tool 1/2 -> Tool 3 Top Findings。"""
    order_result = order_search_tool.execute(status="IN_PROGRESS")
    orders = (order_result.data or {}).get("orders", [])

    order_signals: list[dict[str, Any]] = []
    for order in orders:
        judgement = judge_order_status({**order, "today": today})
        order_signals.append(
            {
                "order_id": order["order_id"],
                "pieces": order["pieces"],
                "current_stage": order["current_stage"],
                "days_to_due": judgement.derived_metrics["days_to_due"],
                "days_since_activity": judgement.derived_metrics["days_since_activity"],
                "labels": judgement.labels,
                "reason_codes": judgement.reason_codes,
            }
        )

    production_results: list[Any] = []
    production_signals: list[dict[str, Any]] = []
    for stage in STAGES:
        # A1 ProductionRetrievalTool 要求至少一个筛选条件，因此按 stage 分别取数。
        result = production_retrieval_tool.execute(stage=stage)
        production_results.append(result)
        records = (result.data or {}).get("records", [])
        if not records:
            continue
        normality = judge_production_normality(records, stage)
        production_signals.append(asdict(normality))

    findings = discover_production_risks(order_signals, production_signals, top_n=top_n)
    all_results = [order_result, *production_results]
    return _result_like(
        order_result,
        data={"findings": [asdict(f) for f in findings], "count": len(findings)},
        evidence=_merge_evidence(all_results),
        warnings=_merge_warnings(all_results),
    )


def estimate_feasibility_from_retrieval(
    *,
    request: dict[str, Any],
    order_search_tool: RetrievalTool,
    production_retrieval_tool: RetrievalTool,
    workshop_retrieval_tool: RetrievalTool,
) -> Any:
    """A1 三类 Retrieval -> Tool 4；Tool 4 本身不再接触 CSV。"""
    category = str(request.get("category", "")).strip().upper()
    if not category:
        raise ValueError("request 缺少有效 category")

    order_result = order_search_tool.execute(status="IN_PROGRESS")
    orders = (order_result.data or {}).get("orders", [])

    production_results: list[Any] = []
    production_records: list[dict[str, Any]] = []
    for stage in STAGES:
        result = production_retrieval_tool.execute(stage=stage)
        production_results.append(result)
        production_records.extend((result.data or {}).get("records", []))

    # A1 已负责 ACTIVE + category compatibility 的客观筛选。
    workshop_result = workshop_retrieval_tool.execute(status="ACTIVE", category=category)
    workshops = (workshop_result.data or {}).get("workshops", [])

    feasibility = estimate_order_feasibility(
        request=request,
        orders=orders,
        production=production_records,
        workshops=workshops,
    )

    all_results = [order_result, *production_results, workshop_result]
    return _result_like(
        order_result,
        data=asdict(feasibility),
        evidence=_merge_evidence(all_results),
        warnings=_merge_warnings(all_results),
    )
