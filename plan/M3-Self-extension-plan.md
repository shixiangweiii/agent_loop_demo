# 实施 Plan · M3 — 自延伸基座（Self-extension Foundation）

> 阶段来源：`docs/Python复刻Pi-Roadmap-v1.md`（v1.1）§4 M3（含「进入门禁」最小扩展协议）。
> 经审批后**第 0 步**先落盘到 `plan/M3-Self-extension-plan.md`。虚拟环境一律用 `./.venv`。
> 前序 plan 已归档：M0/M1/M2。

## Context（为什么做这个）

M0–M2 完成：薄 async loop + 四工具 + tree session + 事件流 + 归因 + Textual TUI（44 离线测试）。M3 复刻 Pi 最独特的能力——**agent 自己写工具扩展、加载、使用**（「应用层自进化脚手架」的落点）。按 roadmap 锁定：扩展默认**子进程隔离**（in-process reload 延后），通过 **JSONL stdin/stdout** 与 core 通信，注册工具进 `ToolRegistry`，状态持久化进 session、`--resume` 可恢复，错误/日志回流事件流（不让扩展变黑盒）。

**前置收尾（M2 遗留）**：补 M2 的 README（TUI 用法）与 roadmap §12（M2 ✅）回写——并入下面任务 0。

预期结果：agent 现场写一个 Python 工具扩展 → `load_extension` 加载 → 新工具立即可用 → 把配置存 session、`--resume` 仍在；扩展的日志/错误在事件流可见。

## 关键设计决策（已锁定 / 已定）

1. **执行隔离 = 子进程**（每个扩展一个长驻 `python ext.py` 子进程）；in-process importlib reload **延后**（仅文档提及，不实现）。
2. **加载方式 = 显式 `load_extension` 工具 + 启动自动加载 `./.mu/extensions/`**。✅ 用户锁定
3. **系统提示加一句自延伸提示**（仍 <1000 token）。✅ 用户锁定
4. **隔离 ≠ 安全沙箱**：M3 子进程只做崩溃隔离；扩展以 agent 同等权限运行（YOLO）。可插拔安全/权限/沙箱 = M3.5。**显著标注**。
5. **扩展状态**以 `{"type":"ext_state",...}` 自定义消息存 session；`convert_to_llm` 已丢弃未知类型（M1）→ 不污染 LLM 上下文；load/resume 时回放最新 state。
6. 保持 Pi-thin：不做扩展市场/分发、复杂依赖管理、Windows、in-process reload、安全沙箱。

## 最小扩展协议（M3 进入门禁 ⓐ–ⓕ）

- **ⓐ manifest**：扩展子进程**启动即在 stdout 首行**输出 `{"type":"manifest","name","version","tools":[schema...],"permissions":[...]}`（权限声明 M3 为 advisory）。
- **ⓑ tool schema 注册**：core 读 manifest → 对每个 tool 调 `ToolRegistry.register(name, schema, handler)`；handler 路由到该扩展子进程。
- **ⓒ IPC（JSONL，每行一个对象）**：
  - core→ext：`{"type":"init","state":{...}}`、`{"type":"execute","id","tool","args"}`、`{"type":"shutdown"}`
  - ext→core：`{"type":"manifest",...}`、`{"type":"result","id","content","terminate"}`、`{"type":"error","id","message"}`、`{"type":"log","level","message"}`、`{"type":"state","state":{...}}`
- **ⓓ session state**：ext 发 `state` → core 持久化为 `ext_state` 消息（latest-wins）；load 时回放进 `init`。
- **ⓔ reload 生命周期**：load(spawn→读 manifest→注册→init+state)→reload(unload+load)→unload(shutdown→必要时进程组 kill→注销工具)→error(子进程崩溃→emit ExtensionError→注销/降级)。
- **ⓕ 错误/日志回流事件流**：ext 的 `log`/`error` 与生命周期 → 新事件 `ExtensionLoaded/Unloaded/Log/Error`，被 renderer/归因消费（反黑盒）。

## 架构（复用 M1/M2，扩展是 ToolRegistry 的动态来源）

```
Agent ── owns ── ExtensionManager(registry, session, emitter)
                   ├─ load(path): spawn python ext.py（子进程, start_new_session）
                   │     stdout 首行=manifest → registry.register(tool→handler)
                   │     后台 reader task: result/error→resolve future; log→emit; state→persist session
                   ├─ call(ext, tool, args): 发 execute → await 结果 → ToolResult
                   ├─ autoload(./.mu/extensions/) at run start
                   └─ aclose(): unload 全部（杀子进程，进程组）
ToolRegistry: 内置 4 工具 + register/unregister 动态扩展工具 + 3 个管理工具
```

## 文件清单

**新增**：
- `mu/extension.py`：`ExtensionManager` + `Extension` + 子进程 IPC（asyncio streams、按 id 关联 future、reader task）。
- `mu/extsdk.py`：扩展作者（含 agent）import 的 SDK——`@tool(name, description, parameters)` 装饰器 + `run_extension(name, version)`（首行发 manifest、JSONL 请求循环、`get_state/set_state/log` 助手）。
- `extensions/README.md`：怎么写扩展（SDK + 协议 + 例子）；以「CDP 截网页」为说明性范例（仅文档，不进测试）。
- `extensions/example_textstats.py`：可确定性验证的示例扩展（如 `word_count`/`reverse` 两个工具），供测试 + 给 agent 自参考。

