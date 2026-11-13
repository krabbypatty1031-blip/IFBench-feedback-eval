# feedback_eval 运行手册

本文件是日常操作入口（命令、NLTK、产物目录、禁止改动的官方路径）。研究定义与评分口径见 [实验协议](EXPERIMENT_PROTOCOL.md)；阶段验收见 [执行计划](EXPERIMENT_EXECUTION_PLAN.md)；任务拆分见 [实施计划](superpowers/plans/2026-11-12-ifbench-feedback.md)；目录职责见 [结构设计](ARCHITECTURE_PLAN.md)。课程说明 PDF 在 Git 仓库外：`../../Group Project Description.pdf`。

下方 Task 1–6 主要保留历史开发和验收记录，命令和结果应结合对应时间的代码与配置理解；真实开发题输出不作为正式测试结果。正式生成与量化分析已完成（2026-11-13）；当前进度与数字见文末 Task 7–8 及 [执行计划当前状态](EXPERIMENT_EXECUTION_PLAN.md)。

## 目录职责

官方部分（实验中只读，不要移动）：`ifbench/`、根目录 `evaluation_lib.py`、`run_eval.py`、`generate_responses.py`、根目录 `config.py`、`data/`、`eval/`（上游示例）、`instructions_test.py`。

本课程实验（可版本控制）：`feedback_eval/`、`configs/feedback_eval/`、`tests/feedback_eval/`，以及上述协议与手册。`feedback_eval/config.py` 提供配置解析，`feedback_eval/environment.py` 提供环境配置读取；原 `store.load_mapping_yaml` 及 CLI 的环境函数导入保持兼容。

本地不入库：`.env`、`.venv/`、`runs/feedback_eval/`、NLTK 数据。不得为清理工作区删除已有试跑记录。移动或改写 `feedback_eval/*.py` 会改变冻结代码哈希；后续正式跑应使用新冻结和新运行目录，不得改旧 `freeze.json`。

## 运行产物约定

新跑统一使用：

- 开发探测：`--store-dir runs/feedback_eval/dev`
- 正式运行：`--store-dir runs/feedback_eval/official`

当前正式产物与敏感性重跑、以及历史开发探测均**原路径保留**。开发探测不要搬进 `official/`，也不当作正式测试结果：

| 目录 | 含义 |
| --- | --- |
| `runs/feedback_eval/official/` | 正式冻结与 2080 条 4096 原始记录；`analysis.json` 为报告表 |
| `runs/feedback_eval/official/analysis_4096_only.json` | 未覆盖 16384/32768 的备份表 |
| `runs/feedback_eval/official/overlay_manifest.json` | 正式表替换了哪些身份 |
| `runs/feedback_eval/official/review_pack/` | 40 题 × 2 × 4 = 320 条盲审包；标签未填 |
| `runs/feedback_eval/sensitivity_empty_b0_16384/` | 空 B0 身份的 16384 重跑（168 条） |
| `runs/feedback_eval/probe_b3_32768/` | gpt-4o item 200 B3 的 32768 探测 |
| `runs/feedback_eval/probe_empty_b0_8192/` | 空 B0 的 8192 探测 |
| `runs/feedback_eval/probe_empty_b0_16384/` | 空 B0 的 16384 探测 |
| `runs/feedback_eval/dev_paid/` | 早期付费入口探测（曾因缺密钥停住） |
| `runs/feedback_eval/dev_paid_deepseek/` | DeepSeek 1 题开发探测 |
| `runs/feedback_eval/dev_two_items_20261112/` | 两题离线/配置试跑 |
| `runs/feedback_eval/dev_two_items_http_20261112/` | 两题真实 HTTP 试跑 |

所有命令在 `IFBench/` 仓库根目录执行。密钥不写入本文件。禁止改官方验证器、`evaluation_lib.py` 和 `data/IFBench_test.jsonl` 的字节。

```bash
export NLTK_DATA="$PWD/ifbench/.nltk_data"
export PYTHONDONTWRITEBYTECODE=1
```

唯一实验数据：`data/IFBench_test.jsonl`。包内 `ifbench/data/IFBench_test.jsonl` 与此文件哈希不同，禁止混用。

## Task 1：官方基线与数据审计

### 测试（实现前为 RED）

实现前：

```text
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_dataset.py -q
# ERROR collecting ... ModuleNotFoundError: No module named 'feedback_eval'
```

实现后：

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_dataset.py -q
# 9 passed
```

### 官方验证器回归

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest instructions_test.py -q
# 71 passed, 144 subtests passed
```

### 官方数据审计

