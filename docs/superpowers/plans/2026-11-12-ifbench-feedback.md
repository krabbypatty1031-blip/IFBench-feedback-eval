# IFBench 反馈修订实验 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task; checkpoints use `- [ ]`. 按用户要求逐任务验收；不要自行提交或推送。

**Goal:** 基于官方 IFBench 比较模型自检、整体验证反馈与约束级反馈对指令遵循、内容质量及成本的影响。

**Architecture:** 官方 IFBench 为只读评分依赖；在独立 `feedback_eval/` 中实现数据清单、评分适配、反馈、调用、记录、流程与统计。模型调用与评分解耦，先用假客户端执行离线集成，再通过开发集冻结配置并运行测试集。

**Tech Stack:** Python 3.11、IFBench 官方验证器、pytest、JSONL、OpenAI 兼容 API。

**Spec:** `EXPERIMENT_PROTOCOL.md`；阶段和验收参考 `EXPERIMENT_EXECUTION_PLAN.md`。

## Global Constraints

- 官方基线 `cd189a9c2889526d8af901d2103fd3e1ffcea6df`；数据 `data/IFBench_test.jsonl`，SHA-256 `d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a`。
- 候选 40 开发／260 测试，先核对同源；正式实验前冻结划分、提示和配置。
- B0 初始、B1 自检、B2 整体反馈、B3 逐约束反馈；三组从同一个 B0 独立修订，通过和失败均修订。
- 主指标 strict 提示级通过率；报告 loose、修复率、退化率、内容质量、成本和缺失。反馈组是模型加验证器系统。
- 确认预算和模型前不发起付费调用；不得重置已有账本或把不可核实的支出当作零。
- 本地运行：`export NLTK_DATA="$PWD/ifbench/.nltk_data"`，`export PYTHONDONTWRITEBYTECODE=1`；所有命令从 IFBench 仓库根目录执行。

## 文件结构

| 路径 | 职责 |
|---|---|
| `feedback_eval/dataset.py` | 读取、哈希、分组、审计及清单 |
| `feedback_eval/scoring.py` | 调用官方 strict/loose，规范化结果 |
| `feedback_eval/feedback.py` | 验证结果到 B2/B3 反馈的映射 |
| `feedback_eval/client.py` | 客户端接口、模型响应与错误类型 |
| `feedback_eval/store.py` | 身份、配置指纹、原子存储及预算记录 |
| `feedback_eval/runner.py` | B0 生成与 B1–B3 独立分支 |
| `feedback_eval/analysis.py` | 配对指标、分母、区间及失败汇总 |
| `feedback_eval/cli.py` | 审计、开发、冻结、运行和分析入口 |
| `tests/feedback_eval/` | 各模块与离线集成测试 |
| `configs/feedback_eval/` | 不含密钥的开发配置、冻结模板 |
| `runs/feedback_eval/` | 忽略的实验数据、账本与冻结记录 |

---

### Task 1: 官方基线与数据审计

**Files:** Create `feedback_eval/__init__.py`, `feedback_eval/dataset.py`, `tests/feedback_eval/test_dataset.py`; document commands in `docs/feedback_eval.md`.

**Interfaces:** `load_items(path: Path) -> list[dict]`; `audit_items(items: list[dict]) -> dict`；返回数量、重复 ID、重复提示、约束次数和类别。

- [x] 用 pytest 编写样本：重复 ID 必须报告，重复提示必须报告，单／双约束计数必须正确；文件缺失或 JSON 错误直接失败。
- [x] 运行 `NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest tests/feedback_eval/test_dataset.py -q`，确认新测试在实现前失败。
- [x] 实现 JSONL 读取与审计；哈希直接对原始文件字节计算，禁止静默规范化提示文本。
- [x] 运行上述测试及 `NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python -m pytest instructions_test.py -q`；预期官方 71 项、144 子测试通过；数据 300 行、300 不同 ID、58 类约束。
- [x] 用官方 `run_eval.py` 和 `data/sample_output.jsonl` 在 `/tmp/ifbench-baseline` 生成两种评分；记录文件名、输入输出和退出状态。验收后才进入下一任务。

### Task 2: 划分与数据清单

**Files:** Modify `feedback_eval/dataset.py`; Create `tests/feedback_eval/test_split.py` 和 `configs/feedback_eval/split.json`。

