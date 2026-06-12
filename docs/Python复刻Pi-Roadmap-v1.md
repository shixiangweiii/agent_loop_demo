# Python 复刻 Pi · Roadmap v1.1

> 日期：2026-06-11
> 目标：用 Python 按 Pi 的实现思路复刻一版极简 coding agent，允许在 Pi 基础上做克制的扩展与创新。
> 本文档定位：**Roadmap（阶段/顺序/取舍/决策点）**，不是实施 plan，不含文件/函数/任务级细节，不含代码。
> 工作代号（占位，可改）：**μ（mu）**——「比 π 更小、且 Pythonic」。
> **修订 v1.1（2026-06-11）**：依据《Python复刻Pi-Roadmap-v1-审批报告》二次复核，采纳全部 6 项发现，并做 2 处高度校正——① 扩展协议作为 **M3 设计门禁**列维度、不在 roadmap 内全量展开（守住 roadmap≠plan）；② 拆分「**最小子进程隔离=M3 前置**」与「**可插拔安全层=M3.5 绿色创新**」。逐项处置见文末 §11。

---

## 0. North Star（这版到底要证明什么）

不是「再造一个 agent 框架」，而是把前几轮调研的结论**具身化**成一个能跑、能读懂、能自我延伸的最小系统，验证三件事：

1. **loop 已收敛**——核心 loop 极薄、无 `max_steps`，差异全在 harness。
2. **harness 应薄且会让位**——只守住模型短期学不会的事（provider 适配、上下文精确控制、session 持久化、工具执行、可观测性），其余交给模型/文件/bash。
3. **自延伸 = 应用层的自进化脚手架**——要新能力让 agent 自己写（Python 同构，比 Pi 的 TS 更自然），而非预置插件目录。

> 衡量标准对齐 Pi：不是堆 benchmark，而是「**能不能成为我日常顺手用的工具**」+「代码读起来像优秀软件」。

---

## 1. 二次反思结论（5 个会决定成败的判断）

1. **复刻 Pi 与 Pi 哲学本身相悖**——照功能清单搬就背叛了「不需要就不建」。→ Roadmap 强制 **M0 先出能跑的最小骨架**，所有创新显式延后并附理由。
2. **自延伸在 Python 里更自然但有坑**——agent 写 Python 跑 Python 语言同构（CodeAct 选 Python 的原因）；但 in-process 热重载不可靠 → 偏向子进程/沙箱隔离。→ 是**机会点**：把 code-as-action 做成一等公民。
3. **三个「不要自己造」**：TUI（Textual）、Provider 抽象（litellm）、异步（asyncio = Pi 的 JS event loop / AbortController 对应物）。
4. **Pi 的 YOLO 是优点也是天花板**——适合个人高信任、不适合企业。→ 创新：safety/permission/sandbox 做成**可插拔层，默认关**（保极简，留后路）。
5. **这版是「具身化前几轮结论的载体」**——要体现：薄 harness、自延伸（v1 落地基础版，eval 护栏的 DGM-lite 属 v2）、code-as-action、延迟/成本归因测量底座。

---

## 2. 忠实复刻 vs 刻意偏离（边界要先划清）

| 维度 | Pi 的做法 | μ 的选择 | 理由 |
|---|---|---|---|
| 核心 loop | 朴素 while、无 max_steps | **忠实复刻** | loop 已收敛，这是灵魂 |
| 工具集 | read/write/edit/bash 四件套 | **忠实复刻**（4 件套） | 极简哲学；缺能力现写 |
| 系统提示 | <1000 token | **忠实复刻** | 信任前沿模型 |
| 事件流 | 全 emit、全可观测 | **忠实复刻** | 拒 sub-agent 黑盒的根因 |
| 上下文管线 | transformContext→convertToLlm | **忠实复刻**（核心武器） | 精确控制进上下文的内容 |
| Session | tree（id/parentId JSONL）+ branch summary | **忠实复刻** | 分支/回溯/side-quest，且把侧分支摘要带回主线 |
| Provider 抽象 | 自建 pi-ai（4 个底层 API） | **偏离：M0 用官方 openai SDK（OpenAI 兼容端点：百炼/DeepSeek/…）；litellm 留 M1+ 接非兼容 provider** | 用户 provider 全为 OpenAI 兼容，openai SDK 更薄、依赖更少；Model 为可替换 Protocol（验收按 portable subset，见 M1） |
| TUI | 自建 pi-tui 差分渲染 | **偏离：用 Textual** | 能力等价，省巨量工程 |
| 自延伸热重载 | TS in-process hot reload | **偏离：子进程/沙箱优先** | Python in-process reload 不可靠 |
| 安全 | YOLO、无权限 | **偏离：可插拔安全层（默认关，M3.5）** | 补企业短板，不破坏极简 |
| code-as-action | 绕 bash 现写脚本 | **创新：native code tool 一等公民（M3.5）** | Python 同构；区别于 bash：in-process 调已注册工具、单轮组合多调用、共享变量状态（验收见 M3.5） |

