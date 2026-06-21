"""
Dualign — FilePairMatcher: 批量文件对发现与匹配引擎

自动从两个目录中发现并匹配源/目标文件对，支持多种匹配规则。

规则引擎:
  - prefix:  文件名前缀匹配 (ch01 → ch01)
  - glob:    glob 通配符匹配 (*.source.md ↔ *.target.md)
  - regex:   正则捕获组提取 ID 配对
  - json_map:显式 JSON 映射文件

用法:
    matcher = FilePairMatcher()
    pairs = matcher.match(
        src_dir="data/chapters/2930/vol_001/raw",
        tgt_dir="data/chapters/2930/vol_001/raw",
        rules=[MatchRule(type="prefix", tgt_pattern="*.target.md")],
    )
"""

from __future__ import annotations

import json
import re
import fnmatch
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dualign.common import FilePair  # noqa: F401

# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════


@dataclass
class MatchedPair:
    """文件对匹配结果。供 FilePairMatcher.match() 及 GUI 批量发现使用。"""

    entry_id: str
    label: str
    src_path: str
    tgt_path: str


@dataclass
class MatchRule:
    """文件对匹配规则。

    Attributes:
        type:     匹配方式
        src_pattern: 源文件匹配模式 (仅 glob/regex 时有效)
        tgt_pattern: 目标文件匹配模式 (仅 glob/regex 时有效)
        id_group:    正则捕获组编号 (仅 regex 时有效)
        suffix_pair: 后缀对，如 (".source.md", ".target.md")，用于 prefix 模式
        sort_key:    当多个候选时如何排序以配对
    """

    type: Literal["prefix", "glob", "regex", "json_map"] = "prefix"
    src_pattern: str = "*.source.md"
    tgt_pattern: str = "*.target.md"
    id_group: int = 1
    suffix_pair: tuple[str, str] = (".source.md", ".target.md")
    sort_key: str = "natural"  # "name" | "numeric" | "natural"


# ═══════════════════════════════════════════════════════════════
# 匹配引擎
# ═══════════════════════════════════════════════════════════════


