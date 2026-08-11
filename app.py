#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 适配 bot-hosting.net 新面板 (/a/billings) 自动续期
# 基于 eooce/Auto-Renew-Bothosting 优化

import os, re, sys, time, json, requests, subprocess
import urllib.parse
from datetime import datetime
from seleniumbase import SB

# ==================== 环境变量 ====================
EMAIL         = os.environ.get("EMAIL") or ""
SESSION_TOKEN = os.environ.get("SESSION_TOKEN") or ""
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""
GH_TOKEN      = os.environ.get("GH_TOKEN") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

DC_TOKEN = ""
if DISCORD_TOKEN:
    _parts = DISCORD_TOKEN.split(",", 1)
    DC_TOKEN = _parts[-1].strip()

if not SESSION_TOKEN and not DC_TOKEN:
    print("ℹ️ 未配置 SESSION_TOKEN 和 DISCORD_TOKEN，脚本终止。")
    sys.exit(1)

COOKIES = {
    "session_token": SESSION_TOKEN,
    "login": "true",
    "theme": "system",
}

_LOGIN_METHOD = "SESSION_TOKEN"

# ==================== 工具函数 ====================
def get_cookie_info(sb, name):
    for c in sb.get_cookies():
        if c.get('name') == name:
            value = c.get('value')
            expiry_ts = c.get('expiry')
            expiry_dt = datetime.fromtimestamp(expiry_ts) if expiry_ts else None
            return value, expiry_dt
    return None, None

def should_update_cookie(new_value, old_value, expiry_dt, days_threshold=3):
    if new_value is None:
        return False
    if new_value != old_value:
        return True
    if expiry_dt:
        remaining = (expiry_dt - datetime.now()).total_seconds()
        if remaining < days_threshold * 24 * 3600:
            return True
    return False

def update_github_secret(secret_name, new_value):
    if not new_value:
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    print(f"🔄 更新 Secret: {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=30, check=False, env=env
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"❌ 更新异常: {e}")
        return False

def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message},
            timeout=10
        )
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        masked = f"{name[:2]}****{name[-2:]}@{domain}" if len(name) > 4 else f"{name}@{domain}"
    else:
        masked = (EMAIL[:2] + '****') if EMAIL else "未知"

    lines = [
        "🇫🇮 Bot-hosting 新面板续期通知",
        "",
        status,
        f"👤 账户: {masked}",
    ]
    if _LOGIN_METHOD != "SESSION_TOKEN":
        lines.append(f"🔐 登录方式: {_LOGIN_METHOD}")
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误: {error}")
    lines.append(f"⏱️ 时间: {now}")
    return "\n".join(lines)

def wait_for_turnstile_pass(sb, timeout=30):
    start = time.time()
    indicators = ["verify you are human", "确认您是真人", "troubleshoot", "just a moment"]
    while time.time() - start < timeout:
        if not any(x in sb.get_page_source().lower() for x in indicators):
            print("✅ Turnstile 已通过")
            return True
        sb.sleep(1)
    print("❌ Turnstile 超时")
    return False

def get_current_ip(proxy_server: str = "") -> str:
    proxies = {"http": proxy_server, "https": proxy_server} if proxy_server else None
    return requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15).text.strip()

def format_countdown(s: str) -> str:
    try:
        h, m, _ = s.split(':')
        h, m = int(h), int(m)
        return f"{h}h{m}min" if h > 0 else f"{m}min"
    except:
        return s

def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",
        r"[Ee]xpires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew",
        r"(\d{2}/\d{2}/\d{4})\s*[\-–]\s*renew",
        r"(\d{4}-\d{2}-\d{2})",
    ]
    for p in patterns:
        m = re.search(p, page_source)
        if m:
            d = m.group(1)
            if len(d.split('/')[-1]) == 4 and len(d.split('/')[0]) == 2:
                a, b, c = d.split('/')
                return f"{c}/{a}/{b}"
            return d
    return None

# ==================== Discord OAuth ====================
DISCORD_CLIENT_ID  = "884382422530158623"
OAUTH_REDIRECT_URI = "https://bot-hosting.net/login"
OAUTH_SCOPE        = "identify email guilds"
DISCORD_API        = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")
STATE_RE = re.compile(r"[?&]state=([^&]+)")

def capture_discord_state(sb) -> str:
    print("🔎 获取 Discord state...")
    sb.uc_open_with_reconnect("https://bot-hosting.net/login/discord", reconnect_time=4)
    time.sleep(2)
    url = sb.get_current_url()
    if "discord.com" not in url:
        return ""
    m = STATE_RE.search(url)
    return urllib.parse.unquote(m.group(1)) if m else ""