---

## 3. 架构总览（概念分层，非文件结构）

对照 Pi 的 4 包，μ 概念上分四层（实现时是否拆包后续 plan 再定）：

```text
┌─ 前端层（可插拔）   ┐  Textual TUI / 纯 stdout / RPC(stdin-stdout JSONL)
├─ harness 层         ┤  Agent Loop + 事件流 + 上下文管线 + tree session + 自延伸
├─ 能力层             ┤  4 工具 + native code-action +（可插拔）安全/沙箱/权限
└─ transport 层       ┘  litellm 统一 provider（流式/异步/abort/成本）
```

关键架构原则（学 mini-swe + Pi）：

- **Protocol 化边界**：Model / Environment / Tool / Frontend 都是接口，可替换（local vs docker vs sandbox；stdout vs TUI）。
- **transport 抽象**：core 与前端解耦——同一个 agent 既能跑 TUI、也能 headless 被 OpenClaw 式 embed。这是 Pi「engine-and-chassis」可被别人嵌入的前提。
- **append-only + tree 双轨**：消息 append-only 保证 KV-cache/序列化确定性；session 以 tree 存（id/parentId）支持分支。
- **async-first**：核心 loop / model 调用 / tool 执行全程异步（§6-1 已锁定），从 M0 起即对齐。

---

## 4. Roadmap 阶段（M0 → M4）

> 每阶段给：目标 / 范围内 / 范围外 / 完成标志 / 为何这个顺序。完成标志是**高层验收**，非任务清单。
> **v1.1 调整**：3 个 🟢 创新已拆入真实阶段（可观测→M1，code-action 与可插拔安全→M3.5），不再笼统挂 M4；新增 **M3.5**；M4 收窄为研究级（v2）。**v1 范围 = M0–M3.5。**

### M0 — Walking Skeleton（忠实最小骨架 · async-first）

- **目标**：证明「薄 async loop + 4 工具 + litellm」端到端能解真实小任务。对标 mini-swe-agent 的 100 行，但内核异步。
- **范围内**：朴素 while loop（无 max_steps）、read/write/edit/bash、litellm 单 provider、线性消息历史、纯 stdout；**核心 loop / model 调用 / tool 执行全部 async**；bash 用 `asyncio.create_subprocess_exec/shell`（**不用**同步 `subprocess.run`）。
- **范围外**：TUI、tree session、自延伸、安全层、多 provider 切换、流式 UI。
- **完成标志**：能完成「读文件→改代码→跑测试」闭环，全程纯文本可观测。CLI 体验可同步，但内核 async-first。
- **为何 async 起步（采纳 P1-a）**：async 决策已锁定（§6-1）；M0 即对齐可省去 M1 把 streaming/abort/消息队列/事件流/并发 subprocess 全量返工的风险。

### M1 — Pi 的 harness 三件武器 + 可观测底座

- **目标**：从「能跑」升到「有 Pi 骨架质感」，并落地 v1 必做的可观测创新。
- **范围内**：
  - ① 事件流（loop 全程 emit）；
  - ② 上下文管线（transform_context / convert_to_llm 两个钩子）；
  - ③ tree session（id/parentId、JSONL、resume/branch）+ **branch summary（采纳 P2-c）**：侧分支结论以 custom message 持久化，resume/branch 时可选注入；完整 compaction 策略可缓，但**保留 summary message 类型与 context transform 钩子**；
  - ④ provider 抽象打磨（流式、asyncio abort、terminate 终止提示）；
  - ⑤ **🟢 可观测 / 延迟-成本归因底座**（事件流的自然产物：每任务拆 LLM / 工具 / 轮数 / token）。
