import os, json, re
from datetime import datetime
from pathlib import Path

class AlertsState:
    def __init__(self, site_id, state_path="alerts_state.json", base_data_dir=None):
        self.site_id = site_id
        self.state_path = state_path
        self.base_data_dir = base_data_dir  # 报文文件根目录，用于全盘扫描
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except:
                    return {}
        return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def scan_board_files(self, fixture, board, part):
        """
        扫描指定板型下当天的所有报文文件，统计该零件出现的总次数，并返回按时间排序的最新3条记录

        Args:
            fixture: 工装名称
            board: 板型名称
            part: 零件名称

        返回: (累计出现次数, 最新3条记录列表)
        """
        if not self.base_data_dir or not os.path.exists(self.base_data_dir):
            return 0, []

        all_records = []

        # 只扫描当天的数据
        from datetime import date
        today = date.today()

        # 构造当天的路径: 年/月/日/fixture/board
        day_path = Path(self.base_data_dir) / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}" / fixture / board

        if not day_path.exists():
            print(f"[扫描] 当天路径不存在: {day_path}")
            return 0, []

        print(f"[扫描] 扫描当天数据，零件: {part}")
        print(f"[扫描] 路径: {day_path}")

        # 修复：扫描该目录及所有子目录下的txt文件（rglob递归搜索）
        for txt_file in day_path.rglob("*.txt"):
            try:
                # 诊断日志：记录文件大小
                file_size = os.path.getsize(txt_file)
                print(f"[DEBUG] 飞书推送读取文件: {txt_file.name}, 大小: {file_size} 字节")

                with open(txt_file, "r", encoding="gbk", errors="ignore") as f:
                    content = f.read()

                print(f"[DEBUG] 飞书推送读取完成: {len(content)} 字符, 文件模式: 只读(r)")

                # 如果文件为空，记录警告
                if file_size > 0 and len(content) == 0:
                    print(f"[警告] 飞书推送: 文件大小非零但读取为空! 文件: {txt_file}")

                # 解析文件，查找该零件的失败记录
                records = self._parse_part_failures(content, part, txt_file.name)
                all_records.extend(records)

                if records:
                    print(f"[扫描] {txt_file.name}: 找到 {len(records)} 条 {part} 失败记录")
            except Exception as e:
                print(f"[扫描] 读取文件失败 {txt_file}: {e}")
                continue

        # 按时间倒序排序
        all_records.sort(key=lambda r: r["ts"], reverse=True)

        # 去重：相同时间+相同测量值的记录只保留一条
        unique_records = []
        seen = set()
        for record in all_records:
            key = (record["ts"], record["meas"])
            if key not in seen:
                seen.add(key)
                unique_records.append(record)

        # 获取最新的3条
        recent3 = unique_records[:3]

        # 修复：返回去重后的数量（保证 count 和 recent3 一致）
        return len(unique_records), recent3

    def _parse_part_failures(self, content, part, filename):
        """
        解析单个报文文件内容，提取指定零件的所有失败记录
        返回: [{"ts": "时间", "sn": "文件名", "min": "下限", "meas": "测量值", "max": "上限", "threshold": "阈值"}, ...]
        """
        from datetime import datetime
        records = []

        # 修复：不用分割符，而是按行扫描，找到每个"零件名 HAS FAILED"后提取其后的数据
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]

            # 查找"零件名 HAS FAILED"
            part_match = re.search(r'(\S+)\s+HAS FAILED', line)
            if not part_match:
                i += 1
                continue

            part_name = part_match.group(1)
            if part_name != part:
                i += 1
                continue

            # 找到目标零件，开始提取数据
            # 向前查找时间戳（在HAS FAILED之前的几行）
            timestamp = ""
            for j in range(max(0, i-25), i):
                time_match = re.search(r'(\w+\s+\w+\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4})', lines[j])
                if time_match:
                    time_str = time_match.group(1)
                    try:
                        dt = datetime.strptime(time_str, "%a %b %d %H:%M:%S %Y")
                        timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        timestamp = time_str
                    break

            # 向后查找测量值和限值（在HAS FAILED之后的几行，直到遇到分隔符或下一个HAS FAILED）
            measured_match = None
            nominal_match = None
            high_match = None
            low_match = None
            threshold_match = None

            for j in range(i+1, min(i+50, len(lines))):
                if 'HAS FAILED' in lines[j] or '####' in lines[j] or 'Serial #' in lines[j]:
                    break

                if not measured_match:
                    measured_match = re.search(r'Measured:\s+([\d.]+[a-zA-Z]*)', lines[j])
                if not nominal_match:
                    nominal_match = re.search(r'Nominal:\s+([\d.]+[a-zA-Z]*)', lines[j])
                if not high_match:
                    high_match = re.search(r'High Limit:\s+([\d.]+[a-zA-Z]*)', lines[j])
                if not low_match:
                    low_match = re.search(r'Low Limit:\s+([\d.]+[a-zA-Z]*)', lines[j])
                if not threshold_match:
                    threshold_match = re.search(r'Threshold:\s+([\d.]+[a-zA-Z]*)', lines[j], re.IGNORECASE)

            # 修复：即使没有测量值也应该记录（某些测试类型如二极管测试可能没有Measured字段）
            # 只要有时间戳或有上下限，就认为是有效的失败记录
            if timestamp or high_match or low_match:
                records.append({
                    "ts": timestamp,
                    "sn": filename,
                    "min": low_match.group(1) if low_match else "",
                    "meas": measured_match.group(1) if measured_match else "",
                    "max": high_match.group(1) if high_match else "",
                    "threshold": threshold_match.group(1) if threshold_match else ""
                })

            i += 1

        return records

    def bump_count(self, fixture, board, part, ts, sn, min_v, meas_v, max_v, threshold_v=""):
        """
        记录新的失败，并通过全盘扫描获取该零件在该板型下当天的真实累计次数和最新3条记录

        返回: (累计次数, 最新3条记录)
        """
        # 获取之前的计数
        fixture_dict = self.data.setdefault(fixture, {})
        board_dict = fixture_dict.setdefault(board, {})
        part_dict = board_dict.setdefault(part, {"count": 0, "recent": [], "message_id": None, "pushed_counts": []})
        previous_count = part_dict.get("count", 0)

        # 确保 pushed_counts 字段存在
        if "pushed_counts" not in part_dict:
            part_dict["pushed_counts"] = []

        # 执行全盘扫描（只扫描当天数据）
        total_count, recent3 = self.scan_board_files(fixture, board, part)

        # 更新状态文件（保留这个记录，方便调试）
        part_dict["count"] = total_count
        part_dict["recent"] = recent3
        part_dict["previous_count"] = previous_count  # 保存之前的计数，用于检测增量

        self._save()
        return total_count, recent3

    def get_previous_count(self, fixture, board, part):
        """获取该零件之前的计数"""
        return self.data.get(fixture, {}).get(board, {}).get(part, {}).get("previous_count", 0)

    def get_recent3(self, fixture, board, part):
        return self.data.get(fixture, {}).get(board, {}).get(part, {}).get("recent", [])

    def count_parts_over3(self, fixture, board):
        board_dict = self.data.get(fixture, {}).get(board, {})
        return sum(1 for p, v in board_dict.items() if v.get("count", 0) >= 3)

    def get_message_id(self, fixture, board, part):
        """获取该零件之前发送的飞书消息ID"""
        return self.data.get(fixture, {}).get(board, {}).get(part, {}).get("message_id")

    def set_message_id(self, fixture, board, part, message_id):
        """保存该零件发送的飞书消息ID，用于后续撤回"""
        fixture_dict = self.data.setdefault(fixture, {})
        board_dict = fixture_dict.setdefault(board, {})
        part_dict = board_dict.setdefault(part, {"count": 0, "recent": [], "message_id": None, "pushed_counts": []})
        part_dict["message_id"] = message_id
        self._save()

    def get_pushed_counts(self, fixture, board, part):
        """获取该零件已推送过的次数列表（例如：[3, 4, 5] 表示已推送过3次、4次、5次的消息）"""
        return self.data.get(fixture, {}).get(board, {}).get(part, {}).get("pushed_counts", [])

    def set_pushed_count(self, fixture, board, part, count):
        """记录该零件某次计数已推送（避免重复推送）"""
        fixture_dict = self.data.setdefault(fixture, {})
        board_dict = fixture_dict.setdefault(board, {})
        part_dict = board_dict.setdefault(part, {"count": 0, "recent": [], "message_id": None, "pushed_counts": []})
        if "pushed_counts" not in part_dict:
            part_dict["pushed_counts"] = []
        if count not in part_dict["pushed_counts"]:
            part_dict["pushed_counts"].append(count)
        self._save()

    def mark_as_pushed(self, fixture, board, part, count):
        """标记该零件某次计数已推送（set_pushed_count的别名）"""
        self.set_pushed_count(fixture, board, part, count)

