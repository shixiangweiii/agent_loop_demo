# M4.0 完整回归评测方案

> 日期：2026-06-12  
> 目标：验证 M4.0 eval + DGM-lite 基座完成后，既不破坏既有 agent-loop 能力，也能跑通新增 eval/DGM 产物链路。  
> 产物目录：`评测/2026-6-12-01`

## 1. 评测范围

本次回归覆盖三类能力：

1. **离线工程回归**
   - 执行全量 `pytest`。
   - 覆盖 M0-M3.5 既有能力与 M4 新增 `eval` / `dgm` 单测。

2. **真实模型 eval 回归**
   - 通过 `python -m mu.eval` 运行内置 `basic-coding` suite。
   - 任务包括：
     - `create_pytest_project`
     - `fix_existing_bug`
     - `implement_slugify`
   - 每个任务在独立 workspace 中运行 agent，外部 validator 用 pytest 验收。
   - 运行时使用 `MU_BASE_URL` / `MU_MODEL` / `MU_API_KEY` 环境变量；API key 不写入任何过程文件。

3. **M4 DGM-lite archive smoke**
   - 使用一个 prompt-only 候选目录叠加到复制 workspace。
   - 使用离线 fake agent 完成 basic suite，验证：
     - candidate workspace 隔离；
     - allowed path overlay；
     - eval run 写入 archive；
     - `archive.jsonl` 与 `latest-summary.{json,md}` 生成；
     - secret redaction 不泄漏。

## 2. 判定标准

- 全量 pytest 退出码为 0。
- 真实模型 eval 若环境变量齐全，应达到 `3/3 PASS`。
- DGM-lite smoke 应达到 `3/3 PASS`，并产生 archive summary。
- 所有落盘产物不得包含 API key / token / secret。

## 3. 过程产物

- `run_m4_regression_eval.py`：可复跑评测脚本。
- `pytest-output.txt`：全量测试输出。
- `real-eval-output.txt`：真实模型 eval CLI 输出。
- `real-eval-runs/`：`python -m mu.eval` 真实模型 eval 原生产物。
- `dgm-smoke/`：DGM-lite smoke 的候选、archive 与 run 产物。
- `m4-regression-eval-report.md`：最终汇总报告。
- `latest-summary.json` / `latest-summary.md`：本次回归总览。

## 4. 风险与约束

- 真实模型 eval 依赖外部模型服务与 key；若当前环境未设置所需变量，该部分会跳过并在报告中标明。
- Docker 当前不可用时，不跑 docker-gated 测试以外的额外 Docker smoke。
- DGM-lite smoke 使用 fake agent，是为了验证 M4 archive/eval plumbing；真实模型能力由 `python -m mu.eval` basic suite 覆盖。
