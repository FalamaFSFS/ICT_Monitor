# -*- coding: utf-8 -*-
import os, time
import re
from typing import List, Optional, Union  # <--- 【为兼容win7,新增这行】
from datetime import datetime, date
from PyQt5.QtCore import QThread, pyqtSignal
import sys
import json
from alerts_state_json import AlertsState



def get_app_dir() -> str:
    # PyInstaller 打包后
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # 源码运行
    return os.path.dirname(os.path.abspath(__file__))


def load_agent_conf():
    base_dir = get_app_dir()
    conf_path = os.path.join(base_dir, "config", "agent.json")

    if not os.path.exists(conf_path):
        raise FileNotFoundError(f"配置文件不存在：{conf_path}")

    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception as e:
        raise RuntimeError(f"agent.json 解析失败，请检查 JSON 格式：{e}")

    if "SITE_ID" not in conf:
        raise KeyError("agent.json 中缺少 SITE_ID 字段")

    if "STATE_PATH" not in conf or not conf["STATE_PATH"]:
        conf["STATE_PATH"] = os.path.join(base_dir, "state", f"{conf['SITE_ID']}_state.json")

    if "SHARE_PATH" not in conf:
        raise KeyError("agent.json 中缺少 SHARE_PATH 字段")

    conf["FAIL_THRESHOLD"] = int(conf.get("FAIL_THRESHOLD", 3))

    return conf


_CONF = load_agent_conf()

SITE_ID = _CONF["SITE_ID"]
STATE_PATH = _CONF["STATE_PATH"]
SHARE_PATH = _CONF["SHARE_PATH"]
FAIL_THRESHOLD = _CONF["FAIL_THRESHOLD"]

# —— wirelist 缓存 —— #
_fixture_cache = {}
_CACHE_REV = 2

_TEXT_EXTS = (".txt", ".lst", ".csv", ".log", ".ini", ".cfg", "")


def _read_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()
    except Exception:
        return []


HEAD_VER_RE = re.compile(r'^test\s+analog\s+"([^"]+)"\s+version\s+"([^"]+)"\s*$', re.IGNORECASE)
HEAD_GEN_RE = re.compile(r'^test\s+analog\s+"([^"]+)"\s*$', re.IGNORECASE)
END_RE = re.compile(r'^\s*end\s+test\s*$', re.IGNORECASE)
WIRE_RE = re.compile(r'^\s*wire\b', re.IGNORECASE)

_OPEN_SHORT_RE = re.compile(r'^\s*(Open|Short)s?\s*#\s*\d+', re.IGNORECASE)


def _parse_open_short_block(lines, i, file_time, boards_root, fixture, board, file_path):
    header = lines[i]
    kind = "Open" if header.strip().lower().startswith("open") else "Short"

    threshold = ""
    for j in range(i, min(i + 5, len(lines))):
        m = re.search(r'Thresh\s+([0-9.]+)', lines[j], flags=re.IGNORECASE)
        if m:
            threshold = m.group(1)
            break

    from_net, to_net = "", ""
    from_nodes, to_nodes = [], []
    measured = ""  # 用于存储测量值

    stop_re = re.compile(r'(^-+\s*$)|(^serial\s*:)|(\b(Open|Short)s?\s*#\s*\d+)', re.IGNORECASE)
    next_i = i + 1
    phase = None

    for j in range(i + 1, min(i + 200, len(lines))):
        s = lines[j]
        if stop_re.search(s):
            next_i = j
            break

        sl = s.strip()
        if sl.lower().startswith("from:"):
            toks = sl[5:].strip().split()
            from_net = toks[0] if toks else ""
            # 提取最后一个数字作为测量值
            if len(toks) > 1:
                measured = toks[-1]
            phase = 'from'
            continue
        if sl.lower().startswith("to:"):
            toks = sl[3:].strip().split()
            to_net = toks[0] if toks else ""
            # 如果From行没有测量值,尝试从To行获取
            if not measured and len(toks) > 1:
                measured = toks[-1]
            phase = 'to'
            continue

        if phase == 'from':
            if sl and not sl.lower().startswith(("to:", "open", "short")):
                from_nodes.append(sl)
        elif phase == 'to':
            if sl and not sl.lower().startswith(("open", "short")):
                to_nodes.append(sl)

    block_time = _find_near_time_for_block(lines, i) or file_time

    # part_text = f"{kind}: {from_net} ↔ {to_net}" if (from_net and to_net) else f"{kind}"

    def _short_list(lst, k=6):
        lst = [x for x in lst if x]
        if len(lst) <= k:
            return ", ".join(lst)
        return ", ".join(lst[:k]) + f" …(+{len(lst) - k})"

    desc_lines = []
    if from_net:
        desc_lines.append(f"From {from_net}: " + _short_list(from_nodes))
    if to_net:
        desc_lines.append(f"To {to_net}: " + _short_list(to_nodes))
    if threshold:
        desc_lines.append(f"Thresh={threshold} (阀值)")

    desc = "; ".join([d for d in desc_lines if d]) or f"{kind} detected"

    rec = {
        "time": block_time,
        "fixture": "",
        "board": "",
        "file": os.path.basename(file_path),
        "part": kind,  # 只显示Open/Short
        "nominal": threshold,
        "low": "",
        "measured": kind,  # 测试值固定写Open/Short
        "high": "",
        "threshold": threshold,  # 阈值
        "desc": "详情见文件",
        "test_source": ""  # Open/Short 类型不适用测试源
    }
    return rec, next_i


def _extract_blocks(lines):
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\n")
        m1 = HEAD_VER_RE.match(line)
        m2 = HEAD_GEN_RE.match(line) if not m1 else None
        if m1 or m2:
            part = m1.group(1) if m1 else m2.group(1)
            board = m1.group(2) if m1 else None
            i += 1
            block_lines = []
            while i < n and not END_RE.match(lines[i]):
                ln = lines[i].rstrip("\n")
                block_lines.append(ln.strip())
                i += 1
            if i < n and END_RE.match(lines[i]):
                i += 1
            while block_lines and not block_lines[0]:
                block_lines.pop(0)
            while block_lines and not block_lines[-1]:
                block_lines.pop()

            blocks.append({
                "part": part,
                "board": board,
                "wires": block_lines
            })

        else:
            i += 1
    return blocks


