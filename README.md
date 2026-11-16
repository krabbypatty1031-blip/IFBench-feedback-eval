# IFBench 反馈修订实验

## 项目简介

本项目是香港浸会大学（HKBU）COMP4136 自然语言处理课程实验。我们基于 IFBench [1]，比较模型自行检查和不同粒度的验证器反馈，是否能提高对输出约束的遵循；同时检查回答是否完成原始任务。项目通过 HKBU GenAI 接口调用模型，不进行本地训练。

## 主要结果

下表为当前合并结果的 strict 提示级通过率（通过数/可评分数）。每道题的全部约束均通过才算通过。

| 模型 | B0 初始 | B1 自我检查 | B2 整体反馈 | B3 逐约束反馈 |
|---|---:|---:|---:|---:|
| gpt-4.1 | 138/259 (53.3%) | 146/259 (56.4%) | 163/259 (62.9%) | 153/259 (59.1%) |
| gpt-4o | 148/259 (57.1%) | 157/259 (60.6%) | 174/259 (67.2%) | 168/259 (64.9%) |

B2 相对 B0 的同题配对提升分别为 **9.65** 和 **10.04 个百分点**。内容审核覆盖全部 260 道测试题、两个模型和四种条件，共 2080 条回答：2046 条 complete、9 条 partial、17 条 incomplete、8 条 cannot_judge。把 complete 当作原任务正确后，B1/B2/B3 相对 B0 的内容完成率差异均不超过 0.77 个百分点，配对区间含零。完整表格及分母见[结果表](outputs/report_materials/RESULTS_TABLES.md)，具体回答见[案例](outputs/report_materials/CASE_STUDIES.md)。

- 报告：[IEEE 草稿](report/report.tex)（本地编译 PDF 见 `report/build/report.pdf`；`build/` 被 Git 忽略）
- PPT：[幻灯片](slides/slides.tex)、[讲稿](slides/speaker_notes.md)、[PDF](slides/slides.pdf)

## 目录结构

```text
.
├── data/IFBench_test.jsonl       # 指定的原始数据
├── ifbench/                      # IFBench 约束与验证器
├── evaluation_lib.py             # 官方 strict / loose 评分
├── feedback_eval/                # 数据划分、生成、反馈、存储及分析
├── configs/feedback_eval/        # 划分、提示、模型和运行参数
├── scripts/                      # 补跑、探测、重试和报告素材生成
├── tests/feedback_eval/          # 离线测试
├── outputs/report_materials/     # 最终结果表、案例和图表
├── outputs/offline_analysis/     # 配对检验、稳健性和案例重评分
├── report/                       # IEEE 报告源文件
├── slides/                       # 答辩幻灯片
├── runs/feedback_eval/           # 本地产生的实验记录；不随 Git 发布
├── docs/                         # 实验协议和运行手册（本地；已被 Git 忽略）
├── .env.example                  # 接口环境变量示例，无真实密钥
├── requirements.txt              # 上游部分依赖列表
├── pyproject.toml, uv.lock       # 项目依赖与锁定版本
└── LICENSE                       # 上游代码许可证
```

`runs/`、`.env` 和本地 NLTK 数据被 Git 忽略。**TODO：正式实验记录、补跑记录和审核标签尚未随仓库发布；仅 clone 不能精确重建表中的既有数字。** 在公布数据包并记录其获取位置之前，可用本仓库代码重新运行实验，但不能声称新生成结果与原结果完全相同。

## 环境配置

已使用 Python **3.11.15**。本地验证和评分使用 CPU，不需要 GPU；真实模型调用需要网络、HKBU GenAI 权限及个人 API key。

