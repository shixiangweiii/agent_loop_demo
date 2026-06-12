# μ (mu) — minimal Pi-style coding agent · M4.1 eval hardening

按 Pi 的实现思路用 Python 复刻的极简 coding agent：一个**薄 async loop** + **四个工具**（read / write / edit / bash）+ **原生 function-calling** + **OpenAI 兼容**模型后端（百炼/DashScope、DeepSeek、OpenAI…）。

进度（见 `docs/Python复刻Pi-Roadmap-v1.md`）：
- **M0** walking skeleton：薄 async loop + 四工具 + 线性历史 + 纯 stdout。
- **M1** harness 三件武器 + 可观测：**事件流**、**上下文管线**、**tree session（JSONL，可分支/续跑/侧分支摘要）**、provider 打磨（**可选流式** / abort / terminate）、**延迟-成本归因报告**。
- **M2** Textual 终端界面：`--tui` 交互式 UI，复用同一 core（事件流的又一个消费者）。headless stdout 仍为默认。
- **M3** 自延伸：agent 可**自己写 Python 工具扩展**并 `load_extension` 加载（子进程隔离 + JSONL 协议）；扩展状态存 session、`--resume` 恢复；`./.mu/extensions/` 启动自动加载。
- **M3.5** native code-action（`--code`，一次写 Python 组合多工具）+ 可插拔权限/沙箱层（`--permission` / `--sandbox`，默认 YOLO）。**v1 到此完整。**
- **M4.1** eval hardening：库内 eval runner、DGM-lite archive、绝对路径 summary、固定 pytest validator rootdir、过程产物 secret scan、full gate。

## 安装（用仓库自带 .venv）

```bash
./.venv/bin/python -m pip install -e ".[dev]"          # headless
./.venv/bin/python -m pip install -e ".[tui,dev]"      # 含 TUI（textual）
```

## 配置（OpenAI 兼容端点）

> μ **只读环境变量，不会自动加载 `.env`**（M0 保持零依赖）。下面两种方式任选其一。

方式 A — 直接 export：

```bash
export MU_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1   # 百炼
export MU_MODEL=qwen-max
export MU_API_KEY=sk-...
```

方式 B — 用 `.env` 文件（需手动 source 进当前 shell）：

```bash
cp .env.example .env          # 填好端点/模型/key
set -a; source .env; set +a   # 显式加载到环境变量（μ 不会自动读取 .env）
```

- DeepSeek：`MU_BASE_URL=https://api.deepseek.com/v1`、`MU_MODEL=deepseek-chat`
- OpenAI：`MU_BASE_URL=https://api.openai.com/v1`、`MU_MODEL=gpt-4o`

## 运行

```bash
./.venv/bin/python -m mu "在 calc.py 写一个 add(a,b)，再写 test_calc.py 用 pytest 测试它，然后运行 pytest 确认通过"
# 从 stdin 传入任务：
echo "列出当前目录文件并简述这个仓库" | ./.venv/bin/python -m mu
# 流式输出（默认关）：
./.venv/bin/python -m mu --stream "解释这个仓库结构"
```

每次运行结束打印**归因报告**（轮数 / LLM 时延 / 工具时延 / token）。会话以 JSONL 持久化在 `./.mu/sessions/<id>.jsonl`（`MU_SESSION_DIR` 可覆盖），启动时打印 session id。

### 续跑 / 分支

```bash
./.venv/bin/python -m mu --resume <session_id> "接着上次继续：再加一个 sub(a,b)"
./.venv/bin/python -m mu --resume <session_id> --branch <node_id> "从某节点分支"
```

> 侧分支摘要（side-quest 结论带回主线）目前是**程序化 API**：`Agent.summarize_branch(branch_leaf, return_to)` / `Session`。交互式分支导航留到后续。

### 终端界面（TUI · M2）

```bash
./.venv/bin/python -m pip install -e ".[tui,dev]"
./.venv/bin/python -m mu --tui              # 交互式：输入任务→看实时对话/工具/归因
./.venv/bin/python -m mu --tui --stream     # 流式实时输出
```

TUI 与 headless 共享同一 `Agent`/`Session`/事件流；`esc` 取消运行、`ctrl+q` 退出。headless（不带 `--tui`）行为不变。

## 自延伸扩展（M3）

agent 缺能力时可**自己写一个 Python 工具扩展**，用 `load_extension` 加载，新工具立即可用。扩展跑在独立子进程里（JSONL 协议），放在 `./.mu/extensions/` 下会**启动自动加载**。写法见 `extensions/README.md` 与示例 `extensions/example_textstats.py`。

```bash
# 例：让 agent 自己造一个工具并用它
./.venv/bin/python -m mu "写一个统计词数的工具扩展，加载它，统计 'hello world foo' 的词数"
```

