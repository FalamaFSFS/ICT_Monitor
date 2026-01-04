# -*- coding: utf-8 -*-
import os
import re
from typing import List, Dict, Set, Optional
from collections import defaultdict
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QHeaderView,
    QAbstractItemView, QStyledItemDelegate, QPlainTextEdit, QFrame, QScroller, QScrollerProperties
)
from PyQt5.QtCore import Qt, QDate, QSize
from PyQt5.QtGui import QTextDocument, QFont, QFontMetrics, QColor, QBrush

from monitor import parse_test_file

try:
    from ui_helpers import WrapDelegate, GridDelegate
except Exception:
    WrapDelegate = GridDelegate = object

# ==================== 重复零件检测工具函数 ====================

_COMMON_SIGNALS = frozenset([
    'gnd', 'vcc', 'vdd', 'vss', 'vee', 'agnd', 'dgnd', 'pgnd',
    'avcc', 'dvcc', 'pvcc', 'avdd', 'dvdd', 'pvdd',
    '3v3', '5v', '12v', '1v8', '2v5', '3v', '1v2',
    'nc', 'shield', 'earth', 'ground'
])

_WIRE_PART_RE = re.compile(r'wire\s+"([^"]+)"', re.IGNORECASE)


def _normalize_part_name(name: str) -> str:
    return (name or "").strip().lower()


def _is_common_signal(part: str) -> bool:
    normalized = _normalize_part_name(part)
    if normalized in _COMMON_SIGNALS:
        return True
    if '%' in normalized:
        suffix = normalized.split('%')[-1]
        if suffix in _COMMON_SIGNALS:
            return True
    return False


def extract_wire_parts_from_desc(desc: str) -> set:
    parts = set()
    if not desc:
        return parts

    for match in _WIRE_PART_RE.finditer(desc):
        part = match.group(1)
        if part and not _is_common_signal(part):
            parts.add(_normalize_part_name(part))

    return parts


def find_cross_part_duplicates_by_file(records_by_file: dict) -> dict:
    result = {}

    for filename, records in records_by_file.items():
        main_part_to_sub_parts = defaultdict(set)

        for rec in records:
            main_part = _normalize_part_name(rec.get("part", ""))
            desc = rec.get("desc", "") or ""

            if not main_part:
                continue

            sub_parts = extract_wire_parts_from_desc(desc)
            main_part_to_sub_parts[main_part].update(sub_parts)

        sub_part_count = defaultdict(set)
        for main_part, sub_parts in main_part_to_sub_parts.items():
            for sub in sub_parts:
                sub_part_count[sub].add(main_part)

        duplicates = {
            sub: main_parts
            for sub, main_parts in sub_part_count.items()
            if len(main_parts) >= 2
        }

        for main_part, sub_parts in main_part_to_sub_parts.items():
            highlight_subs = sub_parts & set(duplicates.keys())
            if highlight_subs:
                result[(filename, main_part)] = highlight_subs

    return result


def format_duplicate_warning(duplicate_parts: set) -> str:
    if not duplicate_parts:
        return ""
    parts_str = ", ".join(sorted(duplicate_parts))
    return f"⚠️ 重复测点: {parts_str}"


# ==================== 只读文本委托 ====================

class ReadOnlyTextDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        ed = QPlainTextEdit(parent)
        ed.setReadOnly(True)
        ed.setFrameStyle(QFrame.NoFrame)
        ed.setWordWrapMode(3)
        ed.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ed.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ed.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        try:
            ed.setFont(option.font)
        except Exception:
            pass
        ed.setStyleSheet("QPlainTextEdit { padding:0; margin:0; }")
        ed.document().setDocumentMargin(0)
        return ed

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data() or "")

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 28)


# ==================== 历史数据页签 ====================

