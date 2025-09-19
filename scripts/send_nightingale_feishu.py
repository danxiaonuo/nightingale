#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🍃  send_nightingale_feishu.py  (卡片版)
========================================
> Nightingale → 飞书机器人 · **交互式消息卡片**

功能：
- 同类告警聚合
- 主机与描述信息去重
- 飞书推送格式为 `互动卡片`，支持 Markdown + 按钮跳转

使用方式：
    cat payload.json | python3 send_nightingale_feishu.py
"""

import sys
import os
import json
import logging
import traceback
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from http.client import HTTPConnection
import requests

###############################################################################
# 配置项
###############################################################################
# 默认告警平台地址（用于拼接"告警详情"跳转链接）
DEFAULT_DOMAIN_URL = "https://n9e.xxx.com"

###############################################################################
# 日志配置
###############################################################################
HTTPConnection.debuglevel = 0  # 关闭底层 HTTP 调试信息

LOG_DIR = "/data/n9e/alerts"
LOG_FILE = "send_nightingale_feishu.log"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)

FMT = "%(__asctime)s %(levelname)s %(name)s:%(lineno)d: %(message)s".replace("__", "")
formatter = logging.Formatter(FMT)

file_hd = TimedRotatingFileHandler(LOG_PATH, when="midnight", interval=1, backupCount=7, encoding="utf-8")
file_hd.setFormatter(formatter)
file_hd.setLevel(logging.DEBUG)

console_hd = logging.StreamHandler(sys.stdout)
console_hd.setFormatter(formatter)
console_hd.setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(file_hd)
logger.addHandler(console_hd)

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

###############################################################################
# 工具函数
###############################################################################

def load_payload():
    """从标准输入读取 JSON 数据"""
    try:
        payload = json.load(sys.stdin)
        logger.debug("✅ 成功读取告警原始数据:\n%s", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    except Exception:
        logger.error("❌ STDIN 解析失败，请确认输入为 JSON 格式")
        logger.debug(traceback.format_exc())
        sys.exit(1)

def aggregate_events(payload):
    """
    聚合告警事件：
    - 按照 (title, group, is_recover) 进行聚合
    - 合并相同主机/主机名称/事件ID/描述/触发值，生成卡片内容
    """
    raw = payload.get("events") or [payload.get("event")]
    agg = {}

    for ev in raw:
        if not isinstance(ev, dict):
            continue

        tags = ev.get("tags_map", {})
        title = payload.get("tpl", {}).get("title") or ev.get("rule_name", "")
        group = tags.get("group") or ev.get("group_name") or "default"
        is_recover = ev.get("is_recovered", False)
        key = (title, group, is_recover)

        inst = tags.get("instance")
        host_name = tags.get("name", "")  # 新增：获取主机名称
        note = ev.get("rule_note") or ev.get("annotations", {}).get("description", "")
        trig_val = ev.get("trigger_value")
        # 恢复事件使用 last_eval_time，告警事件使用 trigger_time
        ts = ev.get("last_eval_time") if is_recover else ev.get("trigger_time")
        time_str = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
        sev = ev.get("severity")

        # 提取 event_id，用于构造详情跳转链接
        event_id = None
        if 'id' in ev:
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        # 初始化每组聚合桶
        bucket = agg.setdefault(key, {
            "hosts": set(), "host_names": set(), "event_ids": set(), "notes": set(), "values": set(),
            "severity": sev, "time": time_str,
            "event_ids": set(),
        })

        if inst:
            bucket["hosts"].add(inst)
        if host_name:
            bucket["host_names"].add(host_name)
        if event_id:
            bucket["event_ids"].add(event_id)
        if note:
            bucket["notes"].add(note)
        if trig_val:
            bucket["values"].add(str(trig_val))
        if event_id:
            bucket["event_ids"].add(event_id)

    # 整理聚合结果为列表
    results = []
    for (title, group, is_rec), data in agg.items():
        hosts = sorted(data["hosts"])
        hosts_str = "N/A" if not hosts else (hosts[0] if len(hosts) == 1 else f"{';'.join(hosts)} 共计 {len(hosts)} 台")
        
        host_names = sorted(data["host_names"])
        host_names_str = "" if not host_names else ";".join(host_names)
        
        event_ids = sorted(data["event_ids"])
        event_ids_str = ";".join(event_ids) if event_ids else ""
        
        res = {
            "title": title,
            "group": group,
            "hosts": hosts_str,
            "host_names": host_names_str,
            "event_ids": event_ids_str,
            "notes": "; ".join(sorted(data["notes"])) or "无",
            "trigger_value": "; ".join(sorted(data["values"])) or "N/A",
            "severity": data["severity"],
            "is_recover": is_rec,
            "time": data["time"],
            "event_id": next(iter(data["event_ids"]), None),  # 只取一个用于详情链接
        }
        results.append(res)
    return results

###############################################################################
# 飞书卡片组装
###############################################################################

GREEN = "green"   # 表示恢复
RED = "red"       # 表示告警

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def mk_markdown_body(item: dict) -> str:
    """生成 Markdown 内容块，用于卡片展示"""
    if item["is_recover"]:
        lines = [
            f"**告警级别:** S{item['severity']} 恢复",
            f"**告警名称:** {item['title']}",
            f"**业务分组:** {item['group']}",
            f"**主机地址:** {item['hosts']}",
        ]
        # 只有当主机名称不为空时才显示
        if item['host_names']:
            lines.append(f"**主机名称:** {item['host_names']}")
        # 只有当事件ID不为空时才显示
        if item['event_ids']:
            lines.append(f"**事件ID:** {item['event_ids']}")
        lines.extend([
            f"**恢复时间:** {item['time']}",
            f"**发送时间:** {now_str()}",
            f"**告警描述:** {item['notes']}",
        ])
    else:
        lines = [
            f"**告警级别:** S{item['severity']} 告警",
            f"**告警名称:** {item['title']}",
            f"**业务分组:** {item['group']}",
            f"**主机地址:** {item['hosts']}",
        ]
        # 只有当主机名称不为空时才显示
        if item['host_names']:
            lines.append(f"**主机名称:** {item['host_names']}")
        # 只有当事件ID不为空时才显示
        if item['event_ids']:
            lines.append(f"**事件ID:** {item['event_ids']}")
        lines.extend([
            f"**触发时值:** {item['trigger_value']}",
            f"**触发时间:** {item['time']}",
            f"**发送时间:** {now_str()}",
            f"**告警描述:** {item['notes']}",
        ])
    return "\n".join(lines)

def build_card(item: dict) -> dict:
    """生成飞书互动卡片的完整 payload"""
    status_flag = "﹝恢复﹞" if item["is_recover"] else "﹝告警﹞"
    title_text = f"信息化监控告警 🔥 {status_flag} {item['group']}- {item['title']}"
    template_color = GREEN if item["is_recover"] else RED

    # 生成"告警详情"跳转链接
    detail_url = f"{DEFAULT_DOMAIN_URL}/alert-his-events/{item['event_id']}" if item.get("event_id") else ""

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title_text[:80]},
            "template": template_color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": mk_markdown_body(item)},
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "告警详情"},
                        "url": detail_url,
                        "type": "primary",
                    }
                ],
            },
        ],
    }
    return {"msg_type": "interactive", "card": card}

###############################################################################
# 飞书发送函数
###############################################################################

def send_feishu(message: dict, token: str):
    """通过 webhook token 向飞书推送一条卡片消息"""
    if not token:
        logger.warning("⚠️ access_token 缺失，跳过发送")
        return
    url = f"https://open.xfchat.xxx.com/open-apis/bot/v2/hook/{token}"
    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=message, timeout=8)
        logger.info("推送: token=%s status=%s", token, resp.status_code)

        try:
            resp_data = resp.json()
        except Exception:
            logger.warning("⚠️ 响应不是 JSON 格式: %s", resp.text)
            return

        if resp_data.get("code") != 0:
            logger.error("❌ 飞书接口返回错误 code=%s msg=%s", resp_data.get("code"), resp_data.get("msg"))
        else:
            logger.debug("📩 响应内容: %s", resp.text)

    except Exception as e:
        logger.error("发送失败: %s", e)
        logger.debug(traceback.format_exc())

###############################################################################
# 主流程入口
###############################################################################

def run():
    """主执行流程：读取 → 聚合 → 构造 → 发送"""
    payload = load_payload()
    items = aggregate_events(payload)

    params = payload.get("params", {})
    tokens = []
    if params.get("access_token"):
        tokens.append(params["access_token"])
    tokens += params.get("access_tokens", [])
    tokens += payload.get("sendtos", [])
    if not tokens:
        logger.warning("⚠️ 未找到任何 access_token，终止推送")
        return

    for it in items:
        msg = build_card(it)
        for tk in tokens:
            send_feishu(msg, tk)

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error("程序异常: %s", e)
        logger.debug(traceback.format_exc())
        sys.exit(1)
