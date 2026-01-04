# -*- coding: utf-8 -*-
import os
from collections import Counter, defaultdict
import time

from PyQt5.QtWidgets import (
    QWidget, QGroupBox, QHBoxLayout, QVBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QSizePolicy, QHeaderView, QLayout, QHeaderView, QStyle,
    QComboBox, QPushButton, QDateEdit, QSpacerItem, QSizePolicy as QSP, QAbstractItemView,QAbstractSpinBox, QScroller, QScrollerProperties
)
from PyQt5.QtCore import Qt, QDate, QTimer
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox, QTableWidgetItem,QHeaderView
from pathlib import Path
from collections import Counter
from fail_parse_util import concurrent_scan_counts, build_model_dir


def _to_epoch(ts_like):
    """支持 'MM-DD HH:MM:SS' 或 'YYYY-MM-DD HH:MM:SS' 或 epoch"""
    if isinstance(ts_like, (int, float)):
        return int(ts_like)
    s = str(ts_like).strip()
    for fmt in ("%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            tm = time.strptime(s, fmt)
            year = tm.tm_year if fmt.startswith("%Y") else time.localtime().tm_year
            return int(time.mktime((year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0, 0, -1)))
        except:
            pass
    return int(time.time())


class ScanWorker(QThread):
    finished = pyqtSignal(dict, str)  # (counts_dict, error_msg or "")

    def __init__(self, root_dir: Path, max_workers=8, parent=None):
        super().__init__(parent)
        self.root_dir = root_dir
        self.max_workers = max_workers

    def run(self):
        try:
            counts = concurrent_scan_counts(self.root_dir, self.max_workers)
            self.finished.emit(dict(counts), "")
        except Exception as e:
            self.finished.emit({}, str(e))



class _Panel(QWidget):
    """单列面板：标题 + 副标题（纯文本）+ 统计表（3列，零件名列可换行）"""
    def __init__(self, title: str, bg: str):
        super().__init__()
        self.wrap = QGroupBox(title)
        self.wrap.setStyleSheet(f"""
            QGroupBox {{
                background:{bg};
                border:1px solid #E6E6E6;
                margin-top: 18px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 4px 8px;
                font-weight: 700;
            }}
        """)

        self.layout = QVBoxLayout(self.wrap)
        self.layout.setContentsMargins(12, 10, 12, 12)
        self.layout.setSpacing(10)

        # 副标题（纯文本）
        self.subtitle = QLabel("")
        sfont = QFont(); sfont.setBold(True)
        self.subtitle.setFont(sfont)
        self.subtitle.setAlignment(Qt.AlignHCenter)
        self.layout.addWidget(self.subtitle)

        # 表格：序号 | 零件名 | 次数
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["序号", "零件名", "次数"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter)
        # 统一成：序号(固定) + 零件名(Stretch) + 次数(固定)
        min_table_w = setup_three_col_table(self.table, w_index=56, w_count=80)
        self.layout.addWidget(self.table)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(self.wrap)

        # —— 锁定面板的最小宽度：表格最小宽度 + 各层左右边距 ——
        wrap_m = self.layout.contentsMargins()  # 给 self.wrap 设置的内部边距
        outer_m = outer.contentsMargins()  # 面板外层布局的边距
        panel_min = min_table_w + wrap_m.left() + wrap_m.right() + outer_m.left() + outer_m.right()
        self.setMinimumWidth(panel_min)



        panel_min = min_table_w + wrap_m.left() + wrap_m.right() + outer_m.left() + outer_m.right()

        # 新增：把分组框/外层样式的边框厚度也加上，避免临界剪裁
        gb_frame = self.wrap.style().pixelMetric(QStyle.PM_DefaultFrameWidth) * 2
        panel_min += gb_frame + 4  # 再给一点冗余

        self.setMinimumWidth(panel_min)

    def render(self, key_text: str, counter: Counter):
        self.subtitle.setText(key_text if key_text else "")

        items = sorted(counter.items(), key=lambda x: (-x[1], x[0])) if counter else []
        self.table.setRowCount(len(items))

        for r, (part, cnt) in enumerate(items):
            name = (part or "").strip()
            low = name.lower()
            if low.startswith("open: "):
                name = "Open:\n" + name[6:].strip()
            elif low.startswith("short: "):
                name = "Short:\n" + name[7:].strip()
            name = name.replace(" ↔ ", "\n↔ ")

            it_rank = QTableWidgetItem(str(r + 1))
            it_name = QTableWidgetItem(name)
            it_cnt = QTableWidgetItem(str(cnt))
            it_rank.setTextAlignment(Qt.AlignCenter)
            it_cnt.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 0, it_rank)
            self.table.setItem(r, 1, it_name)
            self.table.setItem(r, 2, it_cnt)

        self.table.resizeRowsToContents()
        fm = QFontMetrics(self.table.font())
        min_detail = max(140, fm.horizontalAdvance(" 3%P4268 ↔ 3%P4253 "))
        cur_w = self.table.columnWidth(1)
        if cur_w < min_detail:
            self.table.setColumnWidth(1, min_detail)


