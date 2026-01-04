# -*- coding: utf-8 -*-
"""
ICT Monitor 启动器
负责:
1. 启动前自动检查并同步最新版本
2. 启动主程序
3. 提供简洁的进度提示界面
"""
import os
import sys
import subprocess
import time
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLabel,
    QProgressBar, QPushButton, QMessageBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from version_manager import VersionManager


class UpdateThread(QThread):
    """后台更新线程"""
    progress = pyqtSignal(str)  # 进度信息
    finished = pyqtSignal(bool, bool)  # (成功, 有更新)

    def __init__(self, version_manager):
        super().__init__()
        self.vm = version_manager

    def run(self):
        try:
            self.progress.emit("正在检查版本...")
            time.sleep(0.5)  # 让用户看到提示

            need_update, server_ver = self.vm.check_update()

            if not need_update:
                self.progress.emit("当前已是最新版本")
                time.sleep(0.3)
                self.finished.emit(True, False)
                return

            # 需要更新
            version_str = server_ver.get('version', '未知版本')
            self.progress.emit(f"发现新版本: {version_str}")
            time.sleep(0.5)

            self.progress.emit("正在下载更新...")
            success = self.vm.sync_update(server_ver)

            if success:
                self.progress.emit("更新完成!")
                time.sleep(0.3)
                self.finished.emit(True, True)
            else:
                self.progress.emit("更新失败,将使用当前版本启动")
                time.sleep(1)
                self.finished.emit(False, True)

        except Exception as e:
            self.progress.emit(f"更新出错: {str(e)}")
            time.sleep(1)
            self.finished.emit(False, False)


class LauncherDialog(QDialog):
    """启动器对话框"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ICT Monitor 启动器")
        self.setFixedSize(480, 200)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)

        # 初始化版本管理器
        self.vm = VersionManager()

        # 获取主程序路径
        self.main_exe = self._find_main_executable()

        # 布局
        layout = QVBoxLayout()

        # 标题
        title_label = QLabel("ICT 报文监控系统")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        layout.addWidget(title_label)

        # 状态标签
        self.status_label = QLabel("正在初始化...")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self.progress_bar)

        # 版本信息
        local_ver = self.vm.get_local_version()
        version_text = f"当前版本: {local_ver.get('version', '未知')} (Build {local_ver.get('build', 0)})"
        self.version_label = QLabel(version_text)
        self.version_label.setAlignment(Qt.AlignCenter)
        self.version_label.setStyleSheet("color: gray; font-size: 9pt;")
        layout.addWidget(self.version_label)

        # 跳过按钮(初始隐藏)
        self.skip_button = QPushButton("跳过更新,直接启动")
        self.skip_button.clicked.connect(self.launch_main)
        self.skip_button.hide()
        layout.addWidget(self.skip_button)

        self.setLayout(layout)

        # 启动更新检查
        self.update_thread = None
        self.start_update_check()

    def _find_main_executable(self):
        """查找主程序可执行文件"""
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

        # 可能的主程序名称
        candidates = [
            "ICT_Monitor.exe",
            "ICT_Monitor_1.0.9.exe",
            "main.exe"
        ]

        for name in candidates:
            path = os.path.join(base_dir, name)
            if os.path.exists(path):
                return path

        # 如果都找不到,尝试在config里读取
        try:
            version_path = os.path.join(base_dir, "version.json")
            if os.path.exists(version_path):
                import json
                with open(version_path, "r", encoding="utf-8") as f:
                    ver_info = json.load(f)
                    exe_name = ver_info.get("exe_name")
                    if exe_name:
                        path = os.path.join(base_dir, exe_name)
                        if os.path.exists(path):
                            return path
        except:
            pass

        return None

    def start_update_check(self):
        """启动更新检查"""
        self.status_label.setText("正在检查更新...")
        self.skip_button.show()  # 显示跳过按钮

        self.update_thread = UpdateThread(self.vm)
        self.update_thread.progress.connect(self.on_progress)
        self.update_thread.finished.connect(self.on_update_finished)
        self.update_thread.start()

    def on_progress(self, message):
        """更新进度信息"""
        self.status_label.setText(message)

    def on_update_finished(self, success, had_update):
        """更新完成回调"""
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)

        if had_update and success:
            # 有更新且成功
            self.status_label.setText("更新完成,正在启动...")
        elif had_update and not success:
            # 有更新但失败
            self.status_label.setText("更新失败,使用当前版本启动...")
        else:
            # 无更新
            self.status_label.setText("正在启动...")

        # 延迟启动主程序
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(800, self.launch_main)

    def launch_main(self):
        """启动主程序"""
        if self.main_exe is None or not os.path.exists(self.main_exe):
            QMessageBox.critical(
                self,
                "启动失败",
                "找不到主程序文件!\n\n请检查安装目录是否完整。"
            )
            self.reject()
            return

        try:
            # 启动主程序(不等待)
            subprocess.Popen([self.main_exe], cwd=os.path.dirname(self.main_exe))

            # 关闭启动器
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "启动失败",
                f"无法启动主程序:\n{str(e)}"
            )
            self.reject()


class SimpleLauncher:
    """简化启动器 - 无GUI版本,适用于命令行启动"""

    def __init__(self):
        self.vm = VersionManager()

    def run(self):
        """执行启动流程"""
        print("=" * 60)
        print("ICT Monitor 启动器")
        print("=" * 60)

        # 检查更新
        print("\n[1/2] 检查更新...")
        success, had_update = self.vm.auto_update()

        if had_update:
            if success:
                print("✓ 更新完成")
            else:
                print("✗ 更新失败,将使用当前版本")
        else:
            print("✓ 当前已是最新版本")

        # 启动主程序
        print("\n[2/2] 启动主程序...")
        main_exe = self._find_main_executable()

        if main_exe is None:
            print("✗ 错误: 找不到主程序文件")
            return False

        try:
            subprocess.Popen([main_exe], cwd=os.path.dirname(main_exe))
            print("✓ 主程序已启动")
            print("\n" + "=" * 60)
            return True
        except Exception as e:
            print(f"✗ 启动失败: {e}")
            return False

    def _find_main_executable(self):
        """查找主程序可执行文件"""
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

        candidates = [
            "ICT_Monitor.exe",
            "ICT_Monitor_1.0.9.exe",
            "main.exe"
        ]

        for name in candidates:
            path = os.path.join(base_dir, name)
            if os.path.exists(path):
                return path

        # 尝试从version.json读取
        try:
            version_path = os.path.join(base_dir, "version.json")
            if os.path.exists(version_path):
                import json
                with open(version_path, "r", encoding="utf-8") as f:
                    ver_info = json.load(f)
                    exe_name = ver_info.get("exe_name")
                    if exe_name:
                        path = os.path.join(base_dir, exe_name)
                        if os.path.exists(path):
                            return path
        except:
            pass

        return None


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="ICT Monitor 启动器")
    parser.add_argument("--no-gui", action="store_true", help="使用命令行模式(无GUI)")
    args = parser.parse_args()

    if args.no_gui:
        # 命令行模式
        launcher = SimpleLauncher()
        launcher.run()
    else:
        # GUI模式
        app = QApplication(sys.argv)
        app.setFont(QFont("Microsoft YaHei", 10))

        dialog = LauncherDialog()
        dialog.exec_()

        sys.exit(0)


if __name__ == "__main__":
    main()
