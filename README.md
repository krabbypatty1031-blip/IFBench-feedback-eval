# IFBench 验证器反馈修订实验

## 项目简介

本项目是香港浸会大学（HKBU）COMP4136 Natural Language Processing 课程 mini-project。我们在 IFBench [2] 上比较三种单轮修订方式：自行检查、接收整体通过／失败反馈、接收逐条约束反馈。我们用官方验证器检查输出约束，并通过人工内容审核判断回答是否完成了原始任务。

## 主要结果

主指标是 **strict 提示级通过率**。一条回答必须满足该题的全部可验证约束才算通过。下表以各条件的可评分回答为分母，使用补跑合并后的最终结果。

| 模型 | B0 原回答 | B1 自我修订 | B2 整体反馈 | B3 逐约束反馈 |
| --- | ---: | ---: | ---: | ---: |
| gpt-4.1 | 138/259 (53.3%) | 146/259 (56.4%) | **163/259 (62.9%)** | 153/259 (59.1%) |
| gpt-4o | 148/259 (57.1%) | 157/259 (60.6%) | **174/259 (67.2%)** | 168/259 (64.9%) |

- **B2 相比 B0**，同题配对的通过率分别提高 **9.65** 和 **10.04 个百分点**。两项提升在事后同源组符号翻转检验中经 Holm 校正后均达到统计显著。两个模型的 B2 与 B3 直接比较均未检出显著差异。
- **B2 相比 B1**，两个模型均修复了更多初始失败的回答，也让更少初始通过的回答在修订后失败。
- **人工审核**覆盖全部 260 道测试题的 2,080 个题目、模型与条件组合，其中 2,072 条回答可供判断。**818 条**完成了任务但未通过 strict，**19 条**通过 strict 却只完成了部分任务或未完成。各修订条件相对 B0 的内容完成率差异在 Holm 校正后均未达到统计显著。

[完整结果表与图表](outputs/report_materials/README.md) · [案例原文](outputs/report_materials/CASE_STUDIES.md) · [配对检验与稳健性分析](outputs/offline_analysis/README.md)

## 目录结构

```text
.
├── data/IFBench_test.jsonl    # 固定版本的 IFBench 数据
├── ifbench/                   # 上游约束与验证器
├── evaluation_lib.py          # 官方 strict / loose 评分入口
├── configs/feedback_eval/     # 数据划分、同源组、提示词和运行设置
├── feedback_eval/             # 生成、反馈、记录和配对分析
├── scripts/                   # 补跑、离线分析和素材生成
├── outputs/
│   ├── report_materials/      # 最终表格、汇总 JSON、案例和图表
│   └── offline_analysis/      # 检验、稳健性和案例重评分结果
├── .env.example               # 本地接口配置模板
└── pyproject.toml / uv.lock    # 依赖声明与版本锁定
```

逐条运行记录、补跑记录和原始审核包保存在本地 `runs/feedback_eval/`。

## 环境配置

已验证 Python **3.11.15**。本地验证、评分与统计只需要 CPU。用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 安装 [uv.lock](uv.lock) 锁定的依赖，包括 NLTK **3.9.2** 和 HTTPX **0.28.1**。[requirements.txt](requirements.txt) 列出部分上游依赖。

```bash
uv sync --locked --python 3.11
export NLTK_DATA="$PWD/ifbench/.nltk_data"
uv run --no-sync python -m nltk.downloader -d "$NLTK_DATA" \
  punkt punkt_tab stopwords averaged_perceptron_tagger_eng
```

依赖安装到 `.venv/`，NLTK 资源存放在 `ifbench/.nltk_data/`。安装需要网络，无模型 API 费用。已有 NLTK 资源时，可将 `NLTK_DATA` 指向其目录。仅核对已保存的汇总可直接用标准库 Python。

运行真实模型前，根据 [环境变量模板](.env.example) 创建本地配置，并将 `API_KEY` 设置为自己的 HKBU GenAI 密钥。网关和模型由 [official.yaml](configs/feedback_eval/official.yaml) 指定。

```bash
cp -n .env.example .env
```