```bash
.venv/bin/python -c "from pathlib import Path; from feedback_eval.dataset import audit_items, file_sha256, load_items; p=Path('data/IFBench_test.jsonl'); r=audit_items(load_items(p)); print(file_sha256(p), r['n_items'], len(r['constraint_types']), r['n_single_constraint'], r['n_double_constraint'])"
```

| 项 | 值 |
| --- | --- |
| SHA-256 | `d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a` |
| 行数 / 唯一 `key` | 300 / 300 |
| 重复 ID / 重复 prompt | 无 |
| 约束类型 | 58 |
| 单约束 / 双约束 | 256 / 44 |

哈希对原始文件字节计算，不对 prompt 做空白规范化。

### 官方样例评分

输入：

- `--input_data=data/IFBench_test.jsonl`
- `--input_response_data=data/sample_output.jsonl`
- `--output_dir=/tmp/ifbench-baseline`

```bash
mkdir -p /tmp/ifbench-baseline
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m run_eval \
  --input_data=data/IFBench_test.jsonl \
  --input_response_data=data/sample_output.jsonl \
  --output_dir=/tmp/ifbench-baseline
```

| 项 | 值 |
| --- | --- |
| 退出码 | 0 |
| 输出 | `/tmp/ifbench-baseline/sample_output-eval_results_strict.jsonl`（300 行） |
| 输出 | `/tmp/ifbench-baseline/sample_output-eval_results_loose.jsonl`（300 行） |
| strict 提示级准确率 | 0.280000 |
| loose 提示级准确率 | 0.313333 |

`sample_output.jsonl` 是官方示例回答，不是本实验模型结果。仓库内已有 `eval/eval_results_*.jsonl` 行数与命名不同，Task 1 以 `/tmp/ifbench-baseline` 这次运行为准。

## Task 2：划分与数据清单

划分不改写 `data/IFBench_test.jsonl`。组来自两处：规范化 prompt 的完全重复，以及 `configs/feedback_eval/homology.json` 中附依据的人工同源。未列入的题目记为 unknown，不宣称已消除同源或训练污染。

### 测试（实现前为 RED）

```text
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_split.py -q
# ERROR collecting ... ImportError: cannot import name 'SPLIT_ALGORITHM_VERSION'
```

实现后：

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_split.py tests/feedback_eval/test_dataset.py -q
# 21 passed（含独立进程重复生成并比对 split_sha256）
```

### 候选划分（seed=42，算法 split-v1）

```bash
.venv/bin/python -c "from pathlib import Path; from feedback_eval.dataset import build_split_manifest, load_items, write_split_manifest; p=Path('data/IFBench_test.jsonl'); m=build_split_manifest(load_items(p), p, Path('configs/feedback_eval/homology.json'), 42, 40); write_split_manifest(Path('configs/feedback_eval/split.json'), m); print(m['n_dev'], m['n_test'], m['split_sha256'], m['quota_reason'])"
```

| 项 | 值 |
| --- | --- |
| 开发 / 测试 | 40 / 260（配额可精确满足，`quota_reason` 为空） |
| 组数 | 296（4 个人工同源组，0 个规范化重复组，292 个 unknown 单题） |
| 人工同源 | `{68,109}` `{146,158}` `{167,168}` `{273,283}`，本种子下均在测试集 |
| 开发集 | 单约束 36、双约束 4、27 类约束 |
| 测试集 | 单约束 220、双约束 40、57 类约束 |
| `split_sha256` | `41ec3bf4e6a83079a2a7d824f0892e4f52df425e598896cd03900239e5421241` |
| 源数据 SHA-256 | 生成前后不变，仍为协议中的官方哈希 |

11 个仅出现一次的 `custom:*` 约束无法同时进入开发集和测试集。同源审查只覆盖人工能确认的同一用户任务；其余关系未知。

## Task 3：官方评分与反馈

`score()` 调用官方 `evaluation_lib.test_instruction_following_strict/loose`，先深拷贝并去掉 null kwargs，避免改写原始 JSONL 对象。验证器异常记为 `error`，`all_pass` 为 `None`，不记作失败。B3 只引用 prompt 中已出现的约束原文，不把 `5.0` 这类验证器内部参数当成要求。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_feedback.py -q
# 11 passed
```

