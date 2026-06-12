# Python 复刻 Pi Roadmap v1 审批报告

> 日期：2026-06-11
> 被评审文档：`docs/Python复刻Pi-Roadmap-v1.md`
> 评审依据：本仓库前序调研文档、Pi / mini-swe-agent / LiteLLM / Textual / Python 官方资料，以及 CodeAct、DGM、PASTE 等相关论文与项目资料。

## 1. 审批结论

**结论：有条件通过。**

Roadmap 的总体方向成立：它没有把目标设成“再造一个复杂 agent 框架”，而是把前序调研中的核心判断落到一个可运行、可读懂、可自延伸的 Python coding agent 上。North Star 与 Pi、mini-swe-agent 的证据一致：**loop 本身应保持极薄，差异化主要发生在 harness、上下文管线、session、扩展和可观测性上**。

但当前版本仍不建议直接进入实现。原因不是理念错误，而是 **M0 / M1 / M3 / M4 的边界存在几处会导致后续返工的设计冲突**。建议先按本报告的“通过条件”修订 roadmap，再进入 M0 实施 plan。

## 2. 核心判断

1. **方向正确**：Roadmap 对齐了 Pi 的关键经验：四工具、短提示、薄 loop、全事件流、上下文转换管线、tree session、自延伸扩展。
2. **Python 偏离点合理**：使用 LiteLLM、Textual、asyncio，以及将自延伸默认放入子进程/沙箱，都是合理的 Pythonic 取舍。
3. **风险集中在阶段边界**：当前 roadmap 最大问题是一些能力在概念上已锁定，但没有被放入合适阶段，尤其是 asyncio、绿色创新项、扩展 IPC/state 协议。
4. **v1 应坚持“极简但可演进”**：可以做创新，但必须保持独立开关，关掉后能退回 Pi 等价的极简形态。

## 3. 阻塞项与修订建议

### P1. `asyncio-first` 与 M0 的 `subprocess.run` 冲突

Roadmap 在 M0 中写明使用 `subprocess.run` 本地执行，但关键决策点又锁定 `asyncio-first`。这会导致 M1 再迁移 streaming、abort、消息队列、事件流和并发 subprocess，返工风险较高。

**建议：**

- M0 就采用 asyncio core。
- bash 工具使用 `asyncio.create_subprocess_exec` / `asyncio.create_subprocess_shell`。
- CLI 可以保持同步体验，但内部 loop、tool execution、model call 都应 async-first。
- M0 的完成标志仍保持简单：能完成“读文件 -> 改代码 -> 跑测试”的闭环任务。

### P1. v1 范围与 M4 阶段安排冲突

Roadmap 把创新项放到 M4，并强调“最后再做”；但关键决策点又写明 v1 = M0-M3 + 三个绿色创新项。这样会导致 v1 必做能力没有明确阶段归属。

**建议：**

将三个绿色创新项拆入真实阶段：

- **可观测 / 延迟成本归因**：放入 M1，作为事件流的自然产物。
- **可插拔安全 / 沙箱 primitive**：放在 M3 前置，服务自延伸与 code-action。
- **native code-action**：放在 M3 后或新增 `M3.5`，作为可关闭实验能力。

M4 保留给 DGM-lite、程序性记忆、投机/异步执行等更高不确定性的能力。

### P1. 自延伸缺少扩展进程协议与 manifest 设计

Roadmap 提到扩展 API、注册工具、渲染组件、session 状态持久化和子进程/沙箱隔离，但尚未明确扩展如何与 core 通信。这是 M3 成败关键。

**建议：**

在进入 M3 实施前补充最小扩展协议，包括：

- 扩展 manifest：名称、版本、入口、权限需求、工具声明。
- tool schema 注册协议：扩展如何向 core 暴露工具。
- IPC 协议：建议优先 JSONL stdin/stdout，便于观察、调试和沙箱化。
- session state 协议：扩展状态以 custom message 或 dedicated state event 持久化。
- reload 生命周期：load、healthcheck、reload、unload、error。
- 错误与日志回传：必须进入事件流，避免扩展成为新的黑盒。

### P2. LiteLLM provider 抽象的验收标准过强

