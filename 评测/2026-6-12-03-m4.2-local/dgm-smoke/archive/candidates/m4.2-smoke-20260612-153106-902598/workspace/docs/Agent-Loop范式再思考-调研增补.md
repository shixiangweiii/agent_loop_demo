# Agent-Loop 范式再思考 · 调研增补

> 调研时间：2026-06-11
> 定位：作为《Agent-Loop 性能优化调研汇总》《Agent-Loop 范式深度调研报告》两份报告的**增补稿**。
> 出发点：问题从「如何缓解串行 LLM 调用的延迟」抬升到「**如何在 agent-loop 基础上进一步优化、乃至探索新的 agent-loop 范式**」。延迟只是其中一个切面。
> 一句话结论：**loop 这个控制结构已基本收敛**。纯控制流层面的创新边际收益快速递减；真正的范式迁移发生在「loop 内化进权重」和「推理并行/潜空间化」——而这两条应用层都不可控。对应用层（黑盒 API）而言，真正有跑道的是**自进化脚手架**与**投机/异步执行**，以及把 harness 重构成一个**会优雅让位的薄壳**。

---

## 0. 核心重构：把「优化 loop」拆成 5 条隐含假设

经典 loop 只有一行：

```text
while not done:  action = LLM(state);  state = env(action)
```

这一行里藏着 5 条隐含假设。每松开一条，就得到一种「新 loop」。整个 2026 领域其实在沿这 5 个轴同时推进：

| # | 隐含假设 | 松开它 → 新范式 | 代表工作 | 应用层可落地？ |
|---|---|---|---|---|
| 1 | 控制流在外部 harness | 控制流**内化进权重**（model-native） | Beyond Pipelines 综述、Agentic RL、DeepSWE | ❌ 只有训模型的人能做，但必须为它设计 |
| 2 | loop 结构固定 | loop **自我改写 / 进化** | Darwin Gödel Machine | ✅ 进化脚手架而非权重，可做且新颖 |
| 3 | 推理是串行 token | **并行 / 潜空间推理** | Hogwild!、latent/superposition reasoning | ❌ 推理引擎/模型厂层，自托管才用得上 |
| 4 | 每步同步（算完→等工具→再算） | **投机 / 异步重叠** | PASTE、Speculative Actions、Auton | ✅ 可做（sidecar） |
| 5 | loop 是扁平 ReAct | **结构化 / 认知架构控制** | CoALA、Plan-Execute、Graph、Policy-driven | ✅ 可做，但收益递减 |

> 两份原报告主要打透了 **轴 4、轴 5 的工程层**（并行、批处理、ReWOO / Plan-Execute、反思变体）。本增补补齐 **轴 4 的投机执行** 这条空白，并指出 **轴 1、轴 2** 才是真正的范式迁移。

---

## 1. 先校准延迟问题：瓶颈未必在 LLM

实测论文 **What Limits Agentic Systems Efficiency?**（UW-Madison, arXiv:2510.16276）把端到端延迟拆成 **LLM API 延迟** 和 **环境（工具/web）延迟** 两块：

- 在 web-interactive agent 里，**环境延迟最多占到 53.7%**。
- 两个结构性瓶颈：**轮数（number of rounds）** 与 **每轮生成的 token 数**。
- 其提出的加速技术 SpecCache：o4-mini 作 target、GPT-4.1-mini 作 draft。

**含义**：「串行 LLM 调用慢」常常只是表象，约一半时间卡在工具/IO 等待上。任何优化前应**先建延迟归因测量底座**（把每个任务拆成 LLM API / 工具等待 / 轮数 / 每轮 token），否则不知道自己是 LLM-bound 还是 tool-bound。

- 链接：<https://arxiv.org/abs/2510.16276>
- 相关：Efficient Agents（arXiv:2508.02694）——指出 DeepResearch/Manus 类产品因「爆炸式 LLM 调用」（单任务数百次 API 调用）而运营成本高到不可持续。<https://arxiv.org/pdf/2508.02694>

---

## 2. 原报告漏掉的一整条轴：投机执行 / 乐观并发（轴 4）

把 CPU 的**分支预测 / 投机执行**搬进 Agent runtime：在 LLM 还没确认下一步时，**乐观地预测并预执行**很可能要调的工具，把工具等待时间藏到模型思考时间背后。这是 2026 上半年最活跃、且直接对着「串行延迟」打的前沿。

**三条正交的延迟优化轴：**

| 轴 | 思路 | 原报告覆盖 |
|---|---|---|
| 减少串行步数 | ReWOO / LLMCompiler / CodeAct / Batch | ✅ |
| 压缩每步开销 | Caching / Compaction / Tool RAG / Effort / Cascade | ✅ |
| **跨步重叠 / 隐藏延迟（投机）** | 乐观预测 + 预执行，把工具等待藏到模型思考背后 | ❌ 本增补补齐 |