class HistoryTab(QWidget):
    HIGHLIGHT_BG_COLOR = QColor("#FFF9C4")
    HIGHLIGHT_BG_BRUSH = QBrush(HIGHLIGHT_BG_COLOR)

    # 列索引（新增测试源列）
    PART_COL = 3
    DESC_COL = 8
    FILE_COL = 9
    TEST_SOURCE_COL = 10  # 新增

    def __init__(self, base_dir, boards_root, boards_root_fallback="", cache=None):
        super().__init__()
        self.base_dir = base_dir.rstrip("\\/")
        self.boards_root = boards_root.rstrip("\\/")
        self.boards_root_fallback = boards_root_fallback.rstrip("\\/") if boards_root_fallback else ""
        self.cache = cache

        self.HEADERS = [
            "报警时间", "平台", "型号", "零件名",
            "Nominal/Threshold", "LowLimit", "Measured", "HighLimit",
            "描述", "测试源", "log文件名"  # 新增"测试源"
        ]

        # 顶部工具条
        top = QHBoxLayout()
        top.addWidget(QLabel("日期："))
        self.date = QDateEdit(calendarPopup=True)
        self.date.setDate(QDate.currentDate())
        self.date.setDisplayFormat("yyyy-MM-dd")
        top.addWidget(self.date)

        self.btn_query = QPushButton("查询")
        self.btn_query.clicked.connect(lambda: self.on_query(show_msg=True))
        top.addWidget(self.btn_query)
        top.addStretch(1)

        # 树表
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.HEADERS))  # 11列
        self.tree.setHeaderLabels(self.HEADERS)

        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setStretchLastSection(False)

        self.tree.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)

        header = self.tree.header()
        for c in range(8):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Interactive)  # 描述
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)  # 文件名
        header.setSectionResizeMode(10, QHeaderView.ResizeToContents)  # 测试源
        self.tree.setColumnWidth(8, 520)
        header.setStretchLastSection(True)

        self.tree.setStyleSheet("""
            QTreeView::item{ border-bottom: 1px solid #E6E6E6; }
            QTreeView::item:selected{ background:#E8F0FE; }
            QHeaderView::section{
                border-right:1px solid #D6D6D6;
                border-bottom:1px solid #D6D6D6;
                padding:4px;
                background:#FAFAFA;
                font-weight:600;
            }
        """)

        # 所有列加竖线（包括新增的第10列）
        for c in range(0, 11):
            self.tree.setItemDelegateForColumn(c, GridDelegate(self.tree))

        self.desc_delegate = ReadOnlyTextDelegate(self.tree)
        self.tree.setItemDelegateForColumn(self.DESC_COL, self.desc_delegate)

        self.tree.setStyleSheet(self.tree.styleSheet() + """
            QTreeView::item:selected {
                background-color: #1769AA;
                color: #FFFFFF;
                font-weight: 600;
            }
            QHeaderView::section {
                background: #F5F7FA;
                border: none;
                border-right: 1px solid #E0E0E0;
                padding: 4px;
                font-weight: 600;
            }
            QTreeView {
                gridline-color: #E6E6E6;
                alternate-background-color: #FAFAFA;
            }
        """)

        header.sectionResized.connect(lambda *_: (self._tune_column_widths(), self._recalc_desc_heights()))

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tree)

        self.on_query(show_msg=False)

    def _apply_highlight(self, item: QTreeWidgetItem, highlight: bool):
        if highlight:
            for col in range(item.columnCount()):
                item.setBackground(col, self.HIGHLIGHT_BG_BRUSH)

            for col in [self.PART_COL, self.DESC_COL]:
                font = item.font(col)
                font.setBold(True)
                item.setFont(col, font)
        else:
            for col in range(item.columnCount()):
                item.setData(col, Qt.BackgroundRole, None)

            for col in [self.PART_COL, self.DESC_COL]:
                font = item.font(col)
                font.setBold(False)
                item.setFont(col, font)

    def _prepend_warning_to_desc(self, original_desc: str, warning: str) -> str:
        if not warning:
            return original_desc
        if original_desc:
            return f"{warning}\n{original_desc}"
        return warning

    def on_query(self, show_msg: bool = True):
        qd = self.date.date()
        y, m, d = qd.year(), qd.month(), qd.day()
        self.load_history(y, m, d, show_empty_msg=show_msg)

    def load_history(self, year, month, day, show_empty_msg=True):
        self.tree.clear()

        day_dir = os.path.join(self.base_dir, str(year), f"{month:02d}", f"{day:02d}")
        if not os.path.isdir(day_dir):
            if show_empty_msg:
                QMessageBox.information(self, "提示", "该日期没有记录。")
            return

        files = []
        for root, _, fns in os.walk(day_dir):
            for fn in fns:
                if fn.lower().endswith(".txt"):
                    files.append(os.path.join(root, fn))
        if not files:
            if show_empty_msg:
                QMessageBox.information(self, "提示", "该日期没有记录。")
            return

        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        # ========== 第一步：按文件收集所有记录 ==========
        records_by_file = defaultdict(list)
        all_records = []

        for fp in files:
            rel = fp[len(self.base_dir):].lstrip("\\/")
            seg = rel.split(os.sep)
            fixture = seg[3] if len(seg) > 3 else ""
            board = seg[4] if len(seg) > 4 else ""
            filename = os.path.basename(fp)

            records, _ = parse_test_file(fp, fixture, self.boards_root, board, self.boards_root_fallback)
            if not records:
                continue

            for r in records:
                rec = {
                    "time": r.get("time") or "",
                    "fixture": r.get("fixture") or fixture,
                    "board": r.get("board") or board,
                    "part": r.get("part") or "",
                    "nominal": r.get("nominal") or "",
                    "low": r.get("low") or "",
                    "measured": r.get("measured") or "",
                    "high": r.get("high") or "",
                    "desc": r.get("desc") or "",
                    "file": r.get("file") or filename,
                    "test_source": r.get("test_source") or "",  # 新增
                }
                try:
                    ts = datetime.strptime(rec["time"], "%m-%d %H:%M:%S")
                except Exception:
                    ts = None
                rec["_ts"] = ts

                records_by_file[filename].append(rec)
                all_records.append(rec)

        # ========== 第二步：检测跨文件内跨零件的重复 ==========
        cross_duplicates = find_cross_part_duplicates_by_file(records_by_file)

        # ========== 第三步：聚合并填充树 ==========
        # 修改：使用 (file, part, board, fixture) 作为key，避免不同文件的相同零件被合并
        seen = defaultdict(lambda: {"latest": None, "older": []})

        for rec in all_records:
            key = (rec.get("file") or "", rec.get("part") or "", rec.get("board") or "", rec.get("fixture") or "")
            cur = seen[key]["latest"]
            if cur is None:
                seen[key]["latest"] = rec
            else:
                if (rec["_ts"] and cur["_ts"] and rec["_ts"] > cur["_ts"]):
                    seen[key]["older"].append(cur)
                    seen[key]["latest"] = rec
                else:
                    seen[key]["older"].append(rec)

        # 写入树
        for key, bundle in seen.items():
            latest = bundle["latest"]
            if not latest:
                continue

            filename = latest.get("file", "")
            main_part_normalized = _normalize_part_name(latest.get("part", ""))
            duplicate_subs = cross_duplicates.get((filename, main_part_normalized), set())
            should_highlight = len(duplicate_subs) > 0

            desc_text = latest.get("desc", "") or ""
            if should_highlight:
                warning = format_duplicate_warning(duplicate_subs)
                desc_text = self._prepend_warning_to_desc(desc_text, warning)

            # 获取测试源（新增）
            test_source = latest.get("test_source", "") or ""

            vals = [
                latest["time"], latest["fixture"], latest["board"], latest["part"],
                latest["nominal"], latest["low"], latest["measured"], latest["high"],
                desc_text, test_source, latest["file"]  # 新增 test_source
            ]
            parent = QTreeWidgetItem(vals)
            self.tree.addTopLevelItem(parent)

            if should_highlight:
                self._apply_highlight(parent, True)

            self.tree.openPersistentEditor(parent, self.DESC_COL)

            # 旧记录作子项
            for old in bundle["older"]:
                old_filename = old.get("file", "")
                old_main_part = _normalize_part_name(old.get("part", ""))
                old_duplicate_subs = cross_duplicates.get((old_filename, old_main_part), set())
                old_should_highlight = len(old_duplicate_subs) > 0

                old_desc = old.get("desc", "") or ""
                if old_should_highlight:
                    warning = format_duplicate_warning(old_duplicate_subs)
                    old_desc = self._prepend_warning_to_desc(old_desc, warning)

                old_test_source = old.get("test_source", "") or ""

                ov = [
                    old["time"], old["fixture"], old["board"], old["part"],
                    old["nominal"], old["low"], old["measured"], old["high"],
                    old_desc, old_test_source, old["file"]  # 新增 test_source
                ]
                child = QTreeWidgetItem(ov)
                parent.addChild(child)

                if old_should_highlight:
                    self._apply_highlight(child, True)

                self.tree.openPersistentEditor(child, self.DESC_COL)

        self._tune_column_widths()
        self._recalc_desc_heights()

    def _tune_column_widths(self):
        vpw = self.tree.viewport().width() if self.tree.viewport() else 1200

        fm = QFontMetrics(self.tree.font())
        widths = []

        def collect(text: str):
            lines = (text or "").splitlines() or [""]
            for s in lines:
                widths.append(fm.horizontalAdvance(s))

        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            collect(it.text(self.DESC_COL))
            for j in range(it.childCount()):
                collect(it.child(j).text(self.DESC_COL))

        if widths:
            widths.sort()
            p80 = widths[int(len(widths) * 0.8) - 1] if len(widths) > 0 else 0
            natural = p80 + 24
        else:
            natural = 320

        min_w = 240
        max_w = int(vpw * 0.40)
        desc_width = max(min_w, min(natural, max_w))

        if self.DESC_COL < self.tree.columnCount():
            self.tree.setColumnWidth(self.DESC_COL, desc_width)

    def _recalc_desc_heights(self):
        col = self.DESC_COL
        width = self.tree.columnWidth(col)
        if width <= 10:
            return

        font = self.tree.font()
        fm = QFontMetrics(font)
        line_h = fm.lineSpacing()
        max_lines = 4

        def row_h(text: str) -> int:
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setDocumentMargin(0)
            doc.setPlainText(text or "")
            doc.setTextWidth(max(1, width - 8))
            h = int(doc.size().height())

            cap = line_h * max_lines + 4
            return max(26, min(h + 2, cap))

        def apply_item(item: QTreeWidgetItem):
            if item is None:
                return
            h = row_h(item.text(col))
            item.setSizeHint(col, QSize(width, h))

        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            apply_item(parent)
            for j in range(parent.childCount()):
                apply_item(parent.child(j))

    def _autofit_columns(self):
        if self.tree.columnCount() == 0:
            return

        fm = QFontMetrics(self.tree.font())
        vpw = self.tree.viewport().width() if self.tree.viewport() else 1200

        MIN_W = 60
        MAX_W = int(vpw * 0.60)
        DESC_MAX_W = int(vpw * 0.50)

        def text_width_col(col: int, text: str) -> int:
            if col == self.DESC_COL:
                lines = (text or "").splitlines() or [""]
                return max(fm.horizontalAdvance(s) for s in lines) if lines else 0
            else:
                return fm.horizontalAdvance(text or "")

        for col in range(self.tree.columnCount()):
            header_text = self.HEADERS[col] if col < len(self.HEADERS) else ""
            max_px = text_width_col(col, header_text)

            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                max_px = max(max_px, text_width_col(col, it.text(col)))
                for j in range(it.childCount()):
                    ch = it.child(j)
                    max_px = max(max_px, text_width_col(col, ch.text(col)))

            pad = 24
            natural = max_px + pad

            if col == self.DESC_COL:
                natural = max(200, min(natural, DESC_MAX_W))
            elif self.HEADERS[col] in ("文件", "File", "log文件名"):
                natural = max(120, min(natural, MAX_W))
            elif self.HEADERS[col] == "测试源":
                natural = max(70, min(natural, 100))
            else:
                natural = max(MIN_W, min(natural, MAX_W))

            self.tree.setColumnWidth(col, natural)