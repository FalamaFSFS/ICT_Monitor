# -*- coding: utf-8 -*-
"""
ICT Monitor 主程序 - 增强异常处理版本

功能:
1. 全局异常捕获
2. 详细崩溃日志记录
3. 自动创建 crash dumps
4. 优雅退出处理
"""

import sys
import time
import traceback
import logging
from datetime import datetime
from pathlib import Path

# 配置崩溃日志
CRASH_LOG_DIR = Path(__file__).parent / "crash_logs"
CRASH_LOG_DIR.mkdir(exist_ok=True)

crash_log_file = CRASH_LOG_DIR / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(crash_log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def log_exception(exc_type, exc_value, exc_traceback):
    """全局异常处理器"""
    if issubclass(exc_type, KeyboardInterrupt):
        # 允许 Ctrl+C 退出
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.error("=" * 80)
    logger.error("程序发生未捕获异常!")
    logger.error("=" * 80)
    logger.error(f"异常类型: {exc_type.__name__}")
    logger.error(f"异常信息: {exc_value}")
    logger.error("异常堆栈:")
    logger.error("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
    logger.error("=" * 80)

    # 记录系统信息
    try:
        import platform
        logger.error("系统信息:")
        logger.error(f"  操作系统: {platform.system()} {platform.release()}")
        logger.error(f"  Python版本: {platform.python_version()}")
        logger.error(f"  时间: {datetime.now()}")
    except:
        pass


def safe_main():
    """安全启动主程序"""
    logger.info("=" * 80)
    logger.info("ICT Monitor 启动")
    logger.info(f"启动时间: {datetime.now()}")
    logger.info("=" * 80)

    try:
        # 导入主程序模块
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QFont
        import main

        # 创建应用
        app = QApplication(sys.argv)
        app.setFont(QFont("Microsoft YaHei", 10))

        # 创建主窗口
        logger.info("正在创建主窗口...")
        w = main.MainWindow()
        w.show()

        logger.info("主窗口创建成功,进入事件循环")

        # 进入事件循环
        exit_code = app.exec_()

        logger.info(f"程序正常退出,退出码: {exit_code}")
        return exit_code

    except Exception as e:
        logger.error("=" * 80)
        logger.error("程序初始化失败!")
        logger.error("=" * 80)
        logger.error(f"异常: {e}")
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    # 设置全局异常处理器
    sys.excepthook = log_exception

    try:
        exit_code = safe_main()
        sys.exit(exit_code)
    except Exception as e:
        logger.error("=" * 80)
        logger.error("程序顶层异常!")
        logger.error("=" * 80)
        logger.error(f"异常: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
