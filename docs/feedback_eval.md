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
