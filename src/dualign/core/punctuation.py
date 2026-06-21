"""
Dualign — 标点分割器（语言无关句子分割）
==============================

独立的标点感知句子分割模块，用于 N:1 / 1:M 对齐块的修复。

提供:
  - PunctuationHandler   — 标点计数与上下文判定
  - UniversalSplitter    — 硬/软分割点检测（引号感知）
  - calculate_punctuation_similarity — 标点密度相似度
  - detect_language_mix  — 多语言混杂检测
"""

import re
from typing import List, Tuple

# ══════════════════════════════════════════════
# PunctuationHandler
# ══════════════════════════════════════════════


class PunctuationHandler:
    """标点处理工具：计数、上下文判定、相似度计算"""

    ALL_PUNCT_PATTERN = re.compile(r"[^\w\s\u4e00-\u9fff]")

    @staticmethod
    def is_between_ascii_letters(text: str, pos: int) -> bool:
        """判定标点是否夹在 ASCII 字母/数字之间（如 don't, 3.14 不应分割）"""
        if 0 < pos < len(text) - 1:
            left = text[pos - 1]
            right = text[pos + 1]
            try:
                left_ok = left.isascii() and (left.isalpha() or left.isdigit())
                right_ok = right.isascii() and (right.isalpha() or right.isdigit())
                return left_ok and right_ok
            except (UnicodeDecodeError, AttributeError):
                return False
        return False

    @staticmethod
    def is_decimal_point(text: str, pos: int) -> bool:
        """判定 '.' 是否是数字中的小数点"""
        if text[pos] == "." and 0 < pos < len(text) - 1:
            return text[pos - 1].isdigit() and text[pos + 1].isdigit()
        return False

    @staticmethod
    def should_skip_for_splitting(text: str, pos: int) -> bool:
        """判定某位置的标点是否应跳过（不参与分割）"""
        return PunctuationHandler.is_between_ascii_letters(
            text, pos
        ) or PunctuationHandler.is_decimal_point(text, pos)

    @staticmethod
    def count_punctuation_line(line: str) -> int:
        """统计一行的标点符号数量（去重连续相同标点）"""
        puncts = []
        prev_end = -1
        prev_punct = None
        for m in PunctuationHandler.ALL_PUNCT_PATTERN.finditer(line):
            start, end = m.span()
            punct = m.group()
            if PunctuationHandler.is_between_ascii_letters(line, start):
                continue
            if start != prev_end or punct != prev_punct:
                puncts.append(punct)
            prev_end = end
            prev_punct = punct
        return len(puncts)


# ══════════════════════════════════════════════
# UniversalSplitter
# ══════════════════════════════════════════════


