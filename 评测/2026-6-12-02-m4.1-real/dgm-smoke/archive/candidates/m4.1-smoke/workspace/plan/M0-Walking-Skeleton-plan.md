# 实施 Plan · M0 — Walking Skeleton（async-first）

> 阶段来源：`docs/Python复刻Pi-Roadmap-v1.md`（v1.1）§4 M0。
> 本 plan 经审批后，**第 0 步**先落盘到项目 `plan/M0-Walking-Skeleton-plan.md`（plan 模式下仅能编辑指定计划文件，故先写在这里）。
> 虚拟环境：一律用当前目录下的 `./.venv`（Python 3.12.10，裸环境）。

## Context（为什么做这个）

Roadmap v1.1 把 v1 范围锁定为 M0–M3.5，M0 是第一阶段。目标是用 Python 按 **Pi 的实现思路**做出最小可运行内核，验证「**薄 async loop + 4 工具 + litellm 单 provider** 能端到端解真实小任务」。这是后续 M1（事件流/上下文管线/tree session/可观测归因）、M3（自延伸）、M3.5（code-action/安全层）的地基，因此 M0 必须**内核 async-first**（§6-1 已锁定），避免后续把 streaming/abort/事件流/并发 subprocess 全量返工，同时严守 Pi 极简哲学——只建最小核，不提前造 M1+ 的东西。

预期结果：能跑 `python -m mu "<task>"`，模型通过 read/write/edit/bash 四工具完成「读文件→改代码→跑测试」闭环，全过程纯文本可观测；`pytest` 在不联网的情况下全绿。

## 设计决策（关键取舍，先定清）

1. **工具调用机制：原生 function-calling（Pi 风格），不是 mini-swe 的正则解析文本。** 通过 litellm `tools=[...]` 传 JSON schema、解析 `message.tool_calls`。理由：用户要求「按 Pi 的实现思路」，且 M1 的 terminate 提示、M3 扩展注册、M3.5 native code-action 都建在「已注册工具」之上；文本解析会与后续阶段冲突。
2. **线性 append-only 消息历史 = prompt（mini-swe 洞察）。** `messages: list[dict]`（OpenAI 格式：system/user/assistant(+tool_calls)/tool），每轮原样传给模型。tree session 留到 M1。
3. **朴素 while loop，无 max_steps。** 当 assistant 消息不含 tool_calls 即终止（Pi 哲学）。靠 Ctrl-C / asyncio 取消优雅中断；迭代/成本预算属 M3.5 安全层，M0 不做。
4. **工具返回字符串、错误也返回字符串（不抛异常）**，让模型自纠错（Pi/Claude Code 风格）。
5. **async-first 落到实处**：模型用官方 `openai` SDK 的 `AsyncOpenAI(...).chat.completions.create(...)`（详见决策 9）；bash 用 `asyncio.create_subprocess_shell` + `asyncio.wait_for` 超时（默认 120s，无状态每次新进程）；文件 IO 用 `asyncio.to_thread` 避免阻塞事件循环。M0 工具调用**顺序执行**（并行留 M1）。
6. **可观测 = 纯 stdout，但留一个单点 seam**（一个 `_emit()` 打印函数）。M1 把它替换为事件流即可，M0 不提前建事件系统。
7. **edit 语义**：old_string 精确且唯一匹配；未找到/不唯一 → 返回错误字符串。
8. **配置（OpenAI 兼容端点）**：`MU_MODEL`（模型名，如 `qwen-max` / `deepseek-chat` / `gpt-4o`）、`MU_BASE_URL`（OpenAI 兼容 base_url，如百炼 `https://dashscope.aliyuncs.com/compatible-mode/v1`、DeepSeek `https://api.deepseek.com/v1`）、`MU_API_KEY`（对应端点的 key）。直接读 `os.environ`，附 `.env.example`，不引入 dotenv 依赖。

9. **传输层 = 官方 `openai` SDK（OpenAI 兼容优先），偏离 roadmap 的 litellm。** 理由：用户的目标 provider（百炼/DeepSeek/…）全是 OpenAI 兼容端点，用 `AsyncOpenAI(base_url=MU_BASE_URL, api_key=MU_API_KEY)` 一套接口即可覆盖，比 litellm 更薄、依赖更少、tool-calling/streaming/async 原生支持。Model 层是 Protocol，M1+ 若需接非 OpenAI 兼容 provider（如原生 Anthropic）再引 litellm，不影响上层。

## 目录与文件（flat `mu/` 单包，不提前拆包）

