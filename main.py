# -*- coding: utf-8 -*-
import sys, time, os, json
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget,QTableWidget, QHeaderView,QMessageBox
from PyQt5.QtGui import QFont,QGuiApplication
from PyQt5.QtCore import QTimer

from monitor import MonitorThread
from real_time_tab import RealTimeTab
from history_tab import HistoryTab
from cache_db import CacheDB
from kanban_tab import KanbanTab, KanbanQueryTab


# 路径配置
BASE_DIR = r"C:\test_data"             # 报文根目录（年\月\日\工装\板子\*.txt）
BOARDS_ROOT = r"C:\Agilent_ICT\boards" # 各工装的 wirelist 所在根目录（主目录）
BOARDS_ROOT_FALLBACK = r"C:\I3070_ICT\boards"  # 备用目录
THRESHOLD_HOURS = 24                   # 实时页清理阈值（小时）


def _get_version_string():
    """获取当前版本号字符串"""
    try:
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        version_path = os.path.join(base_dir, "version.json")
        if os.path.exists(version_path):
            with open(version_path, "r", encoding="utf-8") as f:
                ver_info = json.load(f)
                version = ver_info.get("version", "2.0")
                build = ver_info.get("build", 0)
                return f"{version} (Build {build})"
    except:
        pass
    return "2.0"