class UniversalSplitter:
    """
    语言无关的最小句子分割器，支持中英文混合。

    两种分割类型:
      - 硬分割: 句子结束标点 ( . ! ? 。！？ )
      - 软分割: 从句标点 ( , : ，： ) + 省略号 (...)

    特性:
      - 引号感知：引号内的标点不作为分割点
      - ASCII 单词内标点忽略 (don't → 不分割)
      - 小数点忽略 (3.14 → 不分割)
      - 省略号处理 ("..." → 作为软分割)
      - 引号对分析：前句结束 + 引号闭合 → 硬分割
    """

    SENTENCE_END_PATTERN = r"[.!?。！？]"
    SOFT_SPLIT_PATTERN = r"[,:，：]"

    PAIRS = {
        '"': '"',
        "'": "'",
        "\u201c": "\u201d",
        "\u300c": "\u300d",
        "\u300e": "\u300f",
        "(": ")",
        "\uff08": "\uff09",
        "[": "]",
        "\u3010": "\u3011",
        "{": "}",
        "\uff5b": "\uff5d",
    }
    OPENERS = set(PAIRS.keys())
    CLOSERS = set(PAIRS.values())

    @classmethod
    def _is_part_of_ellipsis(cls, text: str, pos: int) -> bool:
        """判定位置的 '.' 是否属于省略号（≥ 3 个连续点）"""
        if pos >= len(text) or text[pos] != ".":
            return False
        start = pos
        while start > 0 and text[start - 1] == ".":
            start -= 1
        end = pos
        while end < len(text) - 1 and text[end + 1] == ".":
            end += 1
        return (end - start + 1) >= 3

    @classmethod
    def _build_quote_context(cls, text: str) -> List[bool]:
        """构建引号上下文数组。in_quoted[i] = True 表示位置 i 处于某个引号对内部。"""
        n = len(text)
        in_quoted = [False] * n
        stack = []
        i = 0
        while i < n:
            char = text[i]
            try:
                if PunctuationHandler.is_between_ascii_letters(text, i):
                    i += 1
                    continue
            except (IndexError, AttributeError):
                pass
            if (
                char in cls.OPENERS
                and char in cls.CLOSERS
                and cls.PAIRS.get(char) == char
            ):
                if stack and stack[-1][0] == char:
                    _, start = stack.pop()
                    for j in range(start + 1, i):
                        if j < n:
                            in_quoted[j] = True
                else:
                    stack.append((char, i))
            elif char in cls.OPENERS:
                stack.append((char, i))
            elif char in cls.CLOSERS:
                if stack:
                    last_opener, start = stack[-1]
                    expected_closer = cls.PAIRS[last_opener]
                    if char == expected_closer:
                        for j in range(start + 1, i):
                            if j < n:
                                in_quoted[j] = True
                        stack.pop()
            i += 1
        return in_quoted

    @classmethod
    def _find_quote_pairs(cls, text: str) -> List[Tuple[int, int]]:
        """找到所有引号对 (start, close) 位置"""
        n = len(text)
        pairs = []
        stack = []
        i = 0
        while i < n:
            char = text[i]
            try:
                if PunctuationHandler.is_between_ascii_letters(text, i):
                    i += 1
                    continue
            except (IndexError, AttributeError):
                pass
            if (
                char in cls.OPENERS
                and char in cls.CLOSERS
                and cls.PAIRS.get(char) == char
            ):
                if stack and stack[-1][0] == char:
                    _, start = stack.pop()
                    pairs.append((start, i))
                else:
                    stack.append((char, i))
            elif char in cls.OPENERS:
                stack.append((char, i))
            elif char in cls.CLOSERS:
                if stack:
                    last_opener, start = stack[-1]
                    if char == cls.PAIRS[last_opener]:
                        pairs.append((start, i))
                        stack.pop()
            i += 1
        return pairs

    @classmethod
    def _find_unclosed_openers(cls, text: str) -> set:
        """找到未闭合的开放引号位置"""
        n = len(text)
        stack = []
        i = 0
        while i < n:
            char = text[i]
            try:
                if PunctuationHandler.is_between_ascii_letters(text, i):
                    i += 1
                    continue
            except (IndexError, AttributeError):
                pass
            if char in cls.OPENERS:
                stack.append((char, i))
            elif char in cls.CLOSERS:
                if stack:
                    last_opener, _ = stack[-1]
                    if char == cls.PAIRS[last_opener]:
                        stack.pop()
            i += 1
        return {pos for _, pos in stack}

    @classmethod
    def _analyze_quote_pair_split_point(
        cls, text: str, close_pos: int, start_pos: int
    ) -> Tuple[bool, str]:
        """分析引号闭合处是否适合作为分割点。返回: (allow, type)"""
        pos_after_close = close_pos + 1
        if pos_after_close < len(text):
            char_after = text[pos_after_close]
            if char_after in cls.CLOSERS or re.match(r"[.!?。！？]", char_after):
                return (False, "none")
        pos_before_close = close_pos - 1
        while pos_before_close >= 0 and text[pos_before_close].isspace():
            pos_before_close -= 1
        if pos_before_close >= 0:
            char_before = text[pos_before_close]
            if re.match(r"[.!?。！？]", char_before):
                return (True, "hard")
            if char_before == "." and pos_before_close >= 2:
                if cls._is_part_of_ellipsis(text, pos_before_close):
                    return (True, "hard")
        pos_before_open = start_pos - 1
        while pos_before_open >= 0 and text[pos_before_open].isspace():
            pos_before_open -= 1
        if pos_before_open >= 0:
            char_before = text[pos_before_open]
            if re.match(r"[.!?。！？]", char_before):
                return (True, "hard")
            if char_before == "." and pos_before_open >= 2:
                if cls._is_part_of_ellipsis(text, pos_before_open):
                    return (True, "hard")
            return (True, "soft")
        return (True, "soft")

    # ── 公开 API ──

    @classmethod
    def find_hard_split_points(cls, text: str) -> List[int]:
        """找到硬分割点（句子结束标点 + 引号闭合边界）。"""
        if not text:
            return []
        in_quoted = cls._build_quote_context(text)
        quote_pairs = cls._find_quote_pairs(text)
        points = []

        for match in re.finditer(cls.SENTENCE_END_PATTERN, text):
            i = match.start()
            char = match.group()
            pos = match.end()
            if char == "." and i > 0 and i + 1 < len(text):
                if text[i - 1].isdigit() and text[i + 1].isdigit():
                    continue
            if PunctuationHandler.should_skip_for_splitting(text, i):
                continue
            if cls._is_part_of_ellipsis(text, i):
                continue
            if not in_quoted[i]:
                while pos < len(text) and text[pos] in cls.CLOSERS:
                    pos += 1
                if 0 < pos < len(text):
                    points.append(pos)

        for start, close in quote_pairs:
            allow, split_type = cls._analyze_quote_pair_split_point(text, close, start)
            if allow and split_type == "hard":
                pos = close + 1
                while pos < len(text) and text[pos] in cls.CLOSERS:
                    pos += 1
                if 0 < pos <= len(text) and pos not in points:
                    points.append(pos)

        return sorted(points)

    @classmethod
    def find_soft_split_points(cls, text: str) -> List[int]:
        """找到软分割点（逗号、冒号、省略号）。"""
        if not text:
            return []
        in_quoted = cls._build_quote_context(text)
        unclosed = cls._find_unclosed_openers(text)
        points = []

        for match in re.finditer(cls.SOFT_SPLIT_PATTERN, text):
            i = match.start()
            char = match.group()
            pos = match.end()
            if PunctuationHandler.is_between_ascii_letters(text, i):
                continue
            if in_quoted[i]:
                continue
            if i in unclosed:
                continue
            if i - 1 < 0 or i + 1 >= len(text):
                continue
            if char in {",", "\uff0c"}:
                if i > 0 and i + 1 < len(text):
                    if text[i - 1].isdigit() and text[i + 1].isdigit():
                        continue
            if 0 < pos < len(text):
                points.append(pos)

        # 省略号作为软分割
        i = 0
        while i < len(text):
            if text[i] == ".":
                end = i
                while end < len(text) and text[end] == ".":
                    end += 1
                if end - i >= 3:
                    pts = end
                    while pts < len(text) and text[pts] in cls.CLOSERS:
                        pts += 1
                    if pts not in points and pts < len(text):
                        points.append(pts)
                    i = end
                    continue
            i += 1

        return sorted(points)

    @classmethod
    def hard_split(cls, text: str) -> List[str]:
        """按硬分割点拆分文本。"""
        points = cls.find_hard_split_points(text)
        if not points:
            return [text] if text.strip() else []
        parts = []
        prev = 0
        for p in points:
            part = text[prev:p].strip()
            if part:
                parts.append(part)
            prev = p
        part = text[prev:].strip()
        if part:
            parts.append(part)
        return parts


