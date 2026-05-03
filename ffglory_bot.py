"""
Free Fire Guild Glory Telegram Bot
Works on: Local Python 3.10-3.13, Termux, and Google Colab

LOCAL / TERMUX SETUP:
    pip install "python-telegram-bot==21.10" aiohttp
    python ffglory_bot.py

COLAB SETUP:
    Just run this file — dependencies are auto-installed.
"""

# ─── Environment Detection & Auto-Install ────────────────────────────────────
import sys
import subprocess

def _is_colab():
    try:
        import google.colab  # noqa: F401
        return True
    except ImportError:
        return False

def _is_notebook():
    try:
        get_ipython  # noqa: F821
        return True
    except NameError:
        return False

IN_NOTEBOOK = _is_notebook()

# Auto-install only when running in Colab/Jupyter
if IN_NOTEBOOK:
    def _install(pkg):
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])
    _install("python-telegram-bot==21.10")
    _install("aiohttp")
    _install("nest_asyncio")

# Apply nest_asyncio only in notebook environments (Colab/Jupyter)
# — not needed and potentially harmful in plain Python scripts
if IN_NOTEBOOK:
    import nest_asyncio
    nest_asyncio.apply()

import asyncio
import aiohttp
import sqlite3
import logging
import os
import json
import time
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ─── Configuration ───────────────────────────────────────────────────────────
BOT_TOKEN   = "8427579991:AAEQySFbk8rbXdBP3rscWQBuFyYvUW-CQjI"
ADMIN_ID    = 5851349028
PRICE_INR   = 80
GLORY_AMT   = 200_000
UPI_ID      = "ffglory@okhdfcbank"
API_BASE    = "https://jubayer-guild-info.vercel.app/info"
DB_PATH     = "ffglory.db"
QR_PATH_KEY = "qr_path"
DEFAULT_QR  = "default_qr.png"

# ─── Conversation States ──────────────────────────────────────────────────────
WAITING_UID, WAITING_REGION, WAITING_CONFIRMATION, WAITING_UTR, WAITING_UTR_CONFIRM = range(5)

# ─── Region Mapping ──────────────────────────────────────────────────────────
REGION_MAP = {
    "region_india":     ("India",     "ind"),
    "region_indonesia": ("Indonesia", "id"),
    "region_bangladesh":("Bangladesh","bd"),
    "region_pakistan":  ("Pakistan",  "pk"),
    "region_other":     ("Other",     "other"),
}

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Database ─────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            guild_uid  TEXT,
            guild_name TEXT,
            guild_level TEXT,
            guild_xp   TEXT,
            region     TEXT,
            utr        TEXT,
            status     TEXT DEFAULT 'pending',
            timestamp  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Insert default QR path if not set
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (QR_PATH_KEY, DEFAULT_QR))
    conn.commit()
    conn.close()

def db_conn():
    return sqlite3.connect(DB_PATH)

def save_order(user_id, username, guild_uid, guild_name, guild_level, guild_xp, region, utr):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO orders (user_id, username, guild_uid, guild_name, guild_level, guild_xp, region, utr, status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (user_id, username, guild_uid, guild_name, guild_level, guild_xp, region, utr,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def utr_exists(utr: str) -> bool:
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM orders WHERE utr=? LIMIT 1", (utr,))
        return c.fetchone() is not None

def get_user_orders(user_id):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, guild_name, glory_amt, status, timestamp FROM orders WHERE user_id=? ORDER BY id DESC",
                  (user_id,))
        # glory_amt column may not exist – safe fallback
        rows = c.fetchall()
    return rows

def get_user_orders_safe(user_id):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, guild_name, guild_level, region, utr, status, timestamp
            FROM orders WHERE user_id=? ORDER BY id DESC
        """, (user_id,))
        return c.fetchall()

def get_pending_orders():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, user_id, username, guild_name, guild_level, region, utr, timestamp
            FROM orders WHERE status='pending' ORDER BY id DESC
        """)
        return c.fetchall()

def approve_order(order_id):
    with db_conn() as conn:
        conn.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
    return True

def get_stats():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM orders")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
        pending = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM orders WHERE status='approved'")
        approved = c.fetchone()[0]
        revenue = approved * PRICE_INR
    return total, pending, approved, revenue

def get_config(key):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM config WHERE key=?", (key,))
        row = c.fetchone()
    return row[0] if row else None

def set_config(key, value):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))