def _to_epoch(ts_like):
    """支持 'MM-DD HH:MM:SS' / 'YYYY-MM-DD HH:MM:SS' / epoch"""
    if isinstance(ts_like, (int, float)):
        return int(ts_like)
    s = str(ts_like).strip()
    for fmt in ("%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            tm = time.strptime(s, fmt)
            year = tm.tm_year if fmt.startswith("%Y") else time.localtime().tm_year
            return int(time.mktime((year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0, 0, -1)))
        except Exception:
            pass
    return int(time.time())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        version_str = _get_version_string()
        self.setWindowTitle(f"ICT_Monitor_{version_str}")

        # 1) 初始化缓存库
        self.cache = CacheDB("cache/cache.sqlite")

        # 2) 初始化各页签（把 cache 注入）
        self.real_time_tab = RealTimeTab(threshold_hours=THRESHOLD_HOURS, cache=self.cache)
        self.kanban_tab = KanbanTab(cache=self.cache, base_dir=BASE_DIR)
        self.kanban_query_tab = KanbanQueryTab(cache=self.cache, base_dir=BASE_DIR)
        self.history_tab = HistoryTab(base_dir=BASE_DIR, boards_root=BOARDS_ROOT,
                                      boards_root_fallback=BOARDS_ROOT_FALLBACK, cache=self.cache)

        tabs = QTabWidget()
        tabs.addTab(self.real_time_tab, "实时")
        tabs.addTab(self.kanban_tab, "看板")
        tabs.addTab(self.kanban_query_tab, "看板数据查询")
        tabs.addTab(self.history_tab, "历史数据")
        self.setCentralWidget(tabs)
        QTimer.singleShot(0, self._first_show_fit)  # 首次显示后自适应

        # 3) 监控线程
        self.monitor_thread = MonitorThread(base_dir=BASE_DIR, boards_root=BOARDS_ROOT,
                                            boards_root_fallback=BOARDS_ROOT_FALLBACK)
        # 统一走一个回调，里面负责：写缓存 + 计算 end_ts + 通知 UI
        self.monitor_thread.file_parsed.connect(self.on_file_parsed)
        self.monitor_thread.start()

        # （可选）程序启动时，用缓存回灌一次实时页（断点续跑可见效果）
        try:
            for latest, hist in self.cache.fetch_latest_grouped(hours=THRESHOLD_HOURS):
                payload = {
                    "fixture": latest["fixture"],
                    "board": latest["board"],
                    "file": latest.get("file", ""),
                    "records": [],   # 回灌到实时页：只需要最顶和子项内容
                    "summary": {},   # 实时页不依赖 summary；看板使用批次时再聚合
                }
                # 组装成实时页的父+子（你现有 add_record 内部有聚合，不强依赖这里）
                # 直接调用 add_record(payload) 让它自行聚合
                self.real_time_tab.add_record(payload)
        except Exception:
            pass

    def on_file_parsed(self, payload: dict):
        fixture = payload.get("fixture", "") or ""
        board = payload.get("board", "") or ""
        file = payload.get("file", "") or ""
        records = payload.get("records", []) or []
        summary = payload.get("summary", {}) or {}

        # 1) ——写缓存（兜底：records 为空但 summary 有计数时，补“占位记录”）——
        try:
            if not records and summary:
                # 尝试用当前文件的“结束时间”做时间戳；没有就用当前时间
                def _to_epoch(ts_like):
                    import time
                    if isinstance(ts_like, (int, float)):
                        return int(ts_like)
                    s = str(ts_like).strip()
                    for fmt in ("%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                        try:
                            tm = time.strptime(s, fmt)
                            year = tm.tm_year if fmt.startswith("%Y") else time.localtime().tm_year
                            return int(
                                time.mktime((year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0, 0, -1)))
                        except Exception:
                            pass
                    return int(time.time())

                # ——生成占位记录（修正版：保证每条唯一）——
                end_ts = payload.get("end_ts")
                base_ts = _to_epoch(end_ts) if end_ts else int(time.time())

                synth = []
                for i, (part, cnt) in enumerate(summary.items()):
                    n = int(cnt) if str(cnt).isdigit() else 1
                    for k in range(n):
                        synth.append({
                            "time": base_ts + k,  # ← 每条 +k，避免 ts 完全相同
                            "part": str(part),
                            "nominal": "", "low": "",
                            "measured": f"syn#{i}-{k}",  # ← 或者用唯一 measured；两者择一也行
                            "high": "", "desc": ""
                        })
                if synth:
                    self.cache.add_records(fixture, board, file, synth)
            else:
                if records:
                    self.cache.add_records(fixture, board, file, records)
        except Exception:
            pass

        # 2) 计算 end_ts（优先真实 records；否则保持原逻辑）
        if records:
            try:
                payload["end_ts"] = max(
                    _to_epoch(r.get("time")) for r in records if r.get("time")
                )
            except Exception:
                payload["end_ts"] = None

        # 3) 通知 UI
        self.real_time_tab.add_record(payload)
        self.kanban_tab.update_kanban(payload)

    def _first_show_fit(self):
        """首次显示：窗口和表格列自适应；保持 0/2 固定，1 自适应，从而次数列始终贴右。"""
        # 让主窗口初始占屏幕 90%
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.7), int(screen.height() * 0.5))
        self.move(
            screen.x() + (screen.width() - self.width()) // 2,
            screen.y() + (screen.height() - self.height()) // 2
        )

        # 对窗口中所有 QTableWidget 设置列模式：
        # 0 序号: Fixed（定宽）
        # 1 零件名: Stretch（吃掉剩余空间）
        # 2 次数: Fixed（定宽，因 1 Stretch，视觉上始终贴右）
        tables = self.findChildren(QTableWidget)
        for tbl in tables:
            header = tbl.horizontalHeader()
            header.setStretchLastSection(False)  # 让最后一列不吃空白
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Fixed)

            # 给个合理的默认定宽（你可按需改）
            if tbl.columnCount() >= 3:
                tbl.setColumnWidth(0, 56)  # 序号
                tbl.setColumnWidth(2, 80)  # 次数

            # 初次按内容铺一下行高
            tbl.resizeRowsToContents()

    def closeEvent(self, e):
        # === 退出前确认 ===
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "退出警告",
            "请勿关闭ICT零件失效监控系统！",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply in (QMessageBox.Yes, QMessageBox.No):
            e.ignore()
            return
        # 优雅关闭线程与缓存
        try:
            if self.monitor_thread and self.monitor_thread.isRunning():
                self.monitor_thread.requestInterruption()
                self.monitor_thread.wait(1500)
        except Exception:
            pass
        try:
            if hasattr(self, "cache") and self.cache:
                self.cache.close()
        except Exception:
            pass
        e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
