# μ 扩展：自己写工具

μ 的「自延伸」= 你（或 agent）写一个普通 Python 文件声明工具，用 `load_extension` 加载，
新工具立即对模型可用。扩展跑在**独立子进程**里，通过 JSONL 与 core 通信。

> ⚠️ **隔离 ≠ 安全沙箱**：M3 的子进程只做崩溃隔离，扩展以与 agent **同等权限**运行（可读写
> 文件、执行命令）。真正的权限/沙箱在 M3.5。只加载你信任的扩展。

## 最小例子

```python
from mu.extsdk import tool, run_extension

@tool(name="word_count", description="Count words in text.",
      parameters={"type": "object",
                  "properties": {"text": {"type": "string"}},
                  "required": ["text"]})
def word_count(args):
    return str(len(args["text"].split()))

if __name__ == "__main__":
    run_extension(name="textstats", version="0.1")
```

加载与使用（agent 在对话里会这么做）：

1. `write` 把上面的文件写到 `./.mu/extensions/textstats.py`
2. `load_extension("./.mu/extensions/textstats.py")`
3. 直接调用新工具 `word_count`
4. 改了扩展后 `reload_extension("textstats")`

放在 `./.mu/extensions/` 下的扩展会在**下次启动时自动加载**。

## SDK

- `@tool(name, description, parameters, permissions=None)`：声明一个工具。`parameters` 是
  OpenAI JSON Schema。函数签名 `fn(args: dict) -> str`（或返回 `(str, terminate: bool)`）；
  同步或 `async` 均可。
- `get_state()` / `set_state(dict)`：读写扩展状态。`set_state` 会把状态持久化进 session，
  `--resume` 时自动恢复（见示例的 `set_prefix`/`greet`）。
- `log(message, level="info")`：输出日志，回流到 μ 的事件流（不要用 `print`——stdout 是协议通道）。
- `run_extension(name, version)`：启动协议循环（首行输出 manifest，然后处理请求）。

## 协议（JSONL，每行一个对象）

- 启动：扩展在 **stdout 首行**输出 `{"type":"manifest","name","version","tools":[...],"permissions":[...]}`。
- core → ext：`{"type":"init","state":{...}}`、`{"type":"execute","id","tool","args"}`、`{"type":"shutdown"}`
- ext → core：`{"type":"result","id","content","terminate"}`、`{"type":"error","id","message"}`、
  `{"type":"log","level","message"}`、`{"type":"state","state":{...}}`

## 一个更真实的范例（说明用，未随测试运行）

「用 Chrome DevTools Protocol 截网页」可以这样封装成扩展：工具函数里用 `bash`/`http` 驱动
`chrome --headless --screenshot`，把产物路径返回。它需要本机有 Chrome，故不进自动化测试；
但思路与上面的 `textstats` 完全一致——这正是 Pi「缺能力就现写一个工具」的体现。

参见同目录 `example_textstats.py`。