# ─── Unicode Bold Text Helper ─────────────────────────────────────────────────
_BOLD_MAP = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
    "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"
    "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
)
def ub(text: str) -> str:
    return str(text).translate(_BOLD_MAP)

# ─── UPI Deep Link ────────────────────────────────────────────────────────────
def upi_link(amount: int, note: str = "FF+Glory+200K") -> str:
    return (
        f"upi://pay?pa={UPI_ID}"
        f"&pn=FF+Glory+Shop"
        f"&am={amount}"
        f"&cu=INR"
        f"&tn={note}"
    )

# ─── API Helper ───────────────────────────────────────────────────────────────
async def fetch_guild_info(
    clan_id: str,
    region: str,
    retries: int = 5,
    on_attempt=None,
) -> dict | None:
    url = f"{API_BASE}?clan_id={clan_id}&region={region}"
    for attempt in range(retries):
        if on_attempt:
            try:
                await on_attempt(attempt + 1, retries)
            except Exception:
                pass
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        return data
                    else:
                        logger.warning(f"API attempt {attempt+1}: HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"API attempt {attempt+1} failed: {e}")
        if attempt < retries - 1:
            await asyncio.sleep(2)
    return None

# ─── Keyboards ────────────────────────────────────────────────────────────────
def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🔥 BUY GLORY NOW 🔥", callback_data="buy_glory")],
        [InlineKeyboardButton("📦 My Orders", callback_data="my_orders"),
         InlineKeyboardButton("💳 How to Pay", callback_data="how_to_pay")],
        [InlineKeyboardButton("📖 Commands", callback_data="commands")],
        [InlineKeyboardButton("🆘 Support", callback_data="support")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel 🔐", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def region_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 India",     callback_data="region_india"),
         InlineKeyboardButton("🇮🇩 Indonesia", callback_data="region_indonesia")],
        [InlineKeyboardButton("🇧🇩 Bangladesh",callback_data="region_bangladesh"),
         InlineKeyboardButton("🇵🇰 Pakistan",  callback_data="region_pakistan")],
        [InlineKeyboardButton("🌍 Other",      callback_data="region_other")],
        [InlineKeyboardButton("❌ Cancel",     callback_data="cancel_buy")],
    ])

def confirmation_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Pay", callback_data="confirm_yes"),
         InlineKeyboardButton("❌ Cancel",        callback_data="confirm_no")],
        [InlineKeyboardButton("🔄 Change Region", callback_data="change_region"),
         InlineKeyboardButton("✏️ Manual Entry",  callback_data="manual_entry")],
    ])

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Pending Orders", callback_data="view_pending")],
        [InlineKeyboardButton("✅ Approve Order",  callback_data="approve_menu"),
         InlineKeyboardButton("📊 Statistics",     callback_data="stats")],
        [InlineKeyboardButton("🖼️ Change QR Code", callback_data="change_qr")],
        [InlineKeyboardButton("🏠 Main Menu",      callback_data="main_menu")],
    ])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])

# ─── Loading Animation ────────────────────────────────────────────────────────
async def loading_animation(message):
    steps = [
        (20, "🔍 Checking Guild ID..."),
        (40, "🌐 Connecting to Server..."),
        (60, "📡 Fetching Guild Data..."),
        (80, "📊 Processing Information..."),
        (100, "✅ Complete!"),
    ]
    msg = await message.reply_text("⏳ Please wait...\n\n░░░░░░░░░░ 0%")
    for pct, label in steps:
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        await msg.edit_text(f"⏳ {label}\n\n{bar} {pct}%")
        await asyncio.sleep(0.15)
    return msg

# ─── Handlers: /start ─────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_admin = (user.id == ADMIN_ID)
    text = (
        f"Welcome, {user.first_name}!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"FREE FIRE GUILD GLORY SHOP\n\n"
        f"Boost your Guild instantly with\n"
        f"{ub('200,000 Guild Glory')}\n\n"
        f"╔══════════════════════╗\n"
        f"║  💎 {ub('Rs.80')} ➜ {ub('2,00,000 Glory')}  ║\n"
        f"╚══════════════════════╝\n\n"
        f"✅ Fast Delivery — instantly\n"
        f"✅ Safe & Trusted — 100% legit service\n"
        f"✅ Easy Payment — QR / UPI\n"
        f"✅ 24/7 Support — always here to help\n\n"
        f"Tap a button to get started!"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_kb(is_admin))
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text, reply_markup=main_menu_kb(is_admin))