class _QueryPanel(QWidget):
    """
    数据查询面板（文件系统驱动）：
    - 平台来自 BASE_DIR/YYYY/MM/DD 的一级子目录
    - 型号来自 BASE_DIR/YYYY/MM/DD/<平台> 的子目录
    - 新建/删除目录自动刷新（定时扫描）
    """
    def __init__(self, title: str, bg: str, cache, base_dir):
        super().__init__()
        self.cache = cache
        self.base_dir = base_dir
        self._scan_thread = None
        self._last_query_key = None  # 防止期间改选项导致结果串台

        # --- UI 容器 ---
        self.wrap = QGroupBox(title)
        self.wrap.setStyleSheet(f"""
            QGroupBox {{
                background:{bg};
                border:1px solid #E6E6E6;
                margin-top: 18px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 4px 8px;
                font-weight: 700;
            }}
        """)
        root = QVBoxLayout(self.wrap)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        # --- 控件（先创建，再绑定信号） ---  —— 紧凑顶栏
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 0)  # 去掉多余外边距
        ctrl.setSpacing(6)  # 控件之间 6px

        self.cb_fixture = QComboBox()
        self.cb_board = QComboBox()
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.date_edit.wheelEvent = lambda e: e.ignore()
        self.btn_query = QPushButton("查询")

        # 下拉宽度与自适应
        self.cb_fixture.setMinimumWidth(120)
        self.cb_board.setMinimumWidth(160)
        self.cb_fixture.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb_board.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.date_edit.setFixedWidth(160)
        self.btn_query.setFixedWidth(64)

        # 统一标签：右对齐 + 固定宽度（让控件紧贴标签）
        fm = self.fontMetrics()
        lab_w = max(
            fm.horizontalAdvance("平台"),
            fm.horizontalAdvance("型号"),
            fm.horizontalAdvance("日期"),
        ) + 6

        def _lab(txt: str):
            lab = QLabel(txt)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lab.setFixedWidth(lab_w)
            return lab

        # 平台
        ctrl.addWidget(_lab("平台"))
        ctrl.addWidget(self.cb_fixture)

        # 型号
        ctrl.addSpacing(6)
        ctrl.addWidget(_lab("型号"))
        ctrl.addWidget(self.cb_board)

        # 日期
        ctrl.addSpacing(6)
        ctrl.addWidget(_lab("日期"))
        ctrl.addWidget(self.date_edit)

        # 查询按钮靠左、整体靠左排列
        ctrl.addSpacing(8)
        ctrl.addWidget(self.btn_query)
        ctrl.addStretch(1)

        # 适度压缩控件内边距与高度（可选）
        self.wrap.setStyleSheet(self.wrap.styleSheet() + """
            QComboBox { padding:2px 6px; min-height:26px; }
            QDateEdit { padding:2px 6px; min-height:26px; }
            QPushButton { padding:2px 10px; min-height:26px; }
            QLabel { margin:0px; }
        """)

        root.addLayout(ctrl)

        # --- 结果表 ---
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["序号", "零件名", "次数"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter)
        min_table_w = setup_three_col_table(self.table, w_index=56, w_count=80)

        root.addWidget(self.table)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(self.wrap)

        # 锁定查询面板的最小宽度
        wrap_m = root.contentsMargins()
        outer_m = outer.contentsMargins()
        panel_min = min_table_w + wrap_m.left() + wrap_m.right() + outer_m.left() + outer_m.right()
        self.setMinimumWidth(panel_min)

        # --- 信号绑定（一定放在控件创建之后） ---
        self.cb_fixture.currentIndexChanged.connect(self._on_fixture_changed)
        self.date_edit.dateChanged.connect(self._on_date_changed)
        self.btn_query.clicked.connect(self._do_query)

        # --- 首次加载（按当前日期扫描） ---
        self._reload_fixtures_fs()
        self._reload_boards_fs()

        # --- 定时刷新文件系统（新增/删除平台/型号时自动更新） ---
        self._timer = QTimer(self)
        self._timer.setInterval(2000)  # 2s
        self._timer.timeout.connect(self._tick_rescan)
        self._timer.start()


        panel_min = min_table_w + wrap_m.left() + wrap_m.right() + outer_m.left() + outer_m.right()

        # 新增：把分组框/外层样式的边框厚度也加上，避免临界剪裁
        gb_frame = self.wrap.style().pixelMetric(QStyle.PM_DefaultFrameWidth) * 2
        panel_min += gb_frame + 4  # 再给一点冗余

        self.setMinimumWidth(panel_min)

    # ===== 文件系统扫描 =====
    def _day_path(self) -> str:
        if not self.base_dir:
            return ""
        d = self.date_edit.date().toString("yyyy-MM-dd")
        y, m, dd = d.split("-")
        return os.path.join(self.base_dir, y, m, dd)

    def _reload_fixtures_fs(self):
        day_dir = self._day_path()
        fixtures = []
        if day_dir and os.path.isdir(day_dir):
            try:
                fixtures = [n for n in os.listdir(day_dir)
                            if os.path.isdir(os.path.join(day_dir, n))]
            except Exception:
                fixtures = []
        fixtures.sort()

        cur = self.cb_fixture.currentText().strip()
        self.cb_fixture.blockSignals(True)
        self.cb_fixture.clear()
        self.cb_fixture.addItems(fixtures)
        if cur and cur in fixtures:
            self.cb_fixture.setCurrentText(cur)
        self.cb_fixture.blockSignals(False)

    def _reload_boards_fs(self):
        day_dir = self._day_path()
        boards = []
        fix = self.cb_fixture.currentText().strip()
        if day_dir and fix:
            fix_dir = os.path.join(day_dir, fix)
            if os.path.isdir(fix_dir):
                try:
                    boards = [n for n in os.listdir(fix_dir)
                              if os.path.isdir(os.path.join(fix_dir, n))]
                except Exception:
                    boards = []
        boards.sort()

        cur = self.cb_board.currentText().strip()
        self.cb_board.blockSignals(True)
        self.cb_board.clear()
        self.cb_board.addItems(boards)
        if cur and cur in boards:
            self.cb_board.setCurrentText(cur)
        self.cb_board.blockSignals(False)

    def _tick_rescan(self):
        # 平台变化？
        old_fx = [self.cb_fixture.itemText(i) for i in range(self.cb_fixture.count())]
        day_dir = self._day_path()
        new_fx = []
        if day_dir and os.path.isdir(day_dir):
            try:
                new_fx = sorted([n for n in os.listdir(day_dir)
                                 if os.path.isdir(os.path.join(day_dir, n))])
            except Exception:
                new_fx = []
        if new_fx != old_fx:
            self._reload_fixtures_fs()
            self._reload_boards_fs()
            return

        # 型号变化？
        fix = self.cb_fixture.currentText().strip()
        if fix:
            old_bd = [self.cb_board.itemText(i) for i in range(self.cb_board.count())]
            fix_dir = os.path.join(day_dir, fix) if day_dir else ""
            new_bd = []
            if fix_dir and os.path.isdir(fix_dir):
                try:
                    new_bd = sorted([n for n in os.listdir(fix_dir)
                                     if os.path.isdir(os.path.join(fix_dir, n))])
                except Exception:
                    new_bd = []
            if new_bd != old_bd:
                self._reload_boards_fs()

    # ===== 联动 =====
    def _on_fixture_changed(self, _):
        self._reload_boards_fs()

    def _on_date_changed(self, _):
        self._reload_fixtures_fs()
        self._reload_boards_fs()

    # ========= 替换原来的 _do_query =========
    def _do_query(self):
        fix = self.cb_fixture.currentText().strip()
        brd = self.cb_board.currentText().strip()
        qdate = self.date_edit.date()
        if not (fix and brd):
            self.table.setRowCount(0)
            return

        # 构建目录：C:\test_data\YYYY\MM\DD\平台\型号
        root_dir = build_model_dir(self.base_dir, qdate, fix, brd)

        # 记录当前查询键，防止异步结果回填到已变更的选择上
        self._last_query_key = (qdate.toString("yyyy-MM-dd"), fix, brd)

        # UI 防抖 & 清空
        self.btn_query.setEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(0)

        # 目录不存在或无文件 -> 直接提示并恢复
        from pathlib import Path
        p = Path(str(root_dir))
        files = list(p.glob("*.txt")) if p.exists() else []
        if not files:
            self.btn_query.setEnabled(True)
            # 没有报文文件就保持空表，不再用 DB 补齐，避免“空目录也显示1次”
            return

        # 后台线程并发扫描
        class _ScanWorker(QThread):
            finished = pyqtSignal(object, object)  # (counts_dict, query_key)

            def __init__(self, path, key, parent=None):
                super().__init__(parent);
                self.path = path;
                self.key = key

            def run(self):
                try:
                    cnt = concurrent_scan_counts(Path(self.path), max_workers=8)
                    self.finished.emit(dict(cnt), self.key)
                except Exception:
                    self.finished.emit({}, self.key)

        self._scan_thread = _ScanWorker(str(root_dir), self._last_query_key, self)
        self._scan_thread.finished.connect(self._on_scan_finished)
        self._scan_thread.start()

    # ========= 新增：扫描完成回填 =========
    def _on_scan_finished(self, counts_dict, query_key):
        # 若期间用户改了日期/平台/型号，则丢弃本结果
        if query_key != self._last_query_key:
            return

        self.btn_query.setEnabled(True)
        from collections import Counter
        counts = Counter(counts_dict or {})
        items = counts.most_common()
        self.table.setRowCount(len(items))

        for r, (part, cnt) in enumerate(items):
            it_rank = QTableWidgetItem(str(r + 1))
            it_name = QTableWidgetItem(str(part))
            it_cnt = QTableWidgetItem(str(cnt))
            it_rank.setTextAlignment(Qt.AlignCenter)
            it_cnt.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 0, it_rank)
            self.table.setItem(r, 1, it_name)
            self.table.setItem(r, 2, it_cnt)


