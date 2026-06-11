# M0 Walking Skeleton 代码评审报告

> 日期：2026-06-11
> 评审对象：`mu/`、`tests/`、`README.md`、`pyproject.toml`
> 依据文档：`docs/Python复刻Pi-Roadmap-v1.md` v1.1、`plan/M0-Walking-Skeleton-plan.md`
> 评审方式：源码阅读 + 自动化测试 + 针对 bash timeout 的边界复现

## 1. 评审结论

**结论：M0 主路径基本达标，但建议修复 2 个 P1/P2 问题后再进入 M1。**

当前实现已经完成 M0 的核心目标：async-first agent loop、OpenAI 兼容 model wrapper、read/write/edit/bash 四工具、线性 append-only 消息历史、纯 stdout 观测 seam，以及 FakeModel 驱动的离线测试。整体代码保持了 Pi 风格的薄核，没有提前引入 M1+ 的复杂结构。

不过，`bash` timeout 的进程清理存在实际泄漏风险；README 对 `.env` 的说明与代码行为不一致，会让真实用户按文档配置后仍启动失败。另有一些 M0 验收相关的测试缺口，建议在进入 M1 前补上，避免后续把事件流、session、归因系统建在未完全打稳的基础上。

## 2. Findings

### P1. bash timeout 只杀 shell，可能留下子进程继续运行

- 位置：`mu/environment.py:23`-`33`
- 相关测试：`tests/test_tools.py:85`-`88`

`LocalEnvironment.run_bash()` 使用 `asyncio.create_subprocess_shell()` 启动命令，timeout 时只对顶层 shell 调用 `proc.kill()`，随后 `await proc.wait()`。这对简单命令如 `sleep 5` 通常能过测试，但对会派生子进程的命令不可靠，例如：

```bash
python3 -c 'import time; time.sleep(30)' & wait
```

评审中用带唯一 marker 的后台子进程命令复现：`run_bash(..., timeout=0.2)` 返回 timeout 后，仍可通过 `pgrep` 看到标记子进程。也就是说，agent 让 `pytest`、构建脚本、dev server、shell pipeline 超时后，可能留下孤儿进程继续占用端口、CPU 或文件锁。

**影响：**

- M0 的 `bash(timeout)` 语义不完整。
- 后续 M1 的可观测/归因会记录“命令已超时”，但真实环境中命令的子进程可能仍在跑。
- 对 coding agent 很常见的命令（测试套件、npm scripts、服务启动脚本）风险更高。

**建议：**

- Unix/macOS 下启动新进程组：`start_new_session=True` 或 `preexec_fn=os.setsid`。
- timeout 时杀整个进程组：`os.killpg(proc.pid, signal.SIGKILL)`。
- Windows 后续再按平台补 `CREATE_NEW_PROCESS_GROUP`。
- 增加测试：命令派生带唯一 marker 的子进程，timeout 后断言该 marker 不存在。

### P2. README 建议复制 `.env`，但代码不会加载 `.env`

- 位置：`README.md:9`-`12`
- 代码行为：`mu/model.py:26`-`38`
- 计划约束：`plan/M0-Walking-Skeleton-plan.md:22`

README 安装步骤写了：

```bash
cp .env.example .env
```

但实现只读取 `os.environ`，并且 plan 明确说“不引入 dotenv 依赖”。因此用户照 README 复制并填写 `.env` 后直接运行 `python -m mu ...`，仍会得到 `ConfigError`，因为 `.env` 不会被自动加载。

**影响：**

- 真实手工 e2e 很容易按文档失败。
- 这个问题会被误判为 provider/key 配置问题，而不是文档与实现不一致。

**建议：**

二选一即可：

- 保持零依赖：README 改成 `export MU_*` 或 `set -a; source .env; set +a`。
- 或引入 `python-dotenv`，在 CLI 启动时显式加载 `.env`。

按 M0 的“依赖少、薄封装”原则，更建议第一种。

### P2. M0 fake loop 测试没有覆盖“读文件 -> 改代码 -> 跑测试”的闭环

- 位置：`tests/test_agent_loop.py:47`-`119`
- M0 完成标志：`plan/M0-Walking-Skeleton-plan.md:63`-`81`

当前 agent loop 测试覆盖了：