**Interfaces:** `split_groups(groups: list[list[str]], dev_target: int, seed: int) -> tuple[list[str], list[str]]`; `validate_split(all_ids: set[str], dev: list[str], test: list[str]) -> None`。

- [ ] 编写测试：相同 seed 同结果；同组不分割；两个集合无交叉且覆盖全部 ID；不可能恰好分配时返回实际数量并记录原因。
- [ ] 固定 seed 42；通过规范化文本的完全重复和人工审计的同源映射生成组，人工映射单独保存并附依据；未知关系记为未知，不宣称消除污染。
- [ ] 输出包含来源哈希、算法版本、组映射、ID 列表、类别分布和划分哈希的清单；不得改写原始 JSONL。
- [ ] 运行 `.venv/bin/python -m pytest tests/feedback_eval/test_split.py -q`；用独立进程重复生成，比较清单哈希，验收后冻结候选划分。

### Task 3: 官方评分与反馈

**Files:** Create `feedback_eval/scoring.py`, `feedback_eval/feedback.py`, `tests/feedback_eval/test_feedback.py`。

**Interfaces:** `score(item: dict, response: str) -> dict` 返回 strict、loose 的 `all_pass: bool` 与 `per_constraint: list[bool]`；`make_feedback(item: dict, strict: dict, mode: str) -> str`，mode 为 `B2` 或 `B3`。

- [ ] 编写通过、失败、双约束与验证器异常测试；与 `evaluation_lib.test_instruction_following_strict/loose` 对比，断言 B3 状态与约束顺序一致。
- [ ] 使用官方 `InputExample` 和严格／宽松函数，逐次复制输入以避免评分函数修改 kwargs。将异常表示为独立错误，不能转成失败。
- [ ] B2 返回整体状态；B3 返回逐项状态和事先审核过的原始要求。无法可靠映射题目约束时抛出明确错误，不通过猜测或把验证器内部参数直接假装成自然语言要求。
- [ ] 开发集审计反馈正确性；记录 58 类规则的资源与支持情况。不为正式测试观察结果来决定排除。运行 `.venv/bin/python -m pytest tests/feedback_eval/test_feedback.py -q`，全部通过。

### Task 4: 调用与预算存储

**Files:** Create `feedback_eval/client.py`, `feedback_eval/store.py`, `tests/feedback_eval/test_client_store.py`。

**Interfaces:** `Client.generate(messages: list[dict], settings: dict) -> dict`；结果字段 `text`, `usage`, `finish_reason`, `latency_ms`, `error`。`record_key(protocol: str, model: str, item_id: str, condition: str, repeat: int) -> str`。

- [ ] 使用假客户端测试成功、超时、限流、截断、无 usage 和重复请求；确认异常、空回答和截断不会标为正常拒答或自动评分失败。
- [ ] 实现可配置 API base、模型及参数；密钥仅由环境读取；记录调用与计量，未知费用明确为未知。规范 JSON 指纹含数据、模型、提示、代码版本和参数。
- [ ] 实现原子写入与安全恢复；同一身份不同内容、配置指纹变化或不完整账本必须拒绝续跑。并发下预算使用锁或单进程分配，超时费用未知时保留待核实额度。
- [ ] 确认此前账本位置与余额；缺失则标为无法核实，不得重置。运行 `.venv/bin/python -m pytest tests/feedback_eval/test_client_store.py -q`，无网络和付费调用。

### Task 5: 修订流程与离线集成

**Files:** Create `feedback_eval/runner.py`, `feedback_eval/cli.py`, `tests/feedback_eval/test_runner.py`；增加 `configs/feedback_eval/prompts.json`。

**Interfaces:** `run_item(item: dict, client: Client, config: dict) -> dict` 返回 B0 及 B1–B3 的回答、状态、评分与计量；失败分支独立记录。

- [ ] 假客户端测试：B0 恰好调用一次，三个分支均包含相同初始答案且互不串联；通过的初始答案仍修订；B2/B3 的状态对应 strict 官方分数。
- [ ] 实现初始回答与三个独立消息分支；通过提示词要求保留用户任务、只返回最终答案。B2/B3 通过时允许保持不变；验证异常时不伪造反馈。
- [ ] 在开发集使用示例检查提示与反馈，锁定前不得使用测试输出调参。实现离线 dry-run，显式展示调用数，不触网。
- [ ] 运行 `.venv/bin/python -m pytest tests/feedback_eval/test_runner.py -q`，并用假客户端运行 4 道模拟题确认重启不会重复生成。

