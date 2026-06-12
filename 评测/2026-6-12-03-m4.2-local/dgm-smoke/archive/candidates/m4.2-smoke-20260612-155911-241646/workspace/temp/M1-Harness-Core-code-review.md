# M1 Harness Core 代码评审报告

> 日期：2026-06-11
> 评审对象：M1 实施后的 `mu/`、`tests/`、`README.md`、`pyproject.toml`
> 依据文档：`plan/M1-Harness-Core-plan.md`、`docs/Python复刻Pi-Roadmap-v1.md`
> 评审方式：源码阅读 + 全量测试 + 针对取消 / session / branch summary 边界的离线复现

## 1. 评审结论

**结论：M1 主干实现质量不错，但建议修复 1 个 P1 问题后再进入下一阶段。**

当前实现已经把 M0 的内存线性 loop 升级为具备 M1 质感的 harness core：

- 事件流：`EventEmitter` + `StdoutRenderer` + `AttributionCollector`
- tree session：JSONL 持久化、`id/parent_id`、branch/resume 基础能力
- 上下文管线：`transform_context` / `convert_to_llm`
- provider 打磨：`ModelResult`、usage/latency、可选 streaming
- terminate seam：`ToolResult(content, terminate)`
- 可观测底座：轮数、LLM/tool 时延、tokens、工具明细

自动化测试通过：`38 passed in 2.20s`。

主要风险集中在 session 完整性：当 agent 在工具执行中被取消时，会持久化一个缺少 tool result 的 assistant tool-call 消息，之后 resume 很可能被 OpenAI 兼容接口拒绝。这个问题会直接影响 M1 的 `--resume` 可靠性，应优先修复。

## 2. Findings

### P1. 取消发生在工具执行中会留下不可续跑的 session

- 位置：`mu/agent.py:93`、`mu/agent.py:129`、`mu/agent.py:110`
- 相关能力：M1 `asyncio abort`、JSONL session、`--resume`

`Agent.run()` 会先将 assistant message 持久化到 session：

```python
assistant_msg = _message_to_dict(result.message)
self.session.append(assistant_msg)
```

如果该 assistant message 包含 `tool_calls`，随后进入 `_run_tool_calls()` 执行工具。若此时发生 `asyncio.CancelledError`，当前实现只 emit `RunAborted("cancelled")`，不会为已经持久化的 tool call 追加对应的 `tool` message。

离线复现结果显示，取消后 JSONL 路径形如：

```json
[
  {"role": "system", "...": "..."},
  {"role": "user", "content": "trigger slow tool"},
  {
    "role": "assistant",
    "content": null,
    "tool_calls": [
      {
        "id": "call_1",
        "type": "function",
        "function": {"name": "slow", "arguments": "{}"}
      }
    ]
  }
]
```

也就是说，assistant 的 `tool_calls` 后面没有对应的 `tool` result。后续 `--resume` 会把这段历史透传给模型；OpenAI 兼容接口通常要求每个 assistant tool call 后都有匹配的 tool response，因此很可能直接拒绝请求。

**影响：**

- M1 的 abort/resume 组合不可靠。
- session JSONL 可能进入“协议上不完整”的状态。
- 后续 context transform、branch summary、归因都建立在 session path 上，会继承这个坏状态。

**建议：**

至少选择一种策略：

1. 工具执行取消时，为尚未完成的每个 tool call 追加错误型 tool message，例如：
   `{"role": "tool", "tool_call_id": "...", "content": "Error: tool execution cancelled"}`
2. 在 `convert_to_llm()` 中检测 dangling assistant tool_calls，并过滤或补齐对应 tool message。
3. 在 `Session.load()` 或 resume 前做 session repair，确保当前 path 满足 OpenAI tool-call 协议。

推荐第 1 种作为主修复：取消发生时就把 session 修成完整历史，最容易解释，也最符合 append-only 的精神。

### P2. Plan 要求的 `Agent.summarize_branch(node_id)` 没实现

- 位置：`plan/M1-Harness-Core-plan.md:59`
- 当前相关实现：`mu/session.py:56`、`mu/context.py:26`

M1 plan 明确要求：

- `Agent` 提供 `summarize_branch(node_id)`
- 取分支路径文本
- 可选调 model 概括
- 切回主线
- 追加 `branch_summary`
- 端到端测试覆盖 side-quest 分支、回溯主线、summary 注入

当前代码只有低层能力：

- `Session.add_branch_summary(content)`
- `convert_to_llm()` 将 `{type: "branch_summary"}` 转成 user 上下文注入

