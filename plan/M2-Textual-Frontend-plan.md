# 实施 Plan · M2 — Textual 前端

> 阶段来源：`docs/Python复刻Pi-Roadmap-v1.md`（v1.1）§4 M2。
> 经审批后**第 0 步**先落盘到 `plan/M2-Textual-Frontend-plan.md`。虚拟环境一律用 `./.venv`。
> 前序 plan 已归档：`plan/M0-Walking-Skeleton-plan.md`、`plan/M1-Harness-Core-plan.md`。

## Context（为什么做这个）

M0/M1 已完成并提交：薄 async loop + 四工具 + tree session + 事件流 + 归因，headless stdout 已通（41 离线测试）。M2 在**不改 core 逻辑**的前提下，加一个 Textual TUI，作为**事件流的又一个消费者 + 输入驱动**，验证 roadmap 的 transport 抽象——前端只是叶子节点，`Agent`/`Session`/`EventEmitter` 完全不耦合前端。headless/RPC 默认保持不变。

预期结果：`mu --tui` 进入交互式终端界面，可多轮提交任务、实时看 assistant（可流式）/ 工具调用 / 结果 / 归因；TUI 与 headless 共享同一 core（M2 完成标志）。

## 关键设计决策（已锁定 / 已定）

1. **`--tui` 显式开**，headless stdout 仍为默认（roadmap「保留 stdout/RPC 不丢」）。✅ 用户锁定
2. **textual 作为可选 extra `[tui]`**，核心保持瘦（Pi-thin）；headless/库用不拉 textual；`--tui` 缺依赖时友好提示装 `[tui]`。✅ 用户锁定
3. **TUI = 事件订阅者 + 输入驱动**，零改 core（只新增 `mu/tui.py`，`cli.py` 加一个 `--tui` 分支）。
4. **async worker 驱动 agent**：`agent.run` 跑在 Textual 异步 worker（同一事件循环），故事件订阅者可**直接更新 widget**，无需跨线程（不用 `call_from_thread`）。所有 await（model/bash/file IO）都让出控制权，UI 不卡。
5. **可注入 `agent_factory`** 供 Pilot 离线测试（FakeModel，无网络/无 key）。
6. 多轮复用同一 `Session`；运行中取消当前任务 → 走 M1 的 `CancelledError` 安全路径（session 仍可 resume）。

## 架构（复用 M1，前端是叶子）

```
EventEmitter ──┬─ StdoutRenderer        (headless 默认)
               ├─ AttributionCollector  (headless 报告)
               └─ TuiRenderer           (M2 新增消费者：更新 Textual widgets)
Agent / Session / events / model / tools   ← 完全复用，不改逻辑
```

## 文件清单

**新增**：`mu/tui.py`（`MuApp` + `TuiRenderer` + 归因 tally）。
**修改**：
- `mu/cli.py`：加 `--tui`；`--tui` 时预检 Model 配置后启动 app（task 可选）；缺 textual 时友好提示；**headless 路径完全不变**。
- `pyproject.toml`：加 `[project.optional-dependencies] tui = ["textual>=0.80"]`。
- `README.md`：TUI 用法。
- `mu/__init__.py`：**不**导入 tui（懒加载，避免 headless 强依赖 textual）。

**新增测试**：`tests/test_tui.py`（Textual `App.run_test()` + Pilot，FakeModel 离线）。

## `mu/tui.py` 设计（plan 级，不写全码）

- **`MuApp(App)`**
  - 构造：`MuApp(session=None, stream=False, agent_factory=None, initial_task=None)`；`agent_factory(emitter, session, stream) -> Agent` 默认建真 Agent，测试注入 FakeModel 工厂。
  - `compose`：`Header`；`RichLog`(对话区, markup+wrap)；底部 `Static`(流式 live 区)；`Input`(任务输入)；`Footer`(状态 + 归因 tally)。
  - App 持有**一个** `EventEmitter`，挂 `TuiRenderer`（+ tally）一次，跨多轮复用。
  - `on_input_submitted`：非运行中则起 `exclusive` async worker `_run_task(task)`；禁用 Input、清空。
  - `_run_task(task)`：`agent = agent_factory(self.emitter, self.session, self.stream)`；`await agent.run(task)`；完成/取消/异常都在对话区反馈并恢复 Input。
  - bindings：`ctrl+c` 退出；运行中 `esc` 取消当前 worker（→ agent 安全取消）。
  - `on_mount`：若 `initial_task` 则自动提交一次。
