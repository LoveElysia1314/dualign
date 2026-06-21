"""
Dualign — RepairAction + AiProposalStore

操作分类:
  info-free (仅存 marker): merge, delete, placeholder, flag, ok
  info-full (存完整文本):   split, edit
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from dualign.models.marker import from_kind as _marker_from_kind

# ── 有效操作类型 ──
_VALID_KINDS = frozenset(
    {
        "merge",
        "split",
        "edit",
        "delete",
        "flag",
        "ok",
        "placeholder_src",
        "placeholder_tgt",
    }
)


@dataclass
class RepairAction:
    """单一修复操作。

    op_index:      snapshot index (外部索引)
    kind:          操作类型 (merge|split|edit|delete|flag|ok|placeholder_src|placeholder_tgt)
    sub_count:     合并行数（仅 merge 使用）
    source:        来源: "auto"(CLI自动修复) / "ai"(AI Agent) / "user"(GUI手动)
    data:          附加数据（info-full 时存 new_src_lines/new_tgt_lines/scores；
                   multi-snap 时存 orig_snaps）
    timestamp:     ISO 时间戳
    """

    op_index: int
    kind: str
    sub_count: int = 1
    source: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"未知操作类型: {self.kind}")
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        # 数据兼容：旧格式 report 中 source 可能存在 data 内；source="" → "auto"
        if not self.source and "source" in self.data:
            self.source = self.data.pop("source")
        if not self.source:
            self.source = "auto"

    # ── 属性 ──

    @property
    def marker(self) -> str:
        """返回带来源前缀的 marker 字符串。

        委托给 marker.py 的 from_kind() 统一管理。
        """
        return _marker_from_kind(self.kind, self.source)

    @property
    def is_merge(self) -> bool:
        """是否为合并操作。"""
        return self.kind == "merge"

    # ── Factory methods ──

    @classmethod
    def make_merge(cls, op_index: int, sub_count: int = 1, **kw) -> RepairAction:
        return cls(
            op_index=op_index, kind="merge", sub_count=sub_count, data=dict(**kw)
        )

    @classmethod
    def make_split(cls, op_index: int, **kw) -> RepairAction:
        return cls(op_index=op_index, kind="split", data=dict(**kw))

    @classmethod
    def make_edit(cls, op_index: int, **kw) -> RepairAction:
        return cls(op_index=op_index, kind="edit", data=dict(**kw))

    @classmethod
    def make_delete(cls, op_index: int, **kw) -> RepairAction:
        return cls(op_index=op_index, kind="delete", data=dict(**kw))

    @classmethod
    def make_flag(cls, op_index: int, note: str = "") -> RepairAction:
        return cls(op_index=op_index, kind="flag", data={"note": note})

    @classmethod
    def make_ok(cls, op_index: int) -> RepairAction:
        return cls(op_index=op_index, kind="ok")

    @classmethod
    def make_placeholder_src(cls, op_index: int, **kw) -> RepairAction:
        return cls(op_index=op_index, kind="placeholder_src", data=dict(**kw))

    @classmethod
    def make_placeholder_tgt(cls, op_index: int, **kw) -> RepairAction:
        return cls(op_index=op_index, kind="placeholder_tgt", data=dict(**kw))

    # ── 序列化 ──

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "op_index": self.op_index,
            "kind": self.kind,
            "sub_count": self.sub_count,
            "source": self.source,
            "data": {},
            "timestamp": self.timestamp,
        }
        for k, v in self.data.items():
            d["data"][k] = sorted(v) if isinstance(v, set) else v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RepairAction:
        return cls(
            op_index=int(d["op_index"]),
            kind=d["kind"],
            sub_count=d.get("sub_count", 1),
            source=d.get("source", ""),
            data=d.get("data", {}),
            timestamp=d.get("timestamp", ""),
        )


# ═══════════════════════════════════════════════════════════════
# AiProposal — AI 建议单条记录
# ═══════════════════════════════════════════════════════════════


@dataclass
class AiProposal:
    """单条 AI 建议的完整记录。

    action:   AI 生成的 RepairAction
    status:   "pending" | "accepted" | "rejected"
    created_at: ISO 时间戳
    resolved_at: ISO 时间戳 (采纳/忽略后设置)
    summary:  简短描述文本（用于卡片显示）
    """

    action: RepairAction
    status: str = "pending"
    created_at: str = ""
    resolved_at: str = ""
    summary: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def accept(self):
        self.status = "accepted"
        self.resolved_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def reject(self):
        self.status = "rejected"
        self.resolved_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def reset(self):
        self.status = "pending"
        self.resolved_at = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Optional[AiProposal]:
        try:
            action = RepairAction.from_dict(d["action"])
            return cls(
                action=action,
                status=d.get("status", "pending"),
                created_at=d.get("created_at", ""),
                resolved_at=d.get("resolved_at", ""),
                summary=d.get("summary", ""),
            )
        except Exception:
            return None


@dataclass
class AiProposalStore:
    """AI 建议持久化存储。按 snap_i 分组。

    独立于 repair_log——重置修复不会丢失 AI 建议。
    """

    proposals: Dict[int, List[AiProposal]] = field(default_factory=dict)

    def add(self, snap_i: int, action: RepairAction, summary: str = ""):
        """添加一条 AI 建议到指定 snap。"""
        existing = self.proposals.get(snap_i, [])
        for p in existing:
            if p.action.op_index == action.op_index and p.action.kind == action.kind:
                if p.status == "accepted":
                    return
                if p.status == "pending":
                    p.action = action
                    p.summary = summary
                    p.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                    return
        prop = AiProposal(action=action, summary=summary)
        self.proposals.setdefault(snap_i, []).append(prop)

    def get(self, snap_i: int) -> List[AiProposal]:
        return self.proposals.get(snap_i, [])

    def get_pending(self) -> List[AiProposal]:
        result = []
        for props in self.proposals.values():
            for p in props:
                if p.status == "pending":
                    result.append(p)
        return result

    def accept(self, snap_i: int, action: RepairAction) -> bool:
        for p in self.proposals.get(snap_i, []):
            if p.action.op_index == action.op_index and p.action.kind == action.kind:
                p.accept()
                return True
        return False

    def reject(self, snap_i: int, action: RepairAction) -> bool:
        for p in self.proposals.get(snap_i, []):
            if p.action.op_index == action.op_index and p.action.kind == action.kind:
                p.reject()
                return True
        return False

    def restore(self, snap_i: int, action: RepairAction) -> bool:
        for p in self.proposals.get(snap_i, []):
            if p.action.op_index == action.op_index and p.action.kind == action.kind:
                p.reset()
                return True
        return False

    def reset(self, snap_i: int):
        for p in self.proposals.get(snap_i, []):
            p.reset()

    def get_status(self, snap_i: int, action: RepairAction) -> str | None:
        for p in self.proposals.get(snap_i, []):
            if p.action.op_index == action.op_index and p.action.kind == action.kind:
                return p.status
        return None

    def to_dict(self) -> dict:
        return {
            str(snap_i): [p.to_dict() for p in props]
            for snap_i, props in self.proposals.items()
        }

    @classmethod
    def from_dict(cls, d: dict) -> AiProposalStore:
        store = cls()
        try:
            for snap_s, props_list in d.items():
                snap_i = int(snap_s)
                for pd in props_list:
                    prop = AiProposal.from_dict(pd)
                    if prop is not None:
                        store.proposals.setdefault(snap_i, []).append(prop)
        except Exception:
            pass
        return store
