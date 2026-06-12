"""示例扩展：文本统计 + 状态持久化演示。

它是一个普通 Python 文件：用 mu.extsdk 声明工具，`python example_textstats.py` 即可作为
μ 的扩展子进程运行。agent 可以读这个文件作为「怎么写扩展」的范例。
"""
from mu.extsdk import get_state, log, run_extension, set_state, tool


@tool(
    name="word_count",
    description="Count the number of words in the given text.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "text to count"}},
        "required": ["text"],
    },
)
def word_count(args):
    n = len(args["text"].split())
    log(f"counted {n} words")
    return str(n)


@tool(
    name="reverse_text",
    description="Reverse the given text.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)
def reverse_text(args):
    return args["text"][::-1]


@tool(
    name="set_prefix",
    description="Persist a greeting prefix into the extension's session state.",
    parameters={
        "type": "object",
        "properties": {"prefix": {"type": "string"}},
        "required": ["prefix"],
    },
)
def set_prefix(args):
    set_state({"prefix": args["prefix"]})
    return f"prefix set to {args['prefix']!r}"


@tool(
    name="greet",
    description="Greet a name using the persisted prefix (demonstrates state across --resume).",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)
def greet(args):
    prefix = get_state().get("prefix", "Hello")
    return f"{prefix}, {args['name']}!"


if __name__ == "__main__":
    run_extension(name="textstats", version="0.1")