- **`TuiRenderer`**（事件订阅者，持 widget 引用，与 `StdoutRenderer` 同构，复用 `render._short`）
  - `RunStarted`→写 user 块 + session id，并**重置** live/tally（支持多轮复用，借鉴 M1 P3a）；
  - `AssistantTextDelta`→累积进 live `Static`（流式实时）；`AssistantText`→写 assistant 块；
  - `ToolCallStarted`→🔧 块；`ToolCallFinished`→📤 结果块；
  - `RunFinished`→flush live + 归因 tally 收尾；`RunAborted`/`ErrorEvent`→提示块。
  - 归因 tally：从 `ModelCallFinished`/`ToolCallFinished`/`TurnStarted` 累计 turns/llm_time/tool_time/tokens → 更新 Footer。

## `cli.py` 改动

- argparse 加 `--tui`（store_true）。
- `main`：若 `ns.tui`：
  - **预检 Model 配置**：构造一次 `Model()`，捕获 `ConfigError` → 打印 + 退出 1（避免进 TUI 首次提交才报错）。
  - 建 `Session`（沿用现有 `--resume/--branch` 逻辑）。
  - 懒 `import mu.tui`；缺 textual → 友好提示 `pip install -e ".[tui]"`，退出 1。
  - `MuApp(session=..., stream=ns.stream, initial_task=task or None).run()`。
  - `--tui` 模式 task 可选（不触发「no task → usage(2)」）。
- 非 `--tui`：现有 headless 路径**一行不改**。

## 复用而非新造

- `Agent.run` / `EventEmitter` / `Session` / `events.*` / `render._short` / headless 的 `StdoutRenderer`·`AttributionCollector` 全部复用。
- 不引入除 `textual` 外的新框架（符合「openai/Textual 之外不叠框架」）。

## 任务顺序（每步保持 `pytest` 绿）

0. **落盘**：本 plan → `plan/M2-Textual-Frontend-plan.md`。
1. **工程化**：`pyproject` 加 `[tui]` extra；`./.venv/bin/python -m pip install -e ".[tui,dev]"`。
2. **tui.py 骨架**：`MuApp` + compose + Input 提交 → exclusive worker → `agent.run`（先用 stdout 占位渲染，跑通输入→运行闭环）。
3. **TuiRenderer**：事件→widget（含流式 live 区）+ 归因 tally 接 Footer。
4. **cli.py**：`--tui` 分支 + 预检 + 缺依赖提示；确认 headless 不变。
5. **tests/test_tui.py**：Pilot 离线——提交任务后断言对话区出现 user/assistant/工具块、共享同一 Session、worker 正常结束（FakeModel）。
6. **README + roadmap §12 回写**。

## 验证

**自动化（必须，无网络/无付费）：**
```
./.venv/bin/python -m pytest -q
```
全绿——新增 Pilot TUI 测试（`App.run_test()` + FakeModel）+ **保持绿的 M0/M1 测试**（headless 回归）。

**手工（需真实 key）：**
```
./.venv/bin/python -m pip install -e ".[tui,dev]"
export MU_BASE_URL=...; export MU_MODEL=...; export MU_API_KEY=...
./.venv/bin/python -m mu --tui            # 交互式：输入任务→看实时对话/工具/归因
./.venv/bin/python -m mu --tui --stream   # 流式实时输出
./.venv/bin/python -m mu "headless 任务"  # 确认 headless 行为不变
```

## M2 明确不做（守 Pi-thin / 留后续）

Web UI / `textual serve`（v2）、复杂主题系统、交互式分支/session 树导航（留后续）、鼠标重交互、语法高亮全家桶、消息队列/steering（非 M1/M2 scope）、并行工具执行。

## 对 Roadmap 的回写（执行后）

在 `docs/Python复刻Pi-Roadmap-v1.md` §12 把 M2 标 ✅，记录两项决策（`--tui` 显式启动、textual 可选 extra `[tui]`）。