- **范围外**：TUI、自延伸、code-action、可插拔安全层。
- **Provider 验收（采纳 P2-a，替换原「切 provider 不丢上下文」）**：μ 自有 session 格式**完整**保存；跨 provider 只承诺 **portable subset**；provider-native blob 可存并在**原 provider** 复用、**不保证迁移**；cost/token 明确为 **best-effort**，不用于精确计费。
- **完成标志**：能从任意节点分支跑 side-quest 再回溯、并把侧分支摘要带回主线；能精确裁剪/注入上下文；每任务有延迟/成本归因。
- **为何这个顺序**：三件武器是 μ 区别于「又一个 while demo」的关键，也是自延伸（往 session 存状态）与归因的地基。

### M2 — Textual 前端（可并行）

- **目标**：流畅的终端体验，验证 transport 抽象（前端只是其中一个消费者）。
- **范围内**：Textual app、流式 Markdown、reactive 状态、Pilot 测试；保留纯 stdout / RPC 模式不丢。
- **范围外**：Web UI（含 `textual serve`，显式延到 v2）、复杂主题系统。
- **完成标志**：TUI 与 headless 模式共享同一 core，互不耦合。
- **为何可并行**：前端是叶子节点，不阻塞 harness 演进；但建议在 M1 之后做，避免给未稳定的 core 套壳。

### M3 — 自延伸基座（杀手锏，适配 Python）

- **目标**：复刻 Pi 最独特能力——agent 自写扩展、注册工具、状态存 session。
- **进入门禁（采纳 P1-c，roadmap 只列维度、不展开实现，留待 M3 plan 定）**：最小扩展协议须先定——
  - ⓐ **manifest**：名 / 版本 / 入口 / 权限需求 / 工具声明；
  - ⓑ **tool schema 注册协议**：扩展如何向 core 暴露工具；
  - ⓒ **IPC**：默认 JSONL stdin/stdout（便于观测 / 调试 / 沙箱化）；
  - ⓓ **session state 协议**：custom message 或 dedicated state event；
  - ⓔ **reload 生命周期**：load / healthcheck / reload / unload / error；
  - ⓕ **错误与日志回流事件流**：不让扩展变成新黑盒（对齐 Pi 反黑盒哲学）。
- **范围内**：扩展 API（注册工具 / 渲染组件 / 持久化 session 状态）；**最小执行隔离 primitive**（默认**子进程**隔离，in-process reload 作可选 fast-path 并标注 caveat）；自延伸闭环（写→载→测→迭代）；随仓库附文档 + 示例供 agent 自参考。
- **范围外**：完整可插拔安全/权限层与托管沙箱 provider（→ M3.5，本阶段仅 subprocess 级隔离）；扩展市场/分发；复杂依赖管理。
- **完成标志**：agent 现场写一个新工具（如「用 CDP 截网页」）、加载、用、把配置存 session，下次 resume 仍在；扩展的错误/日志在事件流可见。
- **为何放这**：依赖 M1 的 session 状态持久化与事件流；是「应用层自进化脚手架」落点。

### M3.5 — 三个绿色创新收尾（v1 收口，采纳 P1-b 拆分）

- **目标**：落地 v1 锁定的 🟢 创新中、依赖自延伸基座的两项（可观测已在 M1）。
- **范围内**：
  - ① **🟢 Native code-as-action**（一等公民，可关闭实验能力）。**与 bash 的边界 / 验收标准（采纳 P2-b）**：能在一次 model round-trip 内组合多个工具调用；能用 Python 控制流表达循环 / 条件 / 并发；受 sandbox/permission 层约束；被事件流与延迟/成本归因观测；**在真实任务中相比 bash 降低 round count 或 token/tool-call 开销**——否则即「第二套 bash」，应砍。
  - ② **🟢 可插拔安全 / 权限 / 沙箱层（默认关）**：在 M3 的 subprocess 隔离之上，抽象 permission 策略与可插拔 sandbox provider（subprocess / E2B / Modal）；默认关时退回 Pi 等价 YOLO。
- **范围外**：DGM-lite、程序性记忆、投机/异步（→ M4）。
- **完成标志**：两项均可**独立开关**；关闭后 μ 退回 M3 形态；code-action 在至少一个真实任务上对 bash 有可测量收益。
- **为何独立成阶段**：两项依赖 M3 的隔离/协议基座；单列便于「关掉即退回极简」的验收。

