# M2 + M3 代码评审报告

> 日期：2026-06-12
> 评审对象：M2 Textual 前端 + M3 自延伸基座实施后的代码
> 依据文档：`plan/M2-Textual-Frontend-plan.md`、`plan/M3-Self-extension-plan.md`、`docs/Python复刻Pi-Roadmap-v1.md`
> 评审方式：源码阅读 + 全量测试 + 针对扩展崩溃 / 同名扩展加载 / TUI 事件覆盖的离线复现

## 1. 评审结论

**结论：M2/M3 主线可用，但 M3 生命周期管理存在 2 个 P1，需要先修。**

当前实现已经完成 M2/M3 的主体能力：

- M2：新增 Textual TUI，`--tui` 显式启动，TUI 作为事件流消费者，没有污染 core。
- M3：新增扩展系统，包含 `ExtensionManager`、`extsdk.py`、JSONL IPC、动态工具注册、示例扩展、扩展状态持久化、自动加载目录。
- 自动化测试通过：`53 passed in 7.75s`。

整体方向符合 Pi-thin：TUI 是叶子，扩展协议很小，工具注册与 session 状态复用了 M1 的底座。主要风险集中在扩展生命周期的异常边界：扩展进程崩溃时 pending 调用不会及时解挂，同名扩展重复加载会造成旧进程和旧工具泄漏。

## 2. Findings

### P1. 扩展子进程执行中崩溃时，调用会卡到 `_CALL_TIMEOUT`，且工具不会被降级/注销

- 位置：`mu/extension.py:255`
- 位置：`mu/extension.py:292`-`296`
- 相关能力：扩展崩溃隔离、错误回流事件流、工具调用可靠性

`ExtensionManager.call()` 发送 `execute` 后等待 pending future：

```python
content, terminate = await asyncio.wait_for(fut, timeout=_CALL_TIMEOUT)
```

reader loop 在进程结束后只 emit `ExtensionError`：

```python
if ext.process.returncode not in (0, None):
    self._emitter.emit(
        ExtensionError(ext.name, f"extension process exited with code {ext.process.returncode}")
    )
```

它没有：

- resolve / reject 属于该扩展的 pending future；
- 注销该扩展注册的工具；
- 从 `_exts` 移除或标记 degraded。

离线复现：临时扩展工具执行 `os._exit(7)`。结果：

- 事件流中出现 `ExtensionError("extension process exited with code 7")`；
- 工具调用没有立即返回；
- 直到 `_CALL_TIMEOUT` 后才返回 `Error: extension crasher tool crash_tool timed out.`

默认 `_CALL_TIMEOUT = 120s`，真实使用时这会让 agent 卡住两分钟。

**影响：**

- “子进程崩溃隔离”不够完整，崩溃不会快速反馈到当前工具调用。
- 已死亡扩展的工具仍在 registry 中，后续调用只会得到“extension not running”或继续混乱。
- TUI/headless 用户都会感到卡顿。

**建议：**

reader loop 发现扩展进程退出时应执行统一降级流程：

1. 找出属于该扩展的 pending futures，并设置错误结果。
2. 注销该扩展的所有工具。
3. 从 `_exts` 移除该扩展，或标记为 crashed。
4. emit `ExtensionError`。

也可以给 pending 结构加 `rid -> ext_name` 映射，避免跨扩展误处理。

### P1. 加载同名 manifest 的第二个扩展会覆盖 manager 状态，导致旧进程和旧工具泄漏

- 位置：`mu/extension.py:177`-`181`
- 相关能力：扩展生命周期、reload、工具注册一致性

当前 `load()` 读到 manifest 后直接：

```python
ext = Extension(name, version, str(p), proc, registered)
self._exts[name] = ext
```

没有检查 `name` 是否已存在。离线复现：两个扩展 manifest 都叫 `same`，第一个注册 `tool_a`，第二个注册 `tool_b`。结果：

- 第二次加载后 `_exts["same"]` 指向第二个进程。
- `tool_a` 仍留在 `ToolRegistry` 中，但 handler 指向第一个扩展名。
- 调 `tool_a` 得到 `Error: unknown tool 'tool_a'` / 扩展路由状态混乱。
- `aclose()` 只卸载第二个扩展；第一个扩展进程仍存活，`tool_a` 仍残留在 registry。

**影响：**

- 扩展进程泄漏。
- registry 与 manager 状态不一致。
- reload / autoload / 用户多次 load 同一个文件时容易进入坏状态。

**建议：**

加载 manifest 后，在注册工具前处理同名扩展：

