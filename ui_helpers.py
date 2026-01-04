# ui_helpers.py
from PyQt5.QtWidgets import (
    QAction, QAbstractItemView, QTableWidget, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QLabel, QLineEdit, QTextEdit, QPlainTextEdit, QShortcut,QStyledItemDelegate
)
from PyQt5.QtGui import QFontMetrics, QColor, QPen,QKeySequence,QGuiApplication
from PyQt5.QtCore import QRect, Qt, QSize,QTimer
_GRID = QColor("#E6E6E6")

class GridDelegate(QStyledItemDelegate):
    """
    给所有列画“完整网格”：
    - 每列右边线
    - 第0列再画左边线
    - 每行底边线
    - 第0行再画顶边线
    这样单元格就是四边都有线，不会双线加粗
    """
    pen = QPen(_GRID)

    def paint(self, painter, option, index):
        # 先让默认绘制
        super().paint(painter, option, index)

        painter.save()
        painter.setPen(self.pen)

        r = option.rect
        row = index.row()
        col = index.column()
        # 右边线
        painter.drawLine(r.topRight(), r.bottomRight())
        # 左边线：仅第0列画一次
        if col == 0:
            painter.drawLine(r.topLeft(), r.bottomLeft())
        # 底边线：每行都画
        painter.drawLine(r.bottomLeft(), r.bottomRight())
        # 顶边线：仅第0行画一次
        if row == 0:
            painter.drawLine(r.topLeft(), r.topRight())

        painter.restore()


class WrapDelegate(QStyledItemDelegate):
    """
    描述列：只按 '\n' 分行显示，不自动换行；
    高度 = 行数 * 行距（最多4行）+ padding；
    同时也画四边网格线，逻辑与 GridDelegate 一致。
    """
    pen = QPen(_GRID)
    _max_lines = 4
    _vpad = 6

    def paint(self, painter, option, index):
        # 先填充文本（逐行）
        painter.save()
        text = index.data() or ""
        lines = text.split("\n")
        fm: QFontMetrics = option.fontMetrics
        line_h = fm.lineSpacing()
        x = option.rect.left() + 4
        y = option.rect.top() + 3
        width = max(16, option.rect.width() - 8)

        for ln in lines[: self._max_lines]:
            # 不自动换行，太长就省略
            elided = fm.elidedText(ln, Qt.ElideRight, width)
            painter.drawText(x, y + fm.ascent(), elided)
            y += line_h

        painter.restore()

        # 再画网格（四边）
        painter.save()
        painter.setPen(self.pen)
        r = option.rect
        row = index.row()
        col = index.column()

        painter.drawLine(r.topRight(), r.bottomRight())   # 右
        if col == 0:
            painter.drawLine(r.topLeft(), r.bottomLeft())  # 左
        painter.drawLine(r.bottomLeft(), r.bottomRight())  # 下
        if row == 0:
            painter.drawLine(r.topLeft(), r.topRight())    # 上

        painter.restore()

    def sizeHint(self, option, index):
        text = index.data() or ""
        fm = option.fontMetrics
        line_h = fm.lineSpacing()
        n = min(len(text.split("\n")), self._max_lines)
        return QSize(option.rect.width(), n * line_h + self._vpad)

# === 通用：全局启用“可选择+可复制” ===
from PyQt5.QtWidgets import QWidget, QMenu, QLineEdit
from PyQt5.QtGui import QClipboard
from PyQt5.QtCore import QPoint

def _copy_from_focus():
    w = QGuiApplication.focusWidget()
    cb = QGuiApplication.clipboard()
    if not w or not cb:
        return

    # 文本输入/编辑类：直接用内置 copy()
    if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
        w.copy()
        return

    # 表格/树：导出选择的单元格为 TSV（行\n、列\t）
    if isinstance(w, (QTableWidget, QTreeWidget)):
        # 取所有选中的“索引”，按行列排序，按行分组
        sel = w.selectedIndexes()
        if not sel:
            return
        sel = sorted(sel, key=lambda ix: (ix.row(), ix.column()))
        rows = {}
        for ix in sel:
            rows.setdefault(ix.row(), []).append(ix)
        lines = []
        for r in sorted(rows.keys()):
            cols = sorted(rows[r], key=lambda ix: ix.column())
            lines.append("\t".join((ix.data() or "") for ix in cols))
        cb.setText("\n".join(lines), QClipboard.Clipboard)
        cb.setText("\n".join(lines), QClipboard.Selection)
        return

    # 其它能导出 text 的控件
    text = getattr(w, "text", None)
    if callable(text):
        t = w.text()
        if t:
            cb.setText(str(t), QClipboard.Clipboard)
            cb.setText(str(t), QClipboard.Selection)

def _install_context_copy(widget: QWidget):
    """在 widget 上安装“右键复制”菜单（通用）"""
    if isinstance(widget, (QTableWidget, QTreeWidget, QTextEdit, QPlainTextEdit, QLineEdit, QLabel)):
        widget.setContextMenuPolicy(Qt.CustomContextMenu)
        def _show_menu(pos: QPoint, w=widget):
            menu = None
            # 文本编辑类 / QLineEdit：合并系统菜单并加“复制”
            if isinstance(w, (QTextEdit, QPlainTextEdit, QLineEdit)):
                menu = w.createStandardContextMenu()
                menu.addSeparator()
            else:
                menu = QMenu(w)
            act = menu.addAction("复制")
            act.setShortcut(QKeySequence.Copy)
            act.triggered.connect(_copy_from_focus)
            menu.exec_(w.mapToGlobal(pos))
        widget.customContextMenuRequested.connect(_show_menu)

def enable_text_selection_and_copy(root: QWidget):
    """
    1) 所有 QLabel 变为可选择文字
    2) 表格/树控件允许“单元格选择”、右键复制
    3) 全局注册 Ctrl+C 快捷键（聚焦控件决定复制来源）
    """
    if not root:
        return
    # ① 全部 QLabel 可选中
    for lab in root.findChildren(QLabel):
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        _install_context_copy(lab)

    # ② 表格/树：选“单元格”，单选（需要多选可改为 ExtendedSelection）
    for tbl in root.findChildren(QTableWidget):
        tbl.setSelectionBehavior(QAbstractItemView.SelectItems)
        tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        _install_context_copy(tbl)

    for tree in root.findChildren(QTreeWidget):
        tree.setSelectionBehavior(QAbstractItemView.SelectItems)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        _install_context_copy(tree)

    # ③ 文本编辑类：只读的设为只读也能复制；加右键复制
    for ed in root.findChildren((QTextEdit, QPlainTextEdit, QLineEdit)):
        try:
            if hasattr(ed, "setReadOnly") and ed.isReadOnly() is False:
                # 保持原逻辑：不强制只读；你若想纯展示页可 setReadOnly(True)
                pass
        except Exception:
            pass
        _install_context_copy(ed)

    # ④ 全局复制快捷键（聚焦控件决定复制行为）
    QShortcut(QKeySequence.Copy, root, activated=_copy_from_focus)
