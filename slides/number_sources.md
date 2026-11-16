# Number & Example Sources (per slide)

路径均相对于项目仓库 `IFBench/`（Mac：Final Project 下的 IFBench；盒子副本：`/workspace/comp4136-final-project/ifbench-review/IFBench/`）。
“recompute/” 指 `/workspace/comp4136-final-project/ifbench-review/recompute/`：我用官方 `evaluation_lib` 从原始运行记录独立重算的脚本与输出，与项目结果文件逐项一致。

| Slide | 数字 / 内容 | 来源 |
|---|---|---|
| 1 | 无数字；Group [XX]、成员与学号为占位 | — |
| 2 | IFEval 25 类约束 | 文献 [2] Zhou et al., arXiv:2311.07911 |
| 2 | IFBench 58 类新约束 | 文献 [1] Pyatkin et al., NeurIPS 2025 D&B；仓库 README |
| 3 | 无数字 | — |
| 4 | 260 test prompts（另 40 dev） | `configs/feedback_eval/split.json`（`n_test: 260`, `n_dev: 40`）；`configs/feedback_eval/official.yaml`（`budget_planning.n_test_items: 260`） |
| 4 | 模型 gpt-4.1 / gpt-4o（HKBU gateway）、temperature 0 | `configs/feedback_eval/official.yaml`（`models`, `parameters.temperature: 0.0`） |
| 4 | 主指标 strict prompt-level | `configs/feedback_eval/official.yaml`（`scoring.primary_metric`） |
| 4–5 | B1/B2/B3 提示与反馈文本 | `configs/feedback_eval/prompts.json`；`feedback_eval/feedback.py::make_feedback`；`feedback_eval/runner.py::build_revision_user_message` |
| 6 | Strict / Loose：gpt-4.1 B0 53.3/59.8, B1 56.4/62.9, B2 62.9/67.2, B3 59.1/64.5；gpt-4o B0 57.1/62.9, B1 60.6/65.3, B2 67.2/71.0, B3 64.9/69.1 | `outputs/report_materials/RESULTS_TABLES.md` 表 1（同值见 `outputs/report_materials/results.json`） |
| 7 | 配对 strict 提升（pp）：B1 3.09/3.47，B2 9.65/10.04，B3 5.79/7.72（gpt-4.1/gpt-4o） | `RESULTS_TABLES.md` 表 3 |
| 7 | “B2、B3 两模型均显著；B1 不显著” | `RESULTS_TABLES.md` 表 3（B2/B3 的 95% 区间不含 0，B1 含 0）；McNemar 精确检验 recompute/metrics.json（B2−B0 p=7e-05 / 0.00016；B3−B0 p=0.011 / 0.0008；B1−B0 p=0.30 / 0.21）|
| 7 | “对缺失值处理稳健” | recompute/metrics.json：`official_4096_only`、`merged_complete_case`、`merged_excluding_rerun_items`、`B2-B0_missing_bounds_pp_n260`（最坏/最好情形 gpt-4.1 9.23/10.38，gpt-4o 9.62/10.77）；`ifbench-review/REVIEW_2026-11-15.md` |
| 8 | 退化率 B1 13.8%/10.8%，B2 5.1%/6.8% | `RESULTS_TABLES.md` 表 4（19/138、16/148、7/138、10/148） |
| 8 | B1 改写 123/138 个已通过答案；B2 原样保留 103/138（gpt-4.1）。gpt-4o 对应为 B1 改写 132/148、B2 保留 106/148（只在讲稿中提及） | 附录脚本 `mechanism.py` 的输出 `mechanism.json`（`gpt-4.1/B1.b0_pass_rewritten=123`、`gpt-4.1/B2.b0_pass_kept=103`、`gpt-4o/B1.b0_pass_rewritten=132`、`gpt-4o/B2.b0_pass_kept=106`）。脚本调用项目自己的合并逻辑（`feedback_eval.cli._prepare_analysis_records` → `analysis.overlay_records`），读 `runs/feedback_eval/official/records/*.json` 及 overlay 补跑目录（含 2026-11-16 恢复正文），与 RESULTS_TABLES.md 用的是同一合并集；“保留”= 修订文本 strip 后与 B0 完全相同。输出里的 `regressed`/`repaired` 与表 4 的分子逐项一致（如 19/7、27/32）。建议把脚本放到 `IFBench/scripts/mechanism.py`，输出写到 `outputs/report_materials/mechanism.json`，作为报告附录 |
| 8 | B2 对已通过答案附加“可保持不变”提示 | `configs/feedback_eval/prompts.json` `allow_unchanged_if_passed` |
| 8 | B3 vs B2 无显著差异 | `RESULTS_TABLES.md` 表 3：B2−B3 +3.86 [−0.39, +8.37]、+2.32 [−2.30, +7.39]，区间含 0 |
| 9 | 例 1（B1 改坏已通过答案）：gpt-4o item 296（format:no_whitespace） | B0 `runs/feedback_eval/official/records/3b5556eb653ffa6911fb54e21ffeb01d30ab341cf1f725aaaa0ae0d3aa0f7c00.json`（strict PASS，全文）；B1 `…/4ae6ecb2ba5813653f54d3b5d21689b9b81ad7f6a84b68d48614c85b44dde4e7.json`（FAIL，全文）；B2 `…/89b1d077a3a91a16169f8d8e033c583781e08a71795c68b0462875964b322081.json`（PASS，全文）。提示取自 `data/IFBench_test.jsonl` key 296，只截掉开头 “In set theory,”。幻灯片上 B0 的长破折号按原字符排版 |
| 9 | 例 2（B0 失败、B2 改对）：gpt-4.1 item 173（ratio:stop_words，≤20%） | B0 `runs/feedback_eval/official/records/5990d27afbd6fd9bfccc751b06d408bba6409429da8039f355b3a7c56df4170c.json`（FAIL，截取开头）；B2 `…/ff2d09d5e19c928e72a96fbefe17df813491fcce7788f239ac58b535ea3159f0.json`（PASS，截取开头）；B1 同样 FAIL（讲稿提及）：`…/fb702dc79e84f6764f403a5412084e033a1a6477335817d07508da924a346035.json`。提示取自 `data/IFBench_test.jsonl` key 173，截取。该例也在 `outputs/report_materials/CASE_STUDIES.md` 中 |
| 9 | 两例都来自 4096-token 正式运行，没有被补跑替换 | `mechanism.py` 用的合并集中这两题未出现在 replaced 列表里（gpt-4o 被替换的题号为 36、47、48、50、51、87、158 等，不含 296；gpt-4.1 不含 173）；四条记录 `finish_reason` 均为 stop |
| 10 | 全量内容审核 2046 complete / 9 partial / 17 incomplete / 8 cannot_judge；单人审核 | `outputs/report_materials/RESULTS_TABLES.md` 表 5；`docs/feedback_eval_review.md`；`runs/feedback_eval/official/review_protocol.json` |

PASS/FAIL 均为官方 strict 判定，已用锁定版本（nltk 3.9.2、emoji 2.15.0）重新评分核对，与记录中的 `score` 一致。
