# -*- coding: utf-8 -*-
import time
import re
from typing import List, Dict, Set, Optional
from collections import defaultdict
from PyQt5.QtWidgets import (
    QWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QHeaderView,
    QAbstractItemView, QStyledItemDelegate, QPlainTextEdit, QFrame
)
from PyQt5.QtCore import Qt, QTimer, QDate, QSize
from PyQt5.QtGui import QTextDocument, QFontMetrics, QColor, QBrush, QFont
from ui_helpers import WrapDelegate, GridDelegate
import math

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


def find_cross_part_duplicates(records: list) -> dict:
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

    result = {}
    for main_part, sub_parts in main_part_to_sub_parts.items():
        highlight_subs = sub_parts & set(duplicates.keys())
        if highlight_subs:
            result[main_part] = highlight_subs

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
        return QSize(option.rect.width(), 56)


# ==================== 时间倒序排序 Item（按UserRole时间戳排序） ====================

class TimeSortItem(QTreeWidgetItem):
    """让QTreeWidget按Qt.UserRole里的时间戳排序（而不是按显示文本）。"""

    def __lt__(self, other):
        try:
            tree = self.treeWidget()
            col = tree.sortColumn() if tree else 0
        except Exception:
            col = 0

        a = self.data(col, Qt.UserRole)
        b = other.data(col, Qt.UserRole)
        try:
            return float(a or 0) < float(b or 0)
        except Exception:
            return (self.text(col) or "") < (other.text(col) or "")


# ==================== 实时页签 ====================