## 数据获取

[固定版本的 IFBench 数据](https://github.com/allenai/IFBench/blob/cd189a9c2889526d8af901d2103fd3e1ffcea6df/data/IFBench_test.jsonl)已保存在 `data/IFBench_test.jsonl`。实验使用[冻结划分](configs/feedback_eval/split.json)，300 道题中有 40 道开发题、260 道测试题，同源题按组划分。数据使用 ODC-BY 1.0 许可，详见[上游项目][ifbench-repo]。

```bash
shasum -a 256 data/IFBench_test.jsonl
```

SHA-256 应为 `d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a`。

## 复现步骤

从仓库根目录执行。clone 后可以直接核对已发布的汇总、离线验证流程，也可以用自己的网关权限重新生成一批回答，并进行评分和人工审核。精确重建原实验需要历史逐条记录和原始审核包，见第 5 步。

1. **核对最终主表。** 读取 [results.json](outputs/report_materials/results.json)，输出每个模型与条件的 strict 分子、分母和通过率。无需 API。

   ```bash
   python3 - <<'PY'
   import json
   from pathlib import Path

   results = json.loads(Path("outputs/report_materials/results.json").read_text())
   for row in results["tables"]:
       score = row["strict_prompt"]
       print(row["model"], row["condition"], f"{score['n']}/{score['d']}", f"{score['value']:.1%}")
   PY
   ```

2. **离线小样本验证。** 使用冻结配置与合成样本。模拟客户端执行 B0–B3，在临时目录生成逐条记录和 `analysis.json`。无需 API。

   ```bash
   smoke_dir=$(mktemp -d /tmp/ifbench-smoke.XXXXXX)
   uv run --no-sync python -m feedback_eval.cli run-official \
     --offline --n-items 2 --store-dir "$smoke_dir"
   uv run --no-sync python -m feedback_eval.cli analyze \
     --store-dir "$smoke_dir" --no-overlay
   ```

3. **重新生成正式测试回答。** 输入为冻结测试划分、正式配置、提示词及自己的接口配置。输出到新的 `runs/feedback_eval/new_official/`，包括冻结配置、逐条记录和运行摘要。会调用模型 API，费用取决于网关计费与配额，金额 **TODO**。

   ```bash
   FEEDBACK_EVAL_AUTHORIZE_SPEND=1 uv run --no-sync python -m feedback_eval.cli \
     run-official --paid --config configs/feedback_eval/official.yaml \
     --store-dir runs/feedback_eval/new_official
   ```

   如果新运行出现空 B0，可按题目和模型以 16,384 token 的上限补跑。该命令另建输出目录并调用模型 API，费用 **TODO**。

   ```bash
   FEEDBACK_EVAL_AUTHORIZE_SPEND=1 uv run --no-sync python scripts/run_empty_b0_16384.py \
     --paid --official-dir runs/feedback_eval/new_official \
     --out-dir runs/feedback_eval/new_sensitivity \
     --config configs/feedback_eval/sensitivity_empty_b0_16384.yaml
   ```

4. **分析新记录并生成盲审包。** 输入为第 3 步的逐条记录。第一个命令输出 strict、loose、修复率、退化率和配对差异至 `analysis.json`。第二个命令生成 `blinded.json`、`key.json` 和待填的 `judgments.csv`。无需 API。

   ```bash
   uv run --no-sync python -m feedback_eval.cli analyze \
     --store-dir runs/feedback_eval/new_official --no-overlay
   uv run --no-sync python -m feedback_eval.cli review-pack \
     --store-dir runs/feedback_eval/new_official --no-overlay \
     --out-dir runs/feedback_eval/new_review
   ```

   如需纳入补跑结果，在两个命令中都用 `--overlay runs/feedback_eval/new_sensitivity` 替换 `--no-overlay`。用新的目录生成审核包，避免覆盖已经填写的标签。审核按原任务完成情况标为 `complete`、`partial`、`incomplete` 或 `cannot_judge`。先标注，再用 `key.json` 匹配模型和条件。

5. **重建原实验合并结果。** 原始记录在 `runs/feedback_eval/official/`，两批原始审核包在其中的 `review_pack/` 和 `review_pack_remaining_220/`。重建时使用与最终正文对应的标签，并按下面的顺序合并、检验和生成结果表。命令会重写相应的分析产物，适合在历史记录的副本上运行。无需 API。

   ```bash
   uv run --no-sync python -m feedback_eval.cli analyze \
     --store-dir runs/feedback_eval/official \
     --overlay runs/feedback_eval/sensitivity_empty_b0_16384 \
     --overlay runs/feedback_eval/retry_recovered_20261116
   uv run --no-sync python scripts/offline_report_analysis.py
   uv run --no-sync python scripts/build_report_materials.py
   ```

   输出分别为运行目录下的 `analysis.json`、`outputs/offline_analysis/` 下的检验结果，以及 `outputs/report_materials/` 下的结果表和案例。仓库已包含[全量审核汇总](outputs/report_materials/content_review_all_260.json)、[后 220 道题的标签表](outputs/report_materials/content_review_remaining_220.csv)和[输入文件清单](outputs/report_materials/source_manifest.json)。完整历史回答与原始审核包的获取方式 **TODO**。

## 实验设置

- **模型与条件**。HKBU GenAI 上的 `gpt-4.1` 和 `gpt-4o`。B0 直接回答原题；B1 自行检查；B2 接收整体 strict 判定；B3 接收逐条约束判定和对应要求。三个修订分支各运行一次，独立使用同一份 B0。B2 和 B3 在 B0 通过时收到允许保留原答的提示。
- **生成与恢复**。原始设置为 temperature `0`、seed `42`、最大输出 `4096` token。选定的空答或错误记录以 `16384` 或 `32768` token 补跑。最终合并数据有 2,072 条可评分回答和 8 条错误，缺失值不填补。
- **评测与统计**。修复率只看 B0 未通过的配对，退化率只看 B0 已通过的配对。配对区间使用同源组 bootstrap（1,000 次，seed `42`）。显著性使用事后同源组符号翻转检验与 Holm 校正。

## 局限

B2 和 B3 对已通过的 B0 有明确的保留原答提示，B1 与 B2 的差异反映整套修订策略。测试集中 220 道题只有一条约束，逐约束反馈在多约束任务上的作用仍需进一步测试。内容标签由一名审核者逐条复核，无法估计评分者间一致性。网关模型和服务状态可能变化，固定种子也无法保证重新生成完全相同的文本。

## 参考文献

[1] J. Zhou *et al*., “Instruction-Following Evaluation for Large Language Models,” arXiv:2311.07911, 2023. [arXiv](https://arxiv.org/abs/2311.07911)

[2] V. Pyatkin *et al*., “Generalizing Verifiable Instruction Following,” in *Adv. Neural Inf. Process. Syst.*, vol. 38, 2025. [NeurIPS](https://proceedings.neurips.cc/paper_files/paper/2025/hash/46499a0622ecf568b72d17b61e45dbd5-Abstract-Datasets_and_Benchmarks_Track.html)

[3] A. Madaan *et al*., “Self-Refine: Iterative Refinement with Self-Feedback,” in *Adv. Neural Inf. Process. Syst.*, vol. 36, 2023. [NeurIPS](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html)

[4] Z. Gou *et al*., “CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing,” in *Proc. Int. Conf. Learn. Represent.*, 2024. [ICLR](https://proceedings.iclr.cc/paper_files/paper/2024/hash/fef126561bbf9d4467dbb8d27334b8fe-Abstract-Conference.html)

## 学术诚信声明

本项目引用上游数据、验证器和相关工作。ChatGPT 和 Grok 辅助了实验代码开发与流程设计。报告由作者独立撰写，再使用生成式 AI 检查语法和润色句式。

## 许可证

上游代码采用 Apache License 2.0，见 [LICENSE](LICENSE)。数据许可与署名要求见[上游项目][ifbench-repo]。课程新增代码的单独授权条款 **TODO**。

[ifbench-repo]: https://github.com/allenai/IFBench#license