> ⚠️ 隔离 ≠ 安全沙箱：M3 子进程只做崩溃隔离，扩展以 agent 同等权限运行（YOLO）。权限/沙箱见 M3.5。

## Code-action 与 安全/沙箱（M3.5，默认全关）

```bash
./.venv/bin/python -m mu --code "统计当前目录所有 .py 文件的总行数"   # 一次写 Python 循环 read，而非逐文件多轮
./.venv/bin/python -m mu --permission readonly "改写 README"          # write/edit/bash 被拒，模型收到 permission denied
./.venv/bin/python -m mu --sandbox docker "ls && echo hi"             # 若装了 docker：bash 在容器内执行
```

- **`--code`**：启用 `code` 工具——模型写 Python，用 `mu.read/write/edit/bash/call` + 控制流在**一次调用**内组合多个工具（少轮数/少 token）。亦可 `MU_CODE_ACTION=1`。
- **`--permission allow|readonly|workspace`**：基于**能力**在 `ToolRegistry` 单一入口 gate 工具。`readonly`=只读（write/edit/bash/code/扩展加载**全部拒绝**）；`workspace`=写限定在 workspace 内（bash/code/扩展因无法限定而拒绝）；默认 `allow`（YOLO）。
- **`--sandbox local|docker`**：可插拔 `Environment` provider；`docker` **仅把 bash 放容器**执行（`--network none` 网络隔离；实验性，需本机 docker）。⚠️ 文件工具仍是宿主 IO，不隔离。E2B/Modal 实现 `Environment` 协议即可插拔。

> ⚠️ code-action 在 `allow` 下是进程内 exec（同 bash 风险，可 `import os` 绕过）；`readonly/workspace` 会整体拦掉 code 工具。code 超时是 soft timeout（线程可能滞留，但其 mu.* 调用会被拒）。要真隔离把 μ 跑容器里。三项默认全关，关掉即退回 M3 形态。

## Eval 与 DGM-lite（M4.1，实验基座 + 护栏硬化）

`python -m mu.eval` 运行库内 eval suite。默认内置 `basic-coding` 三个任务，产物写入 `eval_runs/<timestamp>/`，summary 不记录 API key，并对过程产物运行 secret scan。扫描会覆盖 stdout/stderr/validation/summary/archive 等过程产物；复制 workspace 中的 `sk-...` fixture 不算泄漏，但如果 workspace 文件含真实运行时 env secret 精确值会失败。summary 中的 run dir、workspace、prompt/stdout/stderr/validation 路径均为绝对路径；validator pytest 固定 `--rootdir <workspace>` 并只运行任务自己的测试文件。

```bash
./.venv/bin/python -m mu.eval
./.venv/bin/python -m mu.eval --task fix_existing_bug --timeout 300
```

`python -m mu.dgm` 在复制出来的候选 workspace 中叠加扩展/提示词候选，跑同一 eval suite，并写入 append-only archive；通过项**只归档，不自动应用回主仓库**。

```bash
./.venv/bin/python -m mu.dgm --candidate-dir ./my-candidate --description "prompt hint"
./.venv/bin/python -m mu.dgm --patch ./candidate.patch --parent cand-001
```

M4.0 候选范围刻意很窄：`.mu/extensions/*.py`、`.mu/prompts/*.{md,txt}`、`extensions/*.{py,md,txt}`。`readonly/workspace` 下扩展候选会被拒绝，避免绕过 M3.5 权限边界。

M4.1 提供 full gate 入口，串起离线 pytest、真实模型 basic eval、DGM-lite fake-agent smoke，并在最终产物上再跑一次 secret scan。真实模型 eval 只读取当前 shell 的 `MU_BASE_URL` / `MU_MODEL` / `MU_API_KEY` 或 `OPENAI_API_KEY`，不会写入文件。

```bash
./.venv/bin/python -m mu.eval_gate --run-root "评测/2026-6-12-02"
# 本地只想验证离线链路时可显式跳过真实模型项：
./.venv/bin/python -m mu.eval_gate --allow-missing-model
```

## 测试（无需联网）

```bash
./.venv/bin/python -m pytest -q
```

## 范围（截至 M4.1 · v1 完整 + v2 eval 护栏稳定）

已做：四工具 loop、事件流、上下文管线、tree session + branch summary、可选流式 / abort / terminate、归因底座、Textual TUI、自延伸扩展（子进程 + JSONL 协议）、native code-action、可插拔权限/沙箱层。
M4.0/M4.1 已做：库内 eval 子系统、基础 coding suite、DGM-lite 候选隔离验证与 archive、eval 路径/validator/secret scan/full gate 硬化。
留作 v2 后续：程序性记忆 / meta-tool 编译、投机/异步执行、自动应用通过候选。其余不做：受限解释器、E2B/Modal 具体实现、Web UI / textual serve、完整 compaction、$ 精确计费、并行工具、多 provider 切换。
