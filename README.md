# μ (mu) — minimal Pi-style coding agent · M1

按 Pi 的实现思路用 Python 复刻的极简 coding agent：一个**薄 async loop** + **四个工具**（read / write / edit / bash）+ **原生 function-calling** + **OpenAI 兼容**模型后端（百炼/DashScope、DeepSeek、OpenAI…）。

进度（见 `docs/Python复刻Pi-Roadmap-v1.md`）：
- **M0** walking skeleton：薄 async loop + 四工具 + 线性历史 + 纯 stdout。
- **M1** harness 三件武器 + 可观测：**事件流**、**上下文管线**、**tree session（JSONL，可分支/续跑/侧分支摘要）**、provider 打磨（**可选流式** / abort / terminate）、**延迟-成本归因报告**。

## 安装（用仓库自带 .venv）

```bash
./.venv/bin/python -m pip install -e ".[dev]"
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

> 侧分支摘要（side-quest 结论带回主线）目前是**程序化 API**：`Agent.summarize_branch(branch_leaf, return_to)` / `Session`。交互式分支导航留到 M2（TUI）。

## 测试（无需联网）

```bash
./.venv/bin/python -m pytest -q
```

## M1 范围

已做：事件流、上下文管线（transform_context/convert_to_llm）、tree session + branch summary、可选流式 / abort / terminate、归因底座。
刻意不做（守 Pi 极简，留后续阶段）：TUI（M2）、自延伸扩展（M3）、native code-action / 可插拔安全沙箱（M3.5）、完整 compaction、交互式分支导航、$ 精确计费、并行工具执行、多 provider 切换。