| 项 | 值 |
| --- | --- |
| B2 | 仅整体 strict 通过 / 未通过 |
| B3 | 按 `instruction_id_list` 顺序给出 PASS/FAIL + prompt 原文 |
| 无法映射 | `ConstraintMappingError`，不编造反馈 |
| 验证器异常 | `VerifierFeedbackError`，不编造通过或失败 |
| 58 类映射 | 全部 `supported`（见 `configs/feedback_eval/constraint_support.json`） |
| 开发集未覆盖类型 | 31 类；用全量 300 题 prompt 做结构审计，不用模型输出决定是否排除 |

依赖：NLTK 数据目录；`format:emoji` 另需 `emoji`；`words:odd_even_syllables` 另需 `syllapy`；三个 CSV 自定义约束使用标准库 `csv`。

## Task 4：调用与预算存储

身份是 `(protocol, model, item_id, condition, repeat)`，不用 prompt 文本续跑。`FakeClient` 覆盖成功、超时、限流、截断、缺 usage、空回答；这些状态不是拒答，也不等于评分失败。密钥只从环境变量 `API_KEY` / `FEEDBACK_EVAL_API_KEY` / `AZURE_OPENAI_API_KEY` 读取，不写入记录。配置指纹包含数据哈希、模型、提示哈希、代码版本和参数。

HKBU GenAI 不是 OpenAI `/v1/chat/completions` + Bearer。客户端在 `API_PROVIDER=azure` 或 `api_base` 指向 `genai.hkbu.edu.hk` 时走 Azure 形状：`api-key` 请求头，`POST {api_base}/deployments/{模型}/chat/completions`。推荐本地 `.env`：

```bash
API_PROVIDER=azure
API_BASE=https://genai.hkbu.edu.hk/general/rest
API_KEY=your-hkbu-genai-key
MODEL_A=gpt-4.1
MODEL_B=gpt-4o
# 可选：AZURE_OPENAI_API_VERSION=2024-02-01
```

HKBU 没有 DeepSeek 的 `GET /user/balance`。不得把缺失钱包写成 0；开发探测用 `trial-dev --paid`，正式 `--paid` 在没有核实余额前仍会挡住。DeepSeek 路径保持默认不变。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_client_store.py -q
# 13 passed；测试不发起网络或付费调用
```

此前账本核对（本仓库，2026-11-12）：

| 候选路径 | 状态 |
| --- | --- |
| `runs/feedback_eval/ledger.json` | 不存在 |
| `runs/feedback_eval/budget.json` | 不存在 |
| `docs/budget_ledger.json` | 不存在 |

`inspect_prior_ledger` 结果：`status=unverified_missing`，`spent=None`，`balance=None`，`unit_prices=None`。**不得把历史支出或余额当成 0。** 超时调用记入 `pending_unverified`，费用保持 unknown。开发和正式运行使用不同 `RunStore` 根目录。

## Task 5：修订流程与离线集成

B0 只调用一次，消息为原始 IFBench 题面、不加包装。B1/B2/B3 从同一 B0 独立分支，各一次修订；B0 通过也会修订。B2/B3 通过时提示允许保持原样。验证器异常时不编造反馈、不发起对应修订调用。提示模板见 `configs/feedback_eval/prompts.json`。开发集只做结构核对，不用测试集输出调参。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_runner.py -q
# 8 passed

NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m feedback_eval.cli dry-run --n-items 4 --store-dir /tmp/ifbench-dry-run
# 第一次 planned_calls=16 actual_calls=16
# 第二次 planned_calls=16 actual_calls=0（续跑，不重复生成）
```

`dry-run` 只使用 `FakeClient`，不构造 HTTP 客户端。

## Task 6：开发试验与冻结

冻结记录钉住数据、划分、提示、代码、评分口径、反馈规则、重试、人工审核抽样和成本上限。`ready_for_official_run` 仅在模型标识、单价来源和成本上限都已核验时为 true。当前两项均为未核验，正式运行入口保持关闭。

用户已在会话中授权开发试跑。开发路径在 `FEEDBACK_EVAL_AUTHORIZE_SPEND=1`、API key、以及正的 `call_limit`（默认 16）下才会构造 `OpenAICompatClient`。历史余额仍记为无法核实，不用 0 代替；美元成本上限未确认前 `ready_for_official_run` 保持 false。