- 单工具 `write` 后终止。
- 无 tool call 立即终止。
- bad JSON 转错误字符串。
- 单轮多工具顺序执行。

但没有一个 FakeModel 脚本覆盖 M0 的目标闭环：`read` -> `edit/write` -> `bash(pytest)` -> final。工具单测能证明四工具各自可用，但不能证明 agent messages 在多轮多工具真实闭环中持续保持 OpenAI tool-call 兼容结构。

**影响：**

- 现在的“19 单测通过”仍没有完全锁住 M0 完成标志。
- 后续 M1 重构事件流/上下文管线时，缺少一个能保护 walking skeleton 行为的回归用例。

**建议：**

增加一个离线 e2e-ish FakeModel 测试：

1. 第一轮调用 `read` 读取目标文件。
2. 第二轮调用 `edit` 或 `write` 修改代码。
3. 第三轮调用 `bash` 运行一个小测试命令。
4. 第四轮返回 final answer。

断言最终文件内容、bash exit code 回填、roles 顺序和每轮 `seen_messages` 中的 tool message 都正确。

### P3. `ToolRegistry.schemas()` 返回内部可变对象，后续扩展阶段容易踩坑

- 位置：`mu/tools.py:168`-`169`
- 调用方：`mu/agent.py:39`

`schemas()` 直接返回内部 list。M0 里 OpenAI SDK 通常不会修改它，所以问题不大；但 M3 扩展注册、M3.5 code-action 或测试 fake 如果误改 schema，会污染 registry 全局状态。

**建议：**

- M0 可先不改。
- 进入 M1/M3 前改成返回 `copy.deepcopy(self._schemas)`，或把 schema 建成不可变/只读结构。

### P3. Prompt 与 schema 要求绝对路径，但工具层不校验

- 位置：`mu/prompts.py:11`-`14`
- 位置：`mu/tools.py:92`、`112`、`127`

系统提示和 schema 都要求使用绝对路径，但工具实现接受相对路径。考虑到 M0 是本地 YOLO 执行，这不是阻塞问题；而且相对路径在 CLI 工作目录内有时也方便。但如果目标是和 Pi 一样保持上下文可预测，后续需要明确是“软约束”还是“硬约束”。

**建议：**

- M0 可保持现状。
- M1 做 session/可观测时，在事件里记录 cwd。
- M3.5 安全层再决定是否强制绝对路径或限制 workspace root。

## 3. 正向观察

1. **async-first 真实落地**：agent loop、model call、file IO、bash execution 都走 async 路径，符合 roadmap v1.1 对 M0 的修订。
2. **loop 足够薄**：`Agent.run()` 保持朴素 while，无 `max_steps`，以“无 tool_calls”作为终止条件，符合 Pi 哲学。
3. **工具错误转字符串**：`ToolRegistry.execute()` 和各工具实现都尽量把错误回填给模型，方向正确。
4. **M1 seam 留得克制**：`_emit()` 是单点 stdout 观测 seam，没有提前造事件系统。
5. **测试不联网**：FakeModel 与工具单测覆盖了主路径，`pytest` 离线可跑。

## 4. 验证结果

自动化测试：

```text
19 passed in 0.34s
```

评审期间额外检查：

- 读取 roadmap v1.1 与 M0 plan，确认 M0 范围。
- 检查 `mu/` 实现与 plan 中目录/职责映射。
- 针对 `bash` timeout 派生子进程场景做边界复现，确认存在子进程残留风险。

## 5. 建议处理顺序

1. **先修 P1**：让 `bash` timeout 杀整个进程组，并补派生子进程测试。
2. **再修 P2 文档**：README 改为显式 export/source `.env`，避免真实 e2e 卡在配置。
3. **补 M0 闭环测试**：用 FakeModel 覆盖 read/edit/bash 多轮闭环，作为 M1 重构保护网。
4. **P3 留到 M1/M3**：schema 深拷贝、路径策略可以等扩展/安全边界更清楚后处理。

## 6. 进入 M1 前的建议门禁

建议满足以下条件后再开始 M1：

- `bash(timeout)` 不残留派生子进程。
- README 配置说明与实现一致。
- 新增一个离线 FakeModel 闭环测试，覆盖 `read -> edit/write -> bash -> final`。
- 测试仍保持无网络、无真实 API key 依赖。