# ─── Handlers: /cancel ────────────────────────────────────────────────────────
async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    user = update.effective_user
    is_admin = (user.id == ADMIN_ID)
    await update.message.reply_text("❌ Purchase cancelled.", reply_markup=main_menu_kb(is_admin))
    return ConversationHandler.END

# ─── Handlers: /approve ───────────────────────────────────────────────────────
async def approve_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorised.")
        return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /approve <order_id>")
        return
    order_id = int(args[0])
    approve_order(order_id)
    await update.message.reply_text(f"✅ Order #{order_id} approved!", reply_markup=admin_kb())

# ─── Buy Flow: Start ──────────────────────────────────────────────────────────
async def buy_glory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"🛒 *PURCHASE GUILD GLORY*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *Step 1 of 4* — Enter Guild ID\n\n"
        f"Please send your *Free Fire Guild ID*\n"
        f"_(numbers only, found in guild info)_\n\n"
        f"📌 *Example:* `3046059051`\n\n"
        f"💡 _Tip: Open Free Fire → Guild → Share to find your Guild ID_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel Purchase", callback_data="cancel_buy")
        ]]),
    )
    return WAITING_UID

async def cancel_buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    user = update.effective_user
    await query.edit_message_text(
        f"❌ *Purchase Cancelled*\n\n"
        f"No worries! Tap *🔥 BUY GLORY NOW* anytime to start again.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(user.id == ADMIN_ID),
    )
    return ConversationHandler.END

# ─── Buy Flow: Guild ID ───────────────────────────────────────────────────────
async def receive_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "⚠️ Invalid Guild ID. Please enter *numbers only*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_UID
    ctx.user_data["guild_uid"] = text
    await update.message.reply_text(
        f"✅ *Guild ID Accepted\\!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *Step 2 of 4* — Select Region\n\n"
        f"🆔 ID: `{text}`\n\n"
        f"🌍 Now choose your *Free Fire server region:*",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=region_kb(),
    )
    return WAITING_REGION

# ─── Buy Flow: Region ─────────────────────────────────────────────────────────
async def receive_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    region_key = query.data
    if region_key not in REGION_MAP:
        await query.answer("Unknown region.", show_alert=True)
        return WAITING_REGION

    region_label, region_code = REGION_MAP[region_key]
    ctx.user_data["region_label"] = region_label
    ctx.user_data["region_code"] = region_code

    guild_uid = ctx.user_data.get("guild_uid", "")

    # Live loading message — updates as each retry attempt happens
    loading_msg = await query.message.reply_text(
        "⏳ *Starting guild lookup...*\n\n░░░░░░░░░░ 0%",
        parse_mode=ParseMode.MARKDOWN,
    )

    _RETRY_LABELS = [
        "🔍 Checking Guild ID...",
        "🌐 Connecting to server...",
        "📡 Fetching guild data...",
        "🔄 Retrying — please wait...",
        "⚡ Last attempt — almost there...",
    ]

    async def on_attempt(attempt: int, total: int):
        pct   = int((attempt - 1) / total * 90)
        filled = pct // 10
        bar   = "█" * filled + "░" * (10 - filled)
        label = _RETRY_LABELS[min(attempt - 1, len(_RETRY_LABELS) - 1)]
        try:
            await loading_msg.edit_text(
                f"⏳ *{label}*\n"
                f"Attempt *{attempt}/{total}*\n\n"
                f"{bar} {pct}%",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    # Fetch from API with live progress updates
    data = await fetch_guild_info(guild_uid, region_code, retries=5, on_attempt=on_attempt)

    # Final animation step
    try:
        if data:
            await loading_msg.edit_text("✅ *Guild found!*\n\n██████████ 100%",
                                        parse_mode=ParseMode.MARKDOWN)
        else:
            await loading_msg.edit_text("❌ *Could not fetch guild.*\n\n██████████",
                                        parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await asyncio.sleep(0.4)

    if data:
        guild_name  = data.get("clan_name",   "Unknown")
        guild_level = str(data.get("level",   "?"))
        guild_xp    = str(data.get("xp",      "?"))
        created_at  = data.get("created_at",  "N/A")
        last_active = data.get("last_active",  "N/A")

        ctx.user_data.update({
            "guild_name":  guild_name,
            "guild_level": guild_level,
            "guild_xp":    guild_xp,
        })

        info_text = (
            f"🏰 *GUILD VERIFIED* ✅\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 *Step 3 of 4* — Confirm Details\n\n"
            f"┌─────────────────────────\n"
            f"│ 🏯 Guild: *{guild_name}*\n"
            f"│ ⭐ Level: *{guild_level}*\n"
            f"│ 💠 XP: *{guild_xp}*\n"
            f"│ 🌍 Region: *{region_label}*\n"
            f"│ 📅 Created: *{created_at}*\n"
            f"│ 🕐 Last Active: *{last_active}*\n"
            f"└─────────────────────────\n\n"
            f"🧾 *ORDER SUMMARY*\n"
            f"╔══════════════════════╗\n"
            f"║  🔰 {ub('200,000 Guild Glory')}  ║\n"
            f"║  💰 Price: {ub('Rs.80')} only     ║\n"
            f"╚══════════════════════╝\n\n"
            f"⚠️ _Is this your guild? Confirm to proceed to payment._"
        )
        try:
            await loading_msg.edit_text(info_text, parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=confirmation_kb())
        except Exception:
            await query.message.reply_text(info_text, parse_mode=ParseMode.MARKDOWN,
                                           reply_markup=confirmation_kb())
        return WAITING_CONFIRMATION

    else:
        # API failed
        try:
            await loading_msg.edit_text(
                "⚠️ *Could not fetch guild info from API.*\n\n"
                "The server may be temporarily down. Choose an option below:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Manual Entry",   callback_data="manual_entry")],
                    [InlineKeyboardButton("🔄 Try Another Region", callback_data="change_region")],
                    [InlineKeyboardButton("❌ Cancel",         callback_data="cancel_buy")],
                ]),
            )
        except Exception:
            await query.message.reply_text(
                "⚠️ *Could not fetch guild info.*\n\nChoose an option:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Manual Entry",   callback_data="manual_entry")],
                    [InlineKeyboardButton("🔄 Try Another Region", callback_data="change_region")],
                    [InlineKeyboardButton("❌ Cancel",         callback_data="cancel_buy")],
                ]),
            )
        return WAITING_CONFIRMATION