**修改**：
- `mu/tools.py`：handler 签名统一为 `Callable[[dict], Awaitable[ToolResult|str]]`（内置工具用 `partial` 绑定 env）；新增 `register(name, schema, handler)`（拒绝与现有工具重名）、`unregister(name)`（仅扩展工具）。
- `mu/agent.py`：可选持有 `ExtensionManager`（`extensions: bool = True`）；`run()` 开头一次性 `await manager.autoload()`；新增 `aclose()` 清理子进程；管理工具由 manager 注册进 registry。
- `mu/events.py`：加 `ExtensionLoaded/ExtensionUnloaded/ExtensionLog/ExtensionError`。
- `mu/render.py`：渲染扩展事件（🧩 行）。
- `mu/prompts.py`：加 1–2 句自延伸提示（指向 `load_extension` 与 `extensions/README.md`）。
- `mu/cli.py` / `mu/tui.py`：运行结束 `finally` 调 `agent.aclose()`（杀扩展子进程）。
- `mu/__init__.py`：导出 `ExtensionManager`。

**新增测试**：`tests/test_extension.py`（全部离线：扩展子进程是本地 python，无 LLM/网络）。

## 管理工具（注册进 ToolRegistry，让 agent 能自延伸）

- `load_extension(path)`：加载一个 .py 扩展（绝对/相对路径）。
- `reload_extension(name)`：改完重载（支撑 写→载→测→迭代）。
- `list_extensions()`：列已加载扩展及其工具。
- （`unload` 作为 manager 方法用于 autoload/cleanup，不一定暴露为工具。）

## 任务顺序（每步保持 `pytest` 绿）

0. **落盘 + M2 收尾**：本 plan → `plan/M3-Self-extension-plan.md`；补 M2 的 README（TUI）与 roadmap §12（M2 ✅）。
1. **ToolRegistry 改造**：handler 统一签名 + `register/unregister`（+ 重名拒绝），保持现有 4 工具与测试不变。
2. **extsdk.py**：`@tool` + `run_extension`（manifest 首行、execute 循环、state/log 助手）。
3. **example_textstats.py + extensions/README.md**：可测扩展 + 自参考文档。
4. **extension.py**：`ExtensionManager`（load/call/reload/unload/autoload/aclose + reader task + state 持久化/回放 + 事件）。
5. **events.py + render.py**：扩展事件 + 渲染。
6. **agent.py 接线**：owns manager、autoload、aclose、管理工具注册；`prompts.py` 加提示。
7. **cli.py / tui.py**：finally 调 `aclose`。
8. **tests/test_extension.py**：见验证。
9. **README + roadmap §12（M3）回写**。

## 复用而非新造

- 子进程 + 进程组清理复用 `environment.py:run_bash` 的 `start_new_session`/`killpg` 模式。
- session 自定义消息复用 M1 的 `append` + `convert_to_llm` 丢弃未知类型（ext_state 不污染 LLM）。
- 事件/渲染复用 M1 的 `EventEmitter`/`StdoutRenderer` 与 M2 的 `TuiRenderer`（加分支即可）。
- ToolResult（terminate）复用——扩展可返回 terminate。
- 不引入除 openai/textual 外的新框架（协议手写 JSONL）。

## 验证

**自动化（必须，无网络/无付费/无真实 LLM）：**
```
./.venv/bin/python -m pytest -q
```
覆盖（`test_extension.py`）：
- `ToolRegistry.register/unregister` + 重名拒绝。
- 加载 `example_textstats.py` → `registry.execute("word_count", {...})` 得正确结果。
- `reload_extension` 重载后仍可用。
- ext 写 state → 持久化 → **新 manager 从同一 session 加载时 state 恢复**（resume 语义）。
- 扩展工具抛错 → 返回 error 型 ToolResult + emit `ExtensionError`（不崩主流程）。
- `aclose()` 杀子进程（无残留）。
- **agent 级自延伸闭环（FakeModel 离线）**：脚本化 `load_extension(example)` → 调用新工具 → 断言结果（证明完成标志，无需真实 LLM）。
- 保持绿的 M0/M1/M2 测试。

**手工 e2e（需真实 key）：**
```
export MU_BASE_URL=...; export MU_MODEL=...; export MU_API_KEY=...
./.venv/bin/python -m mu "写一个统计文本词数的工具扩展，加载它，并用它统计 'hello world foo' 的词数"
# 期望：agent write 扩展 → load_extension → 调新工具 → 给出 3；./.mu/extensions/ 下留下扩展
./.venv/bin/python -m mu --resume <id> "用刚才那个扩展再统计另一句"   # 扩展状态/可用性跨会话
```

## M3 明确不做（守 Pi-thin / 留后续）

可插拔安全/权限/沙箱 provider（M3.5）；native code-action（M3.5）；in-process importlib 热重载；扩展市场/分发；复杂依赖管理；Windows 平台；扩展渲染自定义 TUI 组件（仅留协议位，M3 不做 UI 注入）。

## 对 Roadmap 的回写（执行后）

§12 标 M3 ✅，记录两决策（子进程隔离 + 自动加载目录、系统提示加自延伸提示），并重申「安全沙箱在 M3.5」。
