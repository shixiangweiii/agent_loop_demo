# Agent Loop 基础评测方案

> 日期：2026-06-11
> 对象：当前 M0 Walking Skeleton 版本的 `mu` agent-loop
> 目标：运行真实模型与真实工具调用，评估当前 agent-loop 是否能完成最基本的 coding-agent 闭环，而不是只停留在单元测试。

## 1. 评测边界

当前代码属于 M0 阶段，因此评测只覆盖 M0 承诺的能力：

- 薄 async loop：模型返回 tool calls -> 顺序执行工具 -> 回填 tool result -> 再请求模型，直到无 tool call。
- 四工具：`read` / `write` / `edit` / `bash`。
- 线性 append-only OpenAI 格式消息历史。
- 纯 stdout 可观测。
- OpenAI 兼容端点真实调用。

不评测 M1+ 能力：

- tree session、branch summary、上下文 transform、事件流归因、TUI、自延伸、安全/沙箱、native code-action。

## 2. 运行约束

- API key 只通过当前进程环境变量传入，不写入任何文件。
- 所有评测过程文件保存到 `评测/` 目录下。
- 每个任务在 `评测/runs/<timestamp>/<task>/workspace/` 下独立运行。
- runner 对每个任务设置外部超时，避免无 `max_steps` loop 在真实模型下无限运行。
- 评测结束后对工作区执行独立验证命令，不依赖 agent 自述。

## 3. 评测任务

### T1. create_pytest_project

**目的**：验证从空目录开始，agent 能写代码、写测试、运行 pytest，并自行终止。

任务要求：

- 创建 `calc.py`，实现 `add(a, b)` 与 `mul(a, b)`。
- 创建 `test_calc.py`。
- 运行 `pytest -q`。
- 测试通过后给出简短最终回复。

验收：

- `calc.py` 存在。
- `test_calc.py` 存在。
- 外部验证 `pytest -q` 返回 0。

### T2. fix_existing_bug

**目的**：验证 agent 能读取已有文件、运行失败测试、定位并修复 bug。

预置文件：

- `stats_utils.py` 中 `average(nums)` 对长度计算有 bug。
- `test_stats_utils.py` 覆盖平均值与空列表报错。

任务要求：

- 先运行测试观察失败。
- 读取相关文件。
- 修复 bug。
- 再运行测试确认通过。

验收：

- 外部验证 `pytest -q` 返回 0。

### T3. implement_slugify

**目的**：验证 agent 能在已有文件中补函数，并处理多个输入边界。

预置文件：

- `string_utils.py` 中 `slugify(text)` 未实现。
- `test_string_utils.py` 覆盖空格、大小写、标点、多连字符、首尾连字符。

任务要求：

- 运行测试观察失败。
- 实现 `slugify`。
- 运行测试确认通过。

验收：

- 外部验证 `pytest -q` 返回 0。

## 4. 记录指标

每个任务记录：

- agent 进程退出码。
- agent 实际耗时。
- stdout / stderr 原文。
- 外部验证命令退出码。
- 外部验证 stdout / stderr。
- 关键文件是否存在。
- 任务最终判定：pass / fail。

## 5. 结果判定

基础通过标准：

- 至少 2/3 个任务通过。
- 通过任务必须由真实 agent 运行产生，不能手工补改。
- 任一任务若 agent 超时、API 调用失败、验证失败，均记为失败并保留日志。

若 3/3 通过，可认为当前 M0 loop 已具备“基本 coding-agent 闭环能力”，可在修复已知代码评审问题后进入 M1。