# ══════════════════════════════════════════════
# 标点密度相似度
# ══════════════════════════════════════════════


def calculate_punctuation_similarity(
    src_lines: List[str], tgt_lines: List[str]
) -> float:
    """计算两组文本的标点密度相似度。"""
    src_count = sum(
        PunctuationHandler.count_punctuation_line(line) for line in src_lines
    )
    tgt_count = sum(
        PunctuationHandler.count_punctuation_line(line) for line in tgt_lines
    )
    total = src_count + tgt_count
    if total == 0:
        return 1.0
    return 1.0 - abs(src_count - tgt_count) / total


# ══════════════════════════════════════════════
# 语言杂糅检测
# ══════════════════════════════════════════════


def detect_language_mix(text: str) -> bool:
    """检测英文/拉丁文本中是否混入了 CJK 字符。

    日英翻译中常见问题：日文残留字符混入英文翻译。
    阈值：CJK 字符 + 假名 ≥ 1 且拉丁字母数 > (CJK+假名) × 3

    假名范围只计实际假名字母（平假名 U+3041–U+3096、片假名 U+30A1–U+30FA），
    排除假名区块中的标点符号（如 U+30FB ・ 中点、U+30FC ー 长音符等），
    避免类似 "Minato・Kateru" 这类纯英文文本被误判。
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    # 只计假名字母，排除标点符号
    hiragana = sum(1 for ch in text if "\u3041" <= ch <= "\u3096")
    katakana = sum(1 for ch in text if "\u30a1" <= ch <= "\u30fa")
    kana = hiragana + katakana
    cjk_total = cjk + kana
    if cjk_total == 0:
        return False
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return latin > cjk_total * 3