def _scan_wire_files(folder):
    pri, sec = [], []
    for root, _, files in os.walk(folder):
        for fn in files:
            p = os.path.join(root, fn)
            low = fn.lower()
            if not low.endswith(_TEXT_EXTS):
                continue
            if "wire" in low and "list" in low:
                pri.append(p)
            else:
                sec.append(p)
    return pri + sec


def _find_desc_lines(boards_root, fixture, part, board, boards_root_fallback=""):
    # 先在主目录查找
    folder = os.path.join(boards_root, fixture) if fixture else ""
    if folder and os.path.isdir(folder):
        for fp in _scan_wire_files(folder):
            lines = _read_lines(fp)
            if not lines:
                continue
            blocks = _extract_blocks(lines)
            for b in blocks:
                if b["part"] == part and b["board"] == board and b["wires"]:
                    return b["wires"]
            for b in blocks:
                if b["part"] == part and b["board"] is None and b["wires"]:
                    return b["wires"]

    # 主目录找不到，尝试备用目录
    if boards_root_fallback and fixture:
        folder_fallback = os.path.join(boards_root_fallback, fixture)
        if os.path.isdir(folder_fallback):
            for fp in _scan_wire_files(folder_fallback):
                lines = _read_lines(fp)
                if not lines:
                    continue
                blocks = _extract_blocks(lines)
                for b in blocks:
                    if b["part"] == part and b["board"] == board and b["wires"]:
                        return b["wires"]
                for b in blocks:
                    if b["part"] == part and b["board"] is None and b["wires"]:
                        return b["wires"]

    return []


def find_wire_desc(boards_root: str, fixture: str, part: str, board: str, boards_root_fallback: str = "") -> str:
    if not fixture or not part:
        return ""
    cache = _fixture_cache.setdefault(fixture, {})

    key_ver = (part, board or "", _CACHE_REV)
    key_gen = (part, None, _CACHE_REV)

    if key_ver in cache:
        return cache[key_ver]
    if key_gen in cache:
        return cache[key_gen]

    lines = _find_desc_lines(boards_root, fixture, part, board, boards_root_fallback)
    if lines:
        desc = "\n".join(lines)
        cache[key_ver] = desc
        return desc

    lines = _find_desc_lines(boards_root, fixture, part, None, boards_root_fallback)
    desc = "\n".join(lines) if lines else ""
    cache[key_gen] = desc
    return desc


# ==================== testorder 解析：判断测试源 ====================

# testorder 缓存: { fixture: { (part, board): "主程序"/"子程序"/"" } }
_testorder_cache = {}

# 匹配 testorder 中的行
# test jumper "r4839" version "F01RC0TA66"; preshorts  -> 子程序
# skip jumper "r4839" version "F01R20TA05"; preshorts  -> 跳过此版本
# test jumper "r4839"; preshorts                       -> 主程序（无 version）
# test jumper "r5019" !no test probe                   -> 主程序（无 version）
# 注意：行尾可能有分号、感叹号和其他文字，这些都不影响判断
_TESTORDER_LINE_RE = re.compile(
    r'^(test|skip)\s+\w+\s+"([^"]+)"(?:\s+version\s+"([^"]+)")?',
    re.IGNORECASE
)


def _parse_testorder_file(filepath: str) -> dict:
    """
    解析 testorder 文件，返回结构：
    {
        (part, board): "主程序" or "子程序",
        (part, None): "主程序",  # 无 version 的通用行
        ...
    }
    """
    result = {}
    lines = _read_lines(filepath)

    for line in lines:
        line = line.strip()
        if not line:
            continue

        m = _TESTORDER_LINE_RE.match(line)
        if not m:
            continue

        action = m.group(1).lower()  # test / skip
        part = m.group(2)  # 零件名
        board = m.group(3)  # 板子型号（可能为 None）

        if action == "test":
            if board:
                # test xxx "part" version "board" -> 子程序
                result[(part, board)] = "子程序"
            else:
                # test xxx "part" (无 version) -> 主程序
                result[(part, None)] = "主程序"
        elif action == "skip":
            # skip 行本身不直接决定测试源，但记录下来用于判断
            # 如果某个 (part, board) 是 skip，说明这个版本跳过，可能在主程序测
            result[(part, board, "skip")] = True

    return result


def _find_testorder_file(boards_root: str, fixture: str, boards_root_fallback: str = "") -> str:
    """
    查找 testorder 文件路径
    位置: C:/Agilent_ICT/boards/<fixture>/testorder 或 C:/I3070_ICT/boards/<fixture>/testorder
    """
    if not boards_root or not fixture:
        return ""

    # 候选文件名
    candidates = ["testorder", "testorder.txt", "TESTORDER", "TESTORDER.TXT"]

    # 先在主目录查找
    folder = os.path.join(boards_root, fixture)
    if os.path.isdir(folder):
        for name in candidates:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path

    # 主目录找不到，尝试备用目录
    if boards_root_fallback:
        folder_fallback = os.path.join(boards_root_fallback, fixture)
        if os.path.isdir(folder_fallback):
            for name in candidates:
                path = os.path.join(folder_fallback, name)
                if os.path.isfile(path):
                    return path

    return ""


