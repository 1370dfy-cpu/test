#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coin Clicker Bot v3.0 - Speedy Member Edition
مخصوص کانال @speedy_member
"""

import os
import re
import json
import random
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, AuthKeyDuplicatedError
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import LeaveChannelRequest, JoinChannelRequest
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest

# ================== Logging ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("SpeedyMember")

# ================== Anti-Ban Settings ==================
MAX_OPERATIONS_PER_RUN = 30
MIN_DELAY_BETWEEN_ACTIONS = 30
MAX_DELAY_BETWEEN_ACTIONS = 90

DATA_FILE = "memberships.json"
LEAVE_AFTER_DAYS = 5

# ================== Env Variables ==================
_api_id_raw = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()

if not _api_id_raw or not API_HASH:
    raise SystemExit("❌ API_ID یا API_HASH ست نشده است")

API_ID = int(_api_id_raw)
MAIN_CHANNEL = "speedy_member"  # کانال اصلی

# ================== Account Setup ==================
ALL_ACCOUNTS = []
MAX_ACCOUNTS = 5

for i in range(1, MAX_ACCOUNTS + 1):
    session = os.getenv(f"SESSION_{i}")
    phone = os.getenv(f"PHONE_{i}")
    if not session or not phone:
        continue

    proxy_str = os.getenv(f"PROXY_{i}", "").strip()
    parsed_proxy = None
    if proxy_str:
        try:
            clean = proxy_str.replace("socks5://", "").replace("socks4://", "")
            scheme = "socks5" if "socks4" not in proxy_str else "socks4"
            if "@" in clean:
                auth, hostport = clean.split("@")
                user, password = auth.split(":", 1) if ":" in auth else (auth, "")
                host, port = hostport.split(":")
                parsed_proxy = (scheme, host, int(port), True, user, password)
            else:
                host, port = clean.split(":")
                parsed_proxy = (scheme, host, int(port), True, "", "")
        except Exception:
            log.warning(f"⚠️ فرمت پروکسی نامعتبر برای اکانت {i}")

    ALL_ACCOUNTS.append({
        "index": i,
        "phone": phone,
        "session": session,
        "proxy": parsed_proxy,
        "label": phone[-4:],
    })

if not ALL_ACCOUNTS:
    raise SystemExit("❌ هیچ اکانتی لود نشد")

# ================== Data Management ==================
class MembershipManager:
    def __init__(self, file_path=DATA_FILE):
        self.file_path = file_path
        self.data = self.load()
    
    def load(self):
        try:
            if Path(self.file_path).exists():
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            log.warning(f"⚠️ خطا در بارگذاری: {e}")
        return {"memberships": {}}
    
    def save(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"⚠️ خطا در ذخیره: {e}")
    
    def add_membership(self, account_label, channel_id, channel_title):
        if account_label not in self.data["memberships"]:
            self.data["memberships"][account_label] = []
        
        for m in self.data["memberships"][account_label]:
            if m["channel_id"] == channel_id:
                return False
        
        now = datetime.now(timezone.utc)
        self.data["memberships"][account_label].append({
            "channel_id": channel_id,
            "channel_title": channel_title,
            "joined_at": now.isoformat(),
            "leave_at": (now + timedelta(days=LEAVE_AFTER_DAYS)).isoformat()
        })
        self.save()
        return True
    
    def get_memberships_to_leave(self, account_label):
        to_leave = []
        now = datetime.now(timezone.utc)
        
        for m in self.data["memberships"].get(account_label, []):
            leave_at = datetime.fromisoformat(m["leave_at"])
            if now >= leave_at:
                to_leave.append(m)
        
        return to_leave
    
    def remove_membership(self, account_label, channel_id):
        memberships = self.data["memberships"].get(account_label, [])
        self.data["memberships"][account_label] = [
            m for m in memberships if m["channel_id"] != channel_id
        ]
        self.save()
    
    def get_all_memberships(self, account_label):
        return self.data["memberships"].get(account_label, [])

# ================== Helper Functions ==================

async def human_delay():
    delay = random.uniform(MIN_DELAY_BETWEEN_ACTIONS, MAX_DELAY_BETWEEN_ACTIONS)
    await asyncio.sleep(delay)

async def wait_flood_wait(client, error):
    wait_time = min(error.seconds, 60) + random.randint(5, 15)
    log.warning(f"⏳ FloodWait: {error.seconds}s → خواب {wait_time}s")
    await asyncio.sleep(wait_time)

def parse_join_url(url):
    url = (url or "").strip()
    if not url:
        return None, None
    
    m = re.search(r"(?:joinchat/|\+|invite=)([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1).split("?")[0], None
    
    if url.startswith("@"):
        return None, url[1:]
    
    m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})", url)
    if m:
        return None, m.group(1).split("?")[0]
    
    return None, None

def get_all_buttons(msg):
    """دریافت همه دکمه‌ها از پیام"""
    buttons = []
    msg_buttons = getattr(msg, "buttons", None)
    if msg_buttons:
        for row in msg_buttons:
            row_btns = row if isinstance(row, (list, tuple)) else getattr(row, "buttons", None)
            for btn in row_btns or []:
                buttons.append(btn)
    return buttons

def get_button_url(btn):
    """دریافت URL از دکمه - سازگار با همه نسخه‌ها"""
    for attr in ['url', 'data', 'callback_data']:
        if hasattr(btn, attr):
            value = getattr(btn, attr)
            if isinstance(value, bytes):
                try:
                    return value.decode('utf-8')
                except:
                    pass
            elif isinstance(value, str):
                return value
    return None

def find_claim_button(buttons):
    claim_keywords = ["claim", "receive", "get", "coin", "scoin", "دریافت", "سکه", "گرفتن", "دریافت سکه", "الماس"]
    for btn in buttons:
        text = getattr(btn, "text", "").lower()
        for keyword in claim_keywords:
            if keyword in text:
                return btn
    return None

def find_join_buttons(buttons):
    join_keywords = ["join", "member", "عضویت", "عضو", "ورود", "join channel", "عضو شو"]
    join_buttons = []
    for btn in buttons:
        text = getattr(btn, "text", "").lower()
        for keyword in join_keywords:
            if keyword in text:
                join_buttons.append(btn)
                break
    return join_buttons

def extract_urls_from_text(text):
    urls = []
    if not text:
        return urls
    
    pattern = r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_\-]+)'
    matches = re.findall(pattern, text)
    for m in matches:
        urls.append(f"https://t.me/{m}")
    
    return urls

async def click_button(client, entity, msg_id, btn):
    """کلیک روی دکمه با روش صحیح Telethon"""
    try:
        # روش 1: استفاده از GetBotCallbackAnswerRequest
        if hasattr(btn, 'data'):
            result = await client(GetBotCallbackAnswerRequest(
                peer=entity,
                msg_id=msg_id,
                data=btn.data
            ))
            log.info(f"  ✅ کلیک شد: {getattr(btn, 'text', '?')}")
            return True
        
        # روش 2: اگر دکمه URL داره
        btn_url = get_button_url(btn)
        if btn_url:
            log.info(f"  🔗 دکمه URL: {btn_url}")
            return True
            
        return False
    except Exception as e:
        log.warning(f"  ⚠️ خطا در کلیک: {e}")
        return False

async def join_channel(client, label, target, membership_manager):
    try:
        if isinstance(target, str) and not target.startswith("@"):
            target = f"@{target}"
        
        entity = await client.get_entity(target)
        channel_id = entity.id
        channel_title = getattr(entity, "title", str(target))
        
        for m in membership_manager.get_all_memberships(label):
            if m["channel_id"] == channel_id:
                log.info(f"  [{label}] ⏭️ قبلاً عضو شده")
                return False
        
        await human_delay()
        
        await client(JoinChannelRequest(entity))
        log.info(f"  [{label}] ✅ عضو شد: {channel_title}")
        
        membership_manager.add_membership(label, channel_id, channel_title)
        return True
        
    except FloodWaitError as e:
        await wait_flood_wait(client, e)
        return False
    except Exception as e:
        msg_str = str(e).lower()
        if "already participant" in msg_str:
            log.info(f"  [{label}] ℹ️ قبلاً عضو بوده")
        else:
            log.warning(f"  [{label}] ⚠️ خطا: {e}")
        return False

async def leave_channels(client, label, membership_manager):
    to_leave = membership_manager.get_memberships_to_leave(label)
    
    if not to_leave:
        return 0
    
    log.info(f"  [{label}] 🔄 {len(to_leave)} کانال برای خروج")
    left_count = 0
    
    for m in to_leave:
        try:
            await human_delay()
            entity = await client.get_entity(m["channel_id"])
            await client(LeaveChannelRequest(entity))
            log.info(f"  [{label}] 🔙 خارج شد: {m['channel_title']}")
            membership_manager.remove_membership(label, m["channel_id"])
            left_count += 1
        except FloodWaitError as e:
            await wait_flood_wait(client, e)
        except Exception as e:
            log.warning(f"  [{label}] ⚠️ خطا در خروج: {e}")
    
    return left_count

async def process_main_channel(client, label, membership_manager):
    try:
        ent = await client.get_entity(MAIN_CHANNEL)
        title = getattr(ent, "title", MAIN_CHANNEL)[:30]
        log.info(f"  [{label}] 📬 پردازش {title}...")
        
        msgs = await client.get_messages(ent, limit=20)
        
        for msg in msgs:
            if not msg:
                continue
            
            log.info(f"  [{label}] 🔄 پیام #{msg.id}")
            
            buttons = get_all_buttons(msg)
            
            if not buttons:
                # اگر دکمه نبود، لینک‌ها را از متن استخراج کن
                urls = extract_urls_from_text(msg.text)
                if urls:
                    log.info(f"  [{label}] 🔗 {len(urls)} لینک در متن پیدا شد")
                    for url in urls[:3]:  # حداکثر 3 لینک
                        h, u = parse_join_url(url)
                        if h or u:
                            await join_channel(client, label, u or h, membership_manager)
                            await human_delay()
                continue
            
            # عضویت در کانال‌ها
            join_btns = find_join_buttons(buttons)
            for btn in join_btns[:3]:
                btn_url = get_button_url(btn)
                if btn_url:
                    h, u = parse_join_url(btn_url)
                    if h or u:
                        await join_channel(client, label, u or h, membership_manager)
                        await human_delay()
                else:
                    # اگر دکمه callback بود، کلیک کن
                    await click_button(client, ent, msg.id, btn)
                    await human_delay()
            
            # دریافت سکه
            claim_btn = find_claim_button(buttons)
            if claim_btn:
                log.info(f"  [{label}] 🎯 دریافت سکه: {getattr(claim_btn, 'text', '?')}")
                await click_button(client, ent, msg.id, claim_btn)
                await human_delay()
            
            await human_delay()
        
        log.info(f"  [{label}] ✅ کار {title} تمام شد")
        return True
        
    except FloodWaitError as e:
        await wait_flood_wait(client, e)
        return False
    except Exception as e:
        log.error(f"  [{label}] خطا: {e}")
        return False

# ================== Core Flow ==================

async def run_account(acc, membership_manager):
    client = None
    try:
        client = TelegramClient(
            StringSession(acc["session"]),
            API_ID,
            API_HASH,
            proxy=acc["proxy"],
            connection_retries=3,
        )
        
        await client.start()
        me = await client.get_me()
        log.info(f"[{acc['label']}] ✅ {me.first_name or '?'}")
        
        op_cnt = 0
        
        left = await leave_channels(client, acc["label"], membership_manager)
        if left > 0:
            op_cnt += left
        
        if await process_main_channel(client, acc["label"], membership_manager):
            op_cnt += 1
        
        log.info(f"[{acc['label']}] 🏁 {op_cnt} عملیات")
        return True
        
    except FloodWaitError as e:
        log.error(f"[{acc['label']}] ⛔ Flood: {e.seconds}s")
        await asyncio.sleep(min(e.seconds, 120) + 10)
        return False
    except AuthKeyDuplicatedError:
        log.error(f"[{acc['label']}] ⛔ AuthKey تکراری")
        return False
    except Exception as e:
        log.error(f"[{acc['label']}] ❌ خطا: {e}")
        return False
    finally:
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass

async def main():
    log.info("=" * 60)
    log.info("🚀 Coin Clicker Bot v3.0 - Speedy Member Edition")
    log.info(f"📱 {len(ALL_ACCOUNTS)} اکانت")
    log.info(f"📡 کانال: @{MAIN_CHANNEL}")
    log.info("=" * 60)
    
    membership_manager = MembershipManager()
    random.shuffle(ALL_ACCOUNTS)
    success_count = 0
    
    for idx, acc in enumerate(ALL_ACCOUNTS):
        log.info(f"\n📌 اکانت {idx+1}/{len(ALL_ACCOUNTS)}: #{acc['index']} ({acc['label']})")
        
        if await run_account(acc, membership_manager):
            success_count += 1
        
        if idx < len(ALL_ACCOUNTS) - 1:
            sleep_time = random.randint(120, 300)
            log.info(f"⏳ خواب {sleep_time}s...")
            await asyncio.sleep(sleep_time)
    
    log.info(f"\n🏁 پایان: {success_count}/{len(ALL_ACCOUNTS)} موفق")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("⛔ متوقف شد")
    except Exception as e:
        log.error(f"💥 خطا: {e}")
