# -*- coding: utf-8 -*-
from __future__ import annotations
from collections import Counter
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

# ============ 常规 FAIL/NG 行 ============
_FAIL_PATTERNS = [
    re.compile(r'^\s*([^\s]+)\s+HAS\s+FAILED\b', re.IGNORECASE),
    re.compile(r'\b(?:FAIL(?:ED)?|NG)\b[\s:，,:;\-]*([^\s\]]+)', re.IGNORECASE),
    re.compile(r'\[([^\]]+)\]\s*\b(?:FAIL(?:ED)?|NG)\b', re.IGNORECASE),
    re.compile(r'零件(?:名|名称)?[:：]\s*([^\s，,;:]+).*?(?:失败|不合格|NG|\bFAIL(?:ED)?\b)', re.IGNORECASE),
    re.compile(r'([^\s，,;:]+)[\s:，,:;\-]*\b(?:FAIL(?:ED)?|NG|失败|不合格)\b', re.IGNORECASE),
]

# ============ Open/Shorts 设备 token ============
# 例：3%p4268.1 / 3%t4252%nfet.G  -> 3%p4268 / 3%t4252%nfet
_DEV_TOKEN = re.compile(r"""
    \b(
        \d+%                 # 3%
        [a-z][a-z0-9]+       # p4268 / r4256 / t4252
        (?:%[a-z0-9_]+)?     # %nfet（可选）
        (?:\.[A-Za-z0-9_]+)? # .1/.G（可选）
    )\b
""", re.IGNORECASE | re.VERBOSE)

# 简洁箭头行：Open: a ↔ b / Short: a -> b
_ARROW_LINE = re.compile(
    r'\b(?:OPEN|SHORTS?|SHORT)\s*[:：]\s*([^\s]+)\s*([↔<>\-\u2192\u2194\u2013\u2014]+)\s*([^\s]+)',
    re.IGNORECASE
)

# ============ 噪声与过滤 ============
_STOP_WORDS = {'TEST','TOTAL','SUMMARY','PASS','FAIL','NG','ED','HAS'}

_FILENAME_LIKE = re.compile(r'''(?ix)
^(
   \d{10,}(?:[-_]\d+)?    # 6091... 或带 -01 后缀
 | [A-Z0-9]{10,}
)(?:\.txt)?$
''')

# 典型白名单：序号%位号 或 R/C/L/Q/D/Z/U/J/T 开头的位号
# 修复：允许零件名中包含下划线（如 1%s21000_clamp_diode2）
_WHITELIST = re.compile(
    r'^(?:\d+%[a-z][a-z0-9_]+(?:%[a-z0-9_]+)?|[RCLQDZUJT][0-9]{1,5}[A-Za-z0-9\-_]*)$',
    re.IGNORECASE
)

def _normalize_dev(token: str) -> str:
    """去 .引脚并统一小写，便于合并统计。"""
    token = token.strip()
    token = re.sub(r'\.[A-Za-z0-9_]+$', '', token)
    return token.lower()  # <<< 统一小写

def _is_valid_part(name: str) -> bool:
    n = name.strip().strip(',:;[](){}<>')
    if len(n) < 2: return False
    if not re.search(r'[A-Za-z%]', n): return False
    if _FILENAME_LIKE.match(n): return False
    if n.upper() in _STOP_WORDS: return False
    if len(n) > 40: return False
    if not _WHITELIST.match(n): return False
    return True

# ====== 只统计 From/To 主位号，合并成一条 "Open: A ↔ B" / "Short: A ↔ B" ======