def get_test_source(boards_root: str, fixture: str, part: str, board: str, boards_root_fallback: str = "") -> str:
    """
    判断零件的测试源（主程序/子程序）

    逻辑：
    1. 找到 testorder 文件
    2. 查找 test xxx "part" version "board" -> 子程序
    3. 如果是 skip xxx "part" version "board"，但有 test xxx "part" (无version) -> 主程序
    4. 如果只有 test xxx "part" (无version) -> 主程序
    5. 找不到则返回空字符串

    参数:
        boards_root: boards 根目录，如 C:/Agilent_ICT/boards
        fixture: 工装型号，如 UA008
        part: 零件名，如 r1343 或 1%c0077
        board: 板子型号，如 F01R20DM0V
        boards_root_fallback: 备用 boards 目录，如 C:/I3070_ICT/boards

    返回:
        "主程序" / "子程序" / ""
    """
    if not fixture or not part:
        return ""

    # 处理零件名：如果包含 %，提取 % 后面的部分（如 1%c0077 -> c0077）
    clean_part = part
    if '%' in part:
        clean_part = part.split('%', 1)[1]

    # 检查缓存
    cache = _testorder_cache.setdefault(fixture, {})
    cache_key = (clean_part, board)
    if cache_key in cache:
        return cache[cache_key]

    # 查找并解析 testorder 文件
    testorder_path = _find_testorder_file(boards_root, fixture, boards_root_fallback)
    if not testorder_path:
        cache[cache_key] = ""
        return ""

    # 解析文件（整个文件只解析一次，结果放入缓存）
    if "_parsed" not in cache:
        cache["_parsed"] = _parse_testorder_file(testorder_path)

    parsed = cache["_parsed"]

    # 判断逻辑（使用 clean_part 而不是 part）
    # 1. 先看是否有 test xxx "part" version "board" -> 子程序
    if (clean_part, board) in parsed and parsed[(clean_part, board)] == "子程序":
        result = "子程序"
    # 2. 看是否是 skip 这个版本，但有主程序的 test
    elif (clean_part, board, "skip") in parsed and (clean_part, None) in parsed:
        result = "主程序"
    # 3. 只有无 version 的 test -> 主程序
    elif (clean_part, None) in parsed and parsed[(clean_part, None)] == "主程序":
        result = "主程序"
    else:
        result = ""

    cache[cache_key] = result
    return result


# —— 报文解析 —— #

_WEEK = "(Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_MONTH = "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TIME = "([0-2]\\d:[0-5]\\d:[0-5]\\d)"
_YEAR = "(19\\d{2}|20\\d{2}|21\\d{2})"
TIME_RE = re.compile(rf"{_WEEK}\s+{_MONTH}\s+(\d{{1,2}})\s+{_TIME}\s+{_YEAR}")

MON_MAP = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def _time_from_header_line(line: str) -> Optional[str]:
    m = re.search(
        r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+'
        r'(\d{1,2})\s+'
        r'(\d{2}:\d{2}:\d{2})\s+'
        r'(\d{4})',
        line, flags=re.IGNORECASE
    )
    if not m:
        return None

    mon_en = m.group(2).title()
    dd = int(m.group(3))
    t_hms = m.group(4)

    mm = MON_MAP.get(mon_en)
    if not mm:
        return None

    return f"{mm}-{dd:02d} {t_hms}"


def _find_near_time_for_block(lines: List[str], anchor_idx: int) -> Optional[str]:
    start = max(0, anchor_idx - 25)
    for j in range(anchor_idx, start - 1, -1):
        ts = _time_from_header_line(lines[j])
        if ts:
            return ts
    end = min(len(lines), anchor_idx + 10)
    for j in range(anchor_idx + 1, end):
        ts = _time_from_header_line(lines[j])
        if ts:
            return ts
    return None


def parse_report_time(lines):
    joined = "\n".join(lines)
    m = TIME_RE.search(joined)
    if not m:
        return None
    mon_txt = m.group(2)
    day = int(m.group(3))
    tstr = m.group(4)
    year = int(m.group(5))
    mon = MON_MAP.get(mon_txt, 1)
    try:
        dt = datetime(year, mon, day,
                      int(tstr[0:2]), int(tstr[3:5]), int(tstr[6:8]))
        return dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        return None


def _num_from_line(line):
    s = line.split(":", 1)[-1].strip()
    return re.sub(r"\s+", " ", s)


def parse_test_file(file_path, fixture, boards_root, board, boards_root_fallback=""):
    """解析单个报文：返回 (records, summary)；描述按工装+板子优先"""
    records, summary = [], {}
    try:
        # 诊断日志：记录文件大小和读取前的状态
        file_size = os.path.getsize(file_path)
        print(f"[DEBUG] 准备读取文件: {os.path.basename(file_path)}, 大小: {file_size} 字节")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        # 诊断日志：记录读取后的行数
        print(f"[DEBUG] 读取完成: {len(lines)} 行, 文件模式: 只读(r)")

        # ===== 新增：超大报文保护 =====
        MAX_LINES = 300
        if len(lines) > MAX_LINES:
            print(f"[忽略] 报文行数过多 ({len(lines)} 行 > {MAX_LINES})，跳过解析: {file_path}")
            return [], {}
        # =============================

        # 如果文件为空，记录警告
        if file_size > 0 and len(lines) == 0:
            print(f"[警告] 文件大小非零但读取为空! 文件: {file_path}")

    except Exception as e:
        print(f"[错误] 读取文件失败: {file_path}, 错误: {e}")
        return records, summary

    time_str = parse_report_time(lines)
    if not time_str:
        try:
            t = datetime.fromtimestamp(os.path.getmtime(file_path))
            time_str = t.strftime("%m-%d %H:%M:%S")
        except Exception:
            time_str = ""

    n = len(lines)
    i = 0
    while i < n:
        ln = lines[i]

        # ① Open/Short 报文
        if _OPEN_SHORT_RE.match(ln):
            rec, next_i = _parse_open_short_block(lines, i, time_str, boards_root, fixture, board, file_path)
            if rec:
                records.append(rec)
                summary[rec["part"]] = summary.get(rec["part"], 0) + 1
            i = next_i
            continue

        if "HAS FAILED" in ln:
            part = ln.split("HAS FAILED", 1)[0].strip()

            measured = nominal = high = low = threshold = ""

            stop_re = re.compile(r'(^-+\s*$)|(^serial\s*#)|(\bhas\s+failed\b)', re.IGNORECASE)

            measured = nominal = high = low = threshold = ""
            status_val = ""   # ★新增：专门存 Status 的值

            j_end = i + 1
            for j in range(i + 1, min(i + 50, n)):
                s = lines[j]
                if stop_re.search(s):
                    j_end = j
                    break

                low_s = s.lower().strip()
                if low_s.startswith("measured"):
                    measured = _num_from_line(s)
                elif low_s.startswith("nominal"):
                    nominal = _num_from_line(s)
                elif low_s.startswith("high limit"):
                    high = _num_from_line(s)
                elif low_s.startswith("low limit"):
                    low = _num_from_line(s)
                elif low_s.startswith("threshold"):
                    threshold = _num_from_line(s)
                elif low_s.startswith("status"):   # ★新增：解析 Status
                # Status: 2cH  → 取冒号后面的“2cH”
                    status_val = s.split(":", 1)[1].strip() if ":" in s else s.strip()

            if (not nominal) and (not low) and (not high) and threshold:
                nominal = threshold
            # ★新增：循环结束后兜底 —— 没有 Measured 就用 Status
            if (not measured) and status_val:
                measured = status_val

            desc = find_wire_desc(boards_root, fixture, part, board, boards_root_fallback)

            block_time = _find_near_time_for_block(lines, i) or time_str

            # ========== 新增：获取测试源 ==========
            test_source = get_test_source(boards_root, fixture, part, board, boards_root_fallback)

            rec = {
                "time": block_time,
                "fixture": "",
                "board": "",
                "file": os.path.basename(file_path),
                "part": part,
                "nominal": nominal,
                "low": low,
                "measured": measured,
                "high": high,
                "threshold": threshold,  # 新增：阈值字段
                "desc": desc,
                "test_source": test_source
            }

            records.append(rec)
            summary[part] = summary.get(part, 0) + 1
            i = j_end if (j_end > i + 1) else (i + 1)
        else:
            i += 1

    return records, summary


