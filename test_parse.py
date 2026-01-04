# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from monitor import parse_test_file

# 测试解析报文文件
file_path = r'C:\test_data\2025\12\25\BCM25\F03H20AU62\6021000025122432252256-01.txt'
fixture = 'BCM25'
boards_root = r'C:\Agilent_ICT\boards'
board = 'F03H20AU62'
boards_root_fallback = r'C:\I3070_ICT\boards'

print("开始解析报文文件...")
records, summary = parse_test_file(file_path, fixture, boards_root, board, boards_root_fallback)

print(f'\n总记录数: {len(records)}')
print(f'Summary: {summary}')
print(f'\n详细记录:')
for i, rec in enumerate(records, 1):
    print(f"  {i}. {rec['part']:12s} - {rec['time']:15s} - Measured: {rec.get('measured', 'N/A'):15s}")

# 统计每个零件出现的次数
from collections import Counter
part_counter = Counter(r['part'] for r in records)
print(f'\n按零件统计:')
for part, count in part_counter.items():
    print(f"  {part}: {count}次")

print(f'\n对比 summary:')
for part, count in summary.items():
    print(f"  {part}: {count}次")
