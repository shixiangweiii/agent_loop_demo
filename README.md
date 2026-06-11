# μ (mu) — minimal Pi-style coding agent · M0

按 Pi 的实现思路用 Python 复刻的极简 coding agent：一个**薄 async loop** + **四个工具**（read / write / edit / bash）+ **原生 function-calling** + **OpenAI 兼容**模型后端（百炼/DashScope、DeepSeek、OpenAI…）。

这是路线图 `docs/Python复刻Pi-Roadmap-v1.md` 的 **M0（walking skeleton）**。

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
# 或从 stdin 传入任务：
echo "列出当前目录文件并简述这个仓库" | ./.venv/bin/python -m mu
```

## 测试（无需联网）

```bash
./.venv/bin/python -m pytest -q
```

## M0 范围

刻意不做（守 Pi 极简，留给后续阶段）：事件流 / tree session / 上下文管线（M1）、TUI（M2）、自延伸扩展（M3）、安全/沙箱层（M3.5）、多 provider 切换、max_steps、并行工具执行。
