#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_nightingale_email.py
────────────────────────────────────────
• 按 (title, group, cluster, is_recover) 聚合；每组单独发信
• 邮件加入「随机渐变 + CSS 动效」背景，呈现高端动感视觉
"""

import sys
import os
import json
import html
import logging
import traceback
import smtplib
import random          # 用于随机生成渐变色
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from email.mime.text import MIMEText
from email.header import Header

# ─────────────────────────────────────────────
# 全局可配
# ─────────────────────────────────────────────
DEBUG_MODE = False                         # True 时把邮件写入 /tmp 方便预览
LOG_DIR = os.getenv("LOG_DIR", "/data/n9e/alerts")

EMAIL_HOST = ""
EMAIL_PORT = 465
EMAIL_USER = ""
EMAIL_PASS = os.getenv("MAIL_PASS", "")        # 必须: export MAIL_PASS="邮箱密码"
EMAIL_FROM = EMAIL_USER

# ───────── 日志 ─────────
os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, "send_nightingale_email.log")
_fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

fh = TimedRotatingFileHandler(log_path, when='midnight', interval=1,
                              backupCount=7, encoding='utf-8')
fh.setFormatter(_fmt)
fh.setLevel(logging.DEBUG)

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(_fmt)
ch.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh)
logger.addHandler(ch)

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def load_payload() -> dict:
    """读取 stdin 并解析 JSON"""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        logger.debug("✅ 成功读取告警原始数据:\n%s", json.dumps(data, ensure_ascii=False, indent=2))
        return data
    except Exception:
        logger.error("❌ 解析 WebHook payload 失败")
        logger.debug(traceback.format_exc())
        sys.exit(1)


def _to_bool(val) -> bool:
    """将 0/1/True/False/'true'/'1' 等转成布尔"""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "y")
    return False


def aggregate_events(payload: dict) -> list[dict]:
    """
    根据 (title, group, is_recover) 聚合
    返回排序后的列表
    """
    raw_events = payload.get("events") or [payload.get("event")]
    agg: dict[tuple, dict] = {}

    for ev in raw_events:
        if not isinstance(ev, dict):
            logger.warning("⚠️ 非 dict 事件已跳过: %r", ev)
            continue

        tags = ev.get("tags_map") or {}
        title   = payload.get("tpl", {}).get("title") or ev.get("rule_name", "")
        group   = tags.get("group")  or ev.get("group_name") or "default"
        is_recover = _to_bool(ev.get("is_recovered"))

        key = (title, group, is_recover)

        sev_raw = ev.get("severity", 99)
        try:
            severity = int(sev_raw)
        except Exception:
            severity = 99

        ts = ev.get("trigger_time") or ev.get("last_eval_time")
        time_str = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"

        host = tags.get("instance", "N/A")
        host_name = tags.get("name", "")  # 新增：获取主机名称
        note_src = ev.get("rule_note") or ev.get("annotations", {}).get("description", "")
        note_html = html.escape(note_src).replace("\n", "<br>")

        # 获取事件ID
        event_id = None
        if 'id' in ev:
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        agg.setdefault(key, {"severity": severity, "time": time_str, "items": []})
        agg[key]["items"].append({"host": host, "host_name": host_name, "note": note_html, "event_id": event_id})

    # 转为列表并排序（告警→恢复，再按严重级别、时间）
    res = [{
        "title": k[0], "group": k[1], "is_recover": k[2],
        "severity": v["severity"], "time": v["time"], "items": v["items"]
    } for k, v in agg.items()]

    res.sort(key=lambda x: (x["is_recover"], x["severity"], x["time"]))
    logger.debug("✅ 聚合后:\n%s", json.dumps(res, ensure_ascii=False, indent=2))
    return res


# ─────────────────────────────────────────────
# 视觉生成：随机渐变 + 动画
# ─────────────────────────────────────────────
def gen_random_gradient(num_colors: int = None) -> tuple[str, str]:
    """
    生成彩虹色+天空色混合渐变 CSS 片段（每次随机生成10~16个不重复的柔和明亮色彩）
    返回 (linear-gradient字符串, 降级纯色)
    """
    import random
    # 每次随机生成10~16个颜色
    if num_colors is None:
        num_colors = random.randint(10, 16)
    
    def random_rainbow_sky_hex(existing_colors=None):
        if existing_colors is None:
            existing_colors = set()
        tries = 0
        while True:
            # 随机选择颜色类型：天空色(60%) 或 彩虹色(40%)
            if random.random() < 0.6:
                # 天空色：浅蓝、浅青、浅紫
                r = random.randint(180, 240)
                g = random.randint(200, 255)
                b = random.randint(220, 255)
            else:
                # 彩虹色：柔和的红、橙、黄、绿、蓝、紫
                color_type = random.choice(['red', 'orange', 'yellow', 'green', 'blue', 'purple'])
                if color_type == 'red':
                    r, g, b = random.randint(240, 255), random.randint(180, 220), random.randint(180, 220)
                elif color_type == 'orange':
                    r, g, b = random.randint(240, 255), random.randint(200, 240), random.randint(160, 200)
                elif color_type == 'yellow':
                    r, g, b = random.randint(240, 255), random.randint(240, 255), random.randint(180, 220)
                elif color_type == 'green':
                    r, g, b = random.randint(180, 220), random.randint(240, 255), random.randint(180, 220)
                elif color_type == 'blue':
                    r, g, b = random.randint(160, 200), random.randint(200, 240), random.randint(240, 255)
                elif color_type == 'purple':
                    r, g, b = random.randint(200, 240), random.randint(180, 220), random.randint(240, 255)
            
            color = f"#{r:02x}{g:02x}{b:02x}"
            if color not in existing_colors:
                return color
            tries += 1
            if tries > 20:
                return color
    
    colors = set()
    while len(colors) < num_colors:
        colors.add(random_rainbow_sky_hex(colors))
    colors = list(colors)
    angle = random.randint(0, 360)
    gradient_css = f"linear-gradient({angle}deg, {', '.join(colors)})"
    fallback = colors[0]
    return gradient_css, fallback


def build_email(one: dict, recipients: list[str]) -> MIMEText:
    """
    构建带随机动画背景的邮件。
    - 主机地址单行显示，用分号分隔
    - 主机名称单行显示，用分号分隔（如果存在）
    - 事件ID单行显示，用分号分隔（如果存在）
    - 告警描述用 <ul> 展示，自动去重
    """
    status_flag = "﹝恢复﹞" if one["is_recover"] else "﹝告警﹞"
    subject = f"监控告警 🔥 {status_flag} {one['group']}- {one['title']}"
    send_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 去重并拼接主机地址为一行（用分号隔开）
    hosts = sorted(set(h["host"] for h in one["items"] if h.get("host")))
    if not hosts:
        host_line = "N/A"
    elif len(hosts) == 1:
        host_line = hosts[0]
    else:
        host_line = f"{';'.join(hosts)} 共计 {len(hosts)} 台"

    # 去重并拼接主机名称为一行（用分号隔开），过滤空值
    host_names = sorted(set(h["host_name"] for h in one["items"] if h.get("host_name")))
    host_name_line = ";".join(host_names) if host_names else ""

    # 去重并拼接事件ID为一行（用分号隔开），过滤空值
    event_ids = sorted(set(h["event_id"] for h in one["items"] if h.get("event_id")))
    event_id_line = ";".join(event_ids) if event_ids else ""

    # 告警描述去重，按 <li> 展示
    notes = sorted(set(h["note"] for h in one["items"] if h.get("note")))

    # 渐变背景
    gradient_css, fallback_color = gen_random_gradient()

    # 构造 HTML 邮件内容（网页端渐变动画+自适应，客户端降级为纯色）
    html_mail = f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"UTF-8\"><title>信息化监控告警</title>
<style>
 body {{
   margin:0;padding:0;
   background:{gradient_css};
   background-size:400% 400%;
   animation:gradientMove 15s ease infinite;
   -webkit-font-smoothing:antialiased;
 }}
 @keyframes gradientMove {{
   0%   {{background-position:0% 50%;}}
   50%  {{background-position:100% 50%;}}
   100% {{background-position:0% 50%;}}
 }}
</style>
<!--[if mso]>
<style>
body, .wrapper {{ background:{fallback_color} !important; }}
</style>
<![endif]-->
<style>
 .wrapper {{ padding:40px 0;width:100%;box-sizing:border-box; }}
 .main {{
   width:100%;max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;
   box-shadow:0 6px 18px rgba(0,0,0,.08);
   padding:28px 34px;font-size:14px;color:#333;
   font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;
 }}
 .row {{ margin:10px 0; }}
 code {{ background:#f2f4f8;padding:2px 4px;border-radius:4px;font-size:13px; }}
 ul {{ margin:4px 0 12px 18px;padding-left:0; }}
</style>
</head>
<body>
  <div class=\"wrapper\">
    <div class=\"main\">
      <div class=\"row\"><strong>告警级别:</strong> S{one['severity']} {"恢复" if one['is_recover'] else "告警"}</div>
      <div class=\"row\"><strong>告警名称:</strong> {one['title']}</div>
      <div class=\"row\"><strong>业务分组:</strong> {one['group']}</div>
      <div class=\"row\"><strong>主机地址:</strong> <code>{host_line}</code></div>"""
    if host_name_line:
        html_mail += f"""
      <div class=\"row\"><strong>主机名称:</strong> <code>{host_name_line}</code></div>"""
    if event_id_line:
        html_mail += f"""
      <div class=\"row\"><strong>事件ID:</strong> <code>{event_id_line}</code></div>"""
    html_mail += f"""
      <div class=\"row\"><strong>{'恢复时间' if one['is_recover'] else '触发时间'}:</strong> {one['time']}</div>
      <div class=\"row\"><strong>发送时间:</strong> {send_time}</div>
      <div class=\"row\"><strong>告警描述:</strong></div>
      <ul>
        {''.join(f'<li>{n}</li>' for n in notes)}
      </ul>
    </div>
  </div>
</body>
</html>"""

    # 调试模式保存 HTML 到本地
    if DEBUG_MODE:
        fname = f"/tmp/{'recover' if one['is_recover'] else 'alert'}_{one['title']}.html"
        with open(fname, "w", encoding="utf-8") as fp:
            fp.write(html_mail)
        logger.info("📝 本地预览文件: %s", fname)

    msg = MIMEText(html_mail, "html", "utf-8")
    msg["From"] = EMAIL_FROM
    msg["To"] = ",".join(recipients)
    msg["Subject"] = Header(subject, "utf-8")
    return msg


def send_email(msg: MIMEText, recipients: list[str]) -> None:
    """SMTP 发送"""
    if not EMAIL_PASS:
        logger.error("❌ MAIL_PASS 未配置，无法发送邮件")
        return
    try:
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, timeout=10) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.sendmail(EMAIL_FROM, recipients, msg.as_string())
        logger.info("✅ 邮件发送 → %s", recipients)
    except Exception as e:
        logger.error("❌ 邮件发送失败: %s", e)
        logger.debug(traceback.format_exc())


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main() -> None:
    payload = load_payload()
    items   = aggregate_events(payload)

    recipients = [x for x in (payload.get("sendtos") or []) if isinstance(x, str) and "@" in x]
    if not recipients:
        logger.warning("⚠️ 无有效收件人，终止发送")
        return
    logger.debug("📧 收件人: %s", recipients)

    # 先发告警，再发恢复
    for want_recover in (False, True):
        for item in items:
            if item["is_recover"] == want_recover:
                send_email(build_email(item, recipients), recipients)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("❌ 脚本异常: %s", exc)
        logger.debug(traceback.format_exc())
        sys.exit(1)