密钥只从 `IFBench/.env` 或环境变量读取，不写入记录。DeepSeek thinking 默认开启，开发参数固定 `thinking.type=disabled`，避免推理过程进入 IFBench 答案并产生额外输出 token。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_freeze.py -q
```

本次环境核验（2026-11-12，用户授权并填入 `.env` 之后）：

| 项 | 值 |
| --- | --- |
| 候选槽位 | `model_a=deepseek-flash`，`model_b=deepseek-v4-pro`（yaml 中 id 仍为 null，运行时从环境填入） |
| API | `https://api.deepseek.com/v1` |
| 单价写入冻结 | `source=unverified`（官方价目有峰时/非峰时与 cache hit/miss，冻结字段只能记单一 input/output） |
| 账本 | `unverified_missing`，不得当成 0 |
| 用户授权 | 已授权开发试跑；`call_limit=16` |
| `.env` | 已填写；密钥未写入任何 JSON |
| 付费试跑 | `allowed=true`；`runs/feedback_eval/dev_paid_deepseek/` |
| `ready_for_official_run` | `false`（单价未写入冻结、美元上限未确认、余额无法核实） |

预算规划（不是实测费用）：

| 项 | 值 |
| --- | --- |
| 正式输出 | 260 × 2 × 4 = **2080** |
| 重试预留（规划 10%） | 208 |
| 重复性试验 | 0 |
| 合计规划调用 | 2288 |
| 已发生费用 | null（`incurred_status=unverified_missing`） |
| 估算金额 | null（`status=unknown`，`stop_reason=balance_unverified`） |

```bash
.venv/bin/python -m feedback_eval.cli estimate-budget
.venv/bin/python -m feedback_eval.cli freeze --store-dir /tmp/ifbench-task6-freeze
# ready_for_official_run=false
# planned_outputs=2080
# paid_trial_reason=authorization_missing
.venv/bin/python -m feedback_eval.cli freeze --store-dir /tmp/ifbench-task6-freeze
# 同源续跑成功
FEEDBACK_EVAL_AUTHORIZE_SPEND=1 .venv/bin/python -m feedback_eval.cli trial-dev --paid --n-items 1 --store-dir runs/feedback_eval/dev_paid
# 缺密钥时 exit 3；paid_trial_blocked reason=api_key_missing
.venv/bin/python -m feedback_eval.cli trial-dev --paid --n-items 1 --store-dir runs/feedback_eval/dev_paid_deepseek
# planned_calls=10 actual_calls=10 client=OpenAICompatClient
# probe_ok deepseek-flash / deepseek-v4-pro；thinking disabled
.venv/bin/python -m feedback_eval.cli trial-dev --offline --n-items 4 --store-dir /tmp/ifbench-task6-offline
# planned_calls=32 actual_calls=32 client=FakeClient
```

授权后的 1 题开发探测（2026-11-12 14:35 HKT / 06:35 UTC，处于 DeepSeek 工作日峰时 06:00–10:00 UTC）：

| 项 | 值 |
| --- | --- |
| 调用 | 2 次 ping + 1 题 × 2 模型 × 4 条件 = **10**；上限 16 |
| 失败类型 | 全部 `success` / `finish_reason=stop`；无 timeout、429、空回答、缺 usage |
| 开发题 | 官方测试集 `key=1`（dev split 第一题），约束 `count:keywords_multiple` |
| `deepseek-flash` | B0–B3 均 strict/loose 通过；生成 1287 prompt / 1104 completion tokens |
| `deepseek-v4-pro` | B0–B3 均 strict/loose 失败；生成 1849 prompt / 1452 completion tokens |
| ping usage | 两模型各 11 prompt + 1 completion |
| 账本费用 | `spent_this_run.status=unknown`（冻结单价仍为 null） |
| 按官方价目的峰时估算 | flash 约 **$0.0015**，pro 约 **$0.0062**，合计约 **$0.0078**（含 cache hit；不是实测扣款） |
| 冻结 `availability` | 仍为 `unverified`（`freeze.json` 在探测前写入，不能事后改哈希） |