`requirements.txt` 是上游部分依赖列表，未包含完整课程实验依赖；优先使用 `pyproject.toml` 和 `uv.lock` 安装。需先自行安装 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --locked
```

输入：`pyproject.toml`、`uv.lock`；输出：`.venv/`。API 费用：0。**验证状态：本机已验证锁文件离线 dry-run，未在全新环境完成联网安装；TODO：验证干净环境安装。**

以 `.env.example` 为模板建立本地 `.env`，填写自己的接口配置。示例中的模型和参数属于上游默认值；本实验以 `configs/feedback_eval/official.yaml` 为准。不要提交 `.env` 或密钥。

```bash
cp -n .env.example .env
```

输入：`.env.example`；输出：本地 `.env`（已存在时不覆盖）。API 费用：0。将本地配置设为 `API_PROVIDER=azure`，按实际校园接口填写 `API_BASE`、`API_KEY`、`MODEL_A=gpt-4.1`、`MODEL_B=gpt-4o`；费用授权使用 `FEEDBACK_EVAL_AUTHORIZE_SPEND=1`。接口具体权限由使用者确认。

## 数据获取

数据来自 [IFBench 上游固定版本](https://github.com/allenai/IFBench/blob/cd189a9c2889526d8af901d2103fd3e1ffcea6df/data/IFBench_test.jsonl)，放在 `data/IFBench_test.jsonl`。当前版本已在仓库中跟踪；如果缺失，可从上述版本获取。不要混用 `ifbench/data/IFBench_test.jsonl`：其文件哈希不同。

期望 SHA-256：`d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a`。数据许可证为 ODC-BY-1.0；使用数据和第三方模型输出时应遵守上游署名及其他适用条款 [2]。

```bash
shasum -a 256 data/IFBench_test.jsonl
```

输入：数据文件；输出：SHA-256，须与上面的值一致。API 费用：0。**TODO：当前环境无法连接上游 raw 下载端点，尚未验证远程下载文件的字节哈希。**

NLTK 资源不随仓库发布。首次运行需联网下载 `punkt`、`punkt_tab`、`stopwords`、`averaged_perceptron_tagger_eng`；上游验证器在导入时会尝试获取缺失资源。设置 NLTK 目录：

```bash
export NLTK_DATA="$PWD/ifbench/.nltk_data"
export PYTHONDONTWRITEBYTECODE=1
```

输入：当前仓库路径；输出：当前 shell 环境变量。API 费用：0。**TODO：在全新环境核实 NLTK 资源下载。**

## 复现步骤

以下命令从仓库根目录运行。实验生成、补跑及人工审核会产生新的记录；本次实验的既有记录目前未随 Git 发布。

1. **离线小样本检查**。使用假客户端，不调用真实模型。输入：`official.yaml` 和模拟题目；输出：临时目录中的 B0–B3 记录及对账摘要；API 费用：0。

   ```bash
   .venv/bin/python -m feedback_eval.cli run-official --offline --n-items 2 --store-dir /tmp/ifbench-readme-offline
   ```

   若该目录已存在，改用一个新的空目录。这里的样本为合成题，并非正式测试结果。

2. **正式生成**。输入：`data/IFBench_test.jsonl`、`configs/feedback_eval/official.yaml`、提示模板及自己的接口配置；输出：新目录 `runs/feedback_eval/new_official/` 中的冻结信息、账本和逐条记录。该步骤会调用 API；**大致费用：TODO，校园接口单价和扣款未核实**。

   ```bash
   FEEDBACK_EVAL_AUTHORIZE_SPEND=1 .venv/bin/python -m feedback_eval.cli run-official --paid --config configs/feedback_eval/official.yaml --store-dir runs/feedback_eval/new_official
   ```

   使用新的空目录；不要覆盖已有 `official/`。该命令需要有效接口和授权，本 README 的离线验证没有实际执行付费生成。

3. **空回答补跑**。原实验将选出的空 B0 模型／题目及其分支单独补跑。输入：上一步的原始记录和 `sensitivity_empty_b0_16384.yaml`；输出：独立补跑目录；API 费用：**TODO，未核实**。

   ```bash
   .venv/bin/python scripts/run_empty_b0_16384.py --paid --official-dir runs/feedback_eval/new_official --out-dir runs/feedback_eval/new_sensitivity --config configs/feedback_eval/sensitivity_empty_b0_16384.yaml
   ```

   此命令需要实际产生空 B0、有效接口和授权；未在本次验证中进行付费调用。原报告还使用了一条 32768 探测恢复结果；**TODO：公开该探测记录和可复现步骤**。

4. **分析新结果**。输入：新运行记录及补跑目录；输出：新运行目录中的 `analysis.json` 和 `overlay_manifest.json`；API 费用：0。使用明确的 `--overlay`，避免混入本机其他实验。

   ```bash
   .venv/bin/python -m feedback_eval.cli analyze --store-dir runs/feedback_eval/new_official --overlay runs/feedback_eval/new_sensitivity
   ```

   `analyze` 会覆盖目标目录中的分析文件；该命令依赖第 2、3 步产生的记录，未对新付费运行执行验证。

5. **内容审核**。现行协议覆盖测试集全部 260 题。对新的运行目录生成审核包：输入为记录及冻结配置；输出为 `review_pack/blinded.json`、`key.json` 和 `judgments.csv`；API 费用：0。本仓库正式结果使用全量 2080 条标签。

   ```bash
   .venv/bin/python -m feedback_eval.cli review-pack --store-dir runs/feedback_eval/new_official --overlay runs/feedback_eval/new_sensitivity
   ```

   按 [审核协议](docs/feedback_eval_review.md) 完成标注，再揭盲。**不要在已有标签的目录再次运行：该命令会覆盖 `judgments.csv`。** 原实验完整审核标签尚未公开，因此无法由 clone 独立重算现有内容审核数字。上述命令依赖第 2、3 步的真实输出，未对新付费运行执行验证。

已发布的[最终结果表](outputs/report_materials/RESULTS_TABLES.md)和四张 PNG/SVG 图可直接查看。`scripts/build_report_materials.py` 当前依赖未发布的原始 `runs/` 记录及审核包，不能仅从 clone 重建；**TODO：发布经许可审查的记录并验证重建命令。**

## 实验设置

- 数据：300 题，40 题开发、260 题测试；已确认同源题按组划分，种子 42。
- 模型：HKBU GenAI `gpt-4.1`、`gpt-4o`；模型版本与服务端状态依赖接口。
- B0：直接回答原题；B1：自行检查并修订；B2：根据整体通过／失败修订；B3：根据逐约束通过／失败及原题要求修订。三个修订条件独立使用同一份 B0。
- 原始冻结：temperature 0、seed 42、max_tokens 4096；补跑主要为 16384，另有一次 32768 探测。HKBU Azure 请求不发送配置中的 `thinking` 字段。
- 主指标为 strict 提示级通过率；附报 loose、修复率、退化率。配对 bootstrap 使用 1000 次重采样和种子 42。
- 当前报告合并结果中 2072 条成功、0 条空回答、8 条错误；**181** 个唯一身份由更高 token 上限的恢复结果替换（overlay 日志计 182 次替换事件，因为 gpt-4o item 200 B3 经历了 error→empty→success 两跳）。缺失不填补。剩下 8 条错误全部来自 item 147 的 Azure 内容过滤。

## 局限

公开 IFBench 数据是否出现在模型训练集中未知；两个模型不足以代表所有模型。远程 API 和模型可能变化，种子不保证重新生成的文本完全一致。混合补跑后的设置与原始冻结设置不同，需要报告来源和分母。内容审核覆盖 260 道测试题的 2080 条配对回答，不是 2080 道独立题；现有单人标注不能估计评分者间一致性。规则通过率不能替代完整性或事实准确性评价。当前仓库未提供完整原始记录与审核标签，精确复现既有表格仍有缺口。

## 参考文献

[1] V. Pyatkin *et al*., “Generalizing Verifiable Instruction Following,” *Advances in Neural Information Processing Systems*, 2025. [Online]. Available: https://arxiv.org/abs/2507.02833

[2] Allen Institute for AI, “IFBench,” GitHub repository. [Online]. Available: https://github.com/allenai/IFBench

## 学术诚信声明

本项目应明确区分上游 IFBench 的数据、验证器与本项目的实验设计和分析，按课程要求引用使用的资料，并如实报告方法、结果和局限。不得将他人的成果或未经核验的结果冒充为自己的实验结论。

## 许可证

上游代码采用 Apache License 2.0，见 [LICENSE](LICENSE)；数据许可及第三方生成内容条款见上游项目 [2]。本项目新增部分的独立许可：TODO。
