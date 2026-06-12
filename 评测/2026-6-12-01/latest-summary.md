# M4.0 完整回归评测报告

- 时间：2026-06-12 11:24
- 目录：`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-01`
- 结论：离线基座与 DGM-lite smoke 通过；真实模型 basic eval 的任务产物最终 3/3 可验证通过，但原始 full run 暴露 `mu.eval` validator 稳定性问题。

| 项目 | 结果 | 备注 |
|---|---:|---|
| 全量 pytest | PASS | `83 passed, 1 skipped` |
| 原始真实模型 basic suite | WARN | 原始 `2/3`；`create_pytest_project` validator `-9` |
| `create_pytest_project` 手动 revalidation | PASS | 同一 workspace `2 passed` |
| `create_pytest_project` 绝对路径 rerun | PASS | `1/1 PASS` |
| DGM-lite archive smoke | PASS | `3/3 PASS` |
| secret scan | PASS | 未发现真实 key；忽略复制源码中的测试假 key fixture |

详细报告见 `m4-regression-eval-report.md`。
