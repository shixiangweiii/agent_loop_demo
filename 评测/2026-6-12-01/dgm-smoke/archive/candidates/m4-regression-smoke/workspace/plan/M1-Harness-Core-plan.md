# 实施 Plan · M1 — Pi 的 harness 三件武器 + 可观测底座

> 阶段来源：`docs/Python复刻Pi-Roadmap-v1.md`（v1.1）§4 M1。
> 经审批后**第 0 步**先落盘到 `plan/M1-Harness-Core-plan.md`（plan 模式仅能编辑指定计划文件）。
> 虚拟环境：一律用 `./.venv`。前序 M0 plan 已归档于 `plan/M0-Walking-Skeleton-plan.md`。

## Context（为什么做这个）

M0 已完成并提交：薄 async loop + 四工具 + OpenAI 兼容 model + **内存线性历史** + 单点 `_emit` 打印 seam。M1 把 μ 从「能跑」抬到「有 Pi 骨架质感」，并落地 v1 必做的 🟢 可观测创新。共 5 件事（roadmap §4 M1）：① 事件流；② 上下文管线（transform_context/convert_to_llm）；③ tree session + branch summary；④ provider 打磨（流式/abort/terminate）；⑤ 延迟-成本归因底座。

**硬约束**：M1 是一次较大重构，必须保持 M0 行为回归测试绿——尤其 `test_loop_read_edit_bash_closed_loop`（它正是为保护 walking skeleton 行为而加的）。手段：`agent.messages` 保留为「session 当前分支路径」的只读 property，`convert_to_llm` 对标准消息透传，使行为不变、仅结构断言按新 API 微调。

预期结果：能从任意节点分支跑 side-quest 再回溯、把侧分支摘要带回主线；能精确裁剪/注入进入模型的上下文；每个任务结束有「轮数/LLM 时延/工具时延/token」归因报告；session 以 JSONL 持久化、可 `--resume`。

## 关键设计决策（已锁定 / 已定）

1. **全程 asyncio**（沿用 M0）。
2. **Session 存 `./.mu/sessions/<id>.jsonl`**（工作目录本地；`MU_SESSION_DIR` 可覆盖；加进 .gitignore）。✅ 用户锁定
3. **默认非流式，`--stream` 手动开**；流式代码实现但不做默认。✅ 用户锁定
4. **向后兼容护栏**：`agent.messages` = `session.path_to_head()` 的 property；标准消息 `convert_to_llm` 透传 → M0 闭环回归测试行为不变。
5. **Cost 归因 best-effort**：M1 报 tokens + 时延 + 计数；$ 价格表可选（默认不算，标注 best-effort、不用于精确计费），与 roadmap M1 Provider 验收一致。
6. **terminate 机制做最小 plumbing**：`ToolResult(content, terminate)`；四个内置工具永不 terminate（行为不变），但 loop 早停逻辑与 seam 就位，供 M3 扩展用。
7. **保持 Pi-thin**：不提前造 M2（TUI）、M3（扩展注册/自延伸）、M3.5（code-action/安全层）。分支/resume 在 M1 只做 CLI flag + 程序化 API + 测试覆盖；交互式分支导航留 M2。

## 架构演进（M0 → M1）

```
M0:  Agent(list[dict]) ──acomplete──> Model        _emit(kind,text)->print
M1:  Agent(Session tree)
       │  llm_msgs = convert_to_llm(transform_context(session.path_to_head()))   # context.py
       │  ModelResult = await model.acomplete(llm_msgs, tools[, emit])           # model.py(改): usage+latency, 可选 stream
       │  ToolResult  = await tools.execute(name, args)                          # tools.py(改): terminate
       │  emit(Event(...)) ──┬─> StdoutRenderer      # render.py
       │                     ├─> AttributionCollector # observability.py（⑤）
       │                     └─> (M2: TUI)
       └─ Session 持久化 JSONL（id/parent_id）+ branch/resume + branch_summary    # session.py
```

## 文件清单

**新增**：`mu/events.py`（Event 类型 + EventEmitter 订阅分发）、`mu/session.py`（tree session + JSONL + branch/resume/summary）、`mu/context.py`（transform_context / convert_to_llm）、`mu/observability.py`（AttributionCollector 订阅者 + 报告）、`mu/render.py`（StdoutRenderer 订阅者，替代 `_default_emit`）。

**修改**：
- `mu/model.py`：`acomplete` 返回 `ModelResult(message, usage, latency_s)`；新增可选 `stream`/`emit`（流式累积 content 与 tool_call delta，emit `AssistantTextDelta`）；保留单 provider/env 配置。
- `mu/tools.py`：新增 `ToolResult(content: str, terminate: bool=False)`；`ToolRegistry.execute` 返回 `ToolResult`（内置工具返回 str 时包成 `terminate=False`，复用现有四工具实现不动）。
- `mu/agent.py`：用 `Session` 取代 `self.messages` list（保留 `messages` property）；`_emit` 改为 `emit(Event)` 走 EventEmitter；插入 context 管线；terminate 早停；asyncio 取消处理；复用现有 `_message_to_dict`。
- `mu/cli.py`：装配订阅者（StdoutRenderer + AttributionCollector）；新增 `--resume <id>` / `--branch <node>` / `--stream`；读 `MU_SESSION_DIR`；打印 session id；干净处理 Ctrl-C → 取消并落盘。
- `mu/__init__.py`：导出新公共类型。
- `.gitignore`：加 `.mu/`。