# ─── Buy Flow: Change Region ──────────────────────────────────────────────────
async def change_region_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"🔄 *Change Region*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *Step 2 of 4* — Select Region\n\n"
        f"🌍 Choose the correct *Free Fire server region*\n"
        f"for Guild ID: `{ctx.user_data.get('guild_uid', '?')}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=region_kb(),
    )
    return WAITING_REGION

# ─── Buy Flow: Manual Entry ───────────────────────────────────────────────────
async def manual_entry_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["manual_mode"] = True
    await query.edit_message_text(
        f"✏️ *MANUAL GUILD ENTRY*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *Step 3 of 4* — Enter Guild Details\n\n"
        f"Send your guild info in this exact format:\n\n"
        f"`Guild Name | Level | XP`\n\n"
        f"📌 *Example:*\n"
        f"`FlameKings | 12 | 850000`\n\n"
        f"💡 _Find this info in your Free Fire guild profile_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel Purchase", callback_data="cancel_buy")
        ]]),
    )
    return WAITING_CONFIRMATION

async def receive_manual_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("manual_mode"):
        return WAITING_CONFIRMATION

    text = update.message.text.strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        await update.message.reply_text(
            "⚠️ Invalid format. Use:\n`Guild Name | Level | XP`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_CONFIRMATION

    guild_name  = parts[0]
    guild_level = parts[1]
    guild_xp    = parts[2]

    ctx.user_data.update({
        "guild_name":  guild_name,
        "guild_level": guild_level,
        "guild_xp":    guild_xp,
        "manual_mode": False,
    })

    region_label = ctx.user_data.get("region_label", "N/A")
    info_text = (
        f"✏️ *MANUAL ENTRY CONFIRMED*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *Step 3 of 4* — Confirm Details\n\n"
        f"┌─────────────────────────\n"
        f"│ 🏯 Guild: *{guild_name}*\n"
        f"│ ⭐ Level: *{guild_level}*\n"
        f"│ 💠 XP: *{guild_xp}*\n"
        f"│ 🌍 Region: *{region_label}*\n"
        f"└─────────────────────────\n\n"
        f"🧾 *ORDER SUMMARY*\n"
        f"╔══════════════════════╗\n"
        f"║  🔰 {ub('200,000 Guild Glory')}  ║\n"
        f"║  💰 Price: {ub('Rs.80')} only     ║\n"
        f"╚══════════════════════╝\n\n"
        f"⚠️ _Please confirm your guild details before proceeding._"
    )
    await update.message.reply_text(info_text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=confirmation_kb())
    return WAITING_CONFIRMATION

# ─── Buy Flow: Confirm ────────────────────────────────────────────────────────
async def confirm_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qr_path = get_config(QR_PATH_KEY)
    guild_name = ctx.user_data.get("guild_name", "your guild")

    pay_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Have Paid — Enter UTR Now", callback_data="paid_manual")],
    ])

    qr_caption = (
        f"💳 *PAYMENT — Step 4 of 4*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏯 Guild: *{guild_name}*\n"
        f"🔰 Glory: *{ub('2,00,000')}*\n\n"
        f"╔══════════════════════╗\n"
        f"║  💰 Pay {ub('Rs.80')} only!       ║\n"
        f"╚══════════════════════╝\n\n"
        f"📸 *Scan the QR code above to pay*\n"
        f"📱 Works with PhonePe • GPay • Paytm • BHIM\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *After payment:*\n"
        f"Open your UPI app → transaction history\n"
        f"Copy the *12-digit UTR / Ref. ID* and tap the button below ⬇️"
    )

    if qr_path and os.path.exists(qr_path):
        try:
            with open(qr_path, "rb") as f:
                await query.message.reply_photo(
                    f,
                    caption=qr_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=pay_kb,
                )
            try:
                await query.message.delete()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"QR photo send failed: {e}")
            try:
                await query.message.reply_text(
                    qr_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=pay_kb,
                )
            except Exception as e2:
                logger.error(f"confirm_yes failed: {e2}")
                return WAITING_CONFIRMATION
    else:
        try:
            await query.message.reply_text(
                qr_caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=pay_kb,
            )
        except Exception as e:
            logger.error(f"confirm_yes failed: {e}")
            return WAITING_CONFIRMATION

    return WAITING_UTR