**代表工作：**

- **PASTE / Act While Thinking**（Microsoft, arXiv:2603.18897）——洞察：agent 请求语义虽千差万别，但**控制流稳定**（重复出现的 tool-call 序列）、**数据依赖可预测**（参数从上一个工具输出派生）。在 LLM 思考期预测并预执行工具。实测 **任务完成时间降 48.5%、工具吞吐 1.8×**，调度开销 <100ms，**可当 sidecar 挂在现有 runtime 旁边**。<https://arxiv.org/abs/2603.18897>
- **Speculative Actions: A Lossless Framework**（arXiv:2510.04371）——把投机推广到整个 agentic stack：不只投机 LLM 调用，还投机内部/外部工具、MCP server、甚至**人类响应**（human-as-an-API）。用快模型 draft 出响应并行预启动下一步，**commit/rollback 保正确性（lossless）**。<https://arxiv.org/pdf/2510.04371>
- **Optimizing Agentic Inference via Speculative Tool Calls**（arXiv:2512.15834）——给了 client-side（兼容黑盒 API）与 engine-side 两套算法，并提供**解析模型**判断投机何时划算：取决于 *工具延迟 × 投机模型准确率 × decode 成本*。<https://arxiv.org/pdf/2512.15834>
- **Auton 的 Speculative Runtime**（arXiv:2602.23720）——三阶段 predict → lookahead → commit/rollback。安全变体：**不真执行工具，而是先「预测工具输出」**（成功标志 / schema 占位 / 最可能输出），等真结果回来再 commit。规避副作用的关键设计。<https://arxiv.org/pdf/2602.23720>
- 其它：Dynamic Speculative Agent Planning（arXiv:2509.01920）、清华 Reducing Latency of LLM Search Agent via Speculation、May-2026 的 IdleSpec（用工具等待的 idle 时间预规划多个候选下一步）、知识库条目 <https://agentwiki.org/speculative_tool_execution>。

**三个硬约束（不想清楚就只是漂亮 demo）：**

1. **可预测性是前提，不是普适。** 适合稳定控制流 + 参数可派生的场景（检索、RAG、Deep Research、固定 API 编排）；不适合下一步强依赖真实观察的场景（交互式改代码、Debug、GUI 点击）——预测命中率一掉，投机全是浪费算力。与 ReWOO「不适合强交互环境」同构。
2. **副作用是安全红线。** 预执行有副作用的工具（写文件、发邮件、付款）危险。两种安全姿势：(a) 只对**幂等/只读**工具真执行；(b) 对有副作用工具走 Auton 式「只预测输出、不真执行」。
3. **它是拿成本换延迟，不是免费午餐。** 投机产生被丢弃的浪费调用；用 2512.15834 的解析模型先算盈亏平衡点再上，否则延迟降了账单涨了。

---

## 3. 两个真正的范式级转变（不是调参）

### 3.1 轴 1：loop 正在「沉」进模型权重（model-native）

2026 最强信号。**Beyond Pipelines: A Survey of the Paradigm Shift toward Model-native Agentic AI**（arXiv:2510.16720）判断：planning / tool-use / memory 正从「外部脚本模块」迁移成「端到端学到的行为」，RL 是引擎，DeepSeek-R1 是转折点。

判定真 agent 的试金石：**「它能不能自己掌控执行轨迹？」** wrapper 的控制流在外部（开发者写死序列），真 agent 的控制流在内部。

**对应用层的尖锐含义**：今天在 harness 里手写的很多 loop 逻辑（何时 plan、何时反思、何时停），未来会被模型 RL 内化掉。mini-SWE-agent（100 行）、Pi（4 工具）能打 SOTA，正是因为前沿模型已把 loop 策略学进权重——这是原报告 2 观察到的现象，本条给了机制解释。

- 链接：<https://arxiv.org/pdf/2510.16720>

### 3.2 轴 2：loop 自我进化（self-evolving）

**Darwin Gödel Machine（DGM）**（arXiv:2505.22954, ICLR 2026；Sakana AI × Jeff Clune 实验室）把 loop 从「固定结构」变成「**对 agent 设计本身的开放式搜索**」：

- agent 读改自己的 Python 代码，用 coding benchmark **经验性验证**每次改动（替代 Gödel Machine 原始的「形式化证明」假设）。
- 维护一个**多样性 archive**（非贪心，保留非最优个体），形成不断生长的 agent 进化树。
- 性能自己长出来：一个 benchmark 20%→50%，另一个 14.2%→30.7%；**给越多算力涨得越多**。
- 它自己发现的改进包括「加 patch 校验步」「更好的文件查看」「生成并排序多个解」「加失败历史」——正是人类工程师手写 harness 时会加的东西。