Roadmap 写到“切换 provider 不丢上下文”，但 provider 之间在 thinking trace、cache usage、tool call streaming、usage/cost 统计上存在天然差异。Pi 自建 pi-ai 也只能对 token/cache/cost 做 best-effort。

**建议：**

将验收标准改为：

- μ 自己的 session 格式完整保存。
- 跨 provider 只承诺 portable subset。
- provider-native blob 可保存并在原 provider 上复用，但不保证迁移。
- cost/token 追踪明确标注为 best-effort，不用于终端用户精确计费。

### P2. native code-action 需要明确与 bash 的边界

CodeAct 和 Pydantic Code Mode 都支持“模型写 Python 执行动作”的方向，但 coding agent 本来就可以写 Python 文件再用 bash 执行。如果 native code-action 没有清晰收益，就可能变成第二套 bash。

**建议：**

native code-action 的验收标准应至少包含：

- 能在一次 model round-trip 中组合多个工具调用。
- 能通过 Python 控制流表达循环、条件、并发。
- 能被 sandbox/permission 层约束。
- 能被事件流和延迟/成本归因系统观测。
- 在真实任务中相比 bash 降低 round count 或 token/tool-call 开销。

### P2. tree session 缺少 branch summary / compaction 落点

Roadmap 已把 tree session 放进 M1，但没有明确 branch summary 或 compaction。Pi 的 tree session 价值不只是能分支存储，而是支持 side-quest 后回溯，并将另一分支发生的事摘要带回主线。

**建议：**

M1 至少加入：

- branch summary 作为 custom message 持久化。
- resume/branch 时可选择是否注入 summary。
- compaction 可先不做完整策略，但应保留 context transform 钩子和 summary message 类型。

## 4. 通过条件

建议满足以下条件后，将 Roadmap v1 批准进入 M0 实施 plan：

1. M0 内部改为 asyncio-first，不再使用同步 `subprocess.run` 作为核心执行路径。
2. 明确 v1 中三个绿色创新项的阶段归属，不再笼统挂到 M4。
3. 在 roadmap 中补充 M3 自延伸的最小扩展协议边界：manifest、IPC、tool schema、session state、reload lifecycle。
4. 调整 LiteLLM provider 抽象的验收标准，承认 portable subset 与 best-effort cost/token tracking。
5. 给 native code-action 增加可验证收益标准，避免与 bash 能力重叠。
6. 给 tree session 增加 branch summary/custom message 的最小落点。

## 5. 建议后的阶段结构

建议将 roadmap 阶段轻微调整为：

```text
M0  Walking Skeleton
    async-first loop + 4 tools + LiteLLM single provider + stdout

M1  Harness Core
    event stream + transform_context/convert_to_llm + tree session
    + latency/cost attribution + portable provider session

M2  Textual Frontend
    TUI as core consumer, headless/RPC 不丢

M3  Self-extension Foundation
    extension manifest + JSONL IPC + subprocess/sandbox execution
    + session state + reload lifecycle

M3.5 Green Innovations
    native code-action + pluggable safety/permission + observability hardening

M4  Research-grade Extensions
    DGM-lite + procedural memory/meta-tool + speculative/async execution
```

## 6. 参考资料

- Pi 官方仓库：<https://github.com/earendil-works/pi>
- Mario Zechner，What I learned building Pi：<https://mariozechner.at/posts/2025-11-30-pi-coding-agent/>
- Armin Ronacher，Pi: The Minimal Agent Within OpenClaw：<https://lucumr.pocoo.org/2026/1/31/pi/>
- mini-swe-agent：<https://github.com/SWE-agent/mini-swe-agent>
- LiteLLM Streaming + Async：<https://docs.litellm.ai/docs/completion/stream>
- LiteLLM Spend Tracking：<https://docs.litellm.ai/docs/proxy/cost_tracking>
- Python asyncio subprocess：<https://docs.python.org/3/library/asyncio-subprocess.html>
- Python importlib reload：<https://docs.python.org/3/library/importlib.html>
- Textual：<https://textual.textualize.io/>
- CodeAct：<https://arxiv.org/abs/2402.01030>
- Darwin Gödel Machine：<https://arxiv.org/abs/2505.22954>
- PASTE / Act While Thinking：<https://arxiv.org/abs/2603.18897>