class RealTimeTab(QWidget):
    HIGHLIGHT_BG_COLOR = QColor("#FFF9C4")
    HIGHLIGHT_BG_BRUSH = QBrush(HIGHLIGHT_BG_COLOR)

    # 列索引（新增测试源列后更新）
    PART_COL = 3  # 零件名列
    DESC_COL = 8  # 描述列
    FILE_COL = 9  # 文件名列
    TEST_SOURCE_COL = 10  # 测试源列（新增）

    def __init__(self, threshold_hours=24, cache=None):
        super().__init__()
        self.threshold_hours = threshold_hours
        self.cache = cache

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(11)  # 增加到11列
        self.tree.setHeaderLabels([
            "报错时间", "平台", "型号", "零件名",
            "Nominal/Threshold", "LowLimit", "Measured", "HighLimit",
            "描述", "测试源", "log文件名"  # 新增"测试源"
        ])

        self.tree.header().setDefaultAlignment(Qt.AlignCenter)

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

        self.desc_delegate = ReadOnlyTextDelegate(self.tree)

        # 所有列加竖线（包括新增的第10列）
        for c in range(0, 11):
            self.tree.setItemDelegateForColumn(c, GridDelegate(self.tree))

        self.tree.setUniformRowHeights(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectItems)

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
        # 按“报错时间”倒序排序（基于第0列Qt.UserRole时间戳）
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.DescendingOrder)


        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)

        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.setInterval(5 * 60 * 1000)
        self.cleanup_timer.timeout.connect(self.remove_old_records)
        self.cleanup_timer.start()

        # 去重：避免同一条报错在周期性扫描/重复解析时反复插入
        # key -> 首次出现的时间戳(epoch)，用于随时间清理
        self._seen_key_ts = {}
        self._seen_keys = set()
        self._seen_max = 50000
        # 新增：父节点快速查找索引 {(fixture, board, part): QTreeWidgetItem}
        self._parent_index = {}
        
        # 新增：批量更新标志，减少重复计算
        self._batch_updating = False

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

    def _make_record_key(self, rec: dict, current_file: str):
        """为每条报错生成稳定的去重key（跨扫描周期一致）。"""
        return (
            current_file or "",
            rec.get("fixture", "") or "",
            rec.get("board", "") or "",
            rec.get("part", "") or "",
            rec.get("time", "") or "",
            rec.get("nominal", "") or "",
            rec.get("low", "") or "",
            rec.get("measured", "") or "",
            rec.get("high", "") or "",
            rec.get("threshold", "") or "",
            rec.get("test_source", "") or "",
            rec.get("desc", "") or "",
        )

    def _mark_seen(self, key, ts_epoch: float):
        self._seen_keys.add(key)
        self._seen_key_ts[key] = float(ts_epoch or 0)
        # 简单上限保护：超过上限时随机清理一部分最旧key
        if len(self._seen_keys) > self._seen_max:
            try:
                # 删除最旧的10%
                items = sorted(self._seen_key_ts.items(), key=lambda kv: kv[1])
                cut = max(1, int(self._seen_max * 0.10))
                for k, _ in items[:cut]:
                    self._seen_keys.discard(k)
                    self._seen_key_ts.pop(k, None)
            except Exception:
                pass

    def _cleanup_seen(self):
        """随时间清理去重缓存，避免无限增长。"""
        if self.threshold_hours <= 0:
            return
        now_ts = time.time()
        limit = self.threshold_hours * 3600
        # 只在数量较多时做清理，减少开销
        if len(self._seen_key_ts) < 2000:
            return
        for k, ts0 in list(self._seen_key_ts.items()):
            try:
                if ts0 and (now_ts - float(ts0) > limit):
                    self._seen_key_ts.pop(k, None)
                    self._seen_keys.discard(k)
            except Exception:
                pass

    # -*- coding: utf-8 -*-
    # 替换 real_time_tab.py 中的 add_record 方法
    # 性能优化版本：使用哈希表 + 批量 UI 更新



    def add_record(self, file_data):
        """
        高性能版本：
        1. 使用哈希表 O(1) 查找父节点
        2. 批量 UI 更新，减少重绘
        3. 延迟高度计算
        """
        records = file_data.get("records", [])
        if not records:
            return

        current_file = file_data.get("file", "")
        
        # 清理过期去重key
        self._cleanup_seen()
        
        cross_duplicates = find_cross_part_duplicates(records)
        
        # === 性能优化1：禁用排序和信号，批量更新 ===
        self._batch_updating = True
        self.tree.setSortingEnabled(False)  # 暂时禁用排序
        self.tree.setUpdatesEnabled(False)  # 暂停 UI 更新
        
        try:
            # === 按 (fixture, board, part) 分组 ===
            grouped = {}
            
            for rec in records:
                # 去重检查
                k = self._make_record_key(rec, current_file)
                if k in self._seen_keys:
                    continue
                
                fixture = rec.get("fixture", "") or ""
                board = rec.get("board", "") or ""
                part = rec.get("part", "") or ""
                
                group_key = (fixture, board, part)
                
                if group_key not in grouped:
                    grouped[group_key] = []
                grouped[group_key].append(rec)
            
            # === 性能优化2：使用哈希表 O(1) 查找父节点 ===
            items_to_insert = []  # 待插入的新父节点
            
            for group_key, new_recs in grouped.items():
                fixture, board, part = group_key
                
                # O(1) 查找现有父节点
                existing_parent = self._parent_index.get(group_key)
                
                # 验证父节点是否仍在树中（可能已被清理）
                if existing_parent and existing_parent.treeWidget() is None:
                    existing_parent = None
                    self._parent_index.pop(group_key, None)
                
                # 按时间排序（最新的在前）
                new_recs.sort(key=lambda r: r.get("time", ""), reverse=True)
                
                if existing_parent:
                    # === 场景1：已有父节点 ===
                    parent_ts = existing_parent.data(0, Qt.UserRole) or 0
                    
                    latest_rec = new_recs[0] if new_recs else None
                    latest_ts = self._parse_timestamp(latest_rec.get("time", "") if latest_rec else "")
                    
                    # 判断是否需要父子翻转
                    if latest_ts > parent_ts:
                        # === 父子翻转（优化版）===
                        new_parent = self._flip_parent_child(
                            existing_parent, latest_rec, new_recs[1:], 
                            group_key, cross_duplicates, current_file
                        )
                        
                        if new_parent:
                            items_to_insert.append((new_parent, latest_ts))
                            self._parent_index[group_key] = new_parent
                    else:
                        # === 正常追加子节点 ===
                        self._append_children(
                            existing_parent, new_recs, group_key, 
                            cross_duplicates, current_file
                        )
                else:
                    # === 场景2：新建父节点 ===
                    new_parent, latest_ts = self._create_parent_with_children(
                        new_recs, group_key, cross_duplicates, current_file
                    )
                    
                    if new_parent:
                        items_to_insert.append((new_parent, latest_ts))
                        self._parent_index[group_key] = new_parent
            
            # === 性能优化3：批量插入新父节点 ===
            for item, ts in items_to_insert:
                self.tree.insertTopLevelItem(0, item)
                self.tree.openPersistentEditor(item, self.DESC_COL)
        
        finally:
            # === 恢复 UI 更新 ===
            self._batch_updating = False
            self.tree.setUpdatesEnabled(True)
            self.tree.setSortingEnabled(True)
            self.tree.sortItems(0, Qt.DescendingOrder)
        
        # === 性能优化4：延迟清理和高度计算 ===
        self.remove_old_records()
        QTimer.singleShot(100, self._deferred_ui_update)


    def _parse_timestamp(self, time_str):
        """解析时间字符串为时间戳，失败返回0"""
        try:
            from datetime import datetime
            dt = datetime.strptime(f"{datetime.now().year}-{time_str}", "%Y-%m-%d %H:%M:%S")
            return dt.timestamp()
        except Exception:
            return 0


    def _flip_parent_child(self, old_parent, latest_rec, other_recs, group_key, cross_duplicates, current_file):
        """
        父子翻转（优化版）：避免字典复制，直接操作树节点
        返回新的父节点
        """
        fixture, board, part = group_key
        
        # 1. 从树中移除旧父节点（但保留引用）
        parent_index = self.tree.indexOfTopLevelItem(old_parent)
        if parent_index < 0:
            return None
        
        self.tree.takeTopLevelItem(parent_index)
        
        # 2. 创建新父节点
        main_part_normalized = _normalize_part_name(part)
        duplicate_subs = cross_duplicates.get(main_part_normalized, set())
        should_highlight = len(duplicate_subs) > 0
        
        desc_text = latest_rec.get("desc", "") or ""
        if should_highlight:
            warning = format_duplicate_warning(duplicate_subs)
            desc_text = self._prepend_warning_to_desc(desc_text, warning)
        
        test_source = latest_rec.get("test_source", "") or ""
        
        parent_vals = [
            latest_rec["time"], latest_rec["fixture"], latest_rec["board"], latest_rec["part"],
            latest_rec["nominal"], latest_rec["low"], latest_rec["measured"], latest_rec["high"],
            desc_text, test_source, latest_rec["file"]
        ]
        new_parent = TimeSortItem(parent_vals)
        latest_ts = self._parse_timestamp(latest_rec["time"])
        new_parent.setData(0, Qt.UserRole, latest_ts)
        
        k = self._make_record_key(latest_rec, current_file)
        self._mark_seen(k, latest_ts)
        
        if should_highlight:
            self._apply_highlight(new_parent, True)
        
        # 3. 旧父节点直接变成子节点（无需复制数据）
        new_parent.addChild(old_parent)
        self.tree.openPersistentEditor(old_parent, self.DESC_COL)
        
        # 4. 添加其余新记录
        for rec in other_recs:
            child = self._create_child_item(rec, should_highlight, duplicate_subs, current_file)
            if child:
                new_parent.addChild(child)
        
        return new_parent


    def _append_children(self, parent, recs, group_key, cross_duplicates, current_file):
        """追加子节点到现有父节点"""
        fixture, board, part = group_key
        main_part_normalized = _normalize_part_name(part)
        duplicate_subs = cross_duplicates.get(main_part_normalized, set())
        should_highlight = len(duplicate_subs) > 0
        
        for rec in recs:
            child = self._create_child_item(rec, should_highlight, duplicate_subs, current_file)
            if child:
                parent.addChild(child)


    def _create_parent_with_children(self, recs, group_key, cross_duplicates, current_file):
        """创建新父节点及其子节点，返回 (parent, timestamp)"""
        if not recs:
            return None, 0
        
        fixture, board, part = group_key
        latest = recs[0]
        older = recs[1:]
        
        main_part_normalized = _normalize_part_name(part)
        duplicate_subs = cross_duplicates.get(main_part_normalized, set())
        should_highlight = len(duplicate_subs) > 0
        
        desc_text = latest.get("desc", "") or ""
        if should_highlight:
            warning = format_duplicate_warning(duplicate_subs)
            desc_text = self._prepend_warning_to_desc(desc_text, warning)
        
        test_source = latest.get("test_source", "") or ""
        
        parent_vals = [
            latest["time"], latest["fixture"], latest["board"], latest["part"],
            latest["nominal"], latest["low"], latest["measured"], latest["high"],
            desc_text, test_source, latest["file"]
        ]
        parent = TimeSortItem(parent_vals)
        
        latest_ts = self._parse_timestamp(latest["time"])
        parent.setData(0, Qt.UserRole, latest_ts)
        
        k = self._make_record_key(latest, current_file)
        self._mark_seen(k, latest_ts)
        
        if should_highlight:
            self._apply_highlight(parent, True)
        
        # 添加子节点
        for rec in older:
            child = self._create_child_item(rec, should_highlight, duplicate_subs, current_file)
            if child:
                parent.addChild(child)
        
        return parent, latest_ts


    def _create_child_item(self, rec, should_highlight, duplicate_subs, current_file):
        """创建子节点 item"""
        desc_text = rec.get("desc", "") or ""
        if should_highlight:
            warning = format_duplicate_warning(duplicate_subs)
            desc_text = self._prepend_warning_to_desc(desc_text, warning)
        
        test_source = rec.get("test_source", "") or ""
        
        child_vals = [
            rec["time"], rec["fixture"], rec["board"], rec["part"],
            rec["nominal"], rec["low"], rec["measured"], rec["high"],
            desc_text, test_source, rec["file"]
        ]
        child = TimeSortItem(child_vals)
        
        ts = self._parse_timestamp(rec["time"])
        k = self._make_record_key(rec, current_file)
        self._mark_seen(k, ts)
        
        if should_highlight:
            self._apply_highlight(child, True)
        
        self.tree.openPersistentEditor(child, self.DESC_COL)
        
        return child


    def _deferred_ui_update(self):
        """延迟执行的 UI 更新（避免阻塞）"""
        if self._batch_updating:
            return
        
        self._autofit_columns()
        self._recalc_desc_heights()


    def remove_old_records(self):
        """清理过期记录时同步更新索引"""
        if self.threshold_hours <= 0:
            return
        
        now_ts = time.time()
        removed_keys = []
        
        for i in range(self.tree.topLevelItemCount() - 1, -1, -1):
            it = self.tree.topLevelItem(i)
            ts = it.data(0, Qt.UserRole) or 0
            if ts and (now_ts - float(ts) > self.threshold_hours * 3600):
                # 从索引中移除
                fixture = it.text(1)
                board = it.text(2)
                part = it.text(3)
                group_key = (fixture, board, part)
                self._parent_index.pop(group_key, None)
                
                self.tree.takeTopLevelItem(i)
        
        # 同步清理去重缓存
        self._cleanup_seen()

    def _recalc_desc_heights(self):
        col = self.DESC_COL
        width = self.tree.columnWidth(col)
        if width <= 10:
            return

        font = self.tree.font()
        fm = QFontMetrics(font)
        line_h = fm.lineSpacing()
        max_lines = 6

        def row_h(text: str) -> int:
            t = text or ""
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setDocumentMargin(0)
            doc.setPlainText(t)
            doc.setTextWidth(max(1, width - 8))

            raw_h = float(doc.size().height())
            wrapped_lines = max(1, int(math.ceil(raw_h / line_h)))

            min_lines = 2 if ("\n" in t) else 1
            lines = max(min_lines, wrapped_lines)

            if max_lines:
                lines = min(lines, max_lines)

            return max(26, int(lines * line_h + 4))

        def apply_item(item):
            if item is None:
                return
            item.setSizeHint(col, QSize(width, row_h(item.text(col))))

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
                return max(fm.horizontalAdvance(s) for s in lines)
            return fm.horizontalAdvance(text or "")

        for col in range(self.tree.columnCount()):
            header_text = self.tree.headerItem().text(col)
            max_px = text_width_col(col, header_text)
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                max_px = max(max_px, text_width_col(col, it.text(col)))
                for j in range(it.childCount()):
                    ch = it.child(j)
                    max_px = max(max_px, text_width_col(col, ch.text(col)))
            pad = 24
            natural = max_px + pad

            if col == 0:
                extra = self.tree.indentation() + 20
                natural += extra
                natural = max(natural, fm.horizontalAdvance("MM-DD HH:MM:SS") + 6)

            if col == self.DESC_COL:
                natural = max(200, min(natural, DESC_MAX_W))
            elif header_text in ("文件", "File", "log文件名", "Log", "日志", "log"):
                natural = max(120, min(natural, MAX_W))
            elif header_text == "测试源":
                natural = max(200, min(natural, 100))  # 测试源列固定宽度
            else:
                natural = max(MIN_W, min(natural, MAX_W))

            self.tree.setColumnWidth(col, natural)

    def shutdown(self):
        for name in ("monitor_thread", "monitor", "thread"):
            obj = getattr(self, name, None)
            if obj is None:
                continue
            try:
                if hasattr(obj, "stop"):
                    obj.stop()
            except Exception:
                pass
            try:
                if hasattr(obj, "quit"):
                    obj.quit()
            except Exception:
                pass
            try:
                if hasattr(obj, "wait"):
                    obj.wait(3000)
            except Exception:
                pass

        for tname in ("timer", "refresh_timer", "_timer"):
            t = getattr(self, tname, None)
            try:
                if t:
                    t.stop()
            except Exception:
                pass

        for lstname in ("timers", "_timers"):
            lst = getattr(self, lstname, None)
            if isinstance(lst, (list, tuple)):
                for t in lst:
                    try:
                        t.stop()
                    except Exception:
                        pass