**含义**：**harness 工程本身可以被自动化。** 而且关键在于——进化的是脚手架/prompt/工具，**不是权重**，所以应用层可做。

- 链接：<https://arxiv.org/abs/2505.22954> · <https://sakana.ai/dgm/>

---

## 4. 并行 / 潜空间推理（轴 3，主要供了解）

- **Hogwild! Inference**（arXiv:2504.06261）——多个 LLM worker 共享同一个 concurrent attention KV-cache，用 RoPE 拼接不同顺序的 KV 块免重算，worker 能「看到」彼此进度自发协作。关键：QwQ / DeepSeek-R1 **无需微调**即可共享 KV cache 协作。<https://arxiv.org/abs/2504.06261>
- **Adaptive Parallel Reasoning**（BAIR, 2026-05）——综述 GroupThink、Hogwild，批评现有方法「是否并行/并行多少」是外部强加，而非模型自适应决定。<https://bair.berkeley.edu/blog/2026/05/08/adaptive-parallel-reasoning/>
- **Latent / 连续推理**——looped 架构在潜空间内模拟 CoT；**superposition 机制**让模型在连续潜空间维持多条推理轨迹叠加，实现隐式并行思考。权衡：潜空间利于并行，但离散 CoT 在需要随机解码探索的任务上仍更优。
- **Mirror Speculative Decoding**（Apple, 2026-01）——draft 与 target 互为投机，2.8–5.8× 加速。<https://machinelearning.apple.com/research/mirror>

> **判断**：这一条操作 KV-cache，依赖**自托管开源模型**；走 Claude/OpenAI 黑盒 API 用不上，属模型 serving 层。知道即可，应用层优先级低。

---

## 5. 程序性记忆 / 工作流编译（与轴 2 互补：从「跑得快」到「不用跑」）

不是把 loop 跑快，而是把高频 loop 整段消除。

- 核心区分：**episodic memory**（检索历史，决策时仍要重推理）vs **procedural memory**（把「情境→动作」编码成可直接执行的技能，跳过重推理）。
- 最对口的概念是 **meta-tools**：把反复出现的 tool-call 序列**固化成确定性复合工具**，跳过中间 LLM 推理步。
- 代表作：ProcMEM（arXiv:2602.01869）、Hierarchical Procedural Memory / MACLA（arXiv:2512.18950, AAMAS'26）、Agent Workflow Memory（OpenReview NTAhi2JEEE）、Memp、Preping（arXiv:2605.13880，部署前主动构建记忆）。
- 这是原报告「阶段三：沉淀 workflow/skill」的学术化、可量化版本，且是**热路径上收益最稳的**（编译过的工作流近似 lossless，能把热路径轮数压到接近 0）。

---

## 6. 对应用层最关键的判断：你站在「harness / model」边界的哪一侧

5 条轴里有一半不是应用层能做的。对黑盒 API 应用层而言，**真正有跑道的不是发明第 N 种 ReAct 变体（轴 5 已拥挤、收益递减），而是这三件事：**

### 6.1 把 harness 设计成一个「会让位」的薄壳

既然 loop 在往权重里沉，最聪明的赌注不是把今天的 loop 打磨到极致，而是**让 harness 只负责模型短期内学不会的那部分**：

- 环境 / 工具集成
- 治理与安全（lethal trifecta、权限、审计、幂等）
- 持久化记忆
- 多 agent 协调
- 可观测性

模型越强，harness 应越薄、越往这几件事收缩。这是原报告 2「Harness > Loop」结论的**动态升级版**：harness 不只是更重要，它是一个**边界在移动、正在缩小的靶子**。

### 6.2 自进化脚手架（DGM 思路降维到应用层）—— 最新颖且可落地的「新 loop」

不训权重，而是让 agent 在 **eval 护栏**下**自己提议并验证对自己 prompt / 工具 / 工作流的改进**，把验证通过的高频路径**编译成确定性 meta-tool / 程序性记忆**。这等于把 **DGM + 程序性记忆 + 投机** 缝成一个东西：**一个会自己长出 fast-path、并把 fast-path 固化下来的 loop**。它同时改善延迟、成本、可靠性，且应用层能完全掌控。

### 6.3 结构化控制只在「单 while 撑不住时」才上

- CoALA（arXiv:2309.02427）的价值边界很诚实：当 harness 长出**多 session、持久状态、记忆写入、定时任务**时，其内部/外部 action 划分与四类记忆才值回票价；对单轮任务它描述的是不存在的阶段。<https://arxiv.org/abs/2309.02427>
- Plan-Execute 同理：对良定义多步任务更优，对探索型任务 ReAct 仍更好（Web Agents Should Adopt Plan-Then-Execute, arXiv:2605.14290）。
- **别为了「新范式」而结构化，按任务形态选型。**

