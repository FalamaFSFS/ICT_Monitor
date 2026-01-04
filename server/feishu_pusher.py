import os
import json
import time
import glob
import hashlib
import urllib.request

# ===== 路径配置 =====
# 数据根目录:固定为 D:\tool\ICT_DATA (服务器本机路径)
DATA_ROOT = r"D:\tool\ICT_DATA"

# 配置文件:与本脚本同目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(SCRIPT_DIR, "config.json")

# 数据目录
INBOX = os.path.join(DATA_ROOT, "inbox")
SENTRY = os.path.join(DATA_ROOT, "sentry")
LOGS = os.path.join(DATA_ROOT, "logs")

# 确保目录存在
os.makedirs(SENTRY, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)

# ===== 内网飞书网关地址 =====
FEISHU_GATEWAY = "http://10.174.205.76:9080/feishu/chatsend"


def load_conf():
    """读取飞书配置:模板ID / 群ID / 轮询间隔"""
    if not os.path.exists(CONF):
        print(f"[错误] 配置文件不存在: {CONF}")
        print("请创建 config.json 文件,内容如下:")
        print(json.dumps({
            "template_id": "AAqhdJTHofPHY",
            "receiveId": "oc_2384410c9cfdcdfd46a2298d39a53389",
            "poll_interval_sec": 10
        }, indent=2, ensure_ascii=False))
        raise FileNotFoundError(CONF)

    with open(CONF, "r", encoding="utf-8") as f:
        return json.load(f)


def mark_sent(fp: str):
    """标记文件已推送,避免重复"""
    key = hashlib.md5(fp.encode("utf-8")).hexdigest()
    with open(os.path.join(SENTRY, key + ".ok"), "w") as f:
        f.write("")


def is_sent(fp: str) -> bool:
    """检查文件是否已推送"""
    key = hashlib.md5(fp.encode("utf-8")).hexdigest()
    return os.path.exists(os.path.join(SENTRY, key + ".ok"))


def build_template_vars(payload: dict) -> dict:
    """
    将机台上报的payload转换为飞书卡片模板变量

    输入payload格式(ict-alert-v1):
    {
      "site_id": "ICT1",
      "fixture": "UA008",
      "board": "F01R20DM1J",
      "part": "1%c0076",
      "count": 3,
      "over3_count": 5,
      "recent3": [
        {"ts": "11-28 10:38:21", "sn": "xxx.txt", "min": "...", "meas": "...", "max": "...", "threshold": "..."},
        ...
      ],
      "old_message_id": "...",
      "ts_emit": "2025-11-28 10:38:21"
    }

    输出模板变量:
    {
      "site_id": "ICT1",
      "fixture": "UA008",
      "board": "F01R20DM1J",
      "current_part": "1%c0076",
      "alarm_count": 3,
      "total_count": 5,
      "update_time": "11-28 10:38:21",
      "r1_ts": "...", "r1_sn": "...", "r1_part": "...", ...
      "r2_ts": "...", ...
      "r3_ts": "...", ...
    }
    """
    site_id = payload.get("site_id", "")
    fixture = payload.get("fixture", "")
    board = payload.get("board", "")
    part = payload.get("part", "")

    # 当前零件累计失败次数
    alarm_count = int(payload.get("count", 0))

    # 超过阈值的零件种类数
    total_count = int(payload.get("over3_count", 0))

    # 更新时间
    update_time = time.strftime("%m-%d %H:%M:%S")

    # 最近3条记录
    recent = payload.get("recent3") or []

    # 填充到至少3条,避免模板引用报错
    while len(recent) < 3:
        recent.append({})

    def pick(idx, key):
        """提取记录字段"""
        val = str(recent[idx].get(key, "") or "")

        # 时间字段:去掉年份
        if key == "ts" and val:
            # "2025-11-28 16:10:17" -> "11-28 16:10:17"
            parts = val.split(" ")
            if len(parts) == 2:
                date_part = parts[0]
                time_part = parts[1]
                date_components = date_part.split("-")
                if len(date_components) == 3:
                    val = f"{date_components[1]}-{date_components[2]} {time_part}"

        return val

    # 构造模板变量
    vars_dict = {
        "site_id": site_id,
        "fixture": fixture,
        "board": board,
        "alarm_count": alarm_count,
        "total_count": total_count,
        "update_time": update_time,
        "current_part": part,

        # 第一条记录
        "r1_ts": pick(0, "ts"),
        "r1_sn": pick(0, "sn"),
        "r1_part": part,
        "r1_min": pick(0, "min"),
        "r1_meas": pick(0, "meas"),
        "r1_max": pick(0, "max"),
        "r1_threshold": pick(0, "threshold"),

        # 第二条记录
        "r2_ts": pick(1, "ts"),
        "r2_sn": pick(1, "sn"),
        "r2_part": part,
        "r2_min": pick(1, "min"),
        "r2_meas": pick(1, "meas"),
        "r2_max": pick(1, "max"),
        "r2_threshold": pick(1, "threshold"),

        # 第三条记录
        "r3_ts": pick(2, "ts"),
        "r3_sn": pick(2, "sn"),
        "r3_part": part,
        "r3_min": pick(2, "min"),
        "r3_meas": pick(2, "meas"),
        "r3_max": pick(2, "max"),
        "r3_threshold": pick(2, "threshold"),
    }

    return vars_dict