# —— 监听线程 —— #

class MonitorThread(QThread):
    file_parsed = pyqtSignal(object)

    def __init__(self, base_dir: str, boards_root: str, boards_root_fallback: str = ""):
        super().__init__()
        self.base_dir = base_dir.rstrip("\\/")
        self.boards_root = boards_root.rstrip("\\/")
        self.boards_root_fallback = boards_root_fallback.rstrip("\\/") if boards_root_fallback else ""
        # 简化：只记录已扫描过的文件路径集合（检测到新文件就触发全局扫描）
        self.scanned_files = set()  # 已扫描过的文件路径
        _fixture_cache.clear()
        _testorder_cache.clear()  # 清空 testorder 缓存
        self.cur_date = date.today()
        # 记录“当天最新出现的平台/型号目录”，用于无报文换型时刷新看板
        self._last_model_key = ""
        self._last_model_birth = 0   # 用创建时间作为“换型序号”
        self._pending_model = None   # (key, birth_ts, first_seen_ts)
        self._debounce_sec = 3       # 防抖秒数：3~5 秒都行
        # 启动初次扫描时：缓存“全局看板信号”，避免 UI 尚未连接信号导致丢失
        self._startup_global_payloads = []
        # 启动阶段：禁止“目录换型(空summary)”覆盖初次扫描结果
        self._startup_phase = True

    def _dir_birth_ts(self, path: str) -> int:
        try:
            st = os.stat(path)
            # Windows: st_ctime 通常是创建时间；Linux: 可能是 inode change time
            # 我们在产线是 Windows 为主，优先用它
            return int(getattr(st, "st_ctime", 0) or 0)
        except Exception:
            return 0
        
    def _has_any_txt(self, folder: str) -> bool:
        try:
            for root, _, files in os.walk(folder):
                for fn in files:
                    if fn.lower().endswith(".txt"):
                        return True
        except Exception:
            pass
        return False


    def _detect_latest_model_dir_by_birth(self, day_path: str):
        best_fx, best_bd, best_birth = "", "", 0
        if not day_path or (not os.path.isdir(day_path)):
            return best_fx, best_bd, best_birth

        try:
            fixtures = [n for n in os.listdir(day_path)
                        if os.path.isdir(os.path.join(day_path, n))]
        except Exception:
            fixtures = []

        for fx in fixtures:
            fx_dir = os.path.join(day_path, fx)
            try:
                boards = [n for n in os.listdir(fx_dir)
                        if os.path.isdir(os.path.join(fx_dir, n))]
            except Exception:
                boards = []

            for bd in boards:
                bd_dir = os.path.join(fx_dir, bd)

                # （可选最稳）只认有 sentinel 的目录
                # if not os.path.exists(os.path.join(bd_dir, "_MODEL_READY.flag")):
                #     continue

                birth = self._dir_birth_ts(bd_dir)
                if birth > best_birth:
                    best_fx, best_bd, best_birth = fx, bd, birth

        return best_fx, best_bd, best_birth
    
    def _maybe_emit_model_switch_by_dir(self, day_path: str):
        # 启动阶段不发“目录换型”信号，避免覆盖初次扫描/重放出来的看板数据
        if getattr(self, "_startup_phase", False):
            return
        fx, bd, birth = self._detect_latest_model_dir_by_birth(day_path)
        key = f"{fx}/{bd}" if (fx and bd) else ""
        if not key or birth <= 0:
            return

        now = int(time.time())

        # 规则1：如果这个目录“比当前已确认的更早”，忽略（防回切）
        if birth < self._last_model_birth:
            return

        # 规则2：如果和当前一样，不用切
        if key == self._last_model_key:
            # 同一个目录，清掉 pending
            self._pending_model = None
            return

        # 规则3：防抖：先进入 pending，超过 debounce 再确认切换
        if (self._pending_model is None) or (self._pending_model[0] != key):
            self._pending_model = (key, birth, now)
            return

        # pending 同一个 key
        _, pending_birth, first_seen = self._pending_model
        # birth 变了，重新计时
        if pending_birth != birth:
            self._pending_model = (key, birth, now)
            return

        if (now - first_seen) < self._debounce_sec:
            return

        # ——确认切换——
        self._last_model_key = key
        self._last_model_birth = birth
        self._pending_model = None

        # ✅ 关键：如果该型号目录下已经有任何txt报文，则不发“目录换型(空summary)”事件，
        # 避免覆盖/清空看板（启动初次扫描、或已存在报文的目录都属于这种情况）
        bd_dir = os.path.join(day_path, fx, bd)
        if self._has_any_txt(bd_dir):
            return

        self.file_parsed.emit({
            "fixture": fx,
            "board": bd,
            "file": "目录换型",
            "records": [],
            "summary": {},
            "end_ts": birth,
            "is_global_scan": True,
            "source": "dir"
        })


    def _scan_and_process_all(self, day_path, is_initial_scan=False, new_files=None):
        """全局扫描指定路径下的所有文件并处理"""
        if not os.path.isdir(day_path):
            return


        # 仅推送本次新增文件相关的零件（避免触发时补推历史超阈值零件）
        new_files_set = set(new_files) if new_files else None

        # 诊断日志
        # try:
        #     with open("scan_debug.log", "a", encoding="utf-8") as f:
        #         from datetime import datetime
        #         f.write(f"[{datetime.now()}] _scan_and_process_all 开始, is_initial_scan={is_initial_scan}\n")
        # except:
        #     pass

        all_files = []
        for root, _, files in os.walk(day_path):
            for fn in files:
                if fn.lower().endswith(".txt"):
                    fp = os.path.join(root, fn)
                    all_files.append(fp)

        if not all_files:
            return

        # 按修改时间排序
        all_files.sort(key=lambda p: os.path.getmtime(p))

        print(f"[监控线程] 全局扫描: 发现 {len(all_files)} 个文件")

        # 诊断日志
        # try:
        #     with open("scan_debug.log", "a", encoding="utf-8") as f:
        #         from datetime import datetime
        #         f.write(f"[{datetime.now()}] 发现 {len(all_files)} 个文件\n")
        # except:
        #     pass

        # 汇总所有文件的结果
        total_summary = {}  # {零件名: 失败次数}
        all_part_records = {}  # {零件名: [所有记录]}

         # 新增：按“工装/板子型号”分组的统计（专供看板页使用）
        from collections import Counter
        # {(fixture, board): Counter({part: count, ...})}
        board_summaries = {}
        # {(fixture, board): 最新报文文件的修改时间(秒)}
        board_latest_ts = {}

        # 本次新增文件涉及的零件集合：{(fixture, board, part)}
        trigger_parts = set()

        fixture = ""
        board = ""

        for fp in all_files:
            # 诊断日志
            # try:
            #     with open("scan_debug.log", "a", encoding="utf-8") as f:
            #         from datetime import datetime
            #         f.write(f"[{datetime.now()}] 开始处理文件: {os.path.basename(fp)}\n")
            # except:
            #     pass

            rel = fp[len(self.base_dir):].lstrip("\\/")
            seg = rel.split(os.sep)
            # 路径格式: 年\月\日\工装\板型号\*.txt
            fixture = seg[3] if len(seg) > 3 else ""
            board = seg[4] if len(seg) > 4 else ""

            records, summary = parse_test_file(fp, fixture, self.boards_root, board, self.boards_root_fallback)

            # 诊断日志
            # try:
            #     with open("scan_debug.log", "a", encoding="utf-8") as f:
            #         from datetime import datetime
            #         f.write(f"[{datetime.now()}] 文件解析完成: {os.path.basename(fp)}, records={len(records)}, summary={summary}\n")
            # except:
            #     pass


            # 记录本次新增文件涉及的零件（用于控制飞书推送触发范围）
            if (new_files_set is None) or (fp in new_files_set):
                for _p in summary.keys():
                    trigger_parts.add((fixture, board, _p))

            if not records:
                continue

            # 累加到总summary（用于看板页）
            for part, count in summary.items():
                total_summary[part] = total_summary.get(part, 0) + count

            
            # 新增：按 (fixture, board) 分组统计（用于飞书 + 看板）
            key_fb = (fixture, board)
            if key_fb not in board_summaries:
                board_summaries[key_fb] = Counter()
            board_summaries[key_fb].update(summary)

            # 记录这个板子的最新报文文件时间（用文件修改时间即可）
            try:
                mtime = os.path.getmtime(fp)
            except Exception:
                mtime = 0
            old_ts = board_latest_ts.get(key_fb, 0)
            if mtime > old_ts:
                board_latest_ts[key_fb] = mtime

            # 收集所有零件的记录（用于飞书推送的recent3）
            for rec in records:
                part = rec.get("part", "")
                if part:
                    if part not in all_part_records:
                        all_part_records[part] = []
                    all_part_records[part].append({
                        "ts": rec.get("time", ""),
                        "sn": rec.get("file", ""),
                        "min": rec.get("low", ""),
                        "meas": rec.get("measured", ""),
                        "max": rec.get("high", ""),
                        "threshold": rec.get("threshold", ""),
                        # 新增：记录属于哪个工装 / 板号
                        "fixture": fixture,
                        "board": board,
                    })

            # 为每个文件单独发送信号（用于实时页）
            for rec in records:
                rec["fixture"] = fixture
                rec["board"] = board

            file_payload = {
                "fixture": fixture,
                "board": board,
                "file": os.path.basename(fp),
                "records": records,
                "summary": summary,
                "is_global_scan": False  # 单文件信号，实时页用
            }
            self.file_parsed.emit(file_payload)

            # 修复：全局扫描时不调用 on_fail_detected，避免重复扫描和性能问题
            # 原因：on_fail_detected 会调用 state.bump_count() → state.scan_board_files()
            # 这会在全局扫描过程中再次扫描所有文件，导致：
            # 1. 性能问题（每个零件都扫描一次所有文件）
            # 2. 可能的卡顿或死锁
            # 全局扫描的唯一目的是更新看板页显示，不需要在这里推送飞书
            # 飞书推送由单独的机制处理（基于 alerts_state.json 的变化）

        # 打印汇总结果
        print(f"[监控线程] 全局扫描完成，汇总结果: {total_summary}")

        # 诊断日志
        # try:
        #     with open("scan_debug.log", "a", encoding="utf-8") as f:
        #         from datetime import datetime
        #         f.write(f"[{datetime.now()}] 全局扫描完成, total_summary={total_summary}, fixture={fixture}, board={board}\n")
        # except:
        #     pass

        
        # 初始扫描：只显示不推送，但要把“已达到阈值的累计次数”登记为已处理，
        # 避免刚启动后第一次正常扫描触发“补推历史旧报文”。
        if is_initial_scan and board_summaries:
            try:
                for (fx, bd), part_counter in board_summaries.items():
                    if not fx or not bd:
                        continue
                    for part, count in part_counter.items():
                        if count >= FAIL_THRESHOLD:
                            state.mark_as_pushed(fx, bd, part, count)
                # 诊断日志
                # try:
                #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                #         from datetime import datetime
                #         f.write(f"[{datetime.now()}] 初始扫描登记已推送计数完成（不实际推送）\n")
                # except:
                #     pass
            except Exception as e:
                print(f"[初始扫描] 登记已推送计数异常: {e}")
