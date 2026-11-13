# 人工内容审核协议

本文件只规定**内容审核**怎么做。规则通过率仍以官方 IFBench 验证器为准；审核**不是**第二套验证器，也不替代 `analysis.json` 里的配对统计。

审核包由冻结记录里的 40 个测试题 ID 生成，覆盖两个模型与 B0–B3，共 320 条。题目、模型、条件都已打乱并隐藏。产物在 `runs/feedback_eval/official/review_pack/`（不入库）。

**进度（2026-11-13）：** 320 条官方盲审标签已写入 `judgments.csv`（`complete` 311 / `partial` 8 / `incomplete` 1 / `cannot_judge` 0）。包内正文与正式 `analysis.json` 相同，已覆盖 16384/32768 恢复结果。本包 320 条均为 `accounting_status=success` 且有可读正文，因此没有 `cannot_judge`。评分者为单人代理标注（`cursor-grok-4.6`），**不是**独立人类评分者，无法估计评分者间一致性。揭盲摘要在 `judgment_summary.json`。

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

## 本次标注结果（已揭盲）

标签在打开 `key.json` **之前**写完。准则是原任务是否完成，不是 IFBench 约束是否通过。

| 标签 | 条数 | 占比 |
| --- | --- | --- |
| `complete` | 311 | 0.972 |
| `partial` | 8 | 0.025 |
| `incomplete` | 1 | 0.003 |
| `cannot_judge` | 0 | 0 |

- **内容完成率接近天花板：** 两个模型各 160 条；gpt-4.1 155/160，gpt-4o 156/160。B0–B2 均为 78/80，B3 为 77/80。
- **反馈几乎不改变原任务完成：** 同一模型×题目配对里，B0→B1 / B0→B2 内容标签 80/80 相同；B0→B3 仅 1 次退化（gpt-4.1 item 159 B3）。
- **8 条 partial 全是同一题：** item 247（“What does Baudrillard mean?” + 素数词长）。两模型、四条件都只给了一句口号，没有展开解释。其中 6/8 条 **strict 仍通过**——约束满足不等于内容充分。
- **1 条 incomplete：** gpt-4.1 item 159 B3（R029）只回了验证器 `PASS:` 清单，没有改写主祷文。
- **与规则得分对照（本包 320 条均可评分）：** 311 条 `complete` 里 strict 通过 229、失败 82。内容完成但约束失败是主现象；不能把 strict 当内容质量。

本 40 题样本的 strict 通过率高于全测试集 260 题主表（抽样不是按难度分层），只用于内容观察，不替代 `analysis.json` 的配对统计。

## 分析命令（从原始记录重建表）

```bash
.venv/bin/python -m feedback_eval.cli analyze \
  --store-dir runs/feedback_eval/official
.venv/bin/python -m feedback_eval.cli analyze \
  --store-dir runs/feedback_eval/sensitivity_empty_b0_16384
```

正式报告表会覆盖 16384/32768 已恢复的正文（token 上限不当作模型能力）。`official/records/` 里的 4096 JSON 不改写。盲审包与 `analysis.json` 使用同一套合并身份。若只要冻结原文，分析时加 `--no-overlay`。

费用金额保持 `unknown`：HKBU 网关不提供已核验单价与钱包扣款。
