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