# 全局扫描完成后，检查是否需要推送飞书（只在非初始扫描时）
        try:
            if not is_initial_scan and board_summaries:
                # 诊断日志
                # try:
                #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                #         from datetime import datetime
                #         f.write(f"[{datetime.now()}] 开始飞书推送检查, is_initial_scan={is_initial_scan}\n")
                #         f.write(f"[{datetime.now()}] board_summaries_keys={list(board_summaries.keys())}\n")
                # except:
                #     pass

                # 按 (fixture, board) 逐个检查需要推送的零件，
                # 保证每个零件对应自己的工装/板号
                from datetime import datetime
                for (fx, bd), part_counter in board_summaries.items():
                    if not fx or not bd:
                        continue

                    for part, count in part_counter.items():
                        # 诊断日志：记录每个零件的处理
                        # try:
                        #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                        #         f.write(f"[{datetime.now()}] 检查零件: {fx}/{bd}/{part}, count={count}\n")
                        # except:
                        #     pass

                        # 只对“本次新增文件”涉及到的零件触发推送（避免补推历史旧报文）
                        if trigger_parts and (fx, bd, part) not in trigger_parts:
                            # try:
                            #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                            #         from datetime import datetime
                            #         f.write(f"[{datetime.now()}] {fx}/{bd}/{part} 不在本次新增文件触发集合中，跳过推送检查\n")
                            # except:
                            #     pass
                            continue

                        # 只处理累计次数 >= FAIL_THRESHOLD 的零件
                        if count < FAIL_THRESHOLD:
                            continue

                        # 检查这个次数是否已推送过
                        pushed_counts = state.get_pushed_counts(fx, bd, part)

                        # 如果已经推送过本次计数，则跳过
                        if pushed_counts and count in pushed_counts:
                            # try:
                            #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                            #         f.write(f"[{datetime.now()}] {fx}/{bd}/{part} 第{count}次已推送过，跳过\n")
                            # except:
                            #     pass
                            continue

                        # 从 all_part_records 中获取该零件在当前工装/板号下的最新3条记录
                        raw_records = all_part_records.get(part, [])
                        part_records = [r for r in raw_records if r.get("fixture") == fx and r.get("board") == bd]

                        # 按时间戳排序（倒序）
                        part_records_sorted = sorted(part_records, key=lambda r: r.get("ts", ""), reverse=True)
                        recent3 = part_records_sorted[:3]

                        # 诊断日志
                        # try:
                        #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                        #         f.write(f"[{datetime.now()}] {fx}/{bd}/{part} recent3数量={len(recent3)}\n")
                        # except:
                        #     pass


                        # 过期保护：如果最新一条报文时间距离当前超过30分钟，则不推送（并登记避免后续补推）
                        try:
                            from datetime import datetime, timedelta
                            latest_ts_str = recent3[0].get("ts", "") if recent3 else ""
                            latest_dt = None
                            if latest_ts_str:
                                # latest_ts_str 形如 '12-12 08:28:08'
                                latest_dt = datetime.strptime(f"{datetime.now().year}-{latest_ts_str}", "%Y-%m-%d %H:%M:%S")
                            if latest_dt and (datetime.now() - latest_dt) > timedelta(minutes=30):
                                # try:
                                #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                                #         f.write(f"[{datetime.now()}] {fx}/{bd}/{part} 最新ts={latest_ts_str} 已超过30分钟，跳过推送并登记\n")
                                # except:
                                #     pass
                                state.mark_as_pushed(fx, bd, part, count)
                                continue
                        except Exception as _e:
                            # 解析失败不阻断推送
                            pass
                        # 统计当前工装/板号下，所有累计次数 >= FAIL_THRESHOLD 的零件数
                        over3 = state.count_parts_over3(fx, bd)
                        old_message_id = state.get_message_id(fx, bd, part)

                        ts_emit = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        payload = {
                            "schema": "ict-alert-v1",
                            "site_id": SITE_ID,
                            "fixture": fx,
                            "board": bd,
                            "part": part,
                            "count": count,
                            "over3_count": over3,
                            "recent3": recent3,
                            "old_message_id": old_message_id,
                            "ts_emit": ts_emit,
                        }

                        # 真正写出到共享目录
                        ok = write_event_to_share(payload)

                        # try:
                        #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                        #         f.write(f"[{datetime.now()}] {fx}/{bd}/{part} write_event_to_share完成, ok={ok}\n")
                        # except:
                        #     pass

                        if ok:
                            state.mark_as_pushed(fx, bd, part, count)
                            print(f"[飞书推送] {fx}/{bd}/{part}: 第{count}次异常，发送推送")
        except Exception as e:
            # 捕获飞书推送异常，但不影响后续发送全局信号
            print(f"[飞书推送] 推送异常: {e}")
            # try:
            #     with open("scan_debug.log", "a", encoding="utf-8") as f:
            #         from datetime import datetime
            #         f.write(f"[{datetime.now()}] 飞书推送异常: {e}\n")
            # except:
            #     pass

        # 最后按“工装/板子型号”逐个发送全局汇总信号（专供看板页使用）
        # 排序规则：按该板子目录下“最新报文文件时间”从旧到新，
        # 这样 KanbanTab 接收时，最“新”的板子会最终落在“当前在测”，
        # 第二新落在“最近一”，第三新落在“最近二”。
        if board_summaries:
            # 按时间排序 key_fb = (fixture, board)
            sorted_keys = sorted(
                board_summaries.keys(),
                key=lambda k: board_latest_ts.get(k, 0)
            )

            for key_fb in sorted_keys:
                fx, bd = key_fb
                bsum = board_summaries.get(key_fb)
                if not bsum:
                    continue

                end_ts = board_latest_ts.get(key_fb, 0)

                global_payload = {
                    "fixture": fx,
                    "board": bd,
                    "file": "全局扫描",
                    "records": [],  # 看板页不需要详细 records
                    "summary": dict(bsum),
                    "end_ts": end_ts,
                    "is_global_scan": True  # 全局汇总，看板页用
                }

                print(f"[全局扫描] 发送全局信号: {fx}/{bd}, summary={dict(bsum)}")


                # 初次扫描阶段：先缓存起来，稍后在 UI 就绪后重放一次（不重新扫描、不推飞书）
                if is_initial_scan:
                    try:
                        self._startup_global_payloads.append(global_payload)
                    except Exception:
                        pass

                # try:
                #     with open("scan_debug.log", "a", encoding="utf-8") as f:
                #         from datetime import datetime
                #         f.write(f"[{datetime.now()}] 全局信号已发送: {fx}/{bd}, summary={dict(bsum)}\n")
                # except:
                #     pass

                self.file_parsed.emit(global_payload)
        # else:
            # try:
            #     with open("scan_debug.log", "a", encoding="utf-8") as f:
            #         from datetime import datetime
            #         f.write(f"[{datetime.now()}] board_summaries 为空，不发送全局信号！\n")
            # except:
            #     pass


    def run(self):
        import traceback

        try:
            # 程序启动时立即全局扫描并处理今天的所有文件（仅显示，不推送）
            day_path = os.path.join(
                self.base_dir,
                str(self.cur_date.year),
                f"{self.cur_date.month:02d}",
                f"{self.cur_date.day:02d}"
            )

            # # 创建日志文件用于诊断
            # log_file = open("monitor_debug.log", "w", encoding="utf-8")
            # log_file.write(f"[{datetime.now()}] 监控线程启动\n")
            # log_file.flush()

            self._maybe_emit_model_switch_by_dir(day_path)

            print(f"[监控线程] 初始扫描开始...")
            self._scan_and_process_all(day_path, is_initial_scan=True)

            # 记录当前已扫描的文件
            if os.path.isdir(day_path):
                for root, _, files in os.walk(day_path):
                    for fn in files:
                        if fn.lower().endswith(".txt"):
                            self.scanned_files.add(os.path.join(root, fn))

            print(f"[监控线程] 初始扫描完成，已扫描文件数: {len(self.scanned_files)}")
            finalize_initial_scan()
            # 启动后 UI 可能尚未完成信号连接：延迟重放初次扫描的全局看板信号
            # 仅重放（emit），不重新扫描，因此不会触发飞书推送
            try:
                self.msleep(1000)  # 0.8s：给 UI 足够时间完成连接（可在 500~1500ms 调）
                if getattr(self, "_startup_global_payloads", None):
                    for p in self._startup_global_payloads:
                        p2 = dict(p)
                        p2["source"] = "startup_replay"
                        self.file_parsed.emit(p2)

                    self._startup_global_payloads.clear()
                # 启动阶段结束：允许目录换型事件生效
                self._startup_phase = False
            except Exception as e:
                print(f"[启动重放] 异常: {e}")


            # 进入正常的监控循环
            while not self.isInterruptionRequested():
                try:
                    # 跨天处理：清空已扫描文件集合
                    if date.today() != self.cur_date:
                        self.cur_date = date.today()
                        self.scanned_files.clear()
                        self._last_model_key = ""
                        self._last_model_birth = 0
                        self._pending_model = None
                        print(f"[监控线程] 日期变更，清空扫描记录")

                    day_path = os.path.join(
                        self.base_dir,
                        str(self.cur_date.year),
                        f"{self.cur_date.month:02d}",
                        f"{self.cur_date.day:02d}"
                    )

                    self._maybe_emit_model_switch_by_dir(day_path)

                    # 获取当前目录下的所有文件
                    current_files = set()
                    if os.path.isdir(day_path):
                        for root, _, files in os.walk(day_path):
                            for fn in files:
                                if fn.lower().endswith(".txt"):
                                    current_files.add(os.path.join(root, fn))

                    # 检测是否有新文件
                    new_files = current_files - self.scanned_files

                    if new_files:
                        # log_file.write(f"[{datetime.now()}] 检测到 {len(new_files)} 个新文件\n")
                        # log_file.write(f"  new_files: {[os.path.basename(f) for f in new_files]}\n")
                        # log_file.flush()
                        print(f"[监控线程] 检测到 {len(new_files)} 个新文件，等待文件稳定...")

                        # 等待文件修改时间稳定（3秒内不再变化）
                        stable = False
                        wait_count = 0
                        max_wait = 10  # 最多等待10次（10*3=30秒）

                        while not stable and wait_count < max_wait:
                            # 记录当前所有文件的修改时间
                            current_mtimes = {}
                            for fp in current_files:
                                try:
                                    current_mtimes[fp] = os.path.getmtime(fp)
                                except:
                                    pass

                            # 等待3秒
                            self.msleep(3000)

                            # 检查修改时间是否变化
                            all_stable = True
                            for fp in current_files:
                                try:
                                    new_mtime = os.path.getmtime(fp)
                                    if fp in current_mtimes and new_mtime != current_mtimes[fp]:
                                        all_stable = False
                                        print(f"[监控线程] 文件仍在写入: {os.path.basename(fp)}")
                                        break
                                except:
                                    all_stable = False
                                    break

                            if all_stable:
                                stable = True
                                print(f"[监控线程] 文件已稳定，触发全局扫描")
                            else:
                                wait_count += 1
                                if wait_count < max_wait:
                                    print(f"[监控线程] 继续等待文件稳定... ({wait_count}/{max_wait})")

                        if not stable:
                            print(f"[监控线程] 等待超时，强制触发全局扫描")

                        # 触发全局扫描
                        # log_file.write(f"[{datetime.now()}] 开始全局扫描，current_files={len(current_files)}\n")
                        # log_file.flush()
                        print(f"[监控线程] 开始全局扫描...")
                        self._scan_and_process_all(day_path, is_initial_scan=False, new_files=new_files)

                        # 更新已扫描文件集合
                        self.scanned_files = current_files.copy()
                        # log_file.write(f"[{datetime.now()}] 全局扫描完成，scanned_files={len(self.scanned_files)}\n")
                        # log_file.flush()
                        print(f"[监控线程] 全局扫描完成，当前文件总数: {len(self.scanned_files)}")

                except Exception as e:
                    # 捕获监控循环中的异常，但不退出循环
                    print(f"[监控线程] 监控循环出错: {e}")
                    traceback.print_exc()
                    self.msleep(5000)
                    continue

                # 每1.8秒检测一次
                self.msleep(1800)

        except Exception as e:
            # 外层捕获，防止线程静默退出
            print(f"[监控线程] 致命错误，线程即将退出: {e}")
            traceback.print_exc()