# 识别“Open #… / Shorts Report …”块，并只取 From/To 行各自的第一个设备 token
def _extract_open_short_pairs_from_blocks(text: str) -> list[str]:
    pairs = []
    in_block = False
    current_kind = "Open"  # 仅用于显示；当遇到 shorts report 时设为 Short

    for raw in text.splitlines():
        line = raw.strip()
        low  = line.lower()

        # 进入块
        if low.startswith('open #'):
            in_block = True
            current_kind = "Open"
            from_main = to_main = None
            continue
        if low.startswith('shorts report'):
            in_block = True
            current_kind = "Short"
            from_main = to_main = None
            continue

        # 结束块
        if in_block and (line.startswith('------End')
                         or low.startswith('message is:')
                         or low.startswith('serial #')
                         or low.startswith('total of')):
            # 成块才产出
            if from_main and to_main:
                pairs.append(f"{current_kind}: {from_main} ↔ {to_main}")
            in_block = False
            continue

        if not in_block:
            continue

        # 只看 From: / To: 这两行，并且只取各自的“第一个设备 token”
        if low.startswith('from:'):
            m = _DEV_TOKEN.search(line)
            if m:
                token = _normalize_dev(m.group(1))
                if _is_valid_part(token):
                    from_main = token
            continue

        if low.startswith('to:'):
            m = _DEV_TOKEN.search(line)
            if m:
                token = _normalize_dev(m.group(1))
                if _is_valid_part(token):
                    to_main = token
            continue

    return pairs

# 箭头行（例如：Open: 3%p4268 ↔ 3%p4253 / Short: 2%r1 -> 2%r2）直接合并成一条
def _extract_open_short_pairs_from_arrow(text: str) -> list[str]:
    pairs = []
    for line in text.splitlines():
        m = _ARROW_LINE.search(line)
        if not m:
            continue
        a, _, b = m.groups()
        a, b = _normalize_dev(a), _normalize_dev(b)
        if _is_valid_part(a) and _is_valid_part(b):
            # 这里用 line 前缀来判断 Open/Short；默认 Open
            kind = "Open" if re.search(r'\bopen\b', line, re.IGNORECASE) else \
                   ("Short" if re.search(r'\bshorts?\b', line, re.IGNORECASE) else "Open")
            pairs.append(f"{kind}: {a} ↔ {b}")
    return pairs


# ============ 解析：常规 FAIL/NG ============
def _extract_from_fail_lines(text: str) -> list[str]:
    parts = []
    for line in text.splitlines():
        l = line.strip()
        if not l: continue
        for pat in _FAIL_PATTERNS:
            m = pat.search(l)
            if m:
                token = _normalize_dev(m.group(1))
                if _is_valid_part(token):
                    parts.append(token)
                break
    return parts

def extract_failed_parts(text: str) -> list[str]:
    """
    返回列表既可能是“零件名”，也可能是合并后的
    'Open: A ↔ B' / 'Short: A ↔ B'。计数时仍然是 Counter 逐项聚合。
    """
    parts = []

    # ① Open/Short：每块/每行仅一条合并信息
    parts.extend(_extract_open_short_pairs_from_arrow(text))
    parts.extend(_extract_open_short_pairs_from_blocks(text))

    # ② 常规 FAIL/NG 行：逐个零件名
    parts.extend(_extract_from_fail_lines(text))

    return parts


# ============ I/O & 并发 ============
def read_text_with_fallback(p: Path) -> str:
    for enc in ('utf-8','utf-8-sig','gb18030','gbk','ansi'):
        try:
            return p.read_text(encoding=enc, errors='ignore')
        except Exception:
            continue
    return ''

def count_file_parts(file_path: Path) -> Counter:
    return Counter(extract_failed_parts(read_text_with_fallback(file_path)))

def concurrent_scan_counts(root_dir: Path, max_workers: int = 8) -> Counter:
    counts = Counter()
    if not root_dir.exists(): return counts
    files = list(sorted(root_dir.glob('*.txt')))
    if not files: return counts
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(count_file_parts, f) for f in files]
        for fu in as_completed(futs):
            try:
                counts.update(fu.result())
            except Exception:
                continue
    return counts

def build_model_dir(base_dir: str, qdate, platform: str, model: str) -> Path:
    if hasattr(qdate, 'year'):
        y, m, d = qdate.year(), qdate.month(), qdate.day()
    else:
        y, m, d = qdate
    return Path(base_dir) / str(y) / f'{m:02d}' / f'{d:02d}' / platform / model