### Task 6: 开发试验与冻结

**Files:** Modify `feedback_eval/cli.py`, `feedback_eval/store.py`; Create `tests/feedback_eval/test_freeze.py` 与 `configs/feedback_eval/development.yaml`。

**Interfaces:** `build_freeze(config: dict, source_hashes: dict) -> dict`; `validate_resume(existing: dict, current: dict) -> None`，验证完整记录哈希后比较内容。

- [ ] 离线测试篡改配置、数据、提示、代码及缺字段均拒绝恢复；无冻结记录的非空目录不可接管。
- [ ] 完成两个候选模型可用性与实际价格核验；在明确可用预算内仅使用开发题试运行，记录 usage、输出上限、失败类型和实际费用。
- [ ] 将候选 260 × 2 × 4 = 2,080 输出、重试、重复性试验和已发生费用纳入预算估算；无法确认余额或连接时停止依赖该信息的运行并记录原因。
- [ ] 冻结完整模型标识、版本、划分、配置、提示、代码、评分、反馈、重试、人工审核种子及成本上限；运行 `.venv/bin/python -m pytest tests/feedback_eval/test_freeze.py -q` 并验证修改后不能续跑。

### Task 7: 正式运行与质量检查

**Files:** Modify `feedback_eval/cli.py`; Create `tests/feedback_eval/test_execution.py`；产生 `runs/feedback_eval/` 下的冻结记录和结果。

- [ ] 用模拟客户端验证运行调度、预算耗尽时停止、进程中断与恢复、幂等写入和输入输出计数。
- [ ] 使用冻结配置运行正式测试；各模型、题目和条件单独记录状态，不依据中间表现改提示或筛题。
- [ ] 汇总预期、成功、失败、截断、空回答、缺失和费用未知数量；将官方评分结果关联到实际回答并保留原始输出。
- [ ] 运行 `.venv/bin/python -m pytest tests/feedback_eval/test_execution.py -q`；验收所有状态均可对账，任何未完成项明确保留。

### Task 8: 分析与盲审

**Files:** Create `feedback_eval/analysis.py`, `tests/feedback_eval/test_analysis.py`, `docs/feedback_eval_review.md`。

**Interfaces:** `summarize(records: list[dict]) -> dict` 输出 strict/loose 通过数和分母、B0→Bx 修复/退化、完成率、成本与延迟；缺失不纳入成功分母并独立报告。

- [ ] 用手算样本测试：初始失败 4 题修复 2 题为 2/4；初始通过 5 题退化 1 题为 1/5；零分母输出 null；不匹配题目不组成配对。
- [ ] 以题目或识别的同源组为单位配对 bootstrap，固定种子及重复次数；输出差异与区间，类别及单/双约束为探索分析。
- [ ] 在冻结阶段预选约 40 题，隐藏模型、分组并随机顺序，分别审核全部条件的原任务完成情况：完成、部分完成、未完成、无法判断；留存标注，单人审核标记局限。
- [ ] 运行 `.venv/bin/python -m pytest tests/feedback_eval/test_analysis.py -q`；从原始记录重建表格，核对所有分子、分母与缺失数量。

### Task 9: 项目报告与展示

**Files:** Create `docs/report/outline.md`, `docs/report/results.md`, `docs/presentation/outline.md`。

- [ ] 依课程要求组织背景、相关研究、研究问题、方法、实验数据、定量结果、成功／失败案例、限制、个人贡献和 IEEE 文献。
- [ ] 绘制模型与方法通过率、B0→修复／退化、内容质量和成本图，标注分母、区间、数据版本和不完整情况。
- [ ] 保留官方代码 Apache-2.0、数据 ODC-BY-1.0 来源和必要署名；明确新增代码和实验是本项目贡献，不宣称训练数据完全无污染。
- [ ] 从图表回溯原始记录、复核数值及报告和展示一致性，导出课程要求的报告和演示 PDF。

## 自审与执行边界

- Task 1–5 可用模拟模型离线完成；Task 6 的真实接口与费用依赖已有授权和可用预算，Task 7 只能在正式配置冻结后执行。
- 使用现有数据和官方评分；统计结果、模型表现和成本均为待测，不预设研究结果。
- 使用者可逐任务实现与验收；本计划没有执行代码变更、模型调用、提交或推送。