### M4 — 研究级扩展（v2，M4.1 eval 护栏已硬化）

- **目标**：在稳定极简核上叠加高不确定性能力；先落 eval 护栏，再谈自进化。
- **已完成（M4.0）**：库内 eval runner + basic coding suite + DGM-lite 候选隔离验证 / append-only archive。通过项只归档，不自动应用回主仓库。
- **已完成并验收（M4.1）**：eval 路径绝对化、validator pytest rootdir/test file 固定、secret scan（过程产物 + workspace 内真实 env secret 精确匹配）、full gate 入口。M4.0 回归 WARN 作为历史记录保留，M4.1 专门修复；验收见 `评测/2026-6-12-02-m4.1-real/` 与 `评测/2026-6-12-02-m4.1-real-cli/`。
- **后续范围**：🟡 程序性记忆 / meta-tool 编译热路径；🟠 投机/异步执行（隐藏工具等待）；自动应用通过候选。
- **完成标志**：每项可独立开关，关掉后退回 v1 形态；M4.1 已具备更稳定的 eval 护栏和候选 archive 基座，后续能力必须先过 eval / full gate。
- **为何最后**：高不确定、依赖前序基座；M4.0 只做护栏与归档底座，避免直接进入自动自改主仓库。

---

## 5. 创新项分级（落点已对齐阶段）

| 创新 | 等级 | 落点 | 说明 | 关联前序调研 |
|---|---|---|---|---|
| **内置可观测 / 延迟-成本归因底座** | 🟢 v1 | **M1** | 事件流自然产物：每任务拆 LLM/工具/轮数/token | What Limits Agentic Efficiency |
| **Native code-as-action**（不绕 bash） | 🟢 v1 | **M3.5** | 验收须对 bash 有可测量收益，否则砍 | CodeAct / smolagents / Code Mode |
| **可插拔安全/权限/沙箱层（默认关）** | 🟢 v1 | **M3.5** | 建在 M3 subprocess 隔离之上；默认关退回 YOLO | Pi 局限分析 |
| **DGM-lite：扩展提案经 eval 护栏自改进** | 🟡 v2 | **M4.1 ✅ eval 护栏已硬化** | 已落 eval runner + 候选隔离 + append-only archive + full gate；自动应用留后续 | Darwin Gödel Machine + 程序性记忆 |
| **程序性记忆 / meta-tool 编译热路径** | 🟡 v2 | **M4 后续** | 把高频自延伸路径固化为确定性工具 | ProcMEM / AWM / meta-tools |
| **投机/异步执行（隐藏工具等待）** | 🟠 v2 | **M4 后续** | 收益依赖可预测性 + 副作用边界；复杂度高 | PASTE / Speculative Actions |

> 原则：**🟢 做（v1）、🟡 留接口（v2）、🟠 只记录不实现（v2）**。任何一项都必须可独立关闭。

---

## 6. 关键决策点（已定稿 2026-06-11）

| # | 决策 | 结论 | 状态 |
|---|---|---|---|
| 1 | 同步 vs 异步核 | **asyncio-first**；**M0 即 async**（bash 用 asyncio subprocess），流式/abort/消息队列/投机异步自然 | ✅ 用户锁定 |
| 2 | Session：tree vs 线性起步 | **M0 线性、M1 升 tree + branch summary**（分阶段降风险） | ◻️ 默认推荐（未异议） |
| 3 | 自延伸隔离粒度 | **默认子进程**（M3 最小隔离）；in-process reload 作可选 fast-path；完整可插拔沙箱在 M3.5 | ✅ 用户锁定 |
| 4 | 创新范围 | **v1 = M0–M3.5**（3 个 🟢 已拆入 M1 与 M3.5）；🟡/🟠 留 v2(M4) | ✅ 用户锁定 |
| 5 | 是否保留 RPC/embed 模式 | **保留**（transport 抽象成本低、价值大，支持 OpenClaw 式嵌入） | ◻️ 默认推荐（未异议） |

> 含义：全链路 asyncio（含 M0）；M0 线性历史起步、M1 升 tree session + branch summary；自延伸 M3 默认 subprocess 隔离、M3.5 上可插拔安全层；v1 范围 = M0–M3.5（含 3 个 🟢，分落 M1 与 M3.5，每项可独立关闭）；保留 headless/RPC 以便被嵌入。