async def confirm_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    user = update.effective_user
    await query.edit_message_text("❌ Order cancelled.",
                                  reply_markup=main_menu_kb(user.id == ADMIN_ID))
    return ConversationHandler.END

# ─── Buy Flow: UTR ────────────────────────────────────────────────────────────
async def receive_utr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    utr = update.message.text.strip().replace(" ", "")

    # Must be all digits
    if not utr.isdigit():
        await update.message.reply_text(
            "❌ *Wrong UTR Number!*\n\n"
            "UTR must contain *numbers only* — no letters or symbols.\n\n"
            "📋 *Where to find your UTR:*\n"
            "• PhonePe → History → Tap txn → *UTR No.*\n"
            "• GPay → Transaction → *UPI Ref. ID*\n"
            "• Paytm → Passbook → *UPI Ref No.*\n\n"
            "🔁 Send the correct 12-digit UTR number:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_UTR

    # Must be exactly 12 digits
    if len(utr) != 12:
        await update.message.reply_text(
            f"❌ *Wrong UTR Number!*\n\n"
            f"You entered *{len(utr)} digit(s)* — UTR must be exactly *12 digits*.\n\n"
            f"❌ You entered: `{utr}`\n\n"
            f"📋 *Where to find the correct UTR:*\n"
            f"• PhonePe → History → Tap txn → *UTR No.*\n"
            f"• GPay → Transaction → *UPI Ref. ID*\n"
            f"• Paytm → Passbook → *UPI Ref No.*\n\n"
            f"🔁 Send the full 12-digit number:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_UTR

    await update.message.reply_text(
        f"❌ *INVALID UTR / NO PAYMENT FOUND*\n\n"
        f"UTR `{utr}` is not linked to any payment right now.\n\n"
        f"Please check again and send the correct UTR.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_UTR


async def utr_confirm_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    utr  = ctx.user_data.get("pending_utr", "")
    user = update.effective_user
    ud   = ctx.user_data

    if not utr:
        await query.edit_message_text("⚠️ Session expired. Please start again.",
                                      reply_markup=main_menu_kb(user.id == ADMIN_ID))
        return ConversationHandler.END

    save_order(
        user_id    = user.id,
        username   = user.username or user.first_name,
        guild_uid  = ud.get("guild_uid", "manual"),
        guild_name = ud.get("guild_name", "N/A"),
        guild_level= ud.get("guild_level", "N/A"),
        guild_xp   = ud.get("guild_xp", "N/A"),
        region     = ud.get("region_label", "N/A"),
        utr        = utr,
    )

    # Notify admin
    try:
        admin_text = (
            f"🔔 *New Order Received!*\n\n"
            f"👤 User: @{user.username or user.first_name} (ID: {user.id})\n"
            f"🏰 Guild: *{ud.get('guild_name')}*\n"
            f"🆔 Guild ID: `{ud.get('guild_uid', 'manual')}`\n"
            f"📊 Level: {ud.get('guild_level')} | XP: {ud.get('guild_xp')}\n"
            f"🌍 Region: {ud.get('region_label')}\n"
            f"💳 UTR: `{utr}`\n"
            f"💰 Amount: ₹{PRICE_INR}\n\n"
            f"Use /approve <order_id> to approve."
        )
        await ctx.application.bot.send_message(ADMIN_ID, admin_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Failed to notify admin: {e}")

    ctx.user_data.clear()
    is_admin = (user.id == ADMIN_ID)
    await query.edit_message_text(
        f"🎉 *ORDER PLACED SUCCESSFULLY\\!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Your order has been received\\!\n\n"
        f"📋 *Order Details:*\n"
        f"┌─────────────────────────\n"
        f"│ 🔰 Glory: {ub('2,00,000')}\n"
        f"│ 💳 UTR: `{utr}`\n"
        f"│ ⏳ Status: *Pending Review*\n"
        f"└─────────────────────────\n\n"
        f"⏰ *Delivery within 24 hours* after payment verification\\.\n\n"
        f"📦 Track your order using *My Orders* button\\.\n"
        f"🆘 Need help? Tap *Support* anytime\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=main_menu_kb(is_admin),
    )
    return ConversationHandler.END


async def utr_reenter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.pop("pending_utr", None)
    await query.edit_message_text(
        f"🔁 *Re-enter Your UTR*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Open your payment app, find the transaction\n"
        f"and copy the exact *12-digit UTR number*.\n\n"
        f"📋 *Where to find UTR:*\n"
        f"• PhonePe → History → Tap txn → *UTR No.*\n"
        f"• GPay → Transaction → *UPI Ref. ID*\n"
        f"• Paytm → Passbook → *UPI Ref No.*\n\n"
        f"💬 Send the correct UTR now:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_UTR

# ─── My Orders ────────────────────────────────────────────────────────────────
async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    orders = get_user_orders_safe(user.id)
    if not orders:
        text = (
            "📦 *MY ORDERS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🕳️ No orders yet!\n\n"
            "Tap *🔥 BUY GLORY NOW* to place your first order."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=back_kb())
    else:
        lines = [
            "📦 *MY ORDERS*",
            "━━━━━━━━━━━━━━━━━━━━━━\n",
        ]
        for o in orders[:10]:
            oid, gname, glvl, reg, utr, status, ts = o
            emoji = "✅" if status == "approved" else "⏳"
            badge = "DELIVERED ✅" if status == "approved" else "PENDING ⏳"
            lines.append(
                f"{emoji} *Order #{oid}*\n"
                f"┌ 🏯 Guild: *{gname}* (Lv.{glvl})\n"
                f"├ 🌍 Region: {reg}\n"
                f"├ 💳 UTR: `{utr}`\n"
                f"├ 🗓️ Date: {ts[:10]}\n"
                f"└ 📌 Status: *{badge}*\n"
            )
        text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=back_kb())

# ─── How to Pay ───────────────────────────────────────────────────────────────
async def how_to_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        f"💳 *HOW TO PAY*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"╔══════════════════════╗\n"
        f"║  🔰 {ub('2,00,000 Guild Glory')} ║\n"
        f"║  💰 Only {ub('Rs.80')}           ║\n"
        f"╚══════════════════════╝\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 *Step-by-Step Guide:*\n\n"
        f"📌 *Description:*\n"
        f"Free Fire Guild Glory shop for quick guild boosting.\n"
        f"Safe payment, QR checkout, and admin review.\n\n"
        f"1️⃣ Tap *🔥 BUY GLORY NOW* on main menu\n"
        f"2️⃣ Enter your *Guild ID*\n"
        f"3️⃣ Select your *Region*\n"
        f"4️⃣ Confirm guild details\n"
        f"5️⃣ *Scan the QR code* shown in the bot\n"
        f"6️⃣ Complete ₹{PRICE_INR} payment\n"
        f"7️⃣ Copy *UTR / Transaction ID* from UPI app\n"
        f"8️⃣ Send UTR back in the bot\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *Supported Apps:*\n"
        f"📱 PhonePe  •  Google Pay  •  Paytm\n"
        f"📱 BHIM  •  Amazon Pay  •  Any UPI\n\n"
        f"⏰ _Glory delivered within 24 hours after verification_"
    )
    htp_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=htp_kb)

async def commands_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        f"📖 *COMMANDS*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• /start - Open bot menu\n"
        f"• /cancel - Cancel current order\n"
        f"• /approve <order_id> - Approve order (admin)\n\n"
        f"🔥 *Cool Fire Description*\n"
        f"Free Fire Guild Glory made fast, simple, and safe.\n"
        f"Scan QR, pay, send UTR, and wait for approval."
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=back_kb())

# ─── Support ──────────────────────────────────────────────────────────────────
async def support(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        f"🆘 *SUPPORT CENTER*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 We're here to help you 24/7!\n\n"
        f"📞 *Contact Admin:*\n"
        f"┌─────────────────────────\n"
        f"│ 💬 Telegram: @FFGloryAdmin\n"
        f"│ ⏰ Response: within 1–6 hours\n"
        f"└─────────────────────────\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"❓ *Common Issues & Fixes:*\n\n"
        f"🔸 *Wrong UTR submitted*\n"
        f"   → Contact admin with correct UTR\n\n"
        f"🔸 *Glory not delivered after 24h*\n"
        f"   → Check My Orders → message admin\n\n"
        f"🔸 *Payment failed / declined*\n"
        f"   → Try a different UPI app & retry\n\n"
        f"🔸 *Guild ID not found*\n"
        f"   → Use Manual Entry option\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _Always share your Order ID when contacting support_"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=back_kb())

# ─── Admin Panel ──────────────────────────────────────────────────────────────
async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        await query.answer("⛔ Unauthorised!", show_alert=True)
        return
    await query.edit_message_text("⚙️ *Admin Panel*\n\nSelect an option:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=admin_kb())

async def view_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    orders = get_pending_orders()
    if not orders:
        text = "📋 *Pending Orders*\n\nNo pending orders."
    else:
        lines = ["📋 *Pending Orders*\n"]
        for o in orders:
            oid, uid, uname, gname, glvl, reg, utr, ts = o
            lines.append(
                f"🔸 *Order #{oid}*\n"
                f"   User: @{uname} ({uid})\n"
                f"   Guild: {gname} (Lv.{glvl})\n"
                f"   Region: {reg}\n"
                f"   UTR: `{utr}`\n"
                f"   Time: {ts[:16]}\n"
                f"   → /approve {oid}\n"
            )
        text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=admin_kb())

async def stats_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    total, pending, approved, revenue = get_stats()
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"📦 Total Orders: *{total}*\n"
        f"⏳ Pending: *{pending}*\n"
        f"✅ Approved: *{approved}*\n"
        f"💰 Total Revenue: *₹{revenue}*\n"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=admin_kb())

