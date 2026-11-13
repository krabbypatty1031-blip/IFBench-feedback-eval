# 人工内容审核协议

本文件只规定**内容审核**怎么做。规则通过率仍以官方 IFBench 验证器为准；审核**不是**第二套验证器，也不替代 `analysis.json` 里的配对统计。

审核包由冻结记录里的 40 个测试题 ID 生成，覆盖两个模型与 B0–B3，共 320 条。题目、模型、条件都已打乱并隐藏。产物在 `runs/feedback_eval/official/review_pack/`（不入库）。

**进度（2026-11-13）：** `blinded.json` / `key.json` / `judgments.csv` 已生成；320 条标签尚未填写。包内正文与正式 `analysis.json` 相同，已覆盖 16384/32768 恢复结果，因此 200/232/249 等题不再以空 B0 示人。仍无正文的身份标 `cannot_judge`。

## 单人审核限制

本课程作业只有一名审核者（`single_rater: true`）。因此：

- 没有评分者间一致性（inter-rater reliability）。
- 内容标签只能当作对规则得分的补充观察，不能当成独立金标准。
- 标注完成后才能打开 `key.json`。先看密钥再打标签会使盲审失效。

## 标签

对**原任务**判断模型输出是否完成用户请求（不是判断约束是否通过）：

| 标签 | 含义 |
| --- | --- |
| `complete` | 实质完成原任务；即使个别约束失败也可以 |
| `partial` | 部分完成：有可用内容，但明显缺步骤、跑题或只覆盖部分要求 |
| `incomplete` | 几乎没有完成原任务，或正文与题目无关 |
| `cannot_judge` | 无法判断：空正文、API 错误、截断到无法阅读、内容过滤、非目标语言且无法理解 |

空回答、`error`、缺失记录一律标 `cannot_judge`，并在 notes 写状态（例如 `empty_response`）。**不要把空正文当成未完成任务的内容证据**，那是生成失败。

## 操作步骤

在 `IFBench/` 根目录：

```bash
export NLTK_DATA="$PWD/ifbench/.nltk_data"
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m feedback_eval.cli review-pack \
  --store-dir runs/feedback_eval/official
```

生成：

- `blinded.json`：给审核者看的 prompt / response / accounting_status
- `key.json`：review_id → item_id / model / condition（打完标签再打开）
- `judgments.csv`：填写 `label` 与 `notes`

在 `judgments.csv` 逐行填写。一条记录只选上述四个标签之一。有疑点写在 `notes`，不要发明第五个标签。

## 分析命令（从原始记录重建表）

```bash
.venv/bin/python -m feedback_eval.cli analyze \
  --store-dir runs/feedback_eval/official
.venv/bin/python -m feedback_eval.cli analyze \
  --store-dir runs/feedback_eval/sensitivity_empty_b0_16384
```

正式报告表会覆盖 16384/32768 已恢复的正文（token 上限不当作模型能力）。`official/records/` 里的 4096 JSON 不改写。盲审包与 `analysis.json` 使用同一套合并身份。若只要冻结原文，分析时加 `--no-overlay`。

费用金额保持 `unknown`：HKBU 网关不提供已核验单价与钱包扣款。
