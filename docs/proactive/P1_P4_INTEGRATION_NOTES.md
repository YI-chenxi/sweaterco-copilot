# P1 → P4 联调记录与 P3 接口建议

日期：2026-11-05。P1 本地分支：`feat/proactive-intent-parser`；P4 检查版本：`40b4127`（`feat/person4-confirmed-actions-audit`）。这是独立模块的契约验证，尚未合并共享分支或接入 UI。

## 已验证的数据流

1. P1 `parse(text, context).to_dict()` 产出规范化命令。目标 ID 按 P4 要求校验并规范为 UUID。
2. 集成层先检查 `unknown` 和 `missing_fields`。创建、修改、取消调用 P4 `propose_action(workspace_id, command)`；不得直接执行 P3/P5 写操作。
3. 把 P4 返回的提案 UUID 纳入该用户、该工作区的待确认上下文。P1 解析后续确认/拒绝，集成层分别调用 `confirm_action()` / `reject_action()`。确认和拒绝命令本身**不能**传给 `propose_action()`。
4. `list` / `get` 走 P3/P5 的只读服务。P4 只处理变更及提案审计。

隔离联调测试位于 `tests/proactive/test_p1_p4_contract.py`：使用 P4 `40b4127` 的模块和模拟简报执行器，验证“提案前零写入、确认及重复确认合计一次写入、缺字段拒绝提案、拒绝不写入”，结果 **3 passed**。P4 尚未进入 P1 分支；单独运行 P1 测试时该文件会跳过，合并后会自动参与回归。

## 给 P3 的执行适配器建议

P4 的执行器不是简单包装 `BriefingService.create_schedule()`。每个 `briefing.create/update/cancel` 适配器需要：

```python
validate(workspace_id: str, command: dict) -> str | None
execute(
    workspace_id: str,
    command: dict,
    *,
    idempotency_key: str,
    expected_version: str | None,
) -> dict  # ServiceResult
```

- `validate` 只读，校验工作区、字段、目标存在和状态；更新/取消必须返回目标版本。创建可返回 `None`。
- `execute` 在业务写入中原子比较 `expected_version`，并持久记录 `idempotency_key`；相同键重复调用不能产生第二次效果。返回已落库的结果或明确错误。
- 只在 P4 确认后调用 `execute`。业务参数仍由 P3 服务端重新校验，不能信任 P1 的 `missing_fields=[]`。
- 当前 P3 公共服务方法没有版本和幂等入参。建议在 P3 repository 或专用适配器层增加原子能力，再由集成人员接入 P4；不要只在内存里检查版本后调用普通 CRUD。
- P5 到位时也需同样检查；当前 V1 无 `watch.update`，不能注册该操作。

## 尚需集成人员决定

| 问题 | 建议 |
| --- | --- |
| 待确认提案的上下文来源 | 按用户和工作区只提供仍待确认的提案 UUID；P4 当前没有直接的 `list_pending_proposals()` 公共方法，需确定读取路径或补接口。 |
| UI 确认 | 展示目标、参数、有效期及影响范围；确认按钮传明确 UUID，不依赖模型猜测。 |
| 授权 | `actor_provider` 和 `authorizer` 使用服务端可信身份；P1 输出不得充当权限凭据。 |
| 持久化与异常恢复 | P4 的 SQLite 适用于本地；云端需共享持久化。`requires_reconciliation` 为真时先核查业务结果，不能自动再执行。 |

这些问题不影响 P1/P4 的命令字段兼容，但在真实任务执行和部署前必须解决。