async def change_qr_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    ctx.user_data["awaiting_qr"] = True
    await query.edit_message_text(
        "🖼️ *Change QR Code*\n\n"
        "Send the new QR code image now.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_kb(),
    )

async def approve_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    await query.edit_message_text(
        "✅ *Approve Order*\n\n"
        "Use the command:\n`/approve <order_id>`\n\n"
        "View pending orders to get order IDs.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_kb(),
    )

# ─── Photo Handler (QR Update) ────────────────────────────────────────────────
async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    if not ctx.user_data.get("awaiting_qr"):
        return
    photo = update.message.photo[-1]
    file  = await ctx.bot.get_file(photo.file_id)
    path  = "qr_code.png"
    await file.download_to_drive(path)
    set_config(QR_PATH_KEY, path)
    ctx.user_data["awaiting_qr"] = False
    await update.message.reply_text("✅ QR code updated successfully!", reply_markup=admin_kb())

# ─── Paid Manual (UTR prompt after UPI deep-link) ────────────────────────────
async def paid_manual_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        f"✅ *Great\\! Now send your UTR number\\.*\n\n"
        f"📋 *Where to find UTR:*\n"
        f"┌─────────────────────────\n"
        f"│ PhonePe → History → Tap txn → UTR\n"
        f"│ GPay → Transaction → Reference ID\n"
        f"│ Paytm → Passbook → UPI Ref No\\.\n"
        f"└─────────────────────────\n\n"
        f"💬 _Reply with your 12\\-digit UTR / Reference number:_",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

