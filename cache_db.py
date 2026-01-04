# -*- coding: utf-8 -*-
"""
cache_db.py
SQLite 缓存：保存解析记录(records)、已解析文件(files)、缓存版本(meta)、
以及“按批次结束日可查询”的批次窗口(batches)。

使用说明：
    cache = CacheDB("cache/cache.sqlite")
    cache.add_records(fixture, board, file, records)        # 写入记录
    cache.finalize_batch(fixture, board, start_ts, end_ts)  # 结束一批，按 end_ts 的自然日入账
    items = cache.query_counts_by_date(fix, brd, "2025-11-03")
"""

import os
import time
import sqlite3
import hashlib
from collections import defaultdict
from contextlib import closing

# 解析策略/缓存结构变更时+1，旧数据自动失效清空
CACHE_REV = "3"


def _sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()


def _to_epoch(ts_like):
    """支持 'MM-DD HH:MM:SS' / 'YYYY-MM-DD HH:MM:SS' / epoch(int/float)"""
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


class CacheDB:
    def __init__(self, db_path="cache/cache.sqlite"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # 轻量高并发优化
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA temp_store=MEMORY;")
        self._migrate()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # 在 CacheDB 类里新增：
    def topup_counts_by_date(self, fixture: str, board: str, date_str: str, target: dict):
        """
        把某天 (fixture, board) 的 part 计数补齐到 target 指定的数量。
        target 形如: {"1%c0076": 4, "2%c0076": 1, "3%b5015": 1}
        仅补缺。不减少。
        """
        import time
        fixture = (fixture or "").strip();
        board = (board or "").strip()
        date_str = (date_str or "").strip()
        if not (fixture and board and date_str and target):
            return 0

        # 取该日合并后的时间窗口（用你已实现的并区逻辑更稳）
        cur = self.conn.execute("""
            SELECT MIN(start_ts), MAX(end_ts) FROM batches
            WHERE fixture=? AND board=? AND date_for_query=?
        """, (fixture, board, date_str))
        row = cur.fetchone()
        if not row or not row[0] or not row[1]:
            return 0
        st, ed = int(row[0]), int(row[1])
        base_ts = ed  # 借用“批次结束时刻”作为占位基准

        # 先查该日现有计数
        cur2 = self.conn.execute("""
            SELECT part, COUNT(1) FROM records
            WHERE fixture=? AND board=? AND ts BETWEEN ? AND ?
            GROUP BY part
        """, (fixture, board, st, ed))
        have = {p: int(c) for p, c in cur2.fetchall()}

        # 逐项补齐
        to_add = []
        for idx, (part, want) in enumerate(target.items()):
            need = int(want) - int(have.get(part, 0))
            for k in range(max(0, need)):
                to_add.append({
                    "ts": base_ts + k + idx * 100,  # 确保唯一
                    "date": time.strftime("%Y-%m-%d", time.localtime(base_ts)),
                    "fixture": fixture, "board": board,
                    "file": f"~topup@{date_str}",
                    "part": str(part),
                    "nominal": "", "low": "",
                    "measured": f"topup#{idx}-{k}",
                    "high": "", "desc": "",
                    "hash": hashlib.sha1(
                        f"{fixture}|{board}|~topup@{date_str}|{part}|{base_ts + k + idx * 100}|topup#{idx}-{k}".encode(
                            "utf-8")
                    ).hexdigest()
                })

        if not to_add:
            return 0

        with self.conn:
            self.conn.executemany("""
                INSERT OR IGNORE INTO records
                (ts,date,fixture,board,file,part,nominal,low,measured,high,desc,hash)
                VALUES (:ts,:date,:fixture,:board,:file,:part,:nominal,:low,:measured,:high,:desc,:hash)
            """, to_add)
        return len(to_add)

    # ---------------- Schema & 版本控制 ----------------
    def _migrate(self):
        with self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              date TEXT NOT NULL,
              fixture TEXT NOT NULL,
              board TEXT NOT NULL,
              file TEXT NOT NULL,
              part TEXT NOT NULL,
              nominal TEXT, low TEXT, measured TEXT, high TEXT,
              desc TEXT,
              hash TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_records_hash ON records(hash);
            CREATE INDEX IF NOT EXISTS idx_records_time ON records(ts);
            CREATE INDEX IF NOT EXISTS idx_records_key ON records(fixture, board, part);

            CREATE TABLE IF NOT EXISTS files (
              path TEXT PRIMARY KEY,
              mtime INTEGER NOT NULL,
              parsed_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batches (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fixture TEXT NOT NULL,
              board   TEXT NOT NULL,
              start_ts INTEGER NOT NULL,
              end_ts   INTEGER NOT NULL,
              date_for_query TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_batches_fb ON batches(fixture, board, date_for_query);
            """)

            # 版本控制
            cur = self.conn.execute("SELECT v FROM meta WHERE k='CACHE_REV'")
            row = cur.fetchone()
            if (not row) or row[0] != CACHE_REV:
                self.conn.executescript("""
                    DELETE FROM records;
                    DELETE FROM files;
                    DELETE FROM batches;
                """)
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta(k,v) VALUES('CACHE_REV', ?)", (CACHE_REV,)
                )

            # ——关键：启动时先把 batches 里“同窗口重复”的行去重——
            self.conn.execute("""
                DELETE FROM batches
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM batches
                    GROUP BY fixture, board, start_ts, end_ts
                )
            """)

            # 再创建唯一索引；若仍失败，说明还有脏数据，重试前再去重一次
            try:
                self.conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_unique
                    ON batches(fixture, board, start_ts, end_ts)
                """)
            except sqlite3.IntegrityError:
                self.conn.execute("""
                    DELETE FROM batches
                    WHERE rowid NOT IN (
                        SELECT MIN(rowid)
                        FROM batches
                        GROUP BY fixture, board, start_ts, end_ts
                    )
                """)
                self.conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_unique
                    ON batches(fixture, board, start_ts, end_ts)
                """)

    # ---------------- 文件层去重 ----------------
    def mark_file_parsed(self, path: str, mtime: int):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO files(path, mtime, parsed_at) VALUES(?,?,?)",
                (path, int(mtime), int(time.time())),
            )

    def is_file_already_parsed(self, path: str, mtime: int) -> bool:
        cur = self.conn.execute("SELECT mtime FROM files WHERE path=?", (path,))
        r = cur.fetchone()
        return bool(r and int(r[0]) == int(mtime))

    # ---------------- 写入记录 ----------------
    def add_records(self, fixture: str, board: str, file: str, records: list) -> int:
        """
        写入一批 record。record 字段示例：
        {
          "time": "11-03 01:59:58" 或 epoch,
          "part": "Open: A ↔ B",
          "nominal": "...", "low": "...", "measured": "...", "high": "...",
          "desc": "..."
        }
        """
        if not records:
            return 0
        to_insert = []
        for rec in records:
            ts = self._normalize_ts(rec.get("time"))
            date = time.strftime("%Y-%m-%d", time.localtime(ts))
            part = (rec.get("part") or "").strip()
            row = {
                "ts": ts,
                "date": date,
                "fixture": (fixture or "").strip(),
                "board": (board or "").strip(),
                "file": (file or "").strip(),
                "part": part,
                "nominal": rec.get("nominal") or "",
                "low": rec.get("low") or "",
                "measured": rec.get("measured") or "",
                "high": rec.get("high") or "",
                "desc": rec.get("desc") or "",
            }
            row["hash"] = _sha1(
                f"{row['fixture']}|{row['board']}|{row['file']}|{row['part']}|{ts}|{row['measured']}"
            )
            to_insert.append(row)

        with self.conn:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO records
                (ts, date, fixture, board, file, part, nominal, low, measured, high, desc, hash)
                VALUES (:ts,:date,:fixture,:board,:file,:part,:nominal,:low,:measured,:high,:desc,:hash)
                """,
                to_insert,
            )
        return len(to_insert)

    def _normalize_ts(self, t):
        try:
            return _to_epoch(t)
        except Exception:
            return int(time.time())

    # ---------------- 看板/实时：拉取近 N 小时最新 + 历史 ----------------
    def fetch_latest_grouped(self, hours: int = 24):
        """
        返回列表 [(latest_row_dict, [history_row_dict,...]), ...]
        近 hours 小时内，按 (fixture, board, part) 分组，
        每组取最新一条为 latest，其余为 history（按 ts DESC）。
        """
        since = int(time.time()) - int(hours) * 3600
        sql_latest = """
        SELECT r1.*
        FROM records r1
        JOIN (
          SELECT fixture, board, part, MAX(ts) AS mx
          FROM records WHERE ts >= ?
          GROUP BY fixture, board, part
        ) g ON r1.fixture=g.fixture AND r1.board=g.board AND r1.part=g.part AND r1.ts=g.mx
        ORDER BY r1.ts DESC
        """
        latest = []
        cur = self.conn.execute(sql_latest, (since,))
        col = [d[0] for d in cur.description]
        for row in cur.fetchall():
            latest.append(dict(zip(col, row)))

        sql_hist = """
        SELECT * FROM records
        WHERE ts >= ? AND fixture=? AND board=? AND part=? AND ts < ?
        ORDER BY ts DESC
        """
        grouped = []
        for top in latest:
            cur2 = self.conn.execute(
                sql_hist, (since, top["fixture"], top["board"], top["part"], top["ts"])
            )
            col2 = [d[0] for d in cur2.description]
            hist = [dict(zip(col2, r)) for r in cur2.fetchall()]
            grouped.append((top, hist))
        return grouped

    # ---------------- 看板：聚合计数（可选） ----------------
    def aggregate_counts(self, fixture: str, board: str, limit: int = 30):
        cur = self.conn.execute(
            """
            SELECT part, COUNT(1) FROM records
            WHERE fixture=? AND board=?
            GROUP BY part ORDER BY COUNT(1) DESC LIMIT ?
            """,
            (fixture, board, int(limit)),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]

    # ---------------- 批次：结束 & 查询 ----------------
    def finalize_batch(self, fixture: str, board: str, start_ts: int, end_ts: int):
        """
        将“当前在测”的一批入库，查询日期按 end_ts 的自然日计入。
        不重复检查；如需防重复，可自己维护同日/同窗口的去重策略。
        """
        if not (fixture and board and end_ts):
            return
        d = time.strftime("%Y-%m-%d", time.localtime(int(end_ts)))
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO batches(fixture, board, start_ts, end_ts, date_for_query)
                VALUES(?,?,?,?,?)
                """,
                (fixture.strip(), board.strip(), int(start_ts), int(end_ts), d),
            )

    def list_fixtures(self):
        s = set()
        cur = self.conn.execute("SELECT DISTINCT fixture FROM batches")
        s.update(r[0] for r in cur.fetchall() if r[0])
        cur = self.conn.execute("SELECT DISTINCT fixture FROM records")
        s.update(r[0] for r in cur.fetchall() if r[0])
        return sorted(s)

    def list_boards(self, fixture: str):
        fixture = (fixture or "").strip()
        if not fixture:
            return []
        s = set()
        cur = self.conn.execute("SELECT DISTINCT board FROM batches WHERE fixture=?", (fixture,))
        s.update(r[0] for r in cur.fetchall() if r[0])
        cur = self.conn.execute("SELECT DISTINCT board FROM records WHERE fixture=?", (fixture,))
        s.update(r[0] for r in cur.fetchall() if r[0])
        return sorted(s)

    def query_counts_by_date(self, fixture: str, board: str, date_str: str, limit: int = 1000):
        from collections import defaultdict

        fixture = (fixture or "").strip()
        board = (board or "").strip()
        date_str = (date_str or "").strip()
        if not (fixture and board and date_str):
            return []

        # 取该日所有批次窗口
        cur = self.conn.execute("""
            SELECT start_ts, end_ts FROM batches
            WHERE fixture=? AND board=? AND date_for_query=?
            ORDER BY start_ts
        """, (fixture, board, date_str))
        windows = [(int(a), int(b)) for a, b in cur.fetchall()]

        # 并区（合并重叠/相邻窗口）
        merged = []
        for st, ed in sorted(windows, key=lambda x: x[0]):
            if not merged:
                merged.append([st, ed])
            else:
                pst, ped = merged[-1]
                if st <= ped:
                    merged[-1][1] = max(ped, ed)
                else:
                    merged.append([st, ed])

        # 1) 窗口内的所有记录
        rows = []
        for st, ed in merged:
            cur2 = self.conn.execute("""
                SELECT ts, part, file, measured FROM records
                WHERE fixture=? AND board=? AND ts BETWEEN ? AND ?
            """, (fixture, board, st, ed))
            rows += cur2.fetchall()

        # 2) 这一天(date=当天)的记录也纳入（应对少量 ts 漂移/补写占位）
        cur3 = self.conn.execute("""
            SELECT ts, part, file, measured FROM records
            WHERE fixture=? AND board=? AND date=?
        """, (fixture, board, date_str))
        rows += cur3.fetchall()

        # 3) 去重（以与 records.hash 相同的维度近似去重）
        seen = set()
        uniq = []
        for ts, part, file, measured in rows:
            key = (int(ts), str(part), str(file), str(measured))
            if key in seen:
                continue
            seen.add(key)
            uniq.append((part,))

        # 4) 聚合计数
        agg = defaultdict(int)
        for (part,) in uniq:
            agg[str(part)] += 1

        items = sorted(agg.items(), key=lambda x: (-x[1], x[0]))[: int(limit)]
        return items

    # ---------------- 清理 ----------------
    def prune_before(self, days: int = 30):
        """
        删除早于 N 天的记录；batches 会一并清理“窗口完全落在早期”的记录。
        """
        cutoff = int(time.time()) - int(days) * 86400
        with self.conn:
            self.conn.execute("DELETE FROM records WHERE ts < ?", (cutoff,))
            self.conn.execute(
                "DELETE FROM batches WHERE end_ts < ?", (cutoff,)
            )
            self.conn.execute("VACUUM;")