但没有 Agent 级 API，也没有 CLI 或端到端测试能证明“侧分支摘要带回主线”的完整工作流成立。

**影响：**

- Roadmap/README 中“tree session + branch summary”容易被理解为完整可用，但当前更像是预留底座。
- M1 完成标志“把侧分支摘要带回主线”尚未完全达成。

**建议：**

- 补 `Agent.summarize_branch(node_id, target_head=None, use_model=False)` 或类似 API。
- 最小实现可先不调模型，只把分支 path 渲染成 deterministic summary。
- 加端到端测试：主线 -> 从某节点分支 -> 侧分支追加消息 -> 回主线 append summary -> `convert_to_llm` 注入 summary。
- README 中若暂不提供 CLI 操作，应说明 branch summary 目前是程序化 API，不是完整交互式能力。

### P3. `AttributionCollector` 复用时会跨 run 累计

- 位置：`mu/observability.py:30`-`58`

`AttributionCollector` 在初始化时设置计数器，但收到 `RunStarted` 时只更新 `_wall_start`，不会重置 turns、model_calls、tool_counts、tokens 等累计值。

CLI 每次运行都会创建新 collector，因此暂时不暴露；但 M1 的定位是 harness 订阅者，后续被 embed 或测试复用时，同一个 collector 处理多个 run 会把指标混在一起。

**建议：**

- 在 `RunStarted` 时重置所有 run-level 计数。
- 或明确将 collector 设计为 single-run 对象，并在文档 / 类型注释中说明不能复用。

从 M1 可观测底座的长期演进看，更建议支持复用并在 `RunStarted` 重置。

### P3. 包版本元数据仍停留在 M0

- 位置：`mu/__init__.py:23`
- 位置：`pyproject.toml:7`、`pyproject.toml:8`

`mu.__version__` 已是 `0.1.0`，但 `pyproject.toml` 仍是：

```toml
version = "0.0.1"
description = "μ (mu) — a minimal, Pi-style async coding agent (M0 walking skeleton)"
```

**影响：**

- 安装包元数据、CLI 诊断和文档状态不一致。
- 后续如果发布或 editable install 后检查版本，会产生混淆。

**建议：**

- 将 `pyproject.toml` 版本同步为 `0.1.0`。
- description 更新为 M1 harness core。

## 3. 正向观察

1. **M1 没有过度膨胀**：事件、session、context、render、observability 都保持小文件、小接口，符合 Pi-thin 方向。
2. **M0 行为保护较好**：`test_loop_read_edit_bash_closed_loop` 覆盖了 `read -> edit -> bash` 多轮闭环，能保护 walking skeleton。
3. **事件流设计足够简单**：同步订阅者列表即可满足 M1，避免提前引入 pub/sub 框架。
4. **Context 管线留得干净**：标准消息透传，自定义类型转换/过滤，后续 compaction 和记忆注入有明确落点。
5. **bash timeout 修复已吸收 M0 评审意见**：`start_new_session=True` + `killpg` 是正确方向。
6. **README 对 `.env` 行为已修正**：明确说明不会自动加载 `.env`，需要 export 或 source。

## 4. 验证结果

自动化测试：

```text
38 passed in 2.20s
```

额外离线复现：

- 测试 `branch_summary` 注入路径：低层 `Session.add_branch_summary()` + `convert_to_llm()` 可用。
- 测试 `branch_from()` 当前 head 选择：仅内存改变，不单独持久化；后续 append 后才落盘。
- 测试取消发生在模型调用中：session 保留 system/user，状态可解释。
- 测试取消发生在工具执行中：复现 P1 dangling tool call 问题。

## 5. 建议处理顺序

1. **先修 P1**：工具执行取消时补齐 tool result，保证 session 可 resume。
2. **再补 P2**：补 Agent 级 branch summary API 或下调 README/roadmap 的“已完成”表述。
3. **顺手修 P3**：collector 在 `RunStarted` 重置；同步 `pyproject` 版本与描述。
4. 修复后重新跑 `pytest`，并额外增加两个测试：
   - cancel during tool execution 后 session path 没有 dangling tool call。
   - branch side-quest summary 被带回主线并进入 `convert_to_llm()`。

## 6. 进入 M2 前的建议门禁

建议满足以下条件后再进入 M2：

- `--resume` 不会因 dangling tool call session 失败。
- branch summary 至少具备程序化端到端能力，并有测试保护。
- 归因 collector 对多 run 复用行为明确且测试覆盖。
- 包版本元数据与 M1 状态一致。

