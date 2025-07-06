#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import sys, os, json, requests, logging, re, traceback
from datetime import datetime
from http.client import HTTPConnection
from logging.handlers import TimedRotatingFileHandler
from typing import List, Dict, Any

# 🚧 开发环境开启 HTTP 调试，生产建议关闭
HTTPConnection.debuglevel = 1

# 日志配置：滚动日志，保留一周
log_dir = "/data/n9e/alerts"
os.makedirs(log_dir, exist_ok=True)
fh = TimedRotatingFileHandler(os.path.join(log_dir, "send_nightingale_im.log"),
                              when='midnight', backupCount=7, encoding='utf-8')
fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
fh.setFormatter(fmt); fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt); ch.setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh); logger.addHandler(ch)
logging.getLogger("requests").setLevel(logging.WARNING)
urllib3_log = logging.getLogger("urllib3")
urllib3_log.setLevel(logging.DEBUG); urllib3_log.propagate = True

# 默认告警详情地址
DEFAULT_DOMAIN_URL = "https://n9e.xxx.com"

def timeformat(ts: int) -> str:
    """将时间戳转换为可读字符串"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def get_access_token(app_id: str, app_secret: str) -> str:
    """获取飞书 access token"""
    url = "https://open.xfchat.xxx.com/open-apis/auth/v3/tenant_access_token/internal"
    logger.debug("请求 Token：%s", {"app_id": app_id})
    try:
        r = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
        data = r.json()
        logger.debug("Token 响应：%s", data)
        if r.status_code == 200 and data.get("msg") == "ok":
            return data["tenant_access_token"]
        logger.error("获取 Token 失败：%s", data)
    except Exception:
        logger.error("获取 Token 异常", exc_info=True)
    return ""

def load_payload():
    """从标准输入读取 JSON 数据"""
    try:
        payload = json.load(sys.stdin)
        logger.debug("✅ 成功读取告警原始数据: %s", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    except Exception:
        logger.error("❌ STDIN 解析失败，请确认输入为 JSON 格式")
        logger.debug(traceback.format_exc())
        sys.exit(1)

def aggregate_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    聚合事件数据，输出统一结构：
    包括 title, group, hosts, host_names, event_ids, notes, time, severity, is_recover, event_id（如存在）
    """
    raw = payload.get('events') or [payload.get('event')]
    agg = {}
    for ev in raw:
        if not isinstance(ev, dict):
            logger.warning("跳过非字典事件: %r", ev)
            continue

        tags = ev.get('tags_map', {})
        title = payload.get('tpl', {}).get('title') or ev.get('rule_name', '')
        group = tags.get('group') or ev.get('group_name', '')
        is_recover = ev.get('is_recovered', False)
        key = (title, group, is_recover)

        inst = tags.get('instance', '')
        host_name = tags.get('name', '')  # 新增：获取主机名称
        note = ev.get('rule_note') or ev.get('annotations', {}).get('description', '')
        ts = ev.get('trigger_time') or ev.get('last_eval_time', 0)
        tstr = timeformat(int(ts)) if ts else ''
        sev = ev.get('severity', 0)

        # 从 ev 中获取 event_id，如果有字段 'id'
        event_id = None
        if 'id' in ev:
            # 支持数字或列表形式
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        agg.setdefault(key, {
            "hosts": [], "host_names": [], "event_ids": [], "notes": [], "title": title,
            "group": group,
            "time": tstr, "severity": sev, "is_recover": is_recover,
            "event_id": event_id
        })
        if inst:
            agg[key]["hosts"].append(inst)
        if host_name:
            agg[key]["host_names"].append(host_name)
        if event_id:
            agg[key]["event_ids"].append(event_id)
        if note:
            agg[key]["notes"].append(note)

    results = []
    for v in agg.values():
        hosts = sorted(set(v["hosts"]))
        host_names = sorted(set(v["host_names"]))
        event_ids = sorted(set(v["event_ids"]))
        notes = sorted(set(v["notes"]))
        
        # 处理主机地址显示格式
        if not hosts:
            hosts_str = "N/A"
        elif len(hosts) == 1:
            hosts_str = hosts[0]
        else:
            hosts_str = f"{';'.join(hosts)} 共计 {len(hosts)} 台"
        
        # 处理主机名称显示格式
        if not host_names:
            host_names_str = ""
        else:
            host_names_str = ";".join(host_names)
        
        # 处理事件ID显示格式
        event_ids_str = ";".join(event_ids) if event_ids else ""
        
        results.append({
            "title": v["title"],
            "group": v["group"],
            "hosts": hosts_str,
            "host_names": host_names_str,
            "event_ids": event_ids_str,
            "notes": notes[0] if len(notes) == 1 else "; ".join(notes),
            "time": v["time"],
            "severity": v["severity"],
            "is_recover": v["is_recover"],
            "event_id": v.get("event_id")
        })
    return results

