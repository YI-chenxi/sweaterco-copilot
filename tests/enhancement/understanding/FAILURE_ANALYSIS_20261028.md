# NLU 真实评测失败分析

**角色：** Person 2（NLU Evaluation）
**评测日期：** 2026-10-28
**模型：** `gpt-6-luna`
**数据集：** 54 条，SHA-256 `c5ac4539a637c85d6c3f17d70b8a8e24611185481ca2a99f1d9825da94a97726`
**代码提交：** `33d2cdf24c001871d29e3ccb2d980ee10ff9dfee`；报告标记 worktree dirty，因此结果来自未提交工作树。

## 结果概览

完整增强版评测在一次运行中得到 53/54（98.1%）完全匹配。之后新旧比较再次运行增强版 54 条，得到 52/54（96.3%）。这是两次独立的 live 请求批次，差异反映模型输出波动；不应把某一次分数视为确定性保证。

在比较批次中，40 条双方共同支持的单轮单任务案例上：

| 指标 | Enhanced Parser | Legacy Parser |
| --- | ---: | ---: |
| Intent 顺序准确率 | 40/40（100%） | 40/40（100%） |
| Slots 完全正确 | 40/40（100%） | 37/40（92.5%） |
| Missing Slots 完全正确 | 40/40（100%） | 40/40（100%） |
| 共同能力 exact match | 40/40（100%） | 37/40（92.5%） |

比较共使用 94 次模型请求：增强版 54 条，旧版 40 条。两版使用相同模型配置，但 Parser 与 Prompt 不同，且请求分别执行，因此不能把分数差归因于单独某个代码变化。

## Enhanced Parser 失败案例

### eval-050：零数量未标记为非法格式

输入：`Can we accept 0 TOPS pieces by 2026-04-25?`

人工标准答案将 `pieces` 留空、列入 `missing_slots`，并标注 `invalid_format`。首次 54 条运行中，Parser 把数量识别成 `2`，未报告缺失字段，也未产生 issue；比较批次中 slots 和 missing slot 处理正确，但仍未产生 `invalid_format`。两个批次都显示对零数量及其 issue 分类处理不稳定。

**给 Person 1 的建议：**检查零值是否在结构化输出、整数校验或归一化过程中被错误转换；确保零数量保留为无效输入，并生成对应 `invalid_format`，而不是改写为其他正数。

### eval-054：SlotUpdate 包含未变化的已有字段

输入只补充交期 `2026-04-25`。首次完整运行此案例通过；对比批次中，Parser 返回的更新除交期外，还重复包含原上下文中的 `pieces=800` 和 `category=TOPS`，因此更新 exact match 失败。此结果同样显示跨轮输出存在波动。

**给 Person 1 的建议：**SlotUpdate 仅返回本轮明确新增或修改的字段；上下文中未被本轮更新的值应留在现有任务状态，不应重复作为更新输出。

## Legacy Parser 失败案例

以下都是双方共同能力范围内的格式归一化差异：

- `colloquial-01`：`ORD057` 返回为 `ORD057`，预期为 `ORD-057`。
- `colloquial-05`：`ORD 8` 返回为 `ORD 8`，预期为 `ORD-008`。
- `chinese-05`：工序返回为 `PACKAGING`，预期系统规范值为 `PACKING`。

这些结果仅作为旧版基线和迁移风险记录。Person 2 不修改旧 Parser 或 Person 1 的 Parser/Prompt。

## 已知限制

- 54 条样本是项目验收型数据集，不代表所有生产表达。
- `eval-055` ambiguous reference 的标准答案未确认，因此不在数据集内。
- Issue type 有评分，issue field、original value、suggested value 尚未完整纳入指标。
- 新旧 Parser 使用不同 Prompt/实现，且是单次独立调用；模型随机性会影响差异。
- 两份正式报告均记录 worktree dirty。运行时 Git commit 是 `33d2cdf...`，不能将结果描述成从干净、已提交版本复现。
- 历史 48 条报告和 2026-10-27 的 48/48 分数不代表本次 54 条数据。

## 证据报告

- `evaluation/live_enhanced_54_20261028.json` / `.md`：首次 54 条增强版运行，53/54。
- `evaluation/compare_enhanced_54_20261028.json` / `.md`：比较批次中的增强版 54 条运行，52/54。
- `evaluation/compare_legacy_40_20261028.json` / `.md`：旧版 40 条共同能力运行，37/40。

该分析供 Person 2 交接和 Person 1 后续排查使用；尚未向其他成员发送消息。