- 保守方案：若 `name in self._exts`，拒绝加载并杀掉新进程。
- 自动替换方案：先 `await unload(name)`，再注册新扩展。

考虑到已有 `reload_extension(name)`，建议 `load()` 默认拒绝同名扩展，并提示使用 `reload_extension`。

同时补测试：

- 同名扩展第二次 load 被拒绝。
- 失败后新进程被回收。
- 旧工具和旧进程仍保持可用。
- `aclose()` 后没有残留工具/进程。

### P2. TUI 没有渲染扩展生命周期 / 日志 / 错误事件

- 位置：`mu/tui.py:16`-`29`
- 位置：`mu/tui.py:60`-`92`
- 对照：`mu/render.py:62`-`69`

M3 plan 要求扩展日志/错误回流事件流，不让扩展变黑盒。headless 的 `StdoutRenderer` 已处理：

- `ExtensionLoaded`
- `ExtensionUnloaded`
- `ExtensionLog`
- `ExtensionError`

但 TUI 的 `TuiRenderer` 没有导入这些事件，也没有 dispatch 分支。结果是 `--tui` 下扩展加载、日志和错误对用户不可见。

**影响：**

- TUI 与 headless 不等价。
- 扩展在 TUI 中变成黑盒，违背 M3 “错误/日志回流事件流”的初衷。
- 用户在 TUI 中调试扩展会非常困难。

**建议：**

让 `TuiRenderer` 与 `StdoutRenderer` 对齐处理扩展事件，例如：

- `ExtensionLoaded`：写入 `🧩 loaded ...`
- `ExtensionUnloaded`：写入 `🧩 unloaded ...`
- `ExtensionLog`：写入扩展日志块
- `ExtensionError`：写入红色错误块，并更新状态栏

补一个离线 renderer 单测即可覆盖。

### P3. 项目元数据 / README 标题仍停在旧阶段

- 位置：`README.md:1`
- 位置：`pyproject.toml:8`

代码和 roadmap 已到 M3，但：

- README 标题仍是 `M2`
- `pyproject.toml` description 仍是 `M1 harness core`

**影响：**

- 不影响运行，但会误导用户和后续归档。
- editable install / package metadata 与实际能力不一致。

**建议：**

- README 标题改为 M3。
- `pyproject.toml` description 改为 M3 self-extension / harness core。

## 3. 正向观察

1. **TUI 没污染 core**：`MuApp` / `TuiRenderer` 作为事件消费者和输入驱动，整体符合 M2 “前端是叶子”的要求。
2. **扩展协议足够小**：manifest + JSONL stdin/stdout + `execute/result/error/log/state`，很适合 agent 自参考和自写扩展。
3. **SDK 可读性好**：`@tool` + `run_extension` 模式清楚，示例扩展能被模型模仿。
4. **状态持久化路径跑通**：`ext_state` 写入 session，resume 后恢复状态的测试已覆盖。
5. **M1 评审问题已有修复**：取消时补齐 tool result、`summarize_branch`、collector reset、版本同步等都有落实。
6. **测试量增加合理**：新增 extension 和 TUI 测试后，53 个测试通过。

## 4. 验证结果

自动化测试：

```text
53 passed in 7.75s
```

额外离线复现：

- 临时崩溃扩展：执行中 `os._exit(7)`，复现 pending 调用等待超时。
- 同名扩展加载：复现旧进程 / 旧工具泄漏。
- agent 取消工具执行：确认 M1 P1 已修复，取消后 session 中补齐 `Error: tool execution cancelled`。
- CLI 参数解析：`--tui`、`--stream`、`--resume` 组合解析正常。

复现过程中产生的临时扩展进程已清理。

## 5. 建议处理顺序

1. **先修 P1-a**：扩展进程退出时解挂 pending future，并注销/降级扩展。
2. **再修 P1-b**：同名扩展加载拒绝或显式 unload old，再补进程/工具清理测试。
3. **补 P2**：TUI 渲染扩展事件，与 headless 保持一致。
4. **顺手修 P3**：README / pyproject 元数据同步到 M3。

## 6. 进入 M3.5 前的建议门禁

建议满足以下条件后再进入 M3.5：

- 扩展执行中崩溃能在当前工具调用中快速返回错误，不等待 120 秒。
- 崩溃扩展的工具不会继续留在 registry 中。
- 同名扩展重复加载不会造成进程或工具泄漏。
- TUI/headless 都能看到扩展加载、日志、错误事件。
- 全量测试继续通过，并新增上述异常生命周期测试。