```
agent_loop_demo/
  pyproject.toml          # PEP621 元数据 + 依赖；package = mu
  .env.example            # MU_MODEL + provider key 示例
  README.md               # 极简运行说明
  mu/
    __init__.py
    __main__.py           # 使 `python -m mu` 可用
    cli.py                # 取 task（argv/stdin）→ asyncio.run(Agent.run)
    agent.py              # Agent：async while loop + 线性 messages + _emit seam
    model.py              # Model：AsyncOpenAI.chat.completions.create 异步薄封装（base_url/key/model 来自 env）
    tools.py              # ToolRegistry + 4 个 async 工具 + JSON schema
    environment.py        # LocalEnvironment：async bash 执行 + to_thread 文件 IO
    prompts.py            # 系统提示（<1000 token，Pi 风格）
  tests/
    conftest.py           # pytest-asyncio 配置
    test_tools.py         # 4 工具单测（tmp 目录，async，无网络）
    test_agent_loop.py    # FakeModel 脚本化，验证 loop（无网络/无付费）
```

**复用而非新造**：模型层只薄封装官方 `openai` SDK 的 `AsyncOpenAI`（不自建 HTTP/provider 适配）；并发/超时用标准库 asyncio 原语；不引入任何 agent 框架（openai SDK 之外不叠框架，符合 §7 非目标）。

## 任务顺序

0. **落盘**：将本 plan 保存到 `plan/M0-Walking-Skeleton-plan.md`。
1. **工程化**：写 `pyproject.toml`（runtime: `openai`；dev: `pytest`、`pytest-asyncio`），用 `./.venv/bin/python -m pip install -e ".[dev]"` 安装进当前 .venv；加 `.env.example`（百炼/DeepSeek/OpenAI 三组示例）。
2. **environment.py**：LocalEnvironment——`run_bash(cmd, timeout)`（create_subprocess_shell + wait_for，返回 stdout/stderr/exit_code 文本）、`read_file/write_file`（to_thread）。
3. **tools.py**：实现 read/write/edit/bash 四工具（调用 environment）、各自 JSON schema、ToolRegistry（name→schema+handler，async execute，错误转字符串）。
4. **model.py**：Model.acomplete(messages, tools) 薄封装 `AsyncOpenAI(base_url=MU_BASE_URL, api_key=MU_API_KEY).chat.completions.create(model=MU_MODEL, messages, tools, tool_choice="auto")`；返回 message（含 content 与 tool_calls）。
5. **prompts.py**：<1000 token 系统提示（角色 + 四工具用法 + 绝对路径约定 + 简洁回答）。
6. **agent.py**：Agent.run(task)——初始化 messages、async while loop（调用 model → append assistant → 无 tool_calls 则返回 → 否则顺序执行工具、append tool 结果）、`_emit()` 打印 seam。
7. **cli.py / __main__.py / __init__.py**：取 task（argv 优先，否则 stdin）→ `asyncio.run(Agent().run(task))`。
8. **tests**：`test_tools.py`（tmp 目录验证读写改 + bash echo/exit code/超时）；`test_agent_loop.py`（FakeModel 返回脚本化 tool_calls 序列后给最终答复，断言工具被执行、messages 结构正确、无 tool_calls 时终止）；`conftest.py` 配 asyncio 模式。
9. **README.md**：运行/配置说明（用 ./.venv）。

## 验证（对应 M0 完成标志）

**自动化（必须，无网络/无付费）：**
```
./.venv/bin/python -m pytest -q
```
全绿——工具单测 + FakeModel 驱动的 loop 测试（不触达真实 API）。

**手工 e2e（需真实 key，验证「读文件→改代码→跑测试」闭环）：**
```
# 百炼示例（DeepSeek/OpenAI 同理，换 MU_BASE_URL/MU_MODEL/MU_API_KEY）
export MU_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export MU_MODEL=qwen-max
export MU_API_KEY=sk-...
mkdir -p /tmp/mu_scratch && cd /tmp/mu_scratch
/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/.venv/bin/python -m mu \
  "在 calc.py 写一个 add(a,b)，再写 test_calc.py 用 pytest 测试它，然后运行 pytest 确认通过"
```
预期 stdout 可见：模型依次 write(calc.py)→write/edit(test_calc.py)→bash(pytest) 的闭环，最终 pytest 通过、loop 自行结束。
> 注意：tool-calling 行为依赖具体端点——百炼(Qwen)/DeepSeek 均支持 OpenAI 风格 function calling；若某端点不支持，需换支持的模型。

## M0 明确不做（避免 feature creep，守 Pi 哲学）

事件流 / tree session / 上下文管线（→M1）；可观测归因底座（→M1）；TUI / 流式 UI（→M2）；自延伸扩展（→M3）；安全/权限/沙箱（→M3.5，M0 本地 YOLO 执行）；多 provider 切换；max_steps；并行工具执行。

## 对 Roadmap 的调整说明（需在执行后回写 roadmap）

M0 传输层由 roadmap 原定的 **litellm** 调整为**官方 openai SDK（OpenAI 兼容端点优先）**，依据用户提供的 provider（百炼/DeepSeek 等均 OpenAI 兼容）。这是范围内的合理细化，不改变上层架构（Model 仍是可替换 Protocol）。执行 M0 后应在 `docs/Python复刻Pi-Roadmap-v1.md` §2/§9 补一条注记，保持文档为事实源。