class KanbanTab(QWidget):
    """
    看板页（四列竖排）：
    - 当前在测：同一 'fixture/board' 的累计统计（Counter 累加）
    - 最近1/最近2：沿用历史窗口（如不需要可隐藏）
    - 数据查询：平台/型号/日期 -> 查询结果（序号/零件名/次数）
    批次落库规则：当检测到“新的板子型号出现”时，将当前累计作为“一个批次”，
    批次的“可查询日期”按 end_ts 的自然日计算。
    """
    def __init__(self, cache=None, base_dir=None):
        super().__init__()
        self.cache = cache
        self.base_dir = base_dir

        # 四列竖排
        self.p_current = _Panel("最新报错", "#A3C7FD")      # 淡蓝
        self.p_r1      = _Panel("最近1",   "#B4F3C5")      # 淡绿
        self.p_r2      = _Panel("最近2",   "#FFF9E6")      # 淡黄
        # self.p_query = _QueryPanel("数据查询", "#F5F5F5",cache=self.cache, base_dir=self.base_dir)  # ← 传入

        row = QHBoxLayout(self)
        row.setSizeConstraint(QLayout.SetMinimumSize)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(8)
        for p in (self.p_current, self.p_r1, self.p_r2):
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            row.addWidget(p)

        # 统计缓存
        self._cur_key = ""             # "fixture/board"
        self._cur_counter = Counter()  # 当前在测累计
        self._r1_key, self._r1_counter = "", Counter()
        self._r2_key, self._r2_counter = "", Counter()

        # 批次时间边界
        self._batch_start_ts = None
        self._batch_end_ts   = None

        self._refresh_all()

    # === 外部接口：由监控线程/主窗口传入 ===
    def update_kanban(self, file_data: dict):
        fixture = (file_data.get("fixture") or "").strip()
        board = (file_data.get("board") or "").strip()
        summary = file_data.get("summary", {}) or {}
        is_global_scan = file_data.get("is_global_scan", False)  # 是否是全局扫描
        new_key = f"{fixture}/{board}".strip()
        if not new_key:
            return

        # 看板页只处理全局扫描信号，忽略单文件信号（单文件信号给实时页用）
        if not is_global_scan:
            return

        # 诊断日志
        # with open("kanban_debug.log", "a", encoding="utf-8") as f:
        #     from datetime import datetime
        #     f.write(f"[{datetime.now()}] 收到全局信号: {fixture}/{board}, summary={summary}\n")

        end_ts = file_data.get("end_ts")
        if not end_ts:
            recs = file_data.get("records") or []
            if recs:
                end_ts = max(_to_epoch(r.get("time")) for r in recs if r.get("time"))
        end_ts = _to_epoch(end_ts) if end_ts else int(time.time())

        # 全局扫描：直接设置Counter为全局汇总结果
        if self._cur_key == new_key or not self._cur_key:
            # 相同板型：直接替换Counter
            self._cur_key = new_key
            self._cur_counter = Counter(summary)  # 直接设置，不累加
            if not self._batch_start_ts:
                self._batch_start_ts = end_ts
            self._batch_end_ts = end_ts
            print(f"[看板页] 全局扫描更新: {summary}")
        else:
            # 板型切换：把上一批落库，开启新批次
            self._finalize_current_batch_to_db()
            self._r2_key, self._r2_counter = self._r1_key, self._r1_counter
            self._r1_key, self._r1_counter = self._cur_key, self._cur_counter
            self._cur_key = new_key
            self._cur_counter = Counter(summary)
            self._batch_start_ts = end_ts
            self._batch_end_ts = end_ts

        # 防止当天只有1~2个板型时出现“当前/最近2重复”
        self._dedupe_slots()
        self._refresh_all()

        # ★ 新增：不管是否切批，都再尝试刷新一次（首次有数据也能把下拉填上）
        # try:
        #     self.p_query._reload_fixtures()
        # except Exception:
        #     pass


    def _dedupe_slots(self):
        """防重复：当当天只有1~2个板型（或重复信号来回切）时，避免最近2/最近1与当前重复"""
        # 最近1与当前相同则清空最近1
        if self._r1_key and self._r1_key == self._cur_key:
            self._r1_key, self._r1_counter = "", Counter()

        # 最近2如果为空不用管；如果与当前或最近1重复则清空最近2
        if self._r2_key and (self._r2_key == self._cur_key or self._r2_key == self._r1_key):
            self._r2_key, self._r2_counter = "", Counter()

    # === 把“当前在测”的一批，保存为批次 ===
    def _finalize_current_batch_to_db(self):
        if not (self.cache and self._cur_key and self._batch_start_ts and self._batch_end_ts):
            return
        fixture, board = self._cur_key.split("/", 1)
        try:
            self.cache.finalize_batch(
                fixture=fixture,
                board=board,
                start_ts=int(self._batch_start_ts),
                end_ts=int(self._batch_end_ts)
            )
        except Exception as e:
            # 这里不弹窗，避免打断；可改写日志
            pass

    # === 渲染 ===
    def _refresh_all(self):
        self.p_current.render(self._cur_key, self._cur_counter)
        self.p_r1.render(self._r1_key, self._r1_counter)
        self.p_r2.render(self._r2_key, self._r2_counter)

    # === 清空/重置（跨天手动清空等场景） ===
    def reset(self):
        # 落库当前批次后再清（可按需要选择）
        self._finalize_current_batch_to_db()

        self._cur_key, self._cur_counter = "", Counter()
        self._r1_key, self._r1_counter = "", Counter()
        self._r2_key, self._r2_counter = "", Counter()
        self._batch_start_ts = None
        self._batch_end_ts = None
        self._refresh_all()

