"""Agent: 薄 async while loop（无 max_steps），M1 升级。

M0→M1 变化：
- 历史从内存 list 升为 Session 树；`messages` 变为「当前分支路径」只读 property。
- `_emit(kind,text)` 升为结构化事件流（EventEmitter），多订阅者消费。
- 调 model 前过上下文管线：convert_to_llm(transform_context(path))。
- 工具结果带 terminate；本轮工具全部 terminate 则跳过自动后续 LLM 调用。
- 支持 asyncio 取消（Ctrl-C / 程序取消）：落盘已增量完成，emit RunAborted。
保持 Pi 哲学：朴素 while、无 max_steps、以「无 tool_calls」终止。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from . import context as ctx
from .events import (
    AssistantText,
    AssistantTextDelta,
    EventEmitter,
    ModelCallFinished,
    ModelCallStarted,
    RunAborted,
    RunFinished,
    RunStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from .model import Model
from .prompts import SYSTEM_PROMPT
from .session import Session
from .tools import ToolRegistry, ToolResult

Transform = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


class Agent:
    def __init__(
        self,
        model: Any | None = None,
        tools: ToolRegistry | None = None,
        emitter: EventEmitter | None = None,
        session: Session | None = None,
        stream: bool = False,
        transform_context: Transform | None = None,
        convert_to_llm: Transform | None = None,
    ) -> None:
        self.model = model if model is not None else Model()
        self.tools = tools or ToolRegistry()
        self.emitter = emitter or EventEmitter()
        self.session = session or Session()
        self.stream = stream
        self._transform = transform_context or ctx.transform_context
        self._convert = convert_to_llm or ctx.convert_to_llm

    @property
    def messages(self) -> list[dict[str, Any]]:
        """当前分支的线性历史（= 喂给上下文管线的原料）。"""
        return self.session.path_to_head()

    async def run(self, task: str) -> str:
        # 新会话才注入 system；resume 时复用历史，仅追加新 user
        if self.session.head is None:
            self.session.append({"role": "system", "content": SYSTEM_PROMPT})
        self.session.append({"role": "user", "content": task})
        self.emitter.emit(RunStarted(task, self.session.id))

        turn = 0
        try:
            while True:  # 无 max_steps：跑到模型不再调用工具为止
                turn += 1
                self.emitter.emit(TurnStarted(turn))

                llm_messages = self._convert(self._transform(self.session.path_to_head()))
                self.emitter.emit(ModelCallStarted(turn))
                on_delta = (
                    (lambda d: self.emitter.emit(AssistantTextDelta(d))) if self.stream else None
                )
                result = await self.model.acomplete(
                    llm_messages, self.tools.schemas(), stream=self.stream, on_delta=on_delta
                )
                self.emitter.emit(
                    ModelCallFinished(
                        turn, result.latency_s,
                        result.prompt_tokens, result.completion_tokens, result.total_tokens,
                    )
                )

                assistant_msg = _message_to_dict(result.message)
                self.session.append(assistant_msg)
                if assistant_msg.get("content") and not self.stream:
                    self.emitter.emit(AssistantText(assistant_msg["content"]))

                tool_calls = assistant_msg.get("tool_calls") or []
                if not tool_calls:
                    final = assistant_msg.get("content") or ""
                    self.emitter.emit(TurnFinished(turn))
                    self.emitter.emit(RunFinished(final))
                    return final

                terminates = await self._run_tool_calls(tool_calls)
                self.emitter.emit(TurnFinished(turn))
                if terminates and all(terminates):  # 全部 terminate → 跳过自动后续调用
                    self.emitter.emit(RunFinished(""))
                    return ""
        except asyncio.CancelledError:
            self.emitter.emit(RunAborted("cancelled"))
            raise

    async def _run_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[bool]:
        terminates: list[bool] = []
        for i, tc in enumerate(tool_calls):  # M1 仍顺序执行（并行留后续）
            name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments") or "{}"
            call_id = tc["id"]
            t0 = time.perf_counter()
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                res: ToolResult = ToolResult(
                    f"Error: tool arguments were not valid JSON: {raw_args!r}"
                )
            else:
                self.emitter.emit(ToolCallStarted(call_id, name, raw_args))
                try:
                    res = await self.tools.execute(name, args)
                except asyncio.CancelledError:
                    # 取消：为当前及剩余未执行的 tool call 补错误结果，保持 session
                    # 满足「每个 assistant tool_call 都有对应 tool 结果」→ 可 resume。
                    self._append_pending_tool_errors(tool_calls[i:])
                    raise
            dt = time.perf_counter() - t0
            terminate = bool(getattr(res, "terminate", False))
            self.emitter.emit(ToolCallFinished(call_id, name, str(res), dt, terminate))
            terminates.append(terminate)
            self.session.append(
                {"role": "tool", "tool_call_id": call_id, "content": str(res)}
            )
        return terminates

    def _append_pending_tool_errors(self, pending: list[dict[str, Any]]) -> None:
        for tc in pending:
            self.session.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "Error: tool execution cancelled",
                }
            )

    def summarize_branch(
        self,
        branch_leaf_id: str,
        return_to: str,
        summary_text: str | None = None,
    ) -> str:
        """把某侧分支的结论作为摘要带回主线（Pi side-quest → 回主线工作流）。

        - branch_leaf_id：侧分支叶子节点。
        - return_to：要回到的主线节点（摘要追加其后）。
        - summary_text：不提供则按分支路径的 assistant/tool 文本做 deterministic 概括
          （M1 不强制调 model；调 model 概括可后续接入）。
        返回新追加的 branch_summary 节点 id。
        """
        if summary_text is None:
            msgs = self.session.path_to(branch_leaf_id)
            parts = [
                str(m.get("content", ""))
                for m in msgs
                if m.get("role") in ("assistant", "tool") and m.get("content")
            ]
            summary_text = "侧分支结论：" + " | ".join(parts[-3:]) if parts else "侧分支无产出"
        self.session.branch_from(return_to)
        return self.session.add_branch_summary(summary_text)


def _message_to_dict(message: Any) -> dict[str, Any]:
    """把 openai/流式/fake message 转成线性历史里的 dict。"""
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return out
