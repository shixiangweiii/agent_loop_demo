# Pi 极简 Agent 深度调研

> 调研时间：2026-06-11
> 对象：Pi —— Mario Zechner（@badlogic，libGDX 作者）开发的极简 coding agent，OpenClaw 的底层引擎。
> 一手源：[Mario 原始博客](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)、[GitHub earendil-works/pi](https://github.com/earendil-works/pi)、[Armin Ronacher 评测](https://lucumr.pocoo.org/2026/1/31/pi/)。
> 一句话定位：**Pi 不是「又一个 agent 框架」，而是「loop 已收敛、harness 应薄、模型自己内化 loop」这一判断的工程化证据**；它把上一份增补稿里推荐的「自进化脚手架」做成了产品的核心机制。

---

## 0. 命名澄清（避免混淆）

同一项目有多个名字，都是同一个东西：

- 作者：**Mario Zechner**，GitHub handle `@badlogic`。
- 仓库：现为 **`earendil-works/pi`**（早期文献里也叫 `badlogic/pi-mono`，monorepo 内部代号 `pi-mono`）。
- npm scope：从 `@mariozechner/*` 迁移到 **`@earendil-works/*`**。
- 官网（自嘲式品牌）：`shittycodingagent.ai`。
- 体量（截至 2026-03）：约 28.3K stars、3.4K+ commits、181 releases（v0.63.1）、134+ contributors。**OpenClaw（180K+ stars）底层即 Pi。**

---

## 1. 核心哲学：「我不需要它，就不会构建它」

> 原文："if I don't need it, it won't be built. And I don't need a lot of things." / "constraints make for minimal programs."

Pi 的设计公理是 **context engineering 至上**：4 个工具 + < 1000 token 系统提示（含工具定义），把上下文窗口最大限度留给真正的代码和项目信息。其余一切交给扩展系统。

**故意「不做」清单（这比「做什么」更能定义 Pi）：**

| 不做的事 | 理由（原文要点） |
|---|---|
| **无内置 To-Do** | 「模型管理任务状态时更容易混乱」，改用外部 markdown 文件 |
| **无 Plan Mode** | 文件化计划（PLAN.md）可观测、可跨会话持久 |
| **无 MCP 支持** | MCP server 吃 13.7k–18k tokens/个，多数工具模型根本不用；改用「带 README 的 CLI 工具 + bash 按需加载」 |
| **无后台 bash** | 「后台进程管理增加复杂度」，用 tmux 换可观测性 |
| **无 Sub-Agent**（内置） | 「你对 sub-agent 在做什么零可见性」；需要时通过 bash 递归 spawn 自己（`pi --print`） |
| **无权限检查（YOLO）** | 「能读写执行代码时，安全措施本就是安全剧场」；不放心就跑在容器里 |
| **（早期）无 compaction** | Nov-2025 时「缺压缩对我个人没造成问题」——⚠️ 见 §8，此点后续已演进 |

> 关键论断："All frontier models have been RL-trained up the wazoo, so they inherently understand what a coding agent is. There does not appear to be a need for 10,000 tokens of system prompt."（对比 Claude Code 系统提示 10k+ tokens。）

---

## 2. Monorepo 分层架构（4 个包）

严格分层、lockstep 版本（所有包同版本号一起发）：

```text
┌──────────────────────────────┐
│ pi-coding-agent              │  完整 coding agent CLI：4 个内置工具、
│                              │  JSONL session 持久化、compaction、skills、扩展系统
├──────────────────────────────┤
│ pi-agent-core (packages/agent)│  Agent Loop + 状态管理 + 工具执行 + 事件流
├──────────────────────────────┤
│ pi-ai (packages/ai)          │  统一多 Provider LLM API（OpenAI/Anthropic/Google/xAI/
│                              │  Groq/Cerebras/Bedrock/NIM/MiniMax/OpenRouter…）
├──────────────────────────────┤
│ pi-tui (packages/tui)        │  retained-mode 终端 UI，差分渲染、防闪烁、IME（中日文）
└──────────────────────────────┘
```

**工程约定（来自 AGENTS.md，对理解其工程品味有用）：**

- 只用 **erasable TypeScript**（Node strip-only 模式）：禁 parameter properties、enum、namespace、`import =`。
- **禁内联 import**：所有 import 顶层化（无 `await import()`）。
- `models.generated.ts` 自动生成（解析 OpenRouter + models.dev），永不手改。
- coding-agent 测试用 **faux provider**，不打真实付费 API；有专门 `regressions/` 目录。
- 严格 Git 规则防止多 agent 会话并发冲突。

---

## 3. Agent Loop 的真实机制（重点）

**基本循环**（`pi-agent-core`）：

```text
处理用户消息 → 调 LLM → 有 tool calls 则执行、结果喂回 → 重复
→ 直到模型产出「不含 tool call」的回复为止
```

**关键设计决策（和主流框架的差异点）：**

1. **无 `max_steps` 旋钮。** loop「跑到 agent 说完为止」。作者：「我从没遇到需要它的场景，那为什么要加？」
2. **终止由 tool result 控制（细粒度）。** 工具可返回 `terminate: true` 暗示「跳过自动的后续 LLM 调用」。**只有当这一批 finalized tool result 全部 `terminate: true` 时才提前停**；混合批次照常继续。
3. **优雅的 turn 级停止。** 底层调用方可设 `shouldStopAfterTurn`：在 `turn_end` 事件、assistant 响应、工具执行都正常完成后运行；返回 true 则发 `agent_end` 退出——发生在轮询 steering/follow-up 队列、开始下一 turn **之前**。
4. **消息队列（异步注入）。** 每个 turn 后通过 callback 询问队列消息，在下一个 assistant 响应前注入。两种模式：one-at-a-time / all-at-once。支持 agent 工作时插话（steering）。
5. **全事件流。** 所有动作 emit 事件 → 可构建响应式 UI；**强调全程可观测**（这是它拒绝 sub-agent 的根因）。
6. **消息类型转换管线。** `AgentMessage`（含标准 user/assistant/toolResult + 通过 declaration merging 的自定义 app 消息类型）→ `transformContext()`（可裁剪旧消息、注入外部上下文）→ `convertToLlm()`（过滤成 LLM 只懂的三种类型）→ LLM。**这一层是「精确控制进入模型上下文的内容」的关键抓手。**

`Agent` 类在裸 loop 之上加：状态管理、简化事件订阅、消息队列、附件处理（图片/文档）、**transport 抽象**（直接运行 or 通过 proxy/RPC 运行）。

---

## 4. 四个工具 + 极简提示

```text
read  : 读文件内容（文本/图片），支持 offset/limit
write : 创建/覆盖文件，自动建目录
edit  : 精确文本替换（必须精确匹配）
bash  : 执行命令，可选 timeout
```

系统提示核心就一句："You are an expert coding assistant. You help users with coding tasks by reading files, executing commands, editing code, and writing new files." 后接简短 guidelines + 文档指针。

- 可选只读模式：`--tools read,grep,find,ls` 做探索不改动。
- 唯一额外注入系统提示的是 **AGENTS.md**（从 global 到 project 分层加载的项目上下文）。
- **「缺能力就现写」**：要浏览网页？不找 MCP / 扩展，而是**写一个用 Chrome DevTools Protocol 的脚本，bash 跑、解析输出，用完即弃**。

---

## 5. 自我延伸（Pi 最独特的地方）

**核心理念：要新能力，不是下扩展，而是让 agent 自己写扩展。**（Armin：「我的大多数扩展都是 agent 按我的规格创建的。」）

- 扩展 = TypeScript 模块，可**注册工具、渲染 TUI 组件、把状态持久化到 session**。
- **热重载**：agent 写代码 → reload → 测试 → 循环，直到扩展可用。仓库自带文档和示例供 agent 自我参考。
- **扩展可在 session 文件里存自定义消息作为状态**（Armin 认为「极其强大」）。
- 下载他人扩展仍支持；但更鼓励「指给 agent 一个现成扩展，让它照着改一个你要的」。
- Slash command = 带参数的 markdown 模板。示例：code review 命令通过 bash `pi --print` **递归 spawn 自己**当 sub-agent——透明、可观测。

**Session 是树，不是线性列表：**

- 每条消息有 `id` + `parentId`，JSONL 存储。
- 可从任意点**分支**，探索另一条路径，再**回溯**。
- 典型用法（Armin）：主线做任务时，开一条 side-quest 去修一个坏掉的 agent 工具（**不污染主线上下文**），修好后 rewind 回早期，Pi 会 summarize 另一分支发生了什么。

---

## 6. pi-ai：统一 Provider 抽象（被低估的工程价值）

作者洞察：**「其实只需要 4 个 API」**——OpenAI Completions、OpenAI Responses、Anthropic Messages、Google Generative AI。其余 provider 都是这四种的变体。

- **跨 Provider 上下文传递**：可会话中途切模型（Claude→GPT）。Anthropic thinking trace 转成 `<thinking></thinking>` 标签给 OpenAI；签名的 provider blob 在后续请求重放。
- 处理大量 provider 怪癖：Cerebras/xAI/Mistral 拒 `store` 字段；Mistral 用 `max_tokens` 而非 `max_completion_tokens`；reasoning 字段名不一（`reasoning` vs `reasoning_content`）；Google 不支持 tool call streaming。
- **AbortController 全链路中断**，返回部分结果（统一 API 里少见）。
- 浏览器可用（Anthropic / xAI 支持 CORS）。
- token/成本追踪是 best-effort，「个人用够，不适合给终端用户精确计费」。

---

## 7. OpenClaw 集成：engine-and-chassis（引擎与底盘）

OpenClaw **不 fork** coding agent，而是把 Pi 当库**直接 embed**：

- 直接 `import` `pi-agent-core` + `pi-ai`，通过 `createAgentSession()` 实例化 `AgentSession`——**不 spawn 子进程、不走 RPC**。
- 入口 `runEmbeddedPiAgent()`，内部用 Pi 的 `SessionManager` / `SettingsManager` / `DefaultResourceLoader` 建每个 session。
- **自带底盘**：以 Pi 的 `codingTools`（read/bash/edit/write）为起点，**把 bash 换成 exec/process**、为沙箱定制 read/edit/write；因接口不同，用 `pi-tool-definition-adapter.ts` 桥接 `AgentTool` 与 `ToolDefinition` 两种 execute 签名。
- 由此获得：完整 session 生命周期控制、按 channel 定制系统提示、tree session 持久化（分支+compaction）、多账号 auth 轮换 failover、provider 无关切模型。

> 观察者结论：**「embed 一个极简、自延伸的 agent core，是 AI 平台的一种可行架构」**——不自建 runtime 而 import 一个；不预建每个能力而让 agent 现长。

---

## 8. 上下文 / Compaction 的时间线（一处需注意的演进）

- **Mario 原博客（2025-11）**：Pi **没有** compaction，「对我个人没造成问题」，单 session 可达「数百轮交换」。
- **后续（pi-coding-agent，至 2026 Q1）**：二手源与包描述已将 **context compaction** 列为 `pi-coding-agent` 的内置特性。

> 解读：这是 Pi「按需才加」哲学的真实体现——**先不做，等真痛了再加，且加在恰当的层（coding-agent 而非裸 core）**。引用早期「无 compaction」时需注明时间点。

---

## 9. Benchmark 与证据

- **Terminal-Bench 2.0**（Claude Opus 4.5）：Pi 以极简架构与 Codex / Cursor / Windsurf 同台竞争，进入排行榜前列（每任务 5 trials）。
- **旁证**：Terminus 2（Terminal-Bench 团队自己的极简 agent，只给模型一个 tmux session）同样表现优异——**进一步证明极简方法有效**。
- 作者拒绝过度量化：「真正的证明在布丁里，而我的布丁就是我的日常工作。」（无 latency / 成功率等硬指标）。
- 印证此前调研：mini-SWE-agent（~100 行）在 SWE-bench Verified 达 74–76.8%——**loop 不是瓶颈，工具质量、上下文管理才是**。

---

## 10. 批评与局限

| 局限 | 说明 |
|---|---|
| **零护栏信任模型写对 TS** | 扩展是模型生成的 TypeScript，无防 buggy 扩展乱跑的机制；复杂集成（涉外部服务）需人工 review 后再 commit |
| **无内置权限系统** | 不限制 fs / 进程 / 网络 / 凭证访问（YOLO）；安全靠容器隔离，不靠框架 |
| **best-effort 计费** | 不适合做有终端用户的精确计费 |
| **可观测换能力** | 拒 sub-agent / 后台 bash 提升了透明度，但也把并发/编排的复杂度推给用户（tmux、递归 spawn） |
| **依赖前沿模型** | 极短提示成立的前提是「模型已被 RL 训得懂 coding agent」；弱模型上未必成立 |

---

## 11. 我的分析：Pi 对「agent-loop 范式」这个大问题意味着什么

把 Pi 放回前两份调研 + 增补稿的框架里，它几乎是每条结论的活体样本：

1. **Pi 实证了「loop 已收敛」。** 它的 loop 就是教科书 while + 几个细粒度终止/队列/事件钩子，**作者明确拒绝给 loop 加旋钮**。差异化完全不在 loop，而在 harness（pi-ai 的 provider 抽象、tree session、扩展系统）。这正是「Harness > Loop」。

2. **Pi 是「会让位的薄 harness」的范本。** 它把 harness 削到只剩模型学不会的那几件事：**provider 适配、上下文精确控制、session 持久化/分支、工具执行/事件**——其余（to-do、plan、sub-agent、MCP）全部拒绝，理由都是「模型自己能干 / 文件能干」。这与增补稿 §6.1「让 harness 收缩到模型短期学不会的部分」完全同构。

3. **Pi 的自我延伸 = 增补稿推荐的「自进化脚手架」的轻量版。** DGM 是「自改写代码 + benchmark 验证 + archive」；Pi 是「agent 写扩展 + 热重载 + session 存状态 + 人工 review 当护栏」。**它把 self-evolving 从研究降维成了日用产品机制**——这是目前应用层最现成的「自进化」实现参考。

4. **「缺能力就现写脚本，用完即弃」是对 MCP/工具目录范式的釜底抽薪。** 与其预装一堆吃上下文的工具，不如让模型用 bash 即时合成能力（CDP 脚本浏览网页就是例子）。这本质是 **CodeAct 思想 + 极简工具集**的结合，且回避了工具 schema 注入的上下文成本（呼应增补稿对 Tool RAG 的讨论：最好的 tool RAG 是「根本不注入，现写」）。

5. **但要清醒：Pi 的成立强依赖前沿模型 + YOLO 假设。** 它把安全/权限/计费/编排的复杂度外推给了「容器 + tmux + 人工 review」。在企业/多租户/受监管场景，这些恰恰是不能外推的——所以**Pi 是「个人/高信任环境下的最优解」，不是「企业 agent 平台的最优解」**。OpenClaw 之所以要在 Pi 外面套一层底盘（沙箱化 exec、按 channel 定制、多账号 failover），正说明了这条边界。

**结论一句话：**

> Pi 不提供「新 loop」，它提供的是「**在 loop 已收敛后，harness 应该长成什么样**」的最佳答案之一——极薄、全可观测、靠模型自我延伸而非预置插件来扩展。对你而言，Pi 最值得借鉴的不是它的 4 个工具，而是它的**三件武器**：精确的上下文转换管线（`transformContext`/`convertToLlm`）、tree session、以及「让 agent 写扩展 + 热重载 + session 存状态」的自延伸闭环。

---

## 12. 资料索引

| 资料 | 类型 | 重点 |
|---|---|---|
| [What I learned building Pi（Mario Zechner, 2025-11-30）](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/) | 一手 | 完整设计哲学、故意不做清单、provider 抽象、loop 决策 |
| [GitHub earendil-works/pi](https://github.com/earendil-works/pi) | 一手 | 源码、monorepo 结构 |
| [pi AGENTS.md](https://github.com/earendil-works/pi/blob/main/AGENTS.md) | 一手 | 工程约定、包结构、测试/版本策略 |
| [Pi: The Minimal Agent Within OpenClaw（Armin Ronacher, 2026-01-31）](https://lucumr.pocoo.org/2026/1/31/pi/) | 一手评测 | 自延伸、扩展状态持久化、session 树、MCP 取舍 |
| [How 4 Tools Power OpenClaw（Medium）](https://shivamagarwal7.medium.com/agentic-ai-pi-anatomy-of-a-minimal-coding-agent-powering-openclaw-5ecd4dd6b440) | 二手 | 4 工具解剖、OpenClaw 关系 |
| [How to Build a Custom Agent Framework with Pi（nader.substack）](https://nader.substack.com/p/how-to-build-a-custom-agent-framework) | 二手 | embed 集成、createAgentSession |
| [Pi Coding Agent: Agent Loop & Extension（xiaow.dev）](https://xiaow.dev/claude_notes/2026-03-27---Research---Pi-Coding-Agent---Agent-Loop,-Extension-and-Plugin-System) | 二手 | loop 终止/消息转换细节 |
| [Building Pi（Pragmatic Engineer）](https://newsletter.pragmaticengineer.com/p/building-pi-and-what-makes-self-modifying) | 二手 | 自修改软件视角 |
| [npm @earendil-works/pi-agent-core](https://www.npmjs.com/package/@mariozechner/pi-agent-core) | 一手 | 包文档 |
