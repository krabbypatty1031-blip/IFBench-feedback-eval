# feedback_eval 运行记录

所有命令在 `IFBench/` 仓库根目录执行。密钥不写入本文件。

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

身份是 `(protocol, model, item_id, condition, repeat)`，不用 prompt 文本续跑。`FakeClient` 覆盖成功、超时、限流、截断、缺 usage、空回答；这些状态不是拒答，也不等于评分失败。密钥只从环境变量 `API_KEY` / `FEEDBACK_EVAL_API_KEY` 读取，不写入记录。配置指纹包含数据哈希、模型、提示哈希、代码版本和参数。

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

密钥仍只从环境读取。付费试跑还需要 `FEEDBACK_EVAL_AUTHORIZE_SPEND=1`、可用 API key、可核实余额，以及 `cost_cap.status=confirmed`。缺任何一项都会停止并写入原因，不构造 HTTP 客户端。

```bash
NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_freeze.py -q
# 19 passed
```

本次环境核验（2026-11-12）：

| 项 | 值 |
| --- | --- |
| 候选槽位 | `model_a`、`model_b`（`id`/`api_base` 均为 null，`availability=unverified`） |
| 单价 | `source=unverified`，金额为 null |
| 账本 | `unverified_missing`，历史支出与余额不得当作 0 |
| `API_KEY` / `FEEDBACK_EVAL_API_KEY` | 未设置 |
| `FEEDBACK_EVAL_AUTHORIZE_SPEND` | 未设置 |
| 成本上限 | `amount=null`，`status=unverified` |
| 付费试跑 | `allowed=false`，`reason=authorization_missing` |
| `ready_for_official_run` | `false` |

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
.venv/bin/python -m feedback_eval.cli trial-dev --paid --store-dir /tmp/ifbench-task6-paid
# exit 3；paid_trial_blocked reason=authorization_missing；未构造 OpenAICompatClient
.venv/bin/python -m feedback_eval.cli trial-dev --offline --n-items 4 --store-dir /tmp/ifbench-task6-offline
# planned_calls=32 actual_calls=32 client=FakeClient
```

篡改已写入的 `freeze.json`（例如改 `prompt_sha256`）后再 `freeze` 同一目录：`FreezeConflictError`，拒绝续跑。无 `freeze.json` 的非空目录也会拒绝接管。

人工审核抽样已按测试集、`seed=42`、`n=40` 写入冻结记录，供 Task 8 使用；盲审本身尚未执行。授权预算与两个真实模型之前，不要开始 Task 7 正式付费运行。
