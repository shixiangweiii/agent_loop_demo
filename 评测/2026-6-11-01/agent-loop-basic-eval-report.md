# Agent Loop 基础评测报告

> 日期：2026-06-11
> 被测对象：当前 M0 Walking Skeleton 版本 `mu`
> 真实模型：`qwen3.7-plus`
> Base URL：`https://dashscope.aliyuncs.com/compatible-mode/v1`
> API Key：仅通过运行时环境变量使用，未写入文件

## 1. 评测结论

**结论：基础评测通过，3/3 个真实 agent-loop 任务完成。**

这次评测不是单元测试，而是通过真实模型调用、真实 tool-calling、真实文件读写与 bash 执行，验证当前 M0 agent-loop 是否能完成基本 coding-agent 闭环。

结果显示：当前实现已经具备 M0 所要求的基本能力：

- 能从空目录创建代码和测试。
- 能运行测试、读取失败信息、定位并修复已有 bug。
- 能实现缺失函数并通过外部验证。
- 能在工具调用完成后自行停止并输出最终回复。

## 2. 过程文件

- 评测方案：`评测/2026-6-11-01/agent-loop-basic-eval-plan.md`
- 评测 runner：`评测/2026-6-11-01/run_basic_eval.py`
- 本轮运行目录：`评测/2026-6-11-01/runs/20260611-154532`
- runner 汇总：`评测/2026-6-11-01/runs/20260611-154532/summary.md`
- 最新汇总副本：`评测/2026-6-11-01/latest-summary.md`
- 最新 JSON 汇总：`评测/2026-6-11-01/latest-summary.json`

## 3. 实际结果

| 任务 | 结果 | Agent 耗时 | Agent 退出码 | 外部验证 |
|---|---:|---:|---:|---:|
| create_pytest_project | PASS | 173.84s | 0 | 2 passed |
| fix_existing_bug | PASS | 21.85s | 0 | 3 passed |
| implement_slugify | PASS | 24.01s | 0 | 4 passed |

总体：**3/3 通过**。

## 4. 任务观察

### T1. create_pytest_project

Agent 完成了：

- 写入 `calc.py`
- 写入 `test_calc.py`
- 运行测试
- 遇到环境层面的 pytest 问题后自我恢复
- 最终输出简短完成说明

这个任务耗时明显更长，原因不是代码生成困难，而是第一次 `pytest -q` 命中了系统 Python 环境中的 pytest，出现 `_random` 动态库加载失败。Agent 读取错误后执行 `which python3 && python3 --version`，随后改用 `python3 -m pytest -q ...` 跑通。

这说明 loop 能处理工具失败并继续自纠错；同时也暴露出当前 bash 工具继承的 PATH/解释器环境不稳定。

### T2. fix_existing_bug

Agent 完成了：

- 先运行 `pytest -q` 观察失败。
- 读取 `stats_utils.py`。
- 判断 `len(nums) - 1` 是 bug。
- 使用 `edit` 精确替换。
- 再次运行测试，3 个测试全部通过。

这是最贴近 M0 “读文件 -> 改代码 -> 跑测试”闭环的任务，表现良好。

### T3. implement_slugify

Agent 完成了：

- 运行测试观察 `NotImplementedError`。
- 读取/理解测试要求。
- 写入 `slugify` 实现。
- 再次运行测试，4 个测试全部通过。

该任务验证了 agent 能从测试约束中提取实现规则，并通过 bash 验证。

## 5. 指标摘录

| 任务 | Tool 调用次数 | Assistant 中间回复次数 | 说明 |
|---|---:|---:|---|
| create_pytest_project | 5 | 1 | 包含一次失败 pytest 后恢复 |
| fix_existing_bug | 5 | 2 | 有一次中间诊断回复 |
| implement_slugify | 6 | 2 | 读取/测试/实现/复测 |

外部验证全部使用当前项目 `.venv`：

- T1：`2 passed in 0.01s`
- T2：`3 passed in 0.01s`
- T3：`4 passed in 0.01s`

## 6. 暴露的问题

### 6.1 bash 执行环境不稳定

Agent 在 T1 中直接运行 `pytest -q`，命中了系统 Python 的 pytest，而不是项目 `.venv` 中的 pytest。虽然 agent 最终恢复成功，但这会放大评测噪声，也会影响真实使用时的稳定性。

建议后续考虑：

- 在 CLI 启动时把当前 venv 的 `bin` 放到 PATH 前面。
- 或在系统提示/任务说明中建议使用 `python -m pytest`。
- 或让 `LocalEnvironment` 记录并暴露 agent 进程使用的 Python 解释器路径。

### 6.2 与代码评审中的 timeout 风险相互印证

本轮没有最终失败，但 T1 的长耗时再次说明 bash 工具是当前 loop 的主要不确定来源。前一份代码评审中指出的“timeout 只杀顶层 shell、可能留下子进程”仍建议优先修复。

### 6.3 当前评测还很小

本轮只覆盖了最基础的 coding loop，不覆盖：

- 多文件重构。
- 长上下文任务。
- 需要多次错误恢复的任务。
- 命令超时恢复。
- 非 pytest 工具链。
- 成本/token/轮数归因。

这些更适合 M1 可观测底座完成后再做。

## 7. 密钥落盘检查

评测结束后检查 `评测/` 目录中是否出现常见 API key 前缀形态，未发现真实密钥。当前过程文件未保存 API key。

## 8. 下一步建议

进入更系统评测前，建议先修复 M0 代码评审中指出的两个问题：

1. `bash(timeout)` 杀整个进程组，避免残留子进程。
2. README 中 `.env` 使用说明与代码行为对齐。

然后补一轮 M0.1 评测：

- 固定 PATH/venv 后重跑本套 3 任务。
- 增加一个“命令超时后恢复”的任务。
- 增加一个需要读取多个文件再编辑的任务。