---

## 7. 非目标（Pi 哲学的硬性 not-to-do）

- ❌ 内置 To-Do / Plan Mode（用文件）
- ❌ 内置 MCP（需要时扩展或现写脚本）
- ❌ 内置 sub-agent（递归 spawn 自己，保可观测）
- ❌ 默认开启权限/安全护栏（可插拔、默认关）
- ❌ 冗长系统提示（<1000 token）
- ❌ 为「显得高级」而引入框架（litellm/Textual 之外不再叠框架）

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| Feature creep（最大风险，和 Pi 哲学冲突） | M0 硬门禁；创新全部可插拔、默认关 |
| litellm 归一化丢失 provider 保真（如 thinking trace） | M1 验收只承诺 portable subset；native blob 仅原 provider 复用；必要时单 provider 走原生 SDK |
| in-process 热重载不可靠 | M3 默认子进程隔离 |
| native code-action 沦为「第二套 bash」 | M3.5 验收强制要求对 bash 有可测量收益，否则砍 |
| 自延伸扩展成为新黑盒 | 错误/日志强制回流事件流；扩展 IPC 走 JSONL 可观测 |
| 自延伸执行不可信代码 | M3 提供 subprocess 隔离；M3.5 提供可插拔 sandbox provider |
| asyncio 心智负担拖慢迭代 | 全链路统一 async 约定；M0 即对齐避免半途迁移 |
| 「复刻」变「平庸克隆」 | 始终对齐 North Star 的三件验证，而非功能对齐 |

---

## 9. 资料索引