# === 独立标签页：看板数据查询 ===
class KanbanQueryTab(_QueryPanel):
    def __init__(self, cache=None, base_dir=None):
        # 标题直接用“看板数据查询”，背景色延续原来的灰色
        super().__init__(title="看板数据查询", bg="#F5F5F5", cache=cache, base_dir=base_dir)


def setup_three_col_table(tbl, w_index=44, w_count=64):
    """
    3列表：序号(固定) + 零件名(Interactive) + 次数(固定)
    目标：无横向滚动条；最窄时三列表头完整；序号尽量紧凑；次数不再被截。
    返回：min_table_w（表格本体的最小宽度）
    """
    header = tbl.horizontalHeader()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.Fixed)
    header.setSectionResizeMode(1, QHeaderView.Interactive)   # 关键：改为 Interactive
    header.setSectionResizeMode(2, QHeaderView.Fixed)

    # 计算三列表头的最小可读宽
    fm = header.fontMetrics()
    idx_min  = fm.horizontalAdvance(" 序号 ") + 8
    name_min = fm.horizontalAdvance("  零件名  ") + 12
    cnt_min  = fm.horizontalAdvance(" 次数 ") + 8

    # 固定列采用：传入建议值 vs 最小可读宽 的较大者
    w_idx = max(w_index, idx_min)
    w_cnt = max(w_count, cnt_min)
    tbl.setColumnWidth(0, w_idx)
    tbl.setColumnWidth(2, w_cnt)

    # 中间列给一个起始宽度，并设定不能小于 name_min
    tbl.setColumnWidth(1, max(tbl.columnWidth(1), name_min))
    header.setMinimumSectionSize(name_min)

    # 表头文字置中
    for c, text in enumerate(("序号", "零件名", "次数")):
        item = tbl.horizontalHeaderItem(c) or QTableWidgetItem(text)
        item.setText(text)
        item.setTextAlignment(Qt.AlignCenter)
        tbl.setHorizontalHeaderItem(c, item)

    # 预留各种开销：表格边框、垂直表头、样式边框、潜在竖向滚动条宽度
    frame_w   = tbl.frameWidth() * 2
    vhead_w   = tbl.verticalHeader().width() if tbl.verticalHeader().isVisible() else 0
    style_pad = tbl.style().pixelMetric(QStyle.PM_DefaultFrameWidth) * 2
    vscroll_w = tbl.verticalScrollBar().sizeHint().width()  # 即使当前不显示，也预留防被盖
    safety    = 6  # 小冗余，避免不同样式下被吞

    min_table_w = w_idx + name_min + w_cnt + frame_w + vhead_w + style_pad + vscroll_w + safety

    tbl.setMinimumWidth(min_table_w)
    tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    tbl.model().headerDataChanged.emit(Qt.Horizontal, 0, 2)
    return min_table_w