# 获取报文数据根目录（从 main.py 中的 BASE_DIR）
# 由于 monitor.py 被 main.py 导入，我们可以直接使用
# 如果从其他地方调用，需要确保 BASE_DIR 已定义
try:
    from main import BASE_DIR as DATA_ROOT
except ImportError:
    # 如果无法导入，使用默认路径
    DATA_ROOT = r"C:\test_data"

state = AlertsState(site_id=SITE_ID, state_path=STATE_PATH, base_data_dir=DATA_ROOT)

# 初始启动标志，用于控制初始扫描
_initial_scan_mode = True


def on_fail_detected(fixture, board, part, sn, min_v, meas_v, max_v, threshold_v="", is_initial_scan=False):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 获取之前的计数
    previous_count = state.get_previous_count(fixture, board, part)

    # bump_count 现在返回 (累计次数, 最新3条记录) - 只扫描当天数据
    count, recent3 = state.bump_count(fixture, board, part, ts, sn, min_v, meas_v, max_v, threshold_v)

    # 推送逻辑：
    # 1. 初始扫描模式：不推送，只更新状态和显示
    # 2. 正常监控模式：每达到3次就推送（3次、4次、5次...每次都推送，但避免重复）

    if is_initial_scan and _initial_scan_mode:
        # 初始扫描模式：不推送，只记录日志
        print(f"[初始扫描] {fixture}/{board}/{part}: 累计{count}次 (不推送)")
    elif count >= FAIL_THRESHOLD:
        # 正常监控模式：检查当前count是否已推送过
        pushed_counts = state.get_pushed_counts(fixture, board, part)

        if count not in pushed_counts:
            # 这个次数还没推送过，现在推送
            over3 = state.count_parts_over3(fixture, board)
            old_message_id = state.get_message_id(fixture, board, part)

            payload = {
                "schema": "ict-alert-v1",
                "site_id": SITE_ID,
                "fixture": fixture,
                "board": board,
                "part": part,
                "over3_total": over3,
                "count": count,
                "recent3": recent3,
                "ts_emit": ts,
                "old_message_id": old_message_id
            }
            write_event_to_share(payload)

            # 标记为已推送
            state.mark_as_pushed(fixture, board, part, count)

            print(f"[实时监控] [飞书推送] {part}: 第{count}次异常，发送推送")
        else:
            print(f"[实时监控] {part}: 第{count}次异常已推送过，跳过")