[DeepSeek 官方价目](https://api-docs.deepseek.com/quick_start/pricing)（2026-11-12 查阅，每百万 token，USD）：

| 模型 | cache hit 峰时/非峰 | cache miss 峰时/非峰 | 输出 峰时/非峰 |
| --- | --- | --- | --- |
| `deepseek-flash` | $0.006 / $0.003 | $0.30 / $0.15 | $1.20 / $0.60 |
| `deepseek-v4-pro` | $0.044 / $0.022 | $1.32 / $0.66 | $3.96 / $1.98 |

pytest 在进程已加载授权环境时，`test_paid_trial_cli_stops_without_network` 曾误走付费路径（约 44s，可能再消耗约 10 次调用）。之后已用 `tests/feedback_eval/conftest.py` 隔离 `.env` 并拒绝测试套件打开 HTTP。该额外扣款同样无法从本地账本核实金额。

篡改已写入的 `freeze.json`（例如改 `prompt_sha256`）后再 `freeze` 同一目录：`FreezeConflictError`，拒绝续跑。无 `freeze.json` 的非空目录也会拒绝接管。

人工审核抽样已按测试集、`seed=42`、`n=40` 写入冻结记录。盲审协议见 [内容审核](feedback_eval_review.md)。

## Task 7：正式运行与质量检查

正式配置为 `configs/feedback_eval/official.yaml`：协议 `feedback-eval-official-hkbu-v1`，模型 `gpt-4.1` / `gpt-4o`，Azure 形状 HKBU GenAI。校园网关没有公开单价和 `/user/balance`，费用金额记为未知；停止条件为 **`call_cap=2288`**（2080 次规划输出加 10% 余量）。不要把先前 DeepSeek 支出当成本次钱包。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_execution.py tests/feedback_eval/test_budget_guard.py -q
# 含 rehearsal / summarize / BudgetedClient / run-official CLI
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval -q
# 2026-11-13：124 passed
.venv/bin/python -m feedback_eval.cli run-official --offline --n-items 2 --store-dir /tmp/ifbench-official-offline
# expected=16 actual_calls=16 client=FakeClient
.venv/bin/python -m feedback_eval.cli trial-dev --paid --n-items 1 --store-dir runs/feedback_eval/dev
.venv/bin/python -m feedback_eval.cli run-official --paid --store-dir runs/feedback_eval/official
# 260 × 2 × 4 = 2080；断点可续跑；勿在运行中改 feedback_eval/*.py
```

`ready_for_official_run` 在写入 `runs/feedback_eval/prior_balance.json`（`GET /user/balance`）且授权、密钥、确认上限都满足时为 true。历史支出仍为 null，不记成 0。校园网关无已核验扣款，`spent_this_run.status=unknown`。

正式跑已完成（协议 `feedback-eval-official-hkbu-v1`，`freeze_sha256=a333e677…`）。2080 身份均有记录。4096 账本：success **1891**、empty **53**、error **136**。空 B0 几乎都是 `reasoning_tokens` 吃满 cap、`finish_reason=stop` 但正文为空，随后 B1–B3 因空 assistant 触发 HTTP 400。失败身份已按冻结规则隔离后补跑；内容过滤等仍保持缺失。不要再对 official store 发起新的 2080 次 generate。

## Task 8：分析与盲审

正式**报告表**以模型输出为准，不把 `max_tokens` 当成能力指标。`runs/feedback_eval/official/records/` 仍是冻结的 4096 原始 JSON，不会被覆盖。分析时把 16384 空 B0 重跑和 32768 最后一题 B3 覆盖进失败身份：有正文的记录替换 empty/error；4096 已经成功的身份保持不动。4096-only 备份在 `analysis_4096_only.json`，覆盖清单在 `overlay_manifest.json`。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest tests/feedback_eval/test_analysis.py -q
.venv/bin/python -m feedback_eval.cli analyze --store-dir runs/feedback_eval/official
.venv/bin/python -m feedback_eval.cli review-pack --store-dir runs/feedback_eval/official
# 只要 4096 原始表：加 --no-overlay
```

`analysis.json` 从合并后的身份重建配对 strict/loose、修复/退化与同源组 bootstrap。成功分母不含仍失败的 empty/error/missing。费用保持 unknown。盲审操作见 [内容审核](feedback_eval_review.md)。

覆盖后正式记账：success **2059** / empty **11** / error **10**。四条件齐全：gpt-4.1 **257/260**，gpt-4o **254/260**。覆盖 169 个身份（126 error + 43 empty；其中 gpt-4o item 200 B3 由 32768 补上）。

| | gpt-4.1 strict | gpt-4o strict |
| --- | --- | --- |
| B0 | 138/259 = 0.533 | 148/259 = 0.571 |
| B1 | 146/258 = 0.566 | 157/255 = 0.616 |
| B2 | 163/258 = **0.632** | 174/258 = **0.674** |
| B3 | 153/257 = 0.595 | 167/255 = 0.655 |
| B2−B0 bootstrap | +0.097 [0.051, 0.141] | +0.101 [0.054, 0.153] |
| 修复 B2 / 退化 B2 | 32/120 / 7/138 | 36/110 / 10/148 |

B1 相对 B0 约 +3%，区间含 0。并进恢复正文后 B0 通过率低于 4096-only，因为原空题进入分母且大多仍不过约束。`tests/feedback_eval`：**124 passed**。

下一步：在 `review_pack/judgments.csv` 填写 320 条标签（填完前不要打开 `key.json`）；然后写报告与 PPT。