def discord_authorize(state: str) -> str:
    query = urllib.parse.urlencode({
        "client_id": DISCORD_CLIENT_ID, "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI, "scope": OAUTH_SCOPE, "state": state,
    })
    headers = {
        "accept": "*/*", "authorization": DC_TOKEN, "content-type": "application/json",
        "origin": "https://discord.com", "user-agent": DISCORD_UA, "x-discord-locale": "zh-CN",
        "referer": f"https://discord.com/oauth2/authorize?{query}",
    }
    body = json.dumps({
        "permissions": "0", "authorize": True, "integration_type": 0,
        "location_context": {"guild_id": "10000", "channel_id": "10000", "channel_type": 10000},
    })
    proxies = None
    if os.environ.get("IS_PROXY", "false").lower() == "true":
        p = os.environ.get("PROXY_SERVER", "http://127.0.0.1:1080")
        proxies = {"http": p, "https": p}
    try:
        r = requests.post(f"{DISCORD_API}?{query}", headers=headers, data=body, proxies=proxies, timeout=20)
        if r.status_code == 200:
            return r.json().get("location", "")
    except Exception as e:
        print(f"❌ Discord 授权异常: {e}")
    return ""

def do_discord_login(sb) -> bool:
    print("\n🔑 Discord Token 登录...")
    state = capture_discord_state(sb)
    if not state:
        return False
    location = discord_authorize(state)
    if not location:
        return False
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    time.sleep(3)
    for _ in range(30):
        url = sb.get_current_url()
        path = urllib.parse.urlparse(url).path
        if "bot-hosting.net" in url and path != "/login" and not path.startswith("/login/discord"):
            print(f"✅ Discord 登录成功: {url}")
            return True
        time.sleep(0.5)
    return False