def finalize_initial_scan():
    """
    完成初始扫描后调用，切换到正常监控模式
    """
    global _initial_scan_mode

    if not _initial_scan_mode:
        return

    # 切换到正常模式
    _initial_scan_mode = False
    print(f"[初始扫描] 扫描完成，切换到正常监控模式，后续新产生的异常将推送飞书")


def _safe_filename(s: str, max_len: int = 80) -> str:
    s = (s or "").strip()
    # Windows 文件名非法字符
    for ch in '<>:"/\\|?*':
        s = s.replace(ch, '_')
    # 一些容易出问题的字符也替换掉
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = s.replace("↔", "-")
    # 多空格压缩
    while "  " in s:
        s = s.replace("  ", " ")
    # 截断防止路径过长
    if len(s) > max_len:
        s = s[:max_len]
    return s

def write_event_to_share(payload):
    """
    写入报警JSON到共享目录，失败时保存到本地备份
    """
    day = time.strftime("%Y-%m-%d")
    sn = payload["recent3"][0]["sn"] if payload.get("recent3") else "NA"

    fx = _safe_filename(payload.get("fixture", ""), 40)
    bd = _safe_filename(payload.get("board", ""), 40)
    pt = _safe_filename(payload.get("part", ""), 80)
    ts = _safe_filename((payload.get("ts_emit", "") or "").replace(":", "-"), 40)
    sn2 = _safe_filename(sn, 60)

    filename = f"{fx}__{bd}__{pt}__{ts}_{sn2}.json"

    # 原子写：先写临时文件，再替换，避免出现 0KB
    def _atomic_write_json(path: str, data: dict):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    # 尝试写入网络共享路径
    try:
        subdir = os.path.join(SHARE_PATH, day, payload["site_id"])
        os.makedirs(subdir, exist_ok=True)
        path = os.path.join(subdir, filename)
        _atomic_write_json(path, payload)
        print(f"[飞书推送] 成功写入: {path}")
        return True
    except Exception as e:
        print(f"[飞书推送] 网络路径写入失败: {e}")

        # 写入本地备份目录
        try:
            local_backup = os.path.join(os.path.dirname(__file__), "feishu_backup", day, payload["site_id"])
            os.makedirs(local_backup, exist_ok=True)
            local_path = os.path.join(local_backup, filename)
            _atomic_write_json(local_path, payload)
            print(f"[飞书推送] 已保存到本地备份: {local_path}")
            print(f"[飞书推送] 请检查网络配置或手动将文件复制到: {SHARE_PATH}")
            return False
        except Exception as e2:
            print(f"[飞书推送] 本地备份也失败: {e2}")
            return False