def push_feishu_template(template_id: str, template_vars: dict, receive_id: str) -> str:
    """
    通过内网飞书网关发送模板消息

    POST http://10.174.205.76:9080/feishu/chatsend
    Body: {
      "template_id": "...",
      "template_variable": {...},
      "receiveId": "oc_..."
    }
    """
    body = {
        "template_id": template_id,
        "template_variable": template_vars,
        "receiveId": receive_id,
    }

    data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        FEISHU_GATEWAY,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8")


def main():
    """主循环:扫描inbox → 推送飞书 → 标记已推送"""
    print("=" * 60)
    print("飞书推送服务启动")
    print("=" * 60)
    print(f"数据目录: {DATA_ROOT}")
    print(f"配置文件: {CONF}")
    print(f"飞书网关: {FEISHU_GATEWAY}")
    print("=" * 60)

    try:
        conf = load_conf()
    except Exception as e:
        print(f"[错误] 加载配置失败: {e}")
        return

    template_id = conf["template_id"]
    receive_id = conf["receiveId"]
    interval = conf.get("poll_interval_sec", 10)

    print(f"模板ID: {template_id}")
    print(f"接收ID: {receive_id}")
    print(f"轮询间隔: {interval}秒")
    print("=" * 60)
    print("开始监控...")
    print()

    while True:
        try:
            today = time.strftime("%Y-%m-%d")
            pattern = os.path.join(INBOX, today, "*", "*.json")

            # 扫描今天的所有报警JSON
            files = glob.glob(pattern)

            for fp in files:
                if is_sent(fp):
                    continue

                try:
                    # 读取payload
                    with open(fp, "r", encoding="utf-8") as f:
                        payload = json.load(f)

                    # 构造模板变量
                    template_vars = build_template_vars(payload)

                    # 推送飞书
                    resp = push_feishu_template(template_id, template_vars, receive_id)

                    # 标记已推送
                    mark_sent(fp)

                    # 记录日志
                    log_msg = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 推送成功: {os.path.basename(fp)}"
                    print(log_msg)

                    with open(os.path.join(LOGS, "pusher.log"), "a", encoding="utf-8") as lf:
                        lf.write(log_msg + "\n")

                except Exception as e:
                    # 记录错误
                    err_msg = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 推送失败: {os.path.basename(fp)} | {e}"
                    print(err_msg)

                    with open(os.path.join(LOGS, "error.log"), "a", encoding="utf-8") as ef:
                        ef.write(err_msg + "\n")

        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 扫描出错: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n飞书推送服务已停止")
    except Exception as e:
        print(f"\n[致命错误] {e}")
        import traceback
        traceback.print_exc()
