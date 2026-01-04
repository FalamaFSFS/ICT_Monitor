# -*- coding: utf-8 -*-
"""
版本管理模块 - 自动更新功能
支持从网络共享路径自动同步最新版本
"""
import os
import sys
import json
import shutil
import time
from datetime import datetime

def get_app_dir() -> str:
    # PyInstaller 打包后
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # 源码运行
    return os.path.dirname(os.path.abspath(__file__))


class VersionManager:
    """版本管理器 - 负责检查和同步最新版本"""

    def __init__(self, server_path=None, local_dir=None):
        """
        初始化版本管理器

        Args:
            server_path: 服务器端路径(网络共享),默认从agent.json读取
            local_dir: 本地安装目录,默认为程序所在目录
        """
        self.local_dir = local_dir or get_app_dir()

        # 从agent.json读取服务器路径配置
        if server_path is None:
            try:
                config_path = os.path.join(self.local_dir, "config", "agent.json")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        conf = json.load(f)
                        # 使用SHARE_PATH的父目录作为更新服务器路径
                        share_path = conf.get("SHARE_PATH", "")
                        if share_path:
                            # \\10.174.204.202\tool\ICT_DATA\inbox
                            # → \\10.174.204.202\tool\ICT_Monitor\client
                            parts = share_path.rstrip("\\/").split("\\")
                            if len(parts) >= 4:
                                server_path = "\\\\".join(parts[:4]) + "\\ICT_Monitor\\client"
            except Exception as e:
                print(f"[版本管理] 读取配置失败: {e}")

        self.server_path = server_path or r"\\10.174.204.202\tool\ICT_Monitor\client"
        self.version_file = "version.json"

    def get_local_version(self):
        """获取本地版本信息"""
        version_path = os.path.join(self.local_dir, self.version_file)
        if not os.path.exists(version_path):
            return {"version": "0.0.0", "build": 0}

        try:
            with open(version_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[版本管理] 读取本地版本失败: {e}")
            return {"version": "0.0.0", "build": 0}

    def get_server_version(self):
        """获取服务器端最新版本信息"""
        version_path = os.path.join(self.server_path, self.version_file)

        if not os.path.exists(version_path):
            print(f"[版本管理] 服务器版本文件不存在: {version_path}")
            return None

        try:
            with open(version_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[版本管理] 读取服务器版本失败: {e}")
            return None

    def check_update(self):
        """
        检查是否有新版本

        Returns:
            tuple: (需要更新, 服务器版本信息)
        """
        local_ver = self.get_local_version()
        server_ver = self.get_server_version()

        if server_ver is None:
            print("[版本管理] 无法获取服务器版本信息,跳过更新检查")
            return False, None

        local_build = int(local_ver.get("build", 0))
        server_build = int(server_ver.get("build", 0))

        if server_build > local_build:
            print(f"[版本管理] 发现新版本: {server_ver.get('version')} (build {server_build})")
            return True, server_ver
        else:
            print(f"[版本管理] 当前已是最新版本: {local_ver.get('version')} (build {local_build})")
            return False, server_ver

    def sync_update(self, server_ver=None):
        """
        从服务器同步最新版本

        Args:
            server_ver: 服务器版本信息,如果为None则重新获取

        Returns:
            bool: 是否成功
        """
        if server_ver is None:
            server_ver = self.get_server_version()
            if server_ver is None:
                return False

        print(f"[版本管理] 开始同步版本 {server_ver.get('version')}...")

        # 获取需要同步的文件列表
        files_to_sync = server_ver.get("files", [])
        if not files_to_sync:
            print("[版本管理] 警告: 服务器版本信息中没有指定文件列表")
            # 默认同步主程序
            files_to_sync = [server_ver.get("exe_name", "ICT_Monitor.exe")]

        # 创建备份目录
        backup_dir = os.path.join(self.local_dir, "_backup_" + time.strftime("%Y%m%d_%H%M%S"))

        success = True
        synced_files = []

        try:
            for file_path in files_to_sync:
                server_file = os.path.join(self.server_path, file_path)
                local_file = os.path.join(self.local_dir, file_path)

                if not os.path.exists(server_file):
                    print(f"[版本管理] 警告: 服务器文件不存在: {server_file}")
                    continue

                # 如果本地文件存在,先备份
                if os.path.exists(local_file):
                    os.makedirs(backup_dir, exist_ok=True)
                    backup_file = os.path.join(backup_dir, file_path)
                    os.makedirs(os.path.dirname(backup_file), exist_ok=True)
                    try:
                        shutil.copy2(local_file, backup_file)
                        print(f"[版本管理] 已备份: {file_path}")
                    except Exception as e:
                        print(f"[版本管理] 备份失败: {file_path}, {e}")

                # 同步文件
                try:
                    os.makedirs(os.path.dirname(local_file), exist_ok=True)

                    # 对于exe文件,使用安全复制(先复制到临时文件,再替换)
                    if file_path.lower().endswith('.exe'):
                        temp_file = local_file + ".tmp"
                        shutil.copy2(server_file, temp_file)

                        # 如果目标文件存在且正在运行,标记需要重启
                        if os.path.exists(local_file):
                            try:
                                os.remove(local_file)
                            except PermissionError:
                                # 文件正在使用,下次启动时替换
                                print(f"[版本管理] {file_path} 正在使用,将在下次启动时更新")
                                success = False
                                continue

                        os.rename(temp_file, local_file)
                    else:
                        shutil.copy2(server_file, local_file)

                    synced_files.append(file_path)
                    print(f"[版本管理] 已同步: {file_path}")

                except Exception as e:
                    print(f"[版本管理] 同步失败: {file_path}, {e}")
                    success = False

            # 更新本地version.json
            if synced_files:
                try:
                    version_path = os.path.join(self.local_dir, self.version_file)
                    with open(version_path, "w", encoding="utf-8") as f:
                        json.dump(server_ver, f, ensure_ascii=False, indent=2)
                    print(f"[版本管理] 版本信息已更新: {server_ver.get('version')}")
                except Exception as e:
                    print(f"[版本管理] 更新版本信息失败: {e}")

            if success and synced_files:
                print(f"[版本管理] 更新成功! 共同步 {len(synced_files)} 个文件")
                print(f"[版本管理] 备份目录: {backup_dir}")
            elif not synced_files:
                print("[版本管理] 没有需要同步的文件")
                # 删除空备份目录
                if os.path.exists(backup_dir) and not os.listdir(backup_dir):
                    os.rmdir(backup_dir)

            return success

        except Exception as e:
            print(f"[版本管理] 同步过程出错: {e}")
            return False

    def auto_update(self):
        """
        自动检查并更新到最新版本

        Returns:
            tuple: (是否更新成功, 是否有更新)
        """
        need_update, server_ver = self.check_update()

        if not need_update:
            return True, False

        success = self.sync_update(server_ver)
        return success, True


def test_version_manager():
    """测试版本管理器"""
    print("=" * 60)
    print("版本管理器测试")
    print("=" * 60)

    vm = VersionManager()

    print(f"\n本地目录: {vm.local_dir}")
    print(f"服务器路径: {vm.server_path}")

    local_ver = vm.get_local_version()
    print(f"\n本地版本: {local_ver}")

    server_ver = vm.get_server_version()
    print(f"服务器版本: {server_ver}")

    need_update, _ = vm.check_update()
    print(f"\n需要更新: {need_update}")

    if need_update:
        choice = input("\n是否立即更新? (y/n): ")
        if choice.lower() == 'y':
            success, updated = vm.auto_update()
            if success and updated:
                print("\n✓ 更新完成!")
            elif not success:
                print("\n✗ 更新失败!")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_version_manager()