# ─── Main Menu Callback ───────────────────────────────────────────────────────
async def main_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await start(update, ctx)

# ─── Unknown Callback ─────────────────────────────────────────────────────────
async def unknown_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("This button is not active here.", show_alert=False)

# ─── Build Application ────────────────────────────────────────────────────────
def build_app() -> Application:
    init_db()

    # Create default QR placeholder
    if not os.path.exists(DEFAULT_QR):
        try:
            from PIL import Image, ImageDraw, ImageFont
            img  = Image.new("RGB", (300, 300), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)
            draw.rectangle([50, 50, 250, 250], outline="black", width=4)
            draw.text((90, 130), "QR CODE\nPLACEHOLDER", fill="black")
            img.save(DEFAULT_QR)
        except Exception:
            pass  # PIL not available in all envs

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
        connection_pool_size=8,
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(HTTPXRequest(
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0,
            connection_pool_size=8,
        ))
        .build()
    )

    # ── Conversation Handler (Buy Flow) ───────────────────────────────────────
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_glory, pattern="^buy_glory$")],
        states={
            WAITING_UID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_uid),
                CallbackQueryHandler(cancel_buy_cb, pattern="^cancel_buy$"),
            ],
            WAITING_REGION: [
                CallbackQueryHandler(receive_region,
                    pattern="^(region_india|region_indonesia|region_bangladesh|region_pakistan|region_other)$"),
                CallbackQueryHandler(cancel_buy_cb, pattern="^cancel_buy$"),
            ],
            WAITING_CONFIRMATION: [
                CallbackQueryHandler(confirm_yes,       pattern="^confirm_yes$"),
                CallbackQueryHandler(confirm_no,        pattern="^confirm_no$"),
                CallbackQueryHandler(change_region_cb,  pattern="^change_region$"),
                CallbackQueryHandler(manual_entry_cb,   pattern="^manual_entry$"),
                CallbackQueryHandler(cancel_buy_cb,     pattern="^cancel_buy$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_entry),
            ],
            WAITING_UTR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_utr),
                CallbackQueryHandler(paid_manual_cb, pattern="^paid_manual$"),
            ],
            WAITING_UTR_CONFIRM: [
                CallbackQueryHandler(utr_confirm_yes, pattern="^utr_confirm_yes$"),
                CallbackQueryHandler(utr_reenter,     pattern="^utr_reenter$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
            CallbackQueryHandler(cancel_buy_cb, pattern="^cancel_buy$"),
        ],
        per_message=False,
        allow_reentry=True,
    )
    app.add_handler(conv)

    # ── Command Handlers ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("cancel",  cancel_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))

    # ── Callback Handlers (outside conversation) ──────────────────────────────
    app.add_handler(CallbackQueryHandler(my_orders,        pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(how_to_pay,       pattern="^how_to_pay$"))
    app.add_handler(CallbackQueryHandler(commands_cb,      pattern="^commands$"))
    app.add_handler(CallbackQueryHandler(support,          pattern="^support$"))
    app.add_handler(CallbackQueryHandler(admin_panel,      pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(view_pending,     pattern="^view_pending$"))
    app.add_handler(CallbackQueryHandler(approve_menu_cb,  pattern="^approve_menu$"))
    app.add_handler(CallbackQueryHandler(change_qr_cb,     pattern="^change_qr$"))
    app.add_handler(CallbackQueryHandler(stats_cb,         pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(main_menu_cb,     pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(paid_manual_cb,   pattern="^paid_manual$"))
    app.add_handler(CallbackQueryHandler(unknown_callback))

    # ── Photo Handler ─────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    return app

# ─── Entry Point ──────────────────────────────────────────────────────────────
def main():
    """
    run_polling() manages its own event loop — works on Python 3.10-3.13,
    Termux (Android), local machines, and Google Colab.
    Auto-retries on network errors so the bot stays online 24/7.
    """
    import time as _time

    logger.info("Starting FF Glory Bot...")
    app = build_app()

    retry_delay = 5
    max_delay   = 60

    while True:
        try:
            logger.info("Bot is running. Press Ctrl+C to stop.")
            app.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                timeout=20,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30,
            )
            break  # clean exit (e.g. KeyboardInterrupt inside run_polling)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            break
        except Exception as e:
            logger.error(f"Connection error: {e}. Retrying in {retry_delay}s...")
            _time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    print("\n⛔ Bot stopped.")