# ==================== 主流程 ====================
def main():
    print("#" * 32)
    print("   Bot-hosting 新面板自动续期")
    print("#" * 32)

    IS_PROXY     = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS     = os.environ.get("HEADLESS", "false").lower() == "true"

    sb_kwargs = {"uc": True, "headless": HEADLESS}
    if IS_PROXY:
        print(f"🔗 代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 直连")

    global _LOGIN_METHOD

    with SB(**sb_kwargs) as sb:
        try:
            print(f"📍 出口IP: {get_current_ip(PROXY_SERVER if IS_PROXY else '')}")
        except Exception as e:
            print(f"⚠️ 获取IP失败: {e}")

        login_ok = False

        # Cookie 登录
        if SESSION_TOKEN:
            print("🚀 注入 Cookie 登录...")
            sb.open("https://bot-hosting.net/")
            sb.wait_for_ready_state_complete()
            sb.sleep(2)
            for name, value in COOKIES.items():
                if value:
                    sb.add_cookie({"name": name, "value": value, "domain": "bot-hosting.net"})

            sb.open("https://bot-hosting.net/a/billings")
            sb.wait_for_ready_state_complete()
            sb.sleep(3)
            cur = sb.get_current_url()
            print(f"📝 当前URL: {cur}")
            if "/a/billings" in cur and "/login" not in cur and "error=" not in cur:
                login_ok = True
                print("✅ SESSION_TOKEN 登录成功")
            else:
                print("❌ SESSION_TOKEN 登录失败")

        # Discord 备用登录
        if not login_ok and DC_TOKEN:
            _LOGIN_METHOD = "Discord Token"
            print("\n🔄 尝试 Discord OAuth...")
            if do_discord_login(sb):
                sb.open("https://bot-hosting.net/a/billings")
                sb.wait_for_ready_state_complete()
                sb.sleep(3)
                if "/a/billings" in sb.get_current_url():
                    login_ok = True
                    print("✅ Discord 登录成功")

        if not login_ok:
            send_telegram_message(format_notification("❌ 登录失败", error="Cookie 与 Discord 均失败"))
            return

        # 提取到期时间
        sb.sleep(2)
        page = sb.get_page_source()
        current_expiry = extract_expiry_date(page)
        print(f"📅 当前到期: {current_expiry or '未获取到'}")

        # 查找续期按钮 / 倒计时
        outer_selector = None
        countdown = None
        selectors = [
            'button:contains("Renew")',
            'button:contains("Renew free")',
            'a:contains("Renew")',
            '[class*="renew"]',
            '[class*="Renew"]',
        ]

        for sel in selectors:
            try:
                if sb.is_element_visible(sel):
                    txt = sb.get_text(sel)
                    print(f"找到元素: {txt}")
                    if "Renew in" in txt:
                        m = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", txt)
                        if m:
                            countdown = m.group(1)
                        break
                    elif "Renew" in txt and "in" not in txt.lower():
                        outer_selector = sel
                        print(f"✅ 可点击续期按钮: '{txt}'")
                        break
            except:
                pass

        if outer_selector:
            print("🔄 点击外部续期按钮...")
            try:
                sb.click(outer_selector)
                sb.sleep(12)          # 等待弹窗 + Turnstile 加载
            except Exception as e:
                send_telegram_message(format_notification("❌ 续期失败", error=f"点击外部按钮: {e}"))
                return

            # 处理 Turnstile
            print("🔒 处理 Turnstile 验证...")
            passed = False
            for i in range(1, 4):
                try:
                    sb.uc_gui_click_captcha()
                    time.sleep(10)
                except Exception as e:
                    print(f"⚠️ 点击验证码异常: {e}")
                if wait_for_turnstile_pass(sb, timeout=18):
                    passed = True
                    break
                print(f"⏳ 第 {i} 次未通过，重试...")

            if not passed:
                send_telegram_message(format_notification("❌ 续期失败", error="Turnstile 未通过"))
                return

            # 点击确认按钮（兼容多种文字）
            print("⏳ 寻找并点击确认按钮...")
            time.sleep(3)
            confirm_selectors = [
                'button:contains("Renew for 4 days")',
                'button:contains("Renew for")',
                'button:contains("Confirm")',
                'button:contains("Yes")',
                'button:contains("Extend")',
                'button:contains("Continue")',
                'button:contains("续期")',
            ]
            clicked = False
            for sel in confirm_selectors:
                try:
                    if sb.is_element_visible(sel):
                        sb.click(sel, timeout=6)
                        print(f"✅ 已点击确认按钮: {sel}")
                        clicked = True
                        break
                except:
                    pass

            if not clicked:
                print("⚠️ 未找到确认按钮，可能已自动完成或文字变化")

            # 验证结果
            sb.sleep(6)
            new_page = sb.get_page_source()
            new_expiry = extract_expiry_date(new_page)
            new_m = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", new_page)

            if new_m:
                print(f"✅ 续期成功！新倒计时: {new_m.group(1)}")
                send_telegram_message(format_notification(
                    "✅ 续期成功",
                    extra=f"⏱️ 下次可续: {format_countdown(new_m.group(1))}后",
                    expiry_date=new_expiry or current_expiry
                ))
            elif new_expiry and new_expiry != current_expiry:
                print(f"✅ 续期成功，到期日期已更新: {new_expiry}")
                send_telegram_message(format_notification(
                    "✅ 续期成功",
                    expiry_date=new_expiry
                ))
            else:
                print("⚠️ 续期结果未知，请手动检查")
                send_telegram_message(format_notification(
                    "⚠️ 续期结果未知",
                    extra="请登录后台确认",
                    expiry_date=current_expiry
                ))

        else:
            if countdown:
                friendly = format_countdown(countdown)
                print(f"⏳ 未到续期时间: {countdown} ({friendly})")
                send_telegram_message(format_notification(
                    "⏳ 未到续期时间",
                    extra=f"⏱️ 可续期: {friendly}后",
                    expiry_date=current_expiry
                ))
            else:
                print("ℹ️ 未找到续期按钮或倒计时")
                send_telegram_message(format_notification(
                    "ℹ️ 状态未知",
                    extra="请手动检查 /a/billings",
                    expiry_date=current_expiry
                ))

        # 自动更新 SESSION_TOKEN
        print("🔄 检查 SESSION_TOKEN...")
        new_token, token_exp = get_cookie_info(sb, "session_token")
        if should_update_cookie(new_token, SESSION_TOKEN, token_exp):
            if GH_TOKEN:
                if update_github_secret("SESSION_TOKEN", new_token):
                    print("✅ SESSION_TOKEN 已自动更新")
                else:
                    print("⚠️ 更新失败，检查 GH_TOKEN 权限")
            else:
                print(f"⚠️ 未设置 GH_TOKEN，请手动更新 SESSION_TOKEN = {new_token[:6]}...")
        else:
            print("✅ SESSION_TOKEN 无需更新")

        print("🏁 脚本执行完毕")

if __name__ == "__main__":
    main()