| 资料 | 用途 |
|---|---|
| [mini-swe-agent（GitHub）](https://github.com/SWE-agent/mini-swe-agent) · [架构 DeepWiki](https://deepwiki.com/SWE-agent/mini-swe-agent/1.1-architecture-overview) | Python 极简 agent 惯用法：Protocol 抽象、线性历史、subprocess、异常驱动 |
| [litellm（GitHub）](https://github.com/BerriAI/litellm) · [流式+异步](https://docs.litellm.ai/docs/completion/stream) · [成本追踪](https://docs.litellm.ai/docs/proxy/cost_tracking) | Provider 抽象（pi-ai 对应物） |
| [Python asyncio subprocess](https://docs.python.org/3/library/asyncio-subprocess.html) | M0 async bash 执行 |
| [Pydantic AI](https://ai.pydantic.dev/) | 对比项 + Code Mode 参考（不直接采用框架） |
| [Textual（GitHub）](https://github.com/Textualize/textual) · [高性能终端算法](https://textual.textualize.io/blog/2024/12/12/algorithms-for-high-performance-terminal-apps/) | TUI（pi-tui 对应物）：流式 Markdown、segment 合成器、Worker、Pilot |
| [jurigged](https://github.com/breuleux/jurigged) · [importlib 指南](https://docs.python.org/3/library/importlib.html) | 热重载现实与坑 |
| [E2B / Firecracker sandbox 综述](https://www.firecrawl.dev/blog/ai-agent-sandbox) · [Modal × Pydantic AI](https://modal.com/resources/best-code-execution-sandbox-pydantic-ai) | 自写代码的安全隔离执行 |
| [CodeAct](https://arxiv.org/abs/2402.01030) · [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954) · [PASTE / Act While Thinking](https://arxiv.org/abs/2603.18897) | 创新项理论依据 |

---

## 10. 下一步（不在本轮执行）

§6 的 5 个决策点已定稿（3 项用户锁定 + 2 项默认推荐未异议）。后续顺序：① 针对 **M0** 出一份**实施 plan**（含目录/接口/任务拆分/验收用例）→ ② plan 评审通过后再写代码。本轮到 roadmap 修订为止。

---

## 11. 审批修订记录（v1 → v1.1）

| 报告发现 | 严重度 | 是否真实 | 处置 |
|---|---|---|---|
| P1-a：async-first 与 M0 `subprocess.run` 冲突 | P1 | ✅ | **采纳**：M0 改 async-first，bash 走 asyncio subprocess（§4 M0 / §6-1） |
| P1-b：v1 范围与 M4 安排冲突 | P1 | ✅ | **采纳 + 校正**：3 个 🟢 拆入 M1/M3.5；新增 M3.5；M4 收窄为 v2 研究级；并拆分「M3 最小子进程隔离」与「M3.5 可插拔安全层」（§4/§5/§6-4） |
| P1-c：自延伸缺扩展协议/manifest | P1 | ✅ | **采纳（高度校正）**：作为 **M3 进入门禁**列出 manifest/IPC/schema/state/reload/错误回流 6 维度，不在 roadmap 展开实现（§4 M3） |
| P2-a：litellm 验收过强 | P2 | ⚠️ 部分 | **采纳**：改为 portable subset / native blob 仅原 provider 复用 / cost best-effort（§4 M1） |
| P2-b：code-action 与 bash 边界不清 | P2 | ✅ | **采纳**：M3.5 加可验证收益标准，否则砍（§4 M3.5 / §8） |
| P2-c：tree session 缺 branch summary | P2 | ✅ | **采纳**：M1 加 branch summary（custom message + transform 钩子）（§4 M1 / §2） |

> 我对报告的 2 处反向校正：① P1-c 不把协议全量写进 roadmap（plan 级），仅设为 M3 门禁；② 报告将「可插拔安全层」笼统前置 M3，实拆为 M3 前置依赖（subprocess 隔离）+ M3.5 绿色创新（可插拔 sandbox provider）。

---

## 12. 实施进展

| 阶段 | 状态 | 说明 |
|---|---|---|
| **M0 Walking Skeleton** | ✅ 已完成（2026-06-11） | 包 `mu/`（async-first loop + 4 工具 + 官方 openai SDK + 线性历史 + stdout）；plan 见 `plan/M0-Walking-Skeleton-plan.md`；离线闭环 e2e（write→write→bash(pytest)）跑通。传输层按用户要求用 openai SDK 对接 OpenAI 兼容端点（百炼/DeepSeek），细化自 roadmap 原定 litellm（见 §2）。代码评审修复 bash timeout 进程组清理 + README/.env 一致性，**21 单测通过**。 |
| **M1 Harness Core + 可观测** | ✅ 已完成 + 评审修复（2026-06-11） | 新增 `events/session/context/observability/render`，改造 `model/tools/agent/cli`；落地事件流、上下文管线、tree session（JSONL + 分支/续跑/侧分支摘要）、可选流式 + abort + terminate、延迟-成本归因报告。plan 见 `plan/M1-Harness-Core-plan.md`。**代码评审已修**：P1 取消时补全 tool 结果（session 可 resume）、P2 实现 `Agent.summarize_branch`+`Session.path_to`、P3a 归因 collector RunStarted 重置、P3b 版本同步 0.1.0。**41 单测通过**；离线集成验证（renderer+归因+持久化+resume+branch summary）通过；M0 闭环回归保持绿。两项决策：session=`./.mu/sessions/`（MU_SESSION_DIR 可覆盖）、流式默认 off（`--stream` 开）。 |
| **M2 Textual 前端** | ✅ 已完成（2026-06-11） | 新增 `tui.py`（`MuApp` + `TuiRenderer`，事件流的又一个消费者），`cli.py` 加 `--tui`，零改 core；流式 live 区 + 归因 tally。Pilot 离线测试。两项决策：`--tui` 显式启动（headless 默认）、textual 可选 extra `[tui]`。修复一处真 bug：自定义 `_running` 字段与 Textual `App._running` 冲突致交互输入永不响应（已重命名）。44 单测通过。plan 见 `plan/M2-Textual-Frontend-plan.md`。 |
| **M3 自延伸基座** | ✅ 已完成（2026-06-11） | 新增 `extension.py`（`ExtensionManager`：子进程 + JSONL IPC + 工具动态注册 + 生命周期 + 状态持久化）、`extsdk.py`（`@tool`/`run_extension` SDK）、`extensions/`（示例+文档）；`tools.py` 加 register/unregister，`agent.py` owns manager + autoload + aclose，events/render/prompts/cli/tui 接线。最小扩展协议（manifest/IPC/state/reload/错误回流事件流）落地；扩展状态存 session、`--resume` 恢复；`./.mu/extensions/` 自动加载。**子进程=崩溃隔离，非安全沙箱（→M3.5）**。53 单测通过（+9 扩展测试，含 FakeModel 自延伸闭环）。两决策：子进程隔离 + 自动加载目录、系统提示加自延伸提示。plan 见 `plan/M3-Self-extension-plan.md`。 |
| **M3.5 code-action + 可插拔安全/沙箱** | ✅ 已完成（2026-06-12）→ **v1 完整** | 新增 `permission.py`（allow/readonly/workspace 策略，钩在 `ToolRegistry.execute` 单一入口 gate 内置/扩展/code-action 内层）、`codeact.py`（`code` 工具：进程内 exec + `mu.*` 线程↔事件循环桥，一次组合多工具）、`environment.py` 加 `Environment` Protocol + `make_environment` + 实验性 `DockerEnvironment`；`agent/cli/tui/prompts` 接线 `--code/--permission/--sandbox`（均默认关=YOLO，关掉退回 M3）。**65 单测通过 + 1 skipped（docker）**；code-action 离线收益验证（1 轮 code 跑 3 次内层 read vs 3 轮）。决策：code-action 进程内 exec（用户锁定）、三项默认全关、沙箱=抽象+本地+Docker 实验。边界：进程内 exec 同 bash 风险、真隔离靠跑容器；E2B/Modal 仅留接口。plan 见 `plan/M3.5-CodeAction-Sandbox-plan.md`。**代码评审已修**：P1-a 权限层从「工具名黑名单」升级为**capability gating**（readonly/workspace 真正拦住 code/extension/bash，autoload 在 restrictive 策略下跳过）；P1-b code 超时改为诚实 soft-timeout（消息 + 取消 token 阻断滞留线程的 mu.* 调用；直 I/O 仍不可硬停，已文档化）；P2 Docker 加 `--network none` + 文件 IO 限制如实标注；P3 pyproject 同步 M3.5。**72 单测通过 + 1 skipped**。 |
| **M4.0 Eval + DGM-lite 基座** | ✅ 已完成 + 回归评测 WARN（2026-06-12） | 新增 `mu.eval`（库内 eval API + `python -m mu.eval`，复用基础 coding suite、独立 workspace、外部 validator、summary redaction）与 `mu.dgm`（候选 workspace 复制隔离、`.mu/extensions` / `.mu/prompts` / `extensions` 范围限制、append-only archive + latest summary、best 标记）。Prompt 候选通过 `MU_PROMPT_SNIPPET_DIR` / `.mu/prompts` 片段注入，不直接改 `SYSTEM_PROMPT`；通过项只归档，不自动应用主仓库。plan 见 `plan/M4.0-Eval-DGM-lite-plan.md`。回归评测见 `评测/2026-6-12-01/`：全量离线 `83 passed, 1 skipped`；DGM-lite archive smoke `3/3 PASS`；真实模型 basic eval 产物最终 `3/3` 可验证通过，但原始 full run 暴露 `mu.eval` validator / 相对路径稳定性问题（需后续修复路径绝对化与 pytest rootdir/test file 指定）。 |
| **M4.1 Eval Hardening** | ✅ 已完成并验收（2026-06-12） | 在 M4.0 基座上修复回归 WARN：`run_root` / workspace / summary / prompt / `EvalResult` 路径统一绝对化；validator pytest 显式 `--rootdir <workspace>` 并指定任务测试文件；新增 `scan_eval_artifacts_for_secrets`（扫描过程产物与 summary，默认忽略复制 workspace fixture 中的 `sk-...` 假 key，但 workspace 内真实 env secret 精确值会失败）；`python -m mu.eval` 输出绝对 run dir、`Passed: x/y`、secret scan；新增 `python -m mu.eval_gate` 串联离线 pytest、真实 basic eval、DGM-lite fake-agent smoke 并写入 `评测/<date-run>/`。验收结果：全量离线 `87 passed, 1 skipped`；真实 full gate 见 `评测/2026-6-12-02-m4.1-real/`，overall PASS、secret scan PASS、真实 basic eval `3/3`、DGM smoke PASS；原始 CLI full run 见 `评测/2026-6-12-02-m4.1-real-cli/real-eval-runs/`，直接 `3/3 PASS` 且 secret scan PASS。plan 见 `plan/M4.1-Eval-Hardening-plan.md`。 |
| **v2（M4 后续，研究级）** | ⏳ 未开始 | 程序性记忆/meta-tool 编译、投机/异步执行、自动应用通过候选等高不确定能力。 |