class FilePairMatcher:
    """批量文件对匹配器。"""

    # ── 公开匹配入口 ──

    def match(
        self,
        src_dir: str | Path,
        tgt_dir: str | Path,
        rules: list[MatchRule] | None = None,
    ) -> list[MatchedPair]:
        """按规则匹配文件对。

        Args:
            src_dir: 源文件目录
            tgt_dir: 目标文件目录
            rules:   匹配规则列表。为 None 时使用默认规则。

        Returns:
            匹配成功的文件对列表
        """
        src_dir = Path(src_dir)
        tgt_dir = Path(tgt_dir)

        if not src_dir.is_dir():
            raise ValueError(f"源目录不存在: {src_dir}")
        if not tgt_dir.is_dir():
            raise ValueError(f"目标目录不存在: {tgt_dir}")

        if rules is None:
            rules = self._default_rules()

        # 收集文件
        src_files = self._collect_files(src_dir)
        tgt_files = self._collect_files(tgt_dir)

        if not src_files:
            return []
        if not tgt_files:
            return []

        # 逐规则尝试匹配
        for rule in rules:
            if rule.type == "json_map":
                # JSON 映射需要特殊处理—从文件读取
                pairs = self._match_by_json_map(rule, src_dir, tgt_dir)
            else:
                pairs = self._match_by_rule(src_files, tgt_files, rule)
            if pairs:
                return pairs

        # 无规则匹配 → 尝试最简单的排序匹配
        return self._fallback_match(src_files, tgt_files)

    # ── 默认规则 ──

    @staticmethod
    def _default_rules() -> list[MatchRule]:
        """返回一组合理的默认匹配规则（优先级从高到低）。"""
        return [
            # 规则 1: glob — 最常见的命名约定
            MatchRule(
                type="glob", src_pattern="*.source.md", tgt_pattern="*.target.md"
            ),
            # 规则 2: glob — 缩写
            MatchRule(type="glob", src_pattern="*.src.md", tgt_pattern="*.tgt.md"),
            # 规则 3: glob — 通用文本
            MatchRule(type="glob", src_pattern="*.zh.md", tgt_pattern="*.en.md"),
            MatchRule(type="glob", src_pattern="*.cn.md", tgt_pattern="*.en.md"),
            # 规则 4: suffix 匹配
            MatchRule(type="prefix", suffix_pair=(".source.md", ".target.md")),
            MatchRule(type="prefix", suffix_pair=(".src.md", ".tgt.md")),
            # 规则 5: 纯文本无后缀
            MatchRule(type="prefix", suffix_pair=(".md", ".md")),
        ]

    # ── 文件收集 ──

    @staticmethod
    def _collect_files(directory: Path) -> list[Path]:
        """递归收集目录下所有文本文件。"""
        files = []
        for p in directory.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".text"}:
                files.append(p)
        return sorted(files)

    # ── 按规则匹配 ──

    @staticmethod
    def _match_by_rule(
        src_files: list[Path],
        tgt_files: list[Path],
        rule: MatchRule,
    ) -> list[MatchedPair]:
        """按单条规则匹配文件对。"""
        if rule.type == "glob":
            return FilePairMatcher._match_by_glob(src_files, tgt_files, rule)
        elif rule.type == "prefix":
            return FilePairMatcher._match_by_prefix(src_files, tgt_files, rule)
        elif rule.type == "regex":
            return FilePairMatcher._match_by_regex(src_files, tgt_files, rule)
        return []

    @staticmethod
    def _match_by_glob(
        src_files: list[Path],
        tgt_files: list[Path],
        rule: MatchRule,
    ) -> list[MatchedPair]:
        """用 glob 模式匹配。"""
        matched_src = {
            f for f in src_files if fnmatch.fnmatch(f.name, rule.src_pattern)
        }
        matched_tgt = {
            f for f in tgt_files if fnmatch.fnmatch(f.name, rule.tgt_pattern)
        }

        if not matched_src or not matched_tgt:
            return []

        # 按 natural 排序后配对
        src_sorted = FilePairMatcher._natural_sort(matched_src)
        tgt_sorted = FilePairMatcher._natural_sort(matched_tgt)

        return FilePairMatcher._zip_pairs(src_sorted, tgt_sorted)

    @staticmethod
    def _match_by_prefix(
        src_files: list[Path],
        tgt_files: list[Path],
        rule: MatchRule,
    ) -> list[MatchedPair]:
        """用文件名前缀匹配。

        对每个源文件，去掉 suffix_pair[0] 得到前缀，
        找到目标目录中前缀相同且后缀为 suffix_pair[1] 的文件。
        """
        src_suffix, tgt_suffix = rule.suffix_pair
        pairs: list[MatchedPair] = []

        # 构建目标文件映射: 前缀 -> [target_paths]
        tgt_map: dict[str, list[Path]] = {}
        for f in tgt_files:
            name = f.name
            if tgt_suffix and name.endswith(tgt_suffix):
                prefix = name[: -len(tgt_suffix)]
                tgt_map.setdefault(prefix, []).append(f)
            elif not tgt_suffix:
                tgt_map.setdefault(name, []).append(f)

        for sf in src_files:
            name = sf.name
            prefix = name
            if src_suffix and name.endswith(src_suffix):
                prefix = name[: -len(src_suffix)]
            elif src_suffix:
                continue  # 不匹配源后缀的跳过

            if prefix in tgt_map:
                for tf in tgt_map[prefix]:
                    pairs.append(
                        MatchedPair(
                            entry_id=prefix,
                            label=prefix,
                            src_path=str(sf.resolve()),
                            tgt_path=str(tf.resolve()),
                        )
                    )
        return pairs

    @staticmethod
    def _match_by_regex(
        src_files: list[Path],
        tgt_files: list[Path],
        rule: MatchRule,
    ) -> list[MatchedPair]:
        """用正则捕获组匹配。"""
        src_pat = re.compile(rule.src_pattern)
        tgt_pat = re.compile(rule.tgt_pattern)

        src_map: dict[str, Path] = {}
        for f in src_files:
            m = src_pat.match(f.name)
            if m:
                g = m.group(rule.id_group)
                src_map[g] = f

        tgt_map: dict[str, Path] = {}
        for f in tgt_files:
            m = tgt_pat.match(f.name)
            if m:
                g = m.group(rule.id_group)
                tgt_map[g] = f

        pairs: list[MatchedPair] = []
        common_ids = set(src_map) & set(tgt_map)
        for cid in sorted(common_ids):
            pairs.append(
                MatchedPair(
                    entry_id=cid,
                    label=cid,
                    src_path=str(src_map[cid].resolve()),
                    tgt_path=str(tgt_map[cid].resolve()),
                )
            )
        return pairs

    @staticmethod
    def _match_by_json_map(
        rule: MatchRule,
        src_dir: Path,
        tgt_dir: Path,
    ) -> list[MatchedPair]:
        """读取 JSON 映射文件进行匹配。"""
        map_path = Path(rule.tgt_pattern)
        if not map_path.is_absolute():
            map_path = tgt_dir / rule.tgt_pattern
        if not map_path.is_file():
            map_path = src_dir / rule.tgt_pattern
        if not map_path.is_file():
            return []

        try:
            with open(map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        pairs: list[MatchedPair] = []
        raw_pairs = data.get("pairs", data if isinstance(data, list) else [])
        for item in raw_pairs:
            if isinstance(item, dict):
                src = item.get("src", "")
                tgt = item.get("tgt", "")
                label = item.get("label", item.get("id", ""))
                if src and tgt:
                    src_path = Path(src)
                    tgt_path = Path(tgt)
                    if not src_path.is_absolute():
                        src_path = src_dir / src
                    if not tgt_path.is_absolute():
                        tgt_path = tgt_dir / tgt
                    if src_path.is_file() and tgt_path.is_file():
                        pairs.append(
                            MatchedPair(
                                entry_id=label or src_path.stem,
                                label=label or src_path.stem,
                                src_path=str(src_path.resolve()),
                                tgt_path=str(tgt_path.resolve()),
                            )
                        )
        return pairs

    # ── 兜底匹配 ──

    @staticmethod
    def _fallback_match(
        src_files: list[Path],
        tgt_files: list[Path],
    ) -> list[MatchedPair]:
        """兜底：按文件名 natural sort 顺序配对。"""
        src_sorted = FilePairMatcher._natural_sort(src_files)
        tgt_sorted = FilePairMatcher._natural_sort(tgt_files)
        return FilePairMatcher._zip_pairs(src_sorted, tgt_sorted)

    # ── 工具函数 ──

    @staticmethod
    def _zip_pairs(
        src_sorted: list[Path],
        tgt_sorted: list[Path],
    ) -> list[MatchedPair]:
        """等长配对。"""
        n = min(len(src_sorted), len(tgt_sorted))
        pairs: list[MatchedPair] = []
        for i in range(n):
            sf = src_sorted[i]
            tf = tgt_sorted[i]
            label = sf.stem
            pairs.append(
                MatchedPair(
                    entry_id=label,
                    label=label,
                    src_path=str(sf.resolve()),
                    tgt_path=str(tf.resolve()),
                )
            )
        return pairs

    @staticmethod
    def _natural_sort(paths: list[Path]) -> list[Path]:
        """自然排序（human-friendly numeric sorting）。"""

        def _key(p: Path):
            name = p.stem
            parts = re.split(r"(\d+)", name)
            return [int(part) if part.isdigit() else part.lower() for part in parts]

        return sorted(paths, key=_key)
