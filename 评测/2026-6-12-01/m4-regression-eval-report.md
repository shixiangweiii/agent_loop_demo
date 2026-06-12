# M4.0 完整回归评测报告

> 时间：2026-06-12 11:24  
> 目录：`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-01`

## 1. 总体结论

**结论：M4.0 离线基座与 DGM-lite smoke 通过；真实模型 basic eval 的任务产物最终 3/3 可验证通过，但原始 full run 暴露出一个 eval runner / validator 稳定性问题。**

本次回归执行了：

1. 全量离线测试：`83 passed, 1 skipped`。
2. 真实模型 `python -m mu.eval` basic suite：原始 full run 记录为 `2/3`，其中 `create_pytest_project` 的 agent 产物正确，但 validator 进程返回 `-9`。
3. 对 `create_pytest_project` 做绝对路径 targeted rerun：`1/1 PASS`。
4. DGM-lite archive smoke：`3/3 PASS`，archive 正常生成。
5. secret scan：未发现真实 API key 落盘；唯一 `sk-...` 命中来自 DGM 复制仓库中的测试假 key fixture，已记录为 ignored。

## 2. 结果总览

| 项目 | 结果 | 产物 |
|---|---:|---|
| 全量 pytest | PASS | `pytest-output.txt` |
| 真实模型 basic suite 原始 full run | WARN | `real-eval-runs/20260612-111202/summary.md`，原始汇总 `2/3` |
| `create_pytest_project` 手动 revalidation | PASS | `manual-revalidation-create_pytest_project.txt` |
| `create_pytest_project` 绝对路径 targeted rerun | PASS | `real-eval-rerun-abs/20260612-112207/summary.md` |
| DGM-lite archive smoke | PASS | `dgm-smoke/archive/latest-summary.md` |
| secret scan | PASS | `secret-scan-output.json` |

## 3. 真实模型 eval 详情

模型配置：

- Model：`qwen3.7-plus`
- Base URL：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- API Key：仅运行时环境变量使用，未写入报告

原始 full run：

| 任务 | 原始结果 | 说明 |
|---|---:|---|
| `create_pytest_project` | FAIL/WARN | agent 写出 `calc.py` / `test_calc.py`，任务内 `pytest -q` 显示 `2 passed`；外部 validator 返回 `-9` |
| `fix_existing_bug` | PASS | validator `3 passed` |
| `implement_slugify` | PASS | validator `4 passed` |

对 `create_pytest_project` 的后续复核：

- 在原始 workspace 用项目 venv 的绝对 Python 手动跑 pytest：`2 passed in 0.01s`。
- 用绝对 `--run-root` targeted rerun：`1/1 PASS`。

因此，按最终任务产物与绝对路径 rerun 计，真实模型 basic coding 能力为 **3/3 可验证通过**；但原始 `mu.eval` full run 暴露出 validator 稳定性问题，需要作为 M4 后续修复项保留。

## 4. 发现的问题

### P1. `mu.eval` 的 validator 对 run_root / workspace 路径语义仍不够稳

原始 full run 的 `create_pytest_project` 中，agent 已经完成任务并在任务内跑出：

```text
2 passed in 0.01s
```

但 `validation.txt` 记录：

```text
[exit code] -9
```

后续手动 revalidation 在同一 workspace 通过，说明任务产物本身没有失败。

另外，在一次 targeted rerun 中，如果 `--run-root` 使用相对路径，prompt 中的 workspace 也是相对路径；模型按相对路径写文件时，会在当前 workspace 下再嵌套一层 `评测/.../workspace/...`，导致 validator 找不到顶层目标文件并最终 timeout。绝对路径 rerun 通过。

建议：

1. `run_eval_suite()` 内部把 `run_root`、`run_dir`、`workspace` 全部 `resolve()` 成绝对路径。
2. `default_agent_cmd_builder()` 传给模型的 prompt 必须使用绝对 workspace。
3. `run_pytest()` 调用 pytest 时显式指定测试文件，避免 pytest 向上寻找父级 `pyproject.toml`：

```text
python -m pytest -q test_calc.py
python -m pytest -q test_stats_utils.py
python -m pytest -q test_string_utils.py
```

或设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`--rootdir {workspace}`。

### P2. secret scan 需要区分过程产物与复制源码中的测试假 key

初始 scanner 按 `sk-...` 正则扫描整个评测目录，命中了：

```text
dgm-smoke/archive/candidates/m4-regression-smoke/workspace/tests/test_eval.py
```

该文件是 DGM candidate workspace 复制的仓库测试文件，里面包含测试 redaction 的假 key：

```text
sk-test-secret-not-for-disk
```

它不是本次真实 API key 泄漏。修正后的扫描规则忽略复制源码中的测试 fixture，过程输出与报告未发现真实 key 模式。

## 5. DGM-lite smoke

DGM smoke 使用 prompt-only candidate：

```text
.mu/prompts/smoke.md
```

结果：

- candidate id：`m4-regression-smoke`
- changed paths：`.mu/prompts/smoke.md`
- score：`1.0`
- eval：`3/3 PASS`
- archive：
  - `dgm-smoke/archive/archive.jsonl`
  - `dgm-smoke/archive/latest-summary.json`
  - `dgm-smoke/archive/latest-summary.md`

该 smoke 验证了 M4.0 的候选隔离、allowed path overlay、eval run 与 archive summary 链路。

## 6. 产物索引

- 评测方案：`m4-regression-eval-plan.md`
- 可复跑脚本：`run_m4_regression_eval.py`
- 全量测试输出：`pytest-output.txt`
- 原始真实模型 eval 输出：`real-eval-output.txt`
- 原始真实模型 eval run：`real-eval-runs/20260612-111202/`
- 相对 run-root 失败样本：`real-eval-rerun/20260612-111903/`
- 绝对 run-root targeted rerun：`real-eval-rerun-abs/20260612-112207/`
- 手动 revalidation：`manual-revalidation-create_pytest_project.txt`
- DGM smoke archive：`dgm-smoke/archive/`
- secret scan：`secret-scan-output.json`
- 汇总：`latest-summary.json`、`latest-summary.md`

## 7. 建议后续动作

1. 修复 `mu.eval` 的路径绝对化和 pytest rootdir/test file 指定。
2. 将 secret scan 工具化，默认忽略复制源码里的测试假 key fixture，仅扫描运行输出、summary、archive jsonl/md。
3. 修复后再跑一次 full `python -m mu.eval`，目标是原始 full run 直接 `3/3 PASS`，不依赖 targeted rerun 兜底。
