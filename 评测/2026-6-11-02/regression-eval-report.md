# Agent Loop 回归评测报告

> 日期：2026-06-11
> 归档目录：`评测/2026-6-11-02`
> 被测对象：M1 Harness Core 实施后的 `mu`
> 真实模型：`qwen3.7-plus`
> Base URL：`https://dashscope.aliyuncs.com/compatible-mode/v1`
> API Key：仅通过运行时环境变量使用，未写入文件

## 1. 评测结论

**结论：回归评测通过，3/3 个真实 agent-loop 任务完成。**

本轮复用 `2026-6-11-01` 的三任务基础评测口径，对 M1 后的 agent-loop 做真实模型回归验证。相比上一轮 M0 评测，本轮额外验证到 M1 的两个真实运行面：

- 每个任务 stdout 末尾均输出了归因报告。
- 每个任务工作区下均生成了 `.mu/sessions/*.jsonl` session 文件。

## 2. 过程文件

- 回归评测方案：`评测/2026-6-11-02/regression-eval-plan.md`
- 回归 runner：`评测/2026-6-11-02/run_regression_eval.py`
- 本轮运行目录：`评测/2026-6-11-02/runs/20260611-191716`
- 最新汇总：`评测/2026-6-11-02/latest-summary.md`
- 最新 JSON 汇总：`评测/2026-6-11-02/latest-summary.json`

## 3. 实际结果

| 任务 | 结果 | Agent 耗时 | Agent 退出码 | 外部验证 |
|---|---:|---:|---:|---:|
| create_pytest_project | PASS | 44.02s | 0 | 2 passed |
| fix_existing_bug | PASS | 22.29s | 0 | 3 passed |
| implement_slugify | PASS | 39.78s | 0 | 4 passed |

总体：**3/3 通过**。

## 4. M1 回归观察

### 4.1 M0 基础闭环保持可用

三个真实任务均通过外部 pytest 验证：

- `create_pytest_project`：从空工作区创建 `calc.py` 与 `test_calc.py`，外部验证 `2 passed`。
- `fix_existing_bug`：修复 `average()` 的除数 bug，外部验证 `3 passed`。
- `implement_slugify`：实现 `slugify()`，外部验证 `4 passed`。

说明 M1 的事件流、session、context 管线改造没有破坏 M0 的基础 coding loop。

### 4.2 归因报告真实输出

每个任务的 agent stdout 中均出现 1 次 `归因报告`：

| 任务 | Tool 调用次数 | 归因报告次数 |
|---|---:|---:|
| create_pytest_project | 4 | 1 |
| fix_existing_bug | 6 | 1 |
| implement_slugify | 8 | 1 |

这说明 `AttributionCollector` 在真实模型运行路径中已经接入事件流。

### 4.3 Session 持久化真实生成

每个任务工作区下均生成 session JSONL：

| 任务 | Session 文件 | 行数 |
|---|---|---:|
| create_pytest_project | `.mu/sessions/54b0b5b02698.jsonl` | 10 |
| fix_existing_bug | `.mu/sessions/3419fd6c81e2.jsonl` | 13 |
| implement_slugify | `.mu/sessions/c6d607d9c4d6.jsonl` | 18 |

这说明 M1 的 JSONL session append 路径在真实任务中可用。

## 5. 注意事项

本轮评测过程中，首次 runner 复用上轮脚本时输出目录误指向 `2026-6-11-01/runs/20260611-191716`。评测完成后已将本轮 run 目录迁移到 `2026-6-11-02/runs/20260611-191716`，并修正 summary/report 中的路径引用；同时恢复了 `2026-6-11-01` 的 latest summary 入口。

`评测/2026-6-11-02/run_regression_eval.py` 已修正为显式覆盖复用 runner 的全局 `EVAL_DIR` / `PROJECT_ROOT` / `RUN_ROOT`，后续再次运行会写入本目录。

## 6. 密钥落盘检查

评测结束后扫描 `评测/2026-6-11-02`，未发现常见 API key 前缀形态。过程文件未保存 API key。

## 7. 结论与建议

M1 后的 agent-loop 对 M0 三任务评测保持通过，并且真实输出了 M1 的归因报告与 session 文件。建议下一步在修复 M1 代码评审中的 P1 之后，新增一组专门针对 M1 的回归评测：

- `--resume` 真实续跑。
- `--branch` 从历史节点分支。
- 取消/中断后 resume。
- 流式输出 `--stream`。

