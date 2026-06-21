"""
Dualign — AiRepairAgent: Tool-Calling 智能校订代理 (v2)

设计原则:
  - 两层文本模型：初始文本（对齐器原始）+ 当前文本（待审校）
  - AI 只需要判断「当前文本每对 src/tgt 语义对应吗？」
  - 所有工具操作的是初始文本，系统自动 re-repair 更新当前文本
  - 无 auto_note、无 would_*、无策略名暴露给 AI

用法:
  from dualign.services.ai_repair_agent import AiRepairAgent, ChapterContext
  agent = AiRepairAgent(backend="deepseek")
  actions = agent.run(chapter_context)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from abc import ABC, abstractmethod

from dualign.models.action import RepairAction
from dualign.models.state import AlignmentSnapshot
from dualign.models.snap_state import (
    SnapState,
    SnapInfo,
    build_snap_states,
    refresh_snap_states,
    snap_state_to_info,
    build_context_windows,
    _parse_type,
)

logger = logging.getLogger(__name__)


# ── DeepSeek 定价（$ / token）──
DEEPSEEK_PRICES = {
    "prompt": 0.14 / 1e6,
    "completion": 0.28 / 1e6,
    "cache": 0.0028 / 1e6,
}


def compute_cost(
    prompt_tokens: int, cache_tokens: int, completion_tokens: int
) -> float:
    """计算 DeepSeek API 调用费用。"""
    prices = DEEPSEEK_PRICES
    return (
        (prompt_tokens - cache_tokens) * prices["prompt"]
        + cache_tokens * prices["cache"]
        + completion_tokens * prices["completion"]
    )


# ═══════════════════════════════════════════════════════════════
# 1. 数据结构
# ═══════════════════════════════════════════════════════════════


@dataclass
class AgentEvent:
    turn: int
    type: str
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    usage: dict = field(default_factory=dict)
    actions: List[RepairAction] = field(default_factory=list)
    messages: List[dict] = field(default_factory=list)
    turn_log: List[dict] = field(default_factory=list)
    review_action: Optional[RepairAction] = None
    error: str = ""


@dataclass
class ChapterContext:
    chapter_id: str
    chapter_title: str
    total_pairs: int
    snapshot: AlignmentSnapshot
    snap_states: List[SnapState] = field(default_factory=list)
    snap_infos: List[SnapInfo] = field(default_factory=list)
    reviewable_ids: List[int] = field(default_factory=list)

    def get_snap_info(self, snap_id: int) -> Optional[SnapInfo]:
        if 0 <= snap_id < len(self.snap_infos):
            return self.snap_infos[snap_id]
        return None

    def get_snap_state(self, snap_id: int) -> Optional[SnapState]:
        if 0 <= snap_id < len(self.snap_states):
            return self.snap_states[snap_id]
        return None

    @property
    def reviewable_infos(self):
        """返回需要审校的 SnapInfo 列表。"""
        return [si for si in self.snap_infos if si.is_reviewable]

    @classmethod
    def from_repair_state(
        cls,
        state,
        chapter_id="",
        chapter_title="",
        strategy="src",
        model=None,
        skip_auto_repair=False,
    ) -> "ChapterContext":
        """从 RepairState 构造 ChapterContext。

        v2 设计：当前文本始终设置为 auto-repair 后的结果（无论传入的 state 是否已修复）。
        初始文本保持原始对齐输出。AI 只需判断「当前文本正确吗？」。
        auto-repair 是内部状态，不暴露给 AI。

        Args:
            model: 嵌入模型，用于 split 操作。不传时 split 回退为 merge。
            skip_auto_repair: 为 True 时跳过内部 auto_repair（调用方已预修复）。
        """
        from dualign.services.repair import RepairService

        snap = state.snapshot
        total = len(snap.original_ops)

        if skip_auto_repair:
            repaired = state
        else:
            # 始终用 auto-repair 后的状态作为「当前文本」
            repaired = RepairService.auto_repair(state, strategy=strategy, model=model)
        ch = repaired.current

        snap_states = build_snap_states(
            snapshot=snap,
            src_lines=list(snap.original_src_lines),
            tgt_lines=list(snap.original_tgt_lines),
            repair_log=repaired.repair_log,
        )
        snap_states = refresh_snap_states(snap_states, snap, ch, repaired.repair_log)

        snap_infos: List[SnapInfo] = []
        for si in range(total):
            g = ch.group(si)
            src = (
                "\n".join(r.src_text for r in g.rows if r.src_text)
                if g is not None
                else ""
            )
            tgt = (
                "\n".join(r.tgt_text for r in g.rows if r.tgt_text)
                if g is not None
                else ""
            )
            info = snap_state_to_info(snap_states[si], si, src, tgt)
            # 初始文本
            s_idx, t_idx, _ = snap.original_ops[si]
            info.initial_src_text = (
                "\n".join(snap.src_text(i) for i in s_idx) if s_idx else ""
            )
            info.initial_tgt_text = (
                "\n".join(snap.tgt_text(j) for j in t_idx) if t_idx else ""
            )
            info.initial_n_src = len(s_idx)
            info.initial_n_tgt = len(t_idx)
            snap_infos.append(info)
        reviewable_ids = [
            si for si, info in enumerate(snap_infos) if info.is_reviewable
        ]
        return cls(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            total_pairs=total,
            snapshot=snap,
            snap_states=snap_states,
            snap_infos=snap_infos,
            reviewable_ids=reviewable_ids,
        )

    def append_reviewable(self, snap_id: int) -> bool:
        if snap_id in self.reviewable_ids:
            return False
        if not self.get_snap_info(snap_id):
            return False
        self.reviewable_ids.append(snap_id)
        self.reviewable_ids.sort()
        return True


# ═══════════════════════════════════════════════════════════════
# 公共构造器 — 确保嵌入模型就绪
# ═══════════════════════════════════════════════════════════════


def build_chapter_context(
    state,
    strategy: str = "src",
    model=None,
    chapter_id: str = "",
    chapter_title: str = "",
    skip_auto_repair: bool = False,
) -> ChapterContext:
    """从 RepairState 构建 ChapterContext，自动确保嵌入模型就绪。

    GUI 和 Demo 共用此入口，保证 auto_repair 内部对 N:1 / 1:M 等
    场景的 split/merge 行为一致。

    当 model 为 None 时自动尝试加载嵌入模型。若加载失败，
    auto_repair 将回退到 merge（与 model=None 行为一致）。

    Args:
        skip_auto_repair: 为 True 时跳过内部 auto_repair（调用方已预修复）。
    """
    if model is None:
        try:
            from dualign.services.embedding import _try_lazy_load_model

            model = _try_lazy_load_model()
        except Exception as e:
            logger.warning("嵌入模型加载失败: %s（auto_repair 将回退到 merge）", e)
    return ChapterContext.from_repair_state(
        state,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        strategy=strategy,
        model=model,
        skip_auto_repair=skip_auto_repair,
    )


# ═══════════════════════════════════════════════════════════════
# 2. 工具定义 — 从外部 JSON 加载（懒加载）
# ═══════════════════════════════════════════════════════════════

_prompts_dir_cache: str | None = None


def _get_prompts_dir() -> str:
    """定位 prompts/ 目录（懒加载 + 缓存，支持 PyInstaller 打包和开发模式）。"""
    global _prompts_dir_cache
    if _prompts_dir_cache is not None:
        return _prompts_dir_cache

    # 开发模式：__file__ = .../src/dualign/services/ai_repair_agent.py
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "prompts")
    if os.path.isdir(candidate):
        _prompts_dir_cache = candidate
        return candidate

    # PyInstaller 打包：sys._MEIPASS/dualign/services/prompts
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = os.path.join(meipass, "dualign", "services", "prompts")
        if os.path.isdir(candidate):
            _prompts_dir_cache = candidate
            return candidate

    raise FileNotFoundError(
        f"找不到 prompts/ 目录。尝试过:\n"
        f"  1. {os.path.join(here, 'prompts')}\n"
        f"  2. {os.path.join(getattr(sys, '_MEIPASS', ''), 'dualign', 'services', 'prompts') if getattr(sys, '_MEIPASS', None) else '(无 _MEIPASS)'}"
    )


_tools_cache: tuple | None = None


def _load_tools():
    """从 tools.json 加载工具定义，返回 (TOOLS_OPENAI, TOOLS_TEXT_DESCRIPTION)。

    懒加载：首次调用时解析 prompts 目录。
    """
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache
    tools_path = os.path.join(_get_prompts_dir(), "tools.json")
    with open(tools_path, "r", encoding="utf-8") as f:
        tools = json.load(f)

    # OpenAI Function Calling 格式
    openai_tools = []
    for t in tools:
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
        )

    # Ollama XML 文本描述格式
    lines = ["## 工具", "使用 XML 格式调用工具。每轮可调多次，顺序不影响结果。", ""]
    for t in tools:
        name = t["name"]
        desc = t["description"]
        lines.append(f"### {name} — {desc}")
        for ex in t.get("text_examples", []):
            lines.append(f'<tool_call name="{name}">{ex}</tool_call>')
        lines.append("")

    result = (openai_tools, "\n".join(lines).strip())
    _tools_cache = result
    return result


def _get_tools_openai():
    """懒加载 TOOLS_OPENAI。"""
    return _load_tools()[0]


# ═══════════════════════════════════════════════════════════════
# 3. 系统提示词
# ═══════════════════════════════════════════════════════════════

# 策略标签（minimal 映射到 src）
_STRATEGY_LABEL = {
    "src": "原文优先 (src-first)",
    "tgt": "译文优先 (tgt-first)",
    "minimal": "最小变更 (minimal)",
}


def _load_system_prompt(strategy="src") -> str:
    """从 agent-prompt.md 加载系统提示词。"""
    candidate = os.path.join(_get_prompts_dir(), "agent-prompt.md")
    if not os.path.isfile(candidate):
        raise FileNotFoundError(f"agent-prompt.md not found: {candidate}")
    with open(candidate, "r", encoding="utf-8") as f:
        content = f.read()
    parts = content.split("---", 2)
    text = parts[2].strip() if len(parts) >= 3 else content.strip()
    return text


# ═══════════════════════════════════════════════════════════════
# 4. LLM Backend
# ═══════════════════════════════════════════════════════════════


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    reasoning_content: str = ""


class LLMBackend(ABC):
    @abstractmethod
    def chat(
        self, messages: List[dict], thinking: bool = False, tools: list | None = None
    ) -> LLMResponse: ...


class DeepSeekNativeBackend(LLMBackend):
    def __init__(
        self,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        api_key: str = "",
    ):
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.MODEL = model
        self.BASE_URL = base_url
        # 优先使用传入的 api_key，回退到环境变量
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")

    def chat(self, messages, thinking=True, tools=None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai 库未安装，无法使用 DeepSeek 后端")
        api_key = self._api_key
        if not api_key:
            raise ValueError(
                "API Key 未设置\n"
                "   请在设置面板中配置 API Key，或设置环境变量 DEEPSEEK_API_KEY"
            )
        client = OpenAI(api_key=api_key, base_url=self.BASE_URL)
        kwargs = {
            "model": self.MODEL,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if thinking:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            }
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.warning("DeepSeek API 调用失败: %s", e)
            return LLMResponse(content="", usage={"error": str(e)})

        msg = resp.choices[0].message
        usage_raw = resp.usage
        usage = {
            "prompt_tokens": usage_raw.prompt_tokens if usage_raw else 0,
            "completion_tokens": usage_raw.completion_tokens if usage_raw else 0,
            "total_tokens": usage_raw.total_tokens if usage_raw else 0,
        }
        if usage_raw and hasattr(usage_raw, "prompt_tokens_details"):
            details = usage_raw.prompt_tokens_details
            if details and hasattr(details, "cached_tokens"):
                usage["cached_tokens"] = details.cached_tokens
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                )
        reasoning = getattr(msg, "reasoning_content", "") or ""
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            reasoning_content=reasoning,
        )


# ═══════════════════════════════════════════════════════════════
# 5. Tool 执行器
# ═══════════════════════════════════════════════════════════════


def _parse_pair_spec(spec: str) -> List[int]:
    indices: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if "-" in part:
            try:
                s, e = part.split("-", 1)
                indices.update(range(int(s.strip()), int(e.strip()) + 1))
            except (ValueError, TypeError):
                raise ValueError(f"无法解析范围: {part!r}")
        else:
            try:
                indices.add(int(part))
            except (ValueError, TypeError):
                raise ValueError(f"无法解析编号: {part!r}")
    return sorted(indices)


def _parse_op_index(op_index: str) -> tuple[List[int], bool]:
    """解析 snap_range 字符串: "3" → [3], "10-13" → [10,11,12,13]"""
    if op_index.isdigit():
        return [int(op_index)], False
    if re.match(r"^\d+-\d+$", op_index):
        parts = op_index.split("-")
        start, end = int(parts[0]), int(parts[1])
        if start > end:
            raise ValueError(f"范围起止颠倒: {op_index}")
        return list(range(start, end + 1)), True
    raise ValueError(f"无效 snap_range: {op_index!r}")


def compute_auto_action_kind(snap_state, strategy: str) -> Optional[str]:
    """根据 SnapState 的 init_type 和策略推导应执行的自动修复操作 kind。

    返回 kind 字符串（merge/split/delete/placeholder_src/placeholder_tgt），
    或 None（无需操作）。
    与 RepairService.auto_repair 的策略矩阵保持一致。
    """
    if snap_state is None:
        return None
    in_s, in_t = _parse_type(snap_state.init_type)
    if in_s == 1 and in_t == 1:
        return None
    if in_s > 1 and in_t == 1:  # N:1
        return "split" if strategy == "src" else "merge"
    if in_s == 1 and in_t > 1:  # 1:M
        return "merge" if strategy == "src" else "split"
    if in_s > 0 and in_t == 0:  # N:0
        return "placeholder_tgt" if strategy == "src" else "delete"
    if in_s == 0 and in_t > 0:  # 0:M
        return "delete" if strategy == "src" else "placeholder_src"
    if in_s > 1 and in_t > 1:  # N:M
        return "placeholder_tgt" if strategy in ("src", "tgt") else "delete"
    return None


class ToolExecutor:
    def __init__(
        self, ctx: ChapterContext, model=None, initial_state=None, strategy="src"
    ):
        self.ctx = ctx
        self._model = model
        self._state = initial_state
        self._strategy = strategy
        self.reviewed_ids: set = set()
        self.reviewed_actions: Dict[int, RepairAction] = {}

    def execute(self, tool_call: ToolCall) -> str:
        handlers = {
            "view": self._handle_view,
            "ok": self._handle_ok,
            "edit": self._handle_edit,
            "merge": self._handle_merge,
            "delete": self._handle_delete,
            "flag": self._handle_flag,
            "append": self._handle_append,
            "done": self._handle_done,
            "force_done": self._handle_force_done,
        }
        handler = handlers.get(tool_call.name)
        if handler is None:
            return json.dumps(
                {"error": f"未知工具: {tool_call.name}"}, ensure_ascii=False
            )
        try:
            result = handler(tool_call.arguments)
            return (
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False)
            )
        except Exception as e:
            return json.dumps({"error": f"工具执行异常: {e}"}, ensure_ascii=False)

    def _progress(self) -> str:
        total = len(self.ctx.reviewable_ids)
        done = len(self.reviewed_ids)
        pending = [i for i in self.ctx.reviewable_ids if i not in self.reviewed_ids]
        ps = (
            str(pending[:10]) + ("..." if len(pending) > 10 else "")
            if pending
            else "无"
        )
        return (
            f"**进度**: {done}/{total} 剩余 {len(pending)}: {ps}"
            if pending
            else f"**进度**: {done}/{total} ✅ 全部完成"
        )

    def _record_review(self, snap_list: List[int], action: RepairAction):
        for si in snap_list:
            self.reviewed_ids.add(si)
            self.reviewed_actions[si] = action

    def _handle_view(self, args: dict) -> str:
        spec = args.get("pair_spec", "")
        if not spec:
            return "❌ 请提供 pair_spec"
        try:
            snap_ids = _parse_pair_spec(spec)
        except ValueError as e:
            return f"❌ {e}"

        # 用最新状态构建 snap_infos
        snap_infos = self._build_current_snap_infos()

        snap_ids = [i for i in snap_ids if 0 <= i < len(snap_infos)]
        if not snap_ids:
            return "❌ 所有指定的文本对均不存在"
        lines = [
            str(snap_infos[sid]) for sid in snap_ids[:20] if snap_infos[sid] is not None
        ]
        return "\n".join(lines)

    def _build_current_snap_infos(self):
        """如果有 initial_state，通过重放已审校操作构建最新 SnapInfo 列表。"""
        if self._state is None:
            return self.ctx.snap_infos
        s = self._state
        for a in self.reviewed_actions.values():
            if a is not None:
                s = s.apply(a)
        fresh_ctx = ChapterContext.from_repair_state(s)
        return fresh_ctx.snap_infos

    def _get_current_snap_action(self, snap_id: int) -> Optional[RepairAction]:
        """获取该 snap 当前已有的修复操作（不含 ok/flag 元操作）。

        结合 self._state（含预修复）和 self.reviewed_actions（Agent 已执行操作），
        返回该 snap 的最近一次非元操作。若 snap 无修复操作（原始状态），返回 None。
        """
        if self._state is None:
            return None
        s = self._state
        for a in self.reviewed_actions.values():
            if a is not None:
                s = s.apply(a)
        META_KINDS = {"ok", "flag"}
        for a in reversed(s._repair_log):
            if a.op_index == snap_id and a.kind not in META_KINDS:
                return a
        return None

    def _handle_ok(self, args: dict) -> str:
        snap_id = args["snap_id"]
        snap_list = [snap_id]
        anchor = snap_list[0]

        # 统一语义：若 snap 已有修复操作，AI 的 ok 等同于认可该操作
        existing = self._get_current_snap_action(snap_id)
        if existing:
            # 复制原操作的数据（split/edit 需要 new_src_lines 等）
            ra = RepairAction(
                op_index=anchor,
                kind=existing.kind,
                source="ai",
                data=dict(existing.data),
            )
        else:
            # 无修复操作 → 真正的 ok（认可原始对齐结果）
            ra = RepairAction(op_index=anchor, kind="ok", source="ai")

        self._record_review(snap_list, ra)
        return f"### ✅ 确认 — snap {snap_list}\n\n{self._progress()}"

    def _handle_edit(self, args: dict) -> str:
        snap_list, is_range = _parse_op_index(args["snap_range"])
        already = [si for si in snap_list if si in self.reviewed_ids]
        new_src = args.get("new_src", [])
        new_tgt = args.get("new_tgt", [])
        if isinstance(new_src, str):
            new_src = [new_src]
        if isinstance(new_tgt, str):
            new_tgt = [new_tgt]

        # ── 行数校验：当 AI 同时传入两侧时，长度必须相等 ──
        if new_src and new_tgt and len(new_src) != len(new_tgt):
            return (
                f"❌ **edit 拒绝**: new_src ({len(new_src)} 行) 和 new_tgt ({len(new_tgt)} 行) "
                f"行数不等，无法配对。\n\n"
                f"此 snap 的初始原文本有 "
                f"{len(self.ctx.snapshot.original_ops[snap_list[0]][0])} 行。\n\n"
                f"edit 要求同时传入两侧时每行一一配对——确保两侧行数相等，"
                f"或只传需要修改的一侧。"
            )

        # ── 语义校验：当只传一侧，但原始另一侧行数多于修改侧时，结果可能不符合预期 ──
        anchor = snap_list[0]
        if not is_range:
            s_idx, t_idx, _ = self.ctx.snapshot.original_ops[anchor]
            n_orig_src = len(s_idx)
            n_orig_tgt = len(t_idx)
            if not new_src and new_tgt and n_orig_src > 1 and len(new_tgt) < n_orig_src:
                return (
                    f"⚠️ **edit 提示**: 你只提供了 {len(new_tgt)} 行新译文，"
                    f"但该 snap 的初始原文有 {n_orig_src} 行。\n"
                    f"edit 只传 new_tgt 时原文侧保留全部初始原文——"
                    f"结果将是 {n_orig_src}:{len(new_tgt)} 而非 1:1。\n\n"
                    f"如需产出 1:1：提供 {n_orig_src} 行新译文（每行对应一段原文），"
                    f"或 edit 同时传两侧明确配对。"
                )
            if not new_tgt and new_src and n_orig_tgt > 1 and len(new_src) < n_orig_tgt:
                return (
                    f"⚠️ **edit 提示**: 你只提供了 {len(new_src)} 行新原文，"
                    f"但该 snap 的初始译文有 {n_orig_tgt} 行。\n"
                    f"edit 只传 new_src 时译文侧保留全部初始译文——"
                    f"结果将是 {len(new_src)}:{n_orig_tgt} 而非 1:1。\n\n"
                    f"如需产出 1:1：提供 {n_orig_tgt} 行新原文（每行对应一段译文），"
                    f"或 edit 同时传两侧明确配对。"
                )

        if is_range:
            for i, si in enumerate(snap_list):
                if si in self.reviewed_ids:
                    continue
                _src = [new_src[i]] if i < len(new_src) and new_src[i] else []
                _tgt = [new_tgt[i]] if i < len(new_tgt) and new_tgt[i] else []
                ra = RepairAction(
                    op_index=si,
                    kind="edit",
                    source="ai",
                    data={"new_src_lines": _src, "new_tgt_lines": _tgt},
                )
                self.reviewed_ids.add(si)
                self.reviewed_actions[si] = ra
        else:
            # ── 填充缺失侧：AI 只传一侧时，从当前上下文补充另一侧（保留自动修复结果）──
            if (not new_src or not new_tgt) and not is_range:
                info = self.ctx.get_snap_info(anchor)
                if info:
                    if not new_src:
                        new_src = [s for s in info.src_text.split("\n") if s]
                    if not new_tgt:
                        new_tgt = [t for t in info.tgt_text.split("\n") if t]
            ra = RepairAction(
                op_index=anchor,
                kind="edit",
                source="ai",
                data={"new_src_lines": new_src, "new_tgt_lines": new_tgt},
            )
            self._record_review(snap_list, ra)

        suffix = " (已覆盖之前的审校决定)" if already else ""
        return f"### ✏️ 编辑 — snap {snap_list}{suffix}\n\n{self._progress()}"

    def _handle_merge(self, args: dict) -> str:
        snap_list, _ = _parse_op_index(args["snap_range"])
        already = [si for si in snap_list if si in self.reviewed_ids]
        anchor = snap_list[0]
        if len(snap_list) > 1:
            ra = RepairAction(
                op_index=anchor,
                kind="merge",
                source="ai",
                data={"orig_snaps": list(snap_list)},
            )
            self.reviewed_ids.update(snap_list)
            self.reviewed_actions[anchor] = ra
        else:
            ra = RepairAction.make_merge(anchor, source="ai")
            self._record_review(snap_list, ra)
        suffix = " (已覆盖之前的审校决定)" if already else ""
        return f"### 🔗 合并 — snap {snap_list}{suffix}\n\n{self._progress()}"

    def _handle_delete(self, args: dict) -> str:
        snap_list, _ = _parse_op_index(args["snap_range"])
        already = [si for si in snap_list if si in self.reviewed_ids]
        anchor = snap_list[0]
        if len(snap_list) > 1:
            ra = RepairAction(
                op_index=anchor,
                kind="delete",
                source="ai",
                data={"orig_snaps": list(snap_list)},
            )
            self.reviewed_ids.update(snap_list)
            self.reviewed_actions[anchor] = ra
        else:
            ra = RepairAction.make_delete(anchor, source="ai")
            self._record_review(snap_list, ra)
        suffix = " (已覆盖之前的审校决定)" if already else ""
        return f"### 🗑️ 删除 — snap {snap_list}{suffix}\n\n{self._progress()}"

    def _handle_flag(self, args: dict) -> str:
        snap_id = args["snap_id"]
        snap_list = [snap_id]
        already = [si for si in snap_list if si in self.reviewed_ids]
        note = args.get("note", "")
        ra = RepairAction.make_flag(snap_list[0], note=note)
        ra.source = "ai"
        self._record_review(snap_list, ra)
        suffix = " (已覆盖之前的审校决定)" if already else ""
        return f"### 🚩 标记 — snap {snap_list}{suffix}\n\n{self._progress()}"

    def _handle_append(self, args: dict) -> str:
        snap_id = int(args.get("snap_id", -1))
        if snap_id < 0:
            return "❌ **追加失败**: snap_id 无效"
        ok = self.ctx.append_reviewable(snap_id)
        if ok:
            info = self.ctx.get_snap_info(snap_id)
            return f"✅ 已追加 snap {snap_id} ({info.n_src_rows}:{info.n_tgt_rows}) 到待审列表"
        return f"❌ **追加失败**: snap {snap_id} 不存在或已在待审列表中"

    def _handle_done(self, args: dict) -> str:
        remaining = [i for i in self.ctx.reviewable_ids if i not in self.reviewed_ids]
        if remaining:
            return (
                f"❌ **done 拒绝**: 仍有 {len(remaining)} 个待审 snap 未处理: "
                f"{remaining[:15]}{'...' if len(remaining) > 15 else ''}\n\n"
                f"请逐一审查这些 snap 后再调用 done。"
                f"如确有合理原因需要跳过，请使用 `force_done` 并说明理由。"
            )
        return "✅ done" + (f": {args.get('note', '')}" if args.get("note") else "")

    def _handle_force_done(self, args: dict) -> str:
        remaining = [i for i in self.ctx.reviewable_ids if i not in self.reviewed_ids]
        skipped = f"（跳过 {len(remaining)} 项）" if remaining else ""
        note = args.get("note", "")
        return f"✅ force_done{skipped}" + (f": {note}" if note else "")


# ═══════════════════════════════════════════════════════════════
# 6. AiRepairAgent
# ═══════════════════════════════════════════════════════════════


class MaxTurnsExceeded(Exception):
    pass


class AiRepairAgent:
    """Tool-Calling AI 校订代理 (v2)。

    工具: ok / edit / merge / delete / flag / view / append / done
    使用 DeepSeek (OpenAI 兼容) 后端，支持工具调用。
    已移除 Ollama 后端（仅嵌入服务使用 Ollama）。
    """

    def __init__(
        self,
        backend="deepseek",
        temperature=0.0,
        max_turns=20,
        verbose=True,
        model=None,
        strategy="src",
        thinking=True,
        model_name: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        api_key: str = "",
    ):
        self.max_turns = max_turns
        self.verbose = verbose
        self._model = model
        self._strategy = strategy
        self._thinking = thinking
        self._llm = DeepSeekNativeBackend(
            temperature=temperature,
            max_tokens=8192,
            model=model_name,
            base_url=base_url,
            api_key=api_key,
        )
        self._idle_turns = 0

    def run(
        self,
        ctx: ChapterContext,
        on_event: Callable[[AgentEvent], None] | None = None,
        initial_state=None,
    ) -> List[RepairAction]:
        """initial_state: 启动时的 RepairState，view 用它重放已审校操作生成最新状态。"""
        executor = ToolExecutor(
            ctx, model=self._model, initial_state=initial_state, strategy=self._strategy
        )
        messages = self._build_initial_messages(ctx)
        turn_log: List[dict] = []
        all_actions: List[RepairAction] = []

        def _emit(evt_type, **kw):
            if on_event:
                on_event(AgentEvent(type=evt_type, turn=kw.pop("turn", 0), **kw))

        if self.verbose:
            logger.info(
                "Agent 启动: %s | %d 对 | 待审 %d",
                ctx.chapter_id,
                ctx.total_pairs,
                len(ctx.reviewable_ids),
            )

        for turn in range(1, self.max_turns + 1):
            t0 = time.time()
            tools = _get_tools_openai()

            remaining = [
                i for i in ctx.reviewable_ids if i not in executor.reviewed_ids
            ]
            # ── 替换最后一条 user 消息而非追加，避免历史累积冗余 ──
            if remaining:
                _new_progress = f"### 待审进度: {len(executor.reviewed_ids)}/{len(ctx.reviewable_ids)}"
                f" | 剩余: {remaining}\n继续审校剩余 snap。完成后调用 done。"
            else:
                _new_progress = "### ✅ 待审列表已清空\n\n检查是否有遗漏的异常 snap——若有，用 `append` 追加后继续审校。确认无遗漏后，调用 `done` 结束。"
            if messages[-1]["role"] == "user" and "进度" in messages[-1]["content"]:
                messages[-1] = {"role": "user", "content": _new_progress}
            else:
                messages.append({"role": "user", "content": _new_progress})

            _emit("llm_call", turn=turn)
            response = self._llm.chat(messages, thinking=self._thinking, tools=tools)

            # ── LLM 调用失败 → 立即上报错误 ──
            if response.usage and response.usage.get("error"):
                err_msg = response.usage["error"]
                logger.error("LLM 调用失败 (Turn %d): %s", turn, err_msg)
                if on_event:
                    on_event(AgentEvent(type="error", turn=turn, error=err_msg))
                return []

            turn_record = {
                "turn": turn,
                "request_messages": json.loads(
                    json.dumps(messages, ensure_ascii=False, default=str)
                ),
                "response": {
                    "content": response.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    "reasoning_content": response.reasoning_content,
                    "usage": response.usage,
                },
                "tool_results": [],
            }
            turn_log.append(turn_record)

            if self.verbose and response.usage:
                logger.info(
                    "[Turn %d] %d->%d tokens in %.1fs",
                    turn,
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                    time.time() - t0,
                )

            _emit("llm_response", turn=turn, usage=response.usage)

            if not response.tool_calls:
                self._idle_turns += 1
                remaining = [
                    i for i in ctx.reviewable_ids if i not in executor.reviewed_ids
                ]

                if self._idle_turns >= 3 or (self._idle_turns >= 2 and not remaining):
                    # 连续 3 轮空闲 → 强制退出；或 2 轮空闲且已全部完成 → 正常退出
                    if self.verbose:
                        logger.info(
                            "连续 %d 轮无工具调用，%s于 Turn %d",
                            self._idle_turns,
                            "强制退出" if remaining else "审校完成",
                            turn,
                        )
                    all_actions = list(executor.reviewed_actions.values())
                    _emit(
                        "done",
                        turn=turn,
                        actions=all_actions,
                        messages=messages,
                        turn_log=turn_log,
                    )
                    if remaining:
                        logger.warning(
                            "审校强制退出，仍有 %d 个待审 snap 未处理: %s",
                            len(remaining),
                            remaining,
                        )
                    return all_actions

                # 空闲提示：第 1 轮温和提醒，第 2 轮强调 force_done 选项
                if remaining:
                    if self._idle_turns >= 2:
                        _idle_prompt = (
                            f"### ⏳ 审校尚未完成\n\n"
                            f"**进度**: {len(executor.reviewed_ids)}/{len(ctx.reviewable_ids)}\n"
                            f"剩余 {len(remaining)} 个: {remaining[:10]}\n\n"
                            f"你已经连续 {self._idle_turns} 轮没有操作。"
                            f"请继续审查，或使用 `force_done` 跳过剩余项（需说明理由）。"
                        )
                    else:
                        _idle_prompt = (
                            f"### ⏳ 审校尚未完成\n\n"
                            f"**进度**: {len(executor.reviewed_ids)}/{len(ctx.reviewable_ids)}\n"
                            f"继续审校剩余 {len(remaining)} 个: {remaining[:10]}"
                        )
                else:
                    _idle_prompt = (
                        "### ✅ 待审列表已清空\n\n"
                        "检查是否有遗漏的异常 snap——若有，用 `append` 追加后继续审校。"
                        "确认无遗漏后，调用 `done` 结束。"
                    )
                if messages[-1]["role"] == "user" and "进度" in messages[-1]["content"]:
                    messages[-1] = {"role": "user", "content": _idle_prompt}
                else:
                    messages.append({"role": "user", "content": _idle_prompt})
                continue

            self._idle_turns = 0

            tc_list = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ]
            tc_msg = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": tc_list,
            }
            if getattr(response, "reasoning_content", ""):
                tc_msg["reasoning_content"] = response.reasoning_content
            messages.append(tc_msg)

            for tc in response.tool_calls:
                _emit(
                    "tool_start", turn=turn, tool_name=tc.name, tool_args=tc.arguments
                )
                result = executor.execute(tc)
                if self.verbose:
                    rp = result[:120] + "..." if len(result) > 120 else result
                    logger.info("    -> %s(%s) = %s", tc.name, tc.arguments, rp)
                turn_record["tool_results"].append(
                    {"tool_name": tc.name, "arguments": tc.arguments, "result": result}
                )
                _emit(
                    "tool_result",
                    turn=turn,
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    tool_result=result,
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )

            # ── 显式 done 检查：所有工具执行完后，若 AI 调用了 done 则退出 ──
            has_done = any(tc.name == "done" for tc in response.tool_calls)
            if has_done:
                if self.verbose:
                    logger.info("AI 调用 done，审校完成于 Turn %d", turn)
                all_actions = list(executor.reviewed_actions.values())
                _emit(
                    "done",
                    turn=turn,
                    actions=all_actions,
                    messages=messages,
                    turn_log=turn_log,
                )
                return all_actions

        all_actions = list(executor.reviewed_actions.values())
        _emit(
            "done",
            turn=self.max_turns,
            actions=all_actions,
            messages=messages,
            turn_log=turn_log,
        )

        # ── 审校后校验 ──
        unreviewed = [i for i in ctx.reviewable_ids if i not in executor.reviewed_ids]
        if unreviewed:
            logger.warning(
                "审校完成但仍有 %d 个待审 snap 未处理: %s。请检查。",
                len(unreviewed),
                unreviewed,
            )
        return all_actions

    def _build_initial_messages(self, ctx: ChapterContext) -> List[dict]:
        prompt = _load_system_prompt(self._strategy)
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": self._build_initial_user_message(ctx)},
        ]

    # ═══════════════════════════════════════════════════════════════
    # 6. AiRepairAgent (cont.)

    def _build_initial_user_message(self, ctx: ChapterContext) -> str:
        n_reviewable = len(ctx.reviewable_ids)
        review_set = set(ctx.reviewable_ids)
        total = ctx.total_pairs

        scores = [
            float(ctx.snapshot.original_ops[si][2])
            for si in range(len(ctx.snapshot.original_ops))
        ]
        score_line = ""
        if scores:
            avg = sum(scores) / len(scores)
            # 展示待审 snap 的个体评分（不分桶，AI 可以自己判断）
            review_scores = [
                scores[si] for si in ctx.reviewable_ids if si < len(scores)
            ]
            if review_scores:
                score_line = (
                    f"评分 avg={avg:.0%} | 待审评级: "
                    + ", ".join(
                        f"[{si}]{scores[si]:.0%}"
                        for si in ctx.reviewable_ids[:10]
                        if si < len(scores)
                    )
                    + ("…" if len(ctx.reviewable_ids) > 10 else "")
                )
            else:
                score_line = f"评分 avg={avg:.0%}"

        merged_windows = build_context_windows(
            ctx.reviewable_ids, total, window_size=3, merge_gap_threshold=1
        )

        strategy_label = _STRATEGY_LABEL.get(self._strategy, "原文优先")
        lines = [
            f"**章节**: {ctx.chapter_title or ctx.chapter_id}"
            f" | 策略: {strategy_label}"
            f" | 共 {total} 对 | 待审 {n_reviewable} 对"
        ]
        if score_line:
            lines.append(f"**{score_line}**")
        lines.append("")
        lines.append("待审区域（>> 标记异常，±3 上下文一并展示）：")
        lines.append("")

        for start, end in merged_windows:
            for si in range(start, end + 1):
                info = ctx.get_snap_info(si)
                if info is None:
                    continue
                if si not in review_set:
                    lines.append(f"   [{si}] {info.n_src_rows}:{info.n_tgt_rows}")
                    lines.append(f"    src: {info.src_text}")
                    lines.append(f"    tgt: {info.tgt_text}")
                else:
                    lines.append(f">> {info}")

        lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 7. 操作格式化工具函数
# ═══════════════════════════════════════════════════════════════

_ACTION_ICON = {
    "ok": "✅",
    "edit": "✏️",
    "merge": "🔗",
    "split": "❓",
    "delete": "🗑️",
    "flag": "🚩",
    "view": "📖",
    "placeholder_src": "📄",
    "placeholder_tgt": "📄",
}


def format_action(a, ctx=None) -> str:
    """格式化一条操作为人类可读字符串。"""
    kind = a.kind
    icon = _ACTION_ICON.get(kind, "❓")

    if kind == "ok" and ctx is not None:
        ss = ctx.get_snap_state(a.op_index) if hasattr(ctx, "get_snap_state") else None
        resolved = compute_auto_action_kind(ss, "src") if ss else None
        if resolved:
            resolved_icon = _ACTION_ICON.get(resolved, "❓")
            return f"  {resolved_icon} snap[{a.op_index}]  ok \u2192 {resolved}"
        return f"  {icon} snap[{a.op_index}] {kind}"

    detail = ""
    if kind == "edit":
        new_src = a.data.get("new_src_lines", [])
        new_tgt = a.data.get("new_tgt_lines", [])
        sides = []
        if new_src:
            sides.append("src")
        if new_tgt:
            sides.append("tgt")
        side_label = "+".join(sides) if sides else "?"
        if new_tgt:
            detail += f" {side_label}={new_tgt[0]}"
        elif new_src:
            detail += f" {side_label}={new_src[0]}"
    elif kind == "merge":
        orig = a.data.get("orig_snaps", "")
        detail += f" {'合并' + str(orig) if orig else '单snap'}"
    elif kind == "delete":
        detail += " 批量" if a.data.get("orig_snaps", "") else ""
    elif kind == "flag":
        detail += f" note={a.data.get('note', '')}"

    return f"  {icon} snap[{a.op_index}] {kind}{detail}"


# ═══════════════════════════════════════════════════════════════
# 8. Debug 日志导出
# ═══════════════════════════════════════════════════════════════


def dump_agent_debug(
    ctx: ChapterContext,
    actions: list,
    turn_log: list,
    path: str,
    *,
    prompt_tokens: int = 0,
    cache_tokens: int = 0,
    completion_tokens: int = 0,
    elapsed: float = 0.0,
    extra_info: str = "",
):
    """将 Agent 交互过程导出为人类可读的 Markdown 日志。

    Args:
        ctx: ChapterContext — 章节上下文
        actions: 最终操作列表
        turn_log: 每轮交互记录（由 AiRepairAgent.run 内部收集）
        path: 输出 .md 文件路径
        prompt_tokens/cache_tokens/completion_tokens: token 统计
        elapsed: 耗时（秒）
        extra_info: 额外信息（如标准答案命中率），附加在文件头
    """
    import json as _json

    lines: list[str] = []

    # ── 文件头统计 ──
    lines.append("# AI 审校 Debug 日志\n")
    lines.append(f"- **章节**: {ctx.chapter_id} | {ctx.chapter_title}")
    lines.append(
        f"- **总文本对数**: {ctx.total_pairs} | **待审数**: {len(ctx.reviewable_infos)}"
    )
    done = len([a for a in actions if a.kind != "ok"])
    lines.append(f"- **审校完成**: {done}/{len(ctx.reviewable_infos)}")
    lines.append(f"- **轮次**: {len(turn_log)} | **耗时**: {elapsed:.1f}s")
    lines.append(
        f"- **Token**: 输入 {prompt_tokens} (缓存 {cache_tokens}) -> 输出 {completion_tokens}"
    )
    if extra_info:
        lines.append(f"- **额外**: {extra_info}")
    lines.append("")

    # ── 逐轮记录 ──
    for tr in turn_log:
        turn_n = tr.get("turn", "?")
        lines.append("---")
        lines.append(f"## Turn {turn_n}\n")

        resp = tr.get("response", {})
        usage = resp.get("usage", {})
        if usage:
            lines.append(
                f"**Token**: {usage.get('prompt_tokens', '?')} -> {usage.get('completion_tokens', '?')}\n"
            )

        # 推理过程
        rc = resp.get("reasoning_content", "")
        if rc:
            lines.append("### 推理过程 (reasoning)\n")
            lines.append("```markdown")
            lines.append(rc)
            lines.append("```\n")

        # 模型回答
        cc = resp.get("content", "")
        if cc:
            lines.append("### 响应\n")
            lines.append("```markdown")
            lines.append(cc)
            lines.append("```\n")

        # 工具调用
        has_tool_results = bool(tr.get("tool_results"))
        for tc in resp.get("tool_calls", []):
            args_str = tc.get("arguments", "")
            if isinstance(args_str, dict):
                args_str = _json.dumps(args_str, ensure_ascii=False)
            name = tc.get("name", "?")
            lines.append(f"**Tool:** `{name}({args_str})`\n")

        # 工具结果（inline 在对应工具调用下方）
        if has_tool_results:
            lines.append("### 工具执行结果\n")
            for trr in tr.get("tool_results", []):
                tname = trr.get("tool_name", "?")
                targs = trr.get("arguments", {})
                tres = str(trr.get("result", ""))
                lines.append(f"**{tname}**({targs}):")
                lines.append("```")
                lines.append(tres[:500])  # 截断防止文件过大
                lines.append("```\n")

    # ── 最终操作列表 ──
    lines.append("---")
    lines.append("## 最终操作\n")
    if actions:
        for a in actions:
            lines.append(format_action(a, ctx))
    else:
        lines.append("（无操作）")
    lines.append("")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def dump_agent_raw(
    ctx: ChapterContext,
    actions: list,
    turn_log: list,
    path: str,
    *,
    prompt_tokens: int = 0,
    cache_tokens: int = 0,
    completion_tokens: int = 0,
    elapsed: float = 0.0,
):
    """将 Agent 交互过程导出为完整的 JSON 文件（供自动化分析）。

    JSON 结构:
    {
        "chapter_id": "...",
        "strategy": "...",
        "total_pairs": N,
        "reviewable_count": N,
        "turns": N,
        "elapsed_seconds": N.N,
        "token_usage": {...},
        "final_actions": [...],
        "turn_log": [...]    // 包含完整的 request_messages + response + tool_results
    }
    """
    import json as _json

    data = {
        "chapter_id": ctx.chapter_id,
        "strategy": getattr(ctx, "_strategy", ""),
        "total_pairs": ctx.total_pairs,
        "reviewable_count": len(ctx.reviewable_infos),
        "turns": len(turn_log),
        "elapsed_seconds": round(elapsed, 2),
        "token_usage": {
            "prompt": prompt_tokens,
            "cache": cache_tokens,
            "completion": completion_tokens,
        },
        "final_actions": [
            {"op_index": a.op_index, "kind": a.kind, "source": a.source, "data": a.data}
            for a in actions
        ],
        "turn_log": turn_log,
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2, default=str)