---

## 7. 最终结论

> **「探索新 agent-loop」这个目标本身需要打问号。** 证据（Pi、mini-SWE-agent、model-native 趋势）共同指向：**loop 这个控制结构已基本收敛**，纯控制流层面的创新（又一种反思/规划变体）边际收益在快速递减。真正的范式迁移发生在两个应用层控制不了的地方——loop 内化进权重、推理走向并行/潜空间。
>
> 对应用层而言，最有杠杆的不是「发明新 loop」，而是：
> 1. 顺着「边界在移动」这件事，把 harness 重构成**会优雅让位、只守住模型学不会的那几件事的薄壳**；
> 2. 在应用层落地唯一两条还有真跑道的新范式——**自进化脚手架 + 投机/异步执行**——它们能在不碰权重的前提下，把 loop 从「每次重新推理」变成「会自己编译 fast-path、会把工具等待藏起来」的 loop；
> 3. 动手前先建**延迟/成本归因测量底座**，判断瓶颈到底在 LLM 还是环境。

---

## 8. 资料索引（本增补新增，与原两份报告去重）

### 投机 / 异步执行（轴 4）

| 资料 | 重点 |
|---|---|
| [PASTE / Act While Thinking（2603.18897）](https://arxiv.org/abs/2603.18897) | 模式感知投机工具执行，任务时间 −48.5%，可 sidecar |
| [Speculative Actions: A Lossless Framework（2510.04371）](https://arxiv.org/pdf/2510.04371) | 投机推广到整个 stack，commit/rollback 保正确 |
| [Optimizing Agentic Inference via Speculative Tool Calls（2512.15834）](https://arxiv.org/pdf/2512.15834) | client/engine 两套算法 + 盈亏平衡解析模型 |
| [Auton Speculative Runtime（2602.23720）](https://arxiv.org/pdf/2602.23720) | predict→lookahead→commit；「只预测输出」规避副作用 |
| [Speculative Tool Execution 知识库](https://agentwiki.org/speculative_tool_execution) | 概念综述 |

### 范式迁移（轴 1 / 轴 2）

| 资料 | 重点 |
|---|---|
| [Beyond Pipelines: Model-native Agentic AI（2510.16720）](https://arxiv.org/pdf/2510.16720) | 能力从外部脚手架内化进权重，RL 为引擎 |
| [Darwin Gödel Machine（2505.22954）](https://arxiv.org/abs/2505.22954) · [Sakana](https://sakana.ai/dgm/) | 自改写代码 + 经验验证 + 多样性 archive |

### 并行 / 潜空间推理（轴 3）

| 资料 | 重点 |
|---|---|
| [Hogwild! Inference（2504.06261）](https://arxiv.org/abs/2504.06261) | 共享 concurrent attention KV-cache 的并行推理 |
| [Adaptive Parallel Reasoning（BAIR）](https://bair.berkeley.edu/blog/2026/05/08/adaptive-parallel-reasoning/) | 自适应并行推理综述 |
| [Mirror Speculative Decoding（Apple）](https://machinelearning.apple.com/research/mirror) | draft/target 互投机，2.8–5.8× |

### 系统瓶颈 / 成本

| 资料 | 重点 |
|---|---|
| [What Limits Agentic Systems Efficiency?（2510.16276）](https://arxiv.org/abs/2510.16276) | 延迟归因：环境延迟最多占 53.7%；轮数与每轮 token 是瓶颈 |
| [Efficient Agents（2508.02694）](https://arxiv.org/pdf/2508.02694) | 单任务数百次 API 调用，成本不可持续 |

### 程序性记忆 / 结构化控制（轴 5 + 记忆）

| 资料 | 重点 |
|---|---|
| [CoALA（2309.02427）](https://arxiv.org/abs/2309.02427) | 认知架构：内部/外部 action + 四类记忆 |
| [Web Agents Should Adopt Plan-Then-Execute（2605.14290）](https://arxiv.org/html/2605.14290) | Plan-Execute 的安全/效率论证 |
| [ProcMEM（2602.01869）](https://arxiv.org/pdf/2602.01869) | 非参数 PPO 学可复用程序性记忆 |
| [Hierarchical Procedural Memory / MACLA（2512.18950）](https://arxiv.org/html/2512.18950v1) | 外部构建程序性 + 元程序性记忆 |
| [Agent Workflow Memory](https://openreview.net/forum?id=NTAhi2JEEE) | 工作流记忆奠基工作 |