def build_markdown(item: Dict[str, Any]) -> str:
    """根据事件信息构建 Markdown 文本"""
    is_recover = item["is_recover"]
    lines = []
    lines.append(f"**告警级别:** S{item['severity']} {'恢复' if is_recover else '告警'}  ")
    if item["group"]:
        lines.append(f"**业务分组:** {item['group']}  ")
    if item["hosts"]:
        lines.append(f"**主机地址:** {item['hosts']}  ")
    if item["host_names"]:
        lines.append(f"**主机名称:** {item['host_names']}  ")
    if item["event_ids"]:
        lines.append(f"**事件ID:** {item['event_ids']}  ")
    if item["time"]:
        lines.append(f"**{'恢复时间' if is_recover else '触发时间'}:** {item['time']}  ")
    lines.append(f"**发送时间:** {timeformat(int(datetime.now().timestamp()))}  ")
    if item["notes"]:
        lines.append(f"**告警描述:** {item['notes']}  ")
    return "\n".join(lines)

def send_cards(app_id: str, app_secret: str, sendtos: List[str],
               items: List[Dict[str, Any]], domain_url: str):
    """发送飞书卡片，自动附带'告警详情'按钮"""

    token = get_access_token(app_id, app_secret)
    if not token:
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for item in items:
        is_recover = item["is_recover"]
        status_flag = "﹝恢复﹞" if is_recover else "﹝告警﹞"
        header_text = f"监控告警 🔥 {status_flag} {item['group']}- {item['title']}"
        tpl = "green" if is_recover else "red"
        md = build_markdown(item)

        # 优先使用传入的 event_id 字段
        event_id = item.get("event_id")
        if event_id:
            logger.debug("使用 event_id：%s", event_id)
        else:
            notes = item.get("notes", "")
            # 回退：从 note 中正则提取
            match = re.search(r'alert-his-events/([^\s;]+)', notes)
            if match:
                event_id = match.group(1)
                logger.debug("从 notes 提取到 event_id：%s", event_id)

        elements = [{"tag": "markdown", "content": md}]
        if event_id:
            event_url = f"{domain_url}/alert-his-events/{event_id}"
            logger.debug("告警详情按钮 URL：%s", event_url)
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"content": "告警详情", "tag": "plain_text"},
                        "type": "primary",
                        "url": event_url
                    }
                ]
            })

        content = {
            "header": {"title": {"tag": "plain_text", "content": header_text}, "template": tpl},
            "elements": elements
        }
        body = {"msg_type": "interactive", "receive_id": "", "content": json.dumps(content, ensure_ascii=False)}

        for to in sendtos:
            rid = (
                "email" if "@" in to else
                "chat_id" if to.startswith("oc_") else
                "open_id" if to.startswith("ou_") else
                "union_id" if to.startswith("on_") else
                "user_id"
            )
            body["receive_id"] = to
            resp = requests.post(
                "https://open.xfchat.xxx.com/open-apis/im/v1/messages",
                headers=headers,
                params={"receive_id_type": rid},
                json=body,
                timeout=15
            )
            logger.info("发送给 %s 类型=%s, 状态=%s", to, rid, resp.status_code)

def main():
    """主入口：读取 stdin，处理 payload，调用 send_cards"""

    logger.info("脚本启动")
    payload = load_payload()
    items = aggregate_events(payload)
    if not items:
        logger.info("未发现告警事件，退出")
        return

    cfg = payload.get("params", {})
    sendtos = payload.get("sendtos", [])
    domain_url = cfg.get("domain_url", DEFAULT_DOMAIN_URL)

    send_cards(cfg.get("feishuapp_id", ""), cfg.get("feishuapp_secret", ""),
               sendtos, items, domain_url)
    logger.info("脚本结束")

if __name__ == "__main__":
    main()