**测试新增/更新**（全部无网络）：`test_events.py`、`test_session.py`、`test_context.py`、`test_observability.py`、`test_streaming.py`（fake async 流）、`test_terminate.py`；更新 `test_agent_loop.py`（FakeModel 改返回 `ModelResult`，行为断言不变）。

## 执行步骤（依赖序，每步保持 `pytest` 绿）

0. **落盘**：本 plan → `plan/M1-Harness-Core-plan.md`。
1. **事件流 + StdoutRenderer**：定义 `Event` 类型与 `EventEmitter(subscribe/emit)`；`render.py` 复刻 M0 的 stdout 观感；`agent.py` 把 4 处 `_emit(kind,text)` 换成结构化事件；`cli.py` 订阅 renderer。DoD：事件按 `RunStarted→(TurnStarted→ModelCall*→ToolCall*→TurnFinished)*→RunFinished` 顺序发出；stdout 观感与 M0 一致。
2. **tree session（JSONL）**：`Session`（node=id/parent_id/ts/msg；`append`/`path_to_head`/`branch_from`/`load`）；`agent` 用 session 持久化每条消息，`agent.messages` 变 property。DoD：session round-trip（写盘→load→path 一致）；`./.mu/sessions/<id>.jsonl` 生成。
3. **上下文管线**：`context.py` 的 `transform_context`（默认 identity）+ `convert_to_llm`（标准消息透传、未知 type 过滤/转换）；agent 在调用 model 前过一遍。DoD：标准消息透传使 M0 闭环回归测试不变；提供裁剪/注入的单测。
4. **branch summary**：custom 消息 `{type:"branch_summary", content}`；`Session` 支持在主线 head 追加它；`convert_to_llm` 把它渲染成注入上下文；`Agent` 提供 `summarize_branch(node_id)`（取分支路径文本→可选调 model 概括→切回主线→追加 summary）。DoD：side-quest 分支→回溯主线→summary 注入，端到端测试通过。
5. **terminate + model usage**：`ToolResult(content, terminate)`；loop「本轮工具结果全部 terminate 才早停」；`Model.acomplete` 返回 `ModelResult`（含 usage、latency）；`FakeModel` 同步更新。DoD：fake terminate 工具触发早停的测试；usage 透出。
6. **流式（opt-in）+ asyncio abort**：`Model` 支持 `stream=True`（累积 content 与 tool_call arguments delta，emit `AssistantTextDelta`，末块取 usage via `stream_options`）；`Agent.run` 可被取消（`CancelledError` → 落盘当前 session、emit `RunAborted`、关闭流）。DoD：fake async 流累积出正确 message 的测试；取消路径测试（不损坏 session）。
7. **可观测/归因底座**：`AttributionCollector` 订阅事件，累计 turns / LLM wall-clock（总+每次）/ tool wall-clock（总+每工具）/ tokens（prompt/completion/total）；`RunFinished` 时打印报告；$ 成本可选（默认不算，标注 best-effort）。DoD：用合成事件喂 collector，断言汇总数值；真实运行末尾出报告。
8. **CLI 接线 + 文档 + 回归更新**：`--resume/--branch/--stream/MU_SESSION_DIR`，启动打印 session id；更新 README（resume/stream/归因报告/session 目录）；更新 `test_agent_loop.py` 结构断言；`.gitignore` 加 `.mu/`。DoD：全量 `pytest` 绿；手工 e2e 通过。

## 复用而非新造

- 复用 `mu/agent.py:_message_to_dict`（openai/fake message → dict）。
- 复用 `mu/environment.py:LocalEnvironment.run_bash`（已修进程组清理）与四工具实现。
- 复用 `mu/model.py` 的 env 配置与 `AsyncOpenAI` 客户端；流式仅加 `stream=True` 分支，不另起 SDK。
- 事件分发用普通同步回调列表（订阅者只做打印/累加），不引入 pub/sub 框架——符合「openai/Textual 之外不叠框架」。

## 验证

**自动化（必须，无网络/无付费）：**
```
./.venv/bin/python -m pytest -q
```
覆盖：事件顺序、session 树 round-trip 与 branch/resume、context 透传+注入、归因汇总（合成事件）、流式累积（fake 流）、terminate 早停、以及**保持绿的 M0 闭环回归测试**。

**手工 e2e（需真实 key）：**
```
export MU_BASE_URL=...; export MU_MODEL=...; export MU_API_KEY=...
mkdir -p /tmp/mu_scratch && cd /tmp/mu_scratch
PY=/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/.venv/bin/python
$PY -m mu "在 calc.py 写 add(a,b) 并用 pytest 测试，然后运行 pytest"
# 断言：① 末尾出归因报告（turns/时延/token）；② ./.mu/sessions/<id>.jsonl 生成
$PY -m mu --resume <上一步打印的 id> "再加一个 sub(a,b) 并补测试"   # 续跑
$PY -m mu --stream "解释这个仓库结构"                                 # 流式实时输出
```

## M1 明确不做（守 Pi 哲学，留后续阶段）

TUI / `textual serve`（M2）；自延伸扩展注册与热重载（M3）；native code-action（M3.5）；可插拔安全/权限/沙箱 provider（M3.5）；完整 compaction 策略（仅留 summary 类型 + transform 钩子）；交互式分支导航（M2）；$ 精确计费；并行工具执行；多 provider 切换。

## 对 Roadmap 的回写（执行后）

执行完在 `docs/Python复刻Pi-Roadmap-v1.md` §12 实施进展把 M1 标记为 ✅，并记录两项 M1 决策（session=`./.mu/sessions/`、流式默认 off）。
