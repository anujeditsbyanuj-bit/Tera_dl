"""
TeraBox Downloader Bot v5.1
────────────────────────────
New in v5.1:
  ✅ Quality selection (Auto / 1080p / 720p / 480p / 360p)
  ✅ tera.backend.live API (faster, no cookie needed for many links)
  ✅ Force subscribe guard
  ✅ User ban / unban system
  ✅ Per-user download queue (MAX_CONCURRENT limit)
  ✅ /cancel — cancel active download
  ✅ /ping — latency check
  ✅ Bulk folder download (Download All button)
  ✅ Better thumbnails via backend
  ✅ Total data transferred in /stats
  ✅ Admin /banned list
"""

import asyncio
import os
import time
import aiofiles
from collections import defaultdict

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from pyrogram.errors import (
    FloodWait, ChatAdminRequired, PeerIdInvalid,
    UserNotParticipant, ChannelInvalid,
)

from config import Config
from database import db
from terabox import TeraBoxAPI, extract_surl, is_terabox_link, STREAM_QUALITIES
from utils import (
    humanbytes, progress_bar, time_fmt, fmt_duration,
    file_icon, is_video, is_audio, is_image,
)

os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# ── Clients ───────────────────────────────────────────────────────────────────
bot = Client(
    "terabox_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

kuri = None
if Config.SESSION_STRING:
    try:
        from kurigram import Client as KuriClient
        kuri = KuriClient(
            "kuri_session",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.SESSION_STRING,
        )
    except ImportError:
        print("⚠️  kurigram not installed — 4GB uploads disabled")

# ── In-memory state ───────────────────────────────────────────────────────────
active_tasks: dict[int, set] = defaultdict(set)
cancel_events: dict[int, asyncio.Event] = {}
premium_users: set = set()
password_pending: dict = {}
rename_pending: dict = {}

# NEW: quality_pending — user ne quality select nahi ki abhi
# { user_id: { surl, fs_id, uk, share_id, file_info } }
quality_pending: dict = {}


# Quality labels mapping
QUALITY_LABELS = {
    "AUTO":             "🤖 Auto (Best)",
    "M3U8_AUTO_1080":   "🔥 1080p HD",
    "M3U8_FLV_264_720": "📺 720p HD",
    "M3U8_FLV_264_480": "📱 480p",
    "M3U8_FLV_264_360": "💾 360p",
    "DIRECT":           "💿 Original (Direct)",
}


def get_user_limit(user_id: int) -> int:
    if user_id in Config.ADMIN_IDS or user_id in premium_users:
        return Config.MAX_CONCURRENT_PREMIUM
    return Config.MAX_CONCURRENT


# ── Guards ────────────────────────────────────────────────────────────────────
async def check_banned(msg_or_cq) -> bool:
    uid = (msg_or_cq.from_user or msg_or_cq.message.from_user).id
    if await db.is_banned(uid):
        reason = await db.get_ban_reason(uid)
        txt = f"🚫 You are **banned** from using this bot.\nReason: `{reason or 'Not specified'}`"
        if hasattr(msg_or_cq, "reply"):
            await msg_or_cq.reply(txt)
        else:
            await msg_or_cq.answer(txt, show_alert=True)
        return True
    return False


async def check_force_join(msg: Message) -> bool:
    if not Config.FORCE_JOIN:
        return False
    uid = msg.from_user.id
    try:
        member = await bot.get_chat_member(Config.FORCE_JOIN, uid)
        if member.status.value in ("left", "kicked"):
            raise UserNotParticipant
        return False
    except (UserNotParticipant, ChannelInvalid, Exception):
        await msg.reply(
            "🔒 **Join Required!**\n\n"
            f"Join our channel to use this bot:\n👉 https://t.me/{Config.FORCE_JOIN}\n\n"
            "After joining, send your link again!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Join Channel", url=f"https://t.me/{Config.FORCE_JOIN}"),
            ]])
        )
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────
async def get_api() -> TeraBoxAPI:
    cookie = await db.get_config("ndus_cookie") or Config.NDUS_COOKIE
    return TeraBoxAPI(cookie=cookie)


def make_progress(msg: Message, action: str, name: str, start: float):
    last = [0.0]

    async def cb(done: int, total: int):
        now = time.time()
        if now - last[0] < 3:
            return
        last[0] = now
        pct = done / total * 100 if total else 0
        bar = progress_bar(pct)
        spd = done / max(now - start, 0.1)
        eta = (total - done) / spd if spd else 0
        icon = "📥" if action == "dl" else "📤"
        word = "Downloading" if action == "dl" else "Uploading"
        try:
            await msg.edit(
                f"{icon} **{word}...**\n\n"
                f"📄 `{name[:40]}`\n"
                f"{bar} `{pct:.1f}%`\n\n"
                f"{'⬇️' if action == 'dl' else '⬆️'} `{humanbytes(done)}` / `{humanbytes(total)}`\n"
                f"⚡ `{humanbytes(int(spd))}/s`\n"
                f"⏱ ETA: `{time_fmt(int(eta))}`"
            )
        except Exception:
            pass

    return cb


async def send_file(client, chat_id: int, path: str, file_info: dict, caption: str):
    name = file_info.get("server_filename", os.path.basename(path))
    thumb = (
        file_info.get("thumbs", {}).get("url2")
        or file_info.get("thumbs", {}).get("url1")
    )
    dur = file_info.get("duration", 0)
    w = file_info.get("width", 0)
    h = file_info.get("height", 0)

    if is_video(name):
        return await client.send_video(
            chat_id, path, caption=caption, thumb=thumb,
            duration=dur, width=w, height=h, supports_streaming=True,
        )
    elif is_audio(name):
        return await client.send_audio(chat_id, path, caption=caption)
    elif is_image(name):
        return await client.send_photo(chat_id, path, caption=caption)
    else:
        return await client.send_document(chat_id, path, caption=caption, thumb=thumb)


SPLIT_SIZE = 2 * 1024 * 1024 * 1024


async def split_file(path: str, part_size: int = SPLIT_SIZE) -> list[str]:
    parts = []
    part_num = 1
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(part_size)
            if not chunk:
                break
            part_path = f"{path}.part{part_num:03d}"
            async with aiofiles.open(part_path, "wb") as pf:
                await pf.write(chunk)
            parts.append(part_path)
            part_num += 1
    return parts


async def send_file_or_split(
    client, chat_id: int, path: str, file_info: dict,
    caption: str, msg_status, uploader_kuri=None
) -> list:
    size = os.path.getsize(path)
    name = file_info.get("server_filename", os.path.basename(path))

    if size <= SPLIT_SIZE:
        uploader = uploader_kuri if (uploader_kuri and size > 50 * 1024 * 1024) else client
        sent = await send_file(uploader, chat_id, path, file_info, caption)
        return [sent]

    await msg_status.edit(
        f"✂️ **File is {humanbytes(size)} — Auto splitting into 2GB parts...**\n\n"
        f"📄 `{name}`"
    )

    parts = await split_file(path)
    total_parts = len(parts)
    sent_msgs = []

    try:
        for i, part_path in enumerate(parts, 1):
            part_name = f"{name}.part{i:03d}"
            part_size = os.path.getsize(part_path)
            part_caption = (
                f"📦 **{name}**\n"
                f"✂️ Part `{i}/{total_parts}` — `{humanbytes(part_size)}`\n\n"
                f"{caption}"
            )
            await msg_status.edit(
                f"📤 **Uploading part {i}/{total_parts}...**\n\n"
                f"📄 `{part_name}`\n"
                f"📦 `{humanbytes(part_size)}`"
            )
            uploader = uploader_kuri if uploader_kuri else client
            renamed = os.path.join(os.path.dirname(part_path), part_name)
            os.rename(part_path, renamed)
            sent = await client.send_document(
                chat_id, renamed, caption=part_caption, file_name=part_name,
            )
            sent_msgs.append(sent)
            try:
                os.remove(renamed)
            except Exception:
                pass
    finally:
        for p in parts:
            try:
                os.remove(p)
            except Exception:
                pass

    return sent_msgs


def _build_url_from_surl(surl: str) -> str:
    return f"https://terabox.app/sharing/link?surl={surl}"


# ─────────────────────────────────────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(_, msg: Message):
    if await check_banned(msg):
        return
    u = msg.from_user
    await db.add_user(u.id, u.first_name, u.username)
    target = await db.get_target_chat(u.id)
    target_txt = f"`{target}`" if target else "_Not set (sends here)_"

    await msg.reply(
        f"**👋 Hello {u.mention}!**\n\n"
        "📦 **TeraBox Downloader Bot v5.1**\n\n"
        "Send any TeraBox / Nephobox share link and I'll download & send it!\n\n"
        "**🎯 Your Upload Target:**\n"
        f"{target_txt}\n\n"
        "**⚡ Commands:**\n"
        "/start — Home\n"
        "/setchat — Set custom channel/group\n"
        "/clearchat — Remove custom target\n"
        "/mychat — Show current target\n"
        "/stats — Bot statistics\n"
        "/cancel — Cancel active download\n"
        "/ping — Check bot latency\n"
        "/help — Help\n\n"
        "**✅ Supported:**\n"
        "terabox.app · nephobox.com · freeterabox.com\n"
        "1024terabox.com · teraboxapp.com · terasharelink.com",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📢 Updates",
                url=f"https://t.me/{Config.UPDATE_CHANNEL}"
            ) if Config.UPDATE_CHANNEL else InlineKeyboardButton("🤖 Bot", url="https://t.me/me"),
            InlineKeyboardButton(
                "💬 Support",
                url=f"https://t.me/{Config.SUPPORT_GROUP}"
            ) if Config.SUPPORT_GROUP else InlineKeyboardButton("📖 Help", callback_data="help"),
        ]])
    )


@bot.on_message(filters.command("help") & filters.private)
async def cmd_help(_, msg: Message):
    await msg.reply(
        "**📖 How to use:**\n\n"
        "1️⃣ Copy a TeraBox share link\n"
        "2️⃣ Paste here or in your target chat\n"
        "3️⃣ Bot fetches file info & shows buttons\n"
        "4️⃣ **Select Quality** → Download starts!\n\n"
        "**🎬 Quality Options:**\n"
        "🤖 Auto — Best available quality\n"
        "🔥 1080p HD — Full HD\n"
        "📺 720p HD — HD\n"
        "📱 480p — Standard\n"
        "💾 360p — Low data\n"
        "💿 Original — Direct file (no transcoding)\n\n"
        "**📁 Folder links:**\n"
        "Bot lists all files. Tap any to download.\n\n"
        "**🎯 Custom Channel/Group:**\n"
        "Use /setchat to set where bot uploads.\n\n"
        f"**⏳ Auto Delete:** {Config.AUTO_DELETE // 60} minutes.\n\n"
        "**📏 File limits:**\n"
        "• Up to 2 GB → Bot API\n"
        "• Up to 4 GB → MTProto (Kurigram)\n\n"
        "**⛔ Cancel:** Use /cancel to stop."
    )


@bot.on_message(filters.command("ping"))
async def cmd_ping(_, msg: Message):
    start = time.time()
    m = await msg.reply("🏓 Pong!")
    ms = int((time.time() - start) * 1000)
    await m.edit(f"🏓 **Pong!** `{ms}ms`")


@bot.on_message(filters.command("setchat") & filters.private)
async def cmd_setchat(_, msg: Message):
    if await check_banned(msg):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.reply(
            "**🎯 Set Upload Target**\n\n"
            "Send channel/group ID:\n"
            "`/setchat -1001234567890`\n\n"
            "**How to get chat ID:**\n"
            "Forward a message from your channel/group to @userinfobot\n\n"
            "⚠️ Make sure bot is **admin** in that chat!"
        )
    try:
        chat_id = int(parts[1].strip())
    except ValueError:
        return await msg.reply("❌ Invalid ID! Must be a number like `-1001234567890`")

    try:
        chat = await bot.get_chat(chat_id)
        member = await bot.get_chat_member(chat_id, "me")
        if member.status.value not in ("administrator", "creator"):
            return await msg.reply(
                f"❌ I'm not an admin in **{chat.title}**!\n"
                "Please add me as admin first."
            )
        await db.set_target_chat(msg.from_user.id, chat_id)
        await msg.reply(
            f"✅ **Target set!**\n\n"
            f"📢 Chat: **{chat.title}**\n"
            f"🆔 ID: `{chat_id}`\n\n"
            "All your downloads will now go there!"
        )
    except PeerIdInvalid:
        await msg.reply("❌ Chat not found!")
    except ChatAdminRequired:
        await msg.reply("❌ Bot needs admin rights in that chat!")
    except Exception as e:
        await msg.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("clearchat") & filters.private)
async def cmd_clearchat(_, msg: Message):
    await db.clear_target_chat(msg.from_user.id)
    await msg.reply("✅ Target cleared! Files will now be sent here.")


@bot.on_message(filters.command("mychat") & filters.private)
async def cmd_mychat(_, msg: Message):
    target = await db.get_target_chat(msg.from_user.id)
    if not target:
        return await msg.reply("🎯 No target set. Files sent here.\nUse /setchat to set one.")
    try:
        chat = await bot.get_chat(target)
        await msg.reply(f"🎯 **Current Target:**\n📢 {chat.title}\n🆔 `{target}`")
    except Exception:
        await msg.reply(f"🎯 Target ID: `{target}`")


@bot.on_message(filters.command("stats"))
async def cmd_stats(_, msg: Message):
    total_u = await db.total_users()
    total_d = await db.total_downloads()
    total_data = await db.total_data_transferred()
    my_d = await db.user_dl_count(msg.from_user.id)
    await msg.reply(
        f"**📊 Bot Statistics**\n\n"
        f"👥 Total Users: `{total_u}`\n"
        f"📥 Total Downloads: `{total_d}`\n"
        f"💾 Data Transferred: `{humanbytes(total_data)}`\n"
        f"🙋 Your Downloads: `{my_d}`"
    )


@bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(_, msg: Message):
    uid = msg.from_user.id
    ev = cancel_events.get(uid)
    if ev and not ev.is_set():
        ev.set()
        await msg.reply("⛔ **Cancelling download...**")
    else:
        await msg.reply("ℹ️ No active download to cancel.")


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.command("broadcast") & filters.user(Config.ADMIN_IDS))
async def cmd_broadcast(_, msg: Message):
    if not msg.reply_to_message:
        return await msg.reply("Reply to a message to broadcast!")
    users = await db.get_all_user_ids()
    done, fail = 0, 0
    st = await msg.reply(f"📢 Broadcasting to {len(users)} users...")
    for uid in users:
        try:
            await msg.reply_to_message.copy(uid)
            done += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await msg.reply_to_message.copy(uid)
                done += 1
            except Exception:
                fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await st.edit(f"✅ Done!\nSuccess: {done} | Failed: {fail}")


@bot.on_message(filters.command("addcookie") & filters.user(Config.ADMIN_IDS))
async def cmd_addcookie(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.reply(
            "**Usage:** `/addcookie ndus=your_value`\n\n"
            "Get from browser:\n"
            "1. Login to terabox.app\n"
            "2. DevTools → Application → Cookies\n"
            "3. Copy `ndus` cookie value"
        )
    cookie = parts[1].strip()
    if not cookie.startswith("ndus="):
        cookie = f"ndus={cookie}"
    await db.set_config("ndus_cookie", cookie)
    await msg.reply(f"✅ Cookie updated!\n`{cookie[:40]}...`")


@bot.on_message(filters.command("users") & filters.user(Config.ADMIN_IDS))
async def cmd_users(_, msg: Message):
    total = await db.total_users()
    downloads = await db.total_downloads()
    banned = await db.total_banned()
    data = await db.total_data_transferred()
    await msg.reply(
        f"**👥 Users:** `{total}`\n"
        f"**📥 Downloads:** `{downloads}`\n"
        f"**💾 Data:** `{humanbytes(data)}`\n"
        f"**🚫 Banned:** `{banned}`"
    )


@bot.on_message(filters.command("ban") & filters.user(Config.ADMIN_IDS))
async def cmd_ban(_, msg: Message):
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 2:
        return await msg.reply("**Usage:** `/ban <user_id> [reason]`")
    try:
        uid = int(parts[1])
    except ValueError:
        return await msg.reply("❌ Invalid user ID!")
    reason = parts[2] if len(parts) > 2 else ""
    if uid in Config.ADMIN_IDS:
        return await msg.reply("❌ Cannot ban an admin!")
    await db.ban_user(uid, reason)
    await msg.reply(f"✅ User `{uid}` banned.\nReason: `{reason or 'None'}`")


@bot.on_message(filters.command("unban") & filters.user(Config.ADMIN_IDS))
async def cmd_unban(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.reply("**Usage:** `/unban <user_id>`")
    try:
        uid = int(parts[1])
    except ValueError:
        return await msg.reply("❌ Invalid user ID!")
    await db.unban_user(uid)
    await msg.reply(f"✅ User `{uid}` unbanned.")


@bot.on_message(filters.command("banned") & filters.user(Config.ADMIN_IDS))
async def cmd_banned_list(_, msg: Message):
    total = await db.total_banned()
    await msg.reply(f"🚫 Total banned users: `{total}`")


# ─────────────────────────────────────────────────────────────────────────────
#  PASSWORD HANDLER
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.text & filters.private)
async def handle_password(_, msg: Message):
    user_id = msg.from_user.id
    if user_id not in password_pending:
        return

    pwd = msg.text.strip()
    surl, orig_text, status = password_pending.pop(user_id)
    await status.edit(f"🔑 Trying password `{pwd}`...")

    try:
        api = await get_api()
        data = await api.get_file_list(surl, password=pwd)
        await api.close()

        errno = data.get("errno", -1)

        if errno == 9:
            password_pending[user_id] = (surl, orig_text, status)
            return await status.edit("❌ **Wrong password!**\n\nPlease send the correct password:")

        if errno == 8:
            password_pending[user_id] = (surl, orig_text, status)
            return await status.edit("🔐 **Password required!**\n\nPlease send the password:")

        if errno != 0:
            return await status.edit(f"❌ **Failed!** errno: `{errno}`")

        if data.get("errno") == 0:
            await db.cache_set(surl, data)

        files = data.get("list", [])
        uk = data.get("uk")
        share_id = data.get("share_id")

        if not files:
            return await status.edit("❌ No files found!")

        if len(files) == 1 and str(files[0].get("isdir", "0")) == "0":
            await _show_file(status, files[0], surl, orig_text, uk, share_id)
        else:
            await _show_folder(status, files, surl, orig_text, uk, share_id)

    except Exception as e:
        await status.edit(f"❌ Error: `{e}`")


# ─────────────────────────────────────────────────────────────────────────────
#  LINK HANDLER
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.text & (filters.private | filters.group))
async def handle_link(_, msg: Message):
    text = msg.text.strip()
    if not is_terabox_link(text):
        return

    user_id = msg.from_user.id
    if user_id in password_pending:
        return

    if await check_banned(msg):
        return
    if await check_force_join(msg):
        return

    await db.add_user(user_id, msg.from_user.first_name, msg.from_user.username)

    surl = extract_surl(text)
    if not surl:
        return await msg.reply("❌ Could not extract share URL!")

    _limit = get_user_limit(user_id)
    if len(active_tasks.get(user_id, set())) >= _limit:
        return await msg.reply(
            f"⚠️ You already have `{_limit}` active download(s).\n"
            "Use /cancel to stop, or wait for them to finish."
        )

    status = await msg.reply("🔍 **Fetching file info...**")

    try:
        cached = await db.cache_get(surl)
        if cached:
            data = cached
        else:
            api = await get_api()
            data = await api.get_file_list(surl)
            if data.get("errno") == 0:
                await db.cache_set(surl, data)
            await api.close()

        errno = data.get("errno", -1)

        if errno == 8:
            password_pending[user_id] = (surl, text, status)
            return await status.edit(
                "🔐 **This link is password protected!**\n\n"
                "Please send the **password** now:"
            )

        if errno != 0:
            return await status.edit(
                f"❌ **Failed to fetch!**\n"
                f"errno: `{errno}`\n\n"
                "Link may be expired or private."
            )

        files = data.get("list", [])
        uk = data.get("uk")
        share_id = data.get("share_id")

        if not files:
            return await status.edit("❌ No files found in this link!")

        if len(files) == 1 and str(files[0].get("isdir", "0")) == "0":
            await _show_file(status, files[0], surl, text, uk, share_id)
        else:
            await _show_folder(status, files, surl, text, uk, share_id)

    except Exception as e:
        await status.edit(f"❌ Error: `{e}`")


# ─────────────────────────────────────────────────────────────────────────────
#  SHOW FILE — Quality Selection Buttons
# ─────────────────────────────────────────────────────────────────────────────

async def _show_file(msg: Message, f: dict, surl: str, orig_url: str, uk, share_id):
    name = f.get("server_filename", "?")
    size = int(f.get("size", 0))
    fs_id = str(f.get("fs_id", ""))
    dur = f.get("duration", 0)
    icon = file_icon(name)
    thumb = f.get("thumbs", {}).get("url3") or f.get("thumbs", {}).get("url2")

    txt = (
        f"{icon} **{name}**\n\n"
        f"📦 Size: `{humanbytes(size)}`\n"
        f"🔑 MD5: `{f.get('md5', 'N/A')}`\n"
    )
    if dur:
        txt += f"⏱ Duration: `{fmt_duration(dur)}`\n"

    # Base callback prefix
    base = f"{surl}|{fs_id}|{uk or ''}|{share_id or ''}"

    # Video file — quality selection dikhao
    if is_video(name):
        txt += "\n\n**🎬 Quality Select Karo:**"
        btns = [
            # Row 1: 1080p + 720p
            [
                InlineKeyboardButton("🎞 1080p", callback_data=f"qdl|M3U8_AUTO_1080|{base}"),
                InlineKeyboardButton("🎞 720p",  callback_data=f"qdl|M3U8_FLV_264_720|{base}"),
            ],
            # Row 2: 480p + 240p
            [
                InlineKeyboardButton("🎞 480p",  callback_data=f"qdl|M3U8_FLV_264_480|{base}"),
                InlineKeyboardButton("🎞 240p",  callback_data=f"qdl|M3U8_FLV_264_360|{base}"),
            ],
            # Row 3: Auto (Best) — full width
            [
                InlineKeyboardButton("⚡ Auto (Best)", callback_data=f"qdl|AUTO|{base}"),
            ],
            # Row 4: Original — full width
            [
                InlineKeyboardButton("💿 Original", callback_data=f"qdl|DIRECT|{base}"),
            ],
            # Row 5: Rename
            [
                InlineKeyboardButton(
                    "✏️ Rename & Download",
                    callback_data=f"rename|{surl}|{fs_id}|{uk or ''}|{share_id or ''}"
                ),
            ],
            # Row 6: Cancel
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ]
    else:
        # Non-video — sirf download button
        btns = [
            [InlineKeyboardButton(
                f"📥 Download ({humanbytes(size)})",
                callback_data=f"qdl|DIRECT|{base}"
            )],
            [InlineKeyboardButton(
                "✏️ Rename & Download",
                callback_data=f"rename|{surl}|{fs_id}|{uk or ''}|{share_id or ''}"
            )],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ]

    markup = InlineKeyboardMarkup(btns)

    if thumb:
        try:
            await msg.reply_photo(thumb, caption=txt, reply_markup=markup)
            await msg.delete()
            return
        except Exception:
            pass
    await msg.edit(txt, reply_markup=markup)


async def _show_folder(msg: Message, files: list, surl: str, orig_url: str, uk, share_id):
    real_files = [f for f in files if str(f.get("isdir", "0")) == "0"]
    total_size = sum(int(f.get("size", 0)) for f in real_files)
    total = len(real_files)

    txt = (
        f"📁 **Folder — {total} files**\n"
        f"💾 Total Size: `{humanbytes(total_size)}`\n\n"
        "Tap a file to select quality & download:\n"
    )

    btns = []
    for f in real_files[:20]:
        name = f.get("server_filename", "?")
        size = int(f.get("size", 0))
        fs_id = str(f.get("fs_id", ""))
        icon = file_icon(name)
        btns.append([InlineKeyboardButton(
            f"{icon} {name[:35]}  ({humanbytes(size)})",
            callback_data=f"dl|{surl}|{fs_id}|{uk or ''}|{share_id or ''}"
        )])

    if total > 20:
        txt += f"\n_Showing 20/{total} files_"

    if 1 < total <= 10:
        all_ids = "|".join(str(f.get("fs_id", "")) for f in real_files)
        btns.append([InlineKeyboardButton(
            f"📦 Download All ({total} files)",
            callback_data=f"dlall|{surl}|{all_ids}|{uk or ''}|{share_id or ''}"
        )])

    btns.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])
    await msg.edit(txt, reply_markup=InlineKeyboardMarkup(btns))


# ─────────────────────────────────────────────────────────────────────────────
#  QUALITY DOWNLOAD CALLBACK — "qdl|QUALITY|surl|fs_id|uk|share_id"
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_callback_query(filters.regex(r"^qdl\|"))
async def cb_quality_download(client: Client, cq: CallbackQuery):
    parts = cq.data.split("|")
    # qdl | QUALITY | surl | fs_id | uk | share_id
    if len(parts) < 4:
        return await cq.answer("❌ Invalid data!", show_alert=True)

    quality   = parts[1]
    surl      = parts[2]
    fs_id     = parts[3]
    uk_str    = parts[4] if len(parts) > 4 else ""
    sid_str   = parts[5] if len(parts) > 5 else ""
    uk        = int(uk_str) if uk_str else None
    share_id  = int(sid_str) if sid_str else None

    quality_label = QUALITY_LABELS.get(quality, quality)
    await cq.answer(f"⏳ Starting {quality_label}...")

    user_id = cq.from_user.id

    if await check_banned(cq):
        return

    _limit = get_user_limit(user_id)
    if len(active_tasks.get(user_id, set())) >= _limit:
        return await cq.answer(
            f"⚠️ Too many active downloads! Max {_limit}. Use /cancel first.",
            show_alert=True
        )

    msg = cq.message
    task = asyncio.current_task()
    ev = asyncio.Event()
    cancel_events[user_id] = ev
    active_tasks[user_id].add(task)

    dl_path = None
    try:
        await msg.edit(f"⏳ **Preparing {quality_label}...**")
        api = await get_api()

        data = await db.cache_get(surl) or await api.get_file_list(surl)
        files = data.get("list", [])
        file_info = next((f for f in files if str(f.get("fs_id")) == str(fs_id)), {})
        name = file_info.get("server_filename", "file")
        size = int(file_info.get("size", 0))

        # ── Resolve download URL based on selected quality ────────────────────
        if quality == "DIRECT":
            # Direct download — no HLS
            dl_url, is_m3u8, qlabel = await api.resolve_download(
                surl, file_info, uk, share_id, force_direct=True
            )
        elif quality == "AUTO":
            # Auto best quality
            dl_url, is_m3u8, qlabel = await api.resolve_download(
                surl, file_info, uk, share_id
            )
        else:
            # Specific quality
            dl_url, is_m3u8, qlabel = await api.resolve_download(
                surl, file_info, uk, share_id, quality=quality
            )

        if not dl_url:
            await msg.edit(
                f"❌ **{quality_label} not available!**\n\n"
                "Try a different quality or Original."
            )
            return

        # ── Download ──────────────────────────────────────────────────────────
        dl_path = f"{Config.DOWNLOAD_DIR}/{user_id}_{int(time.time())}_{name}"
        dl_start = time.time()
        await msg.edit(
            f"📥 **Downloading...**\n\n"
            f"📄 `{name}`\n"
            f"🎬 Quality: `{quality_label}`"
        )
        await api.download(
            dl_url, dl_path,
            is_m3u8=is_m3u8,
            progress=make_progress(msg, "dl", name, dl_start),
            cancel_event=ev,
        )
        await api.close()
        dl_time = int(time.time() - dl_start)

        actual_size = os.path.getsize(dl_path)

        # ── Upload ────────────────────────────────────────────────────────────
        target_chat = await db.get_target_chat(user_id) or msg.chat.id
        await msg.edit(f"📤 **Uploading...**\n\n📄 `{name}`")

        bot_me = await client.get_me()
        u = cq.from_user

        def fmt_hms(s):
            h, r = divmod(int(s), 3600)
            m, sec = divmod(r, 60)
            return f"{h}:{m:02d}:{sec:02d}"

        up_start = time.time()
        sent_list = await send_file_or_split(
            client, target_chat, dl_path, file_info,
            caption="uploading...", msg_status=msg, uploader_kuri=kuri
        )
        sent = sent_list[-1] if sent_list else None
        up_time = int(time.time() - up_start)

        dur = file_info.get("duration", 0)
        auto_del_min = Config.AUTO_DELETE // 60 if Config.AUTO_DELETE else 0
        caption = (
            f"{file_icon(name)} **{name}**\n\n"
            f"📦 **Size:** `{humanbytes(actual_size)}`\n"
            f"🎬 **Quality:** `{quality_label}`\n"
        )
        if dur:
            caption += f"⏱ **Duration:** `{fmt_duration(dur)}`\n"
        caption += (
            f"⬇️ **Downloaded in:** `{fmt_hms(dl_time)}`\n"
            f"⬆️ **Uploaded in:** `{fmt_hms(up_time)}`\n"
            f"👤 **Uploaded by:** `{u.id}`\n\n"
        )
        if auto_del_min:
            caption += (
                f"⚠️ Auto-delete in `{auto_del_min} minutes`.\n"
                f"📨 Forward to save permanently.\n\n"
            )
        caption += f"❤️ Powered by @{bot_me.username}"

        try:
            await sent.edit_caption(caption)
        except Exception:
            pass

        if Config.LOG_CHANNEL:
            try:
                await client.send_message(
                    Config.LOG_CHANNEL,
                    f"📥 **New Download**\n"
                    f"👤 [{u.first_name}](tg://user?id={u.id}) `{u.id}`\n"
                    f"📄 `{name}`\n"
                    f"🎬 `{quality_label}`\n"
                    f"📦 `{humanbytes(actual_size)}`"
                )
            except Exception:
                pass

        await db.add_download(user_id, name, actual_size)

        if target_chat != msg.chat.id:
            try:
                chat = await client.get_chat(target_chat)
                await msg.edit(f"✅ **Sent to {chat.title}!**\n📄 `{name}`")
            except Exception:
                await msg.edit(f"✅ **Sent to** `{target_chat}`!")
        else:
            await msg.delete()

        if Config.AUTO_DELETE and sent:
            asyncio.create_task(_auto_delete(sent, Config.AUTO_DELETE))

    except asyncio.CancelledError:
        await msg.edit("⛔ **Download cancelled.**")
    except Exception as e:
        await msg.edit(f"❌ **Error:** `{e}`")
    finally:
        active_tasks[user_id].discard(task)
        cancel_events.pop(user_id, None)
        if dl_path and os.path.exists(dl_path):
            os.remove(dl_path)


# ─────────────────────────────────────────────────────────────────────────────
#  OLD DL CALLBACK — folder se file select karne par quality dikhao
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_callback_query(filters.regex(r"^dl\|"))
async def cb_download(client: Client, cq: CallbackQuery):
    """Folder file tap — quality selection dikhao"""
    await cq.answer()
    parts = cq.data.split("|")
    _, surl, fs_id = parts[0], parts[1], parts[2]
    uk_str = parts[3] if len(parts) > 3 else ""
    sid_str = parts[4] if len(parts) > 4 else ""
    uk = int(uk_str) if uk_str else None
    share_id = int(sid_str) if sid_str else None

    if await check_banned(cq):
        return

    msg = cq.message

    try:
        api = await get_api()
        data = await db.cache_get(surl) or await api.get_file_list(surl)
        await api.close()
        files = data.get("list", [])
        file_info = next((f for f in files if str(f.get("fs_id")) == str(fs_id)), {})
        name = file_info.get("server_filename", "?")
        size = int(file_info.get("size", 0))
        dur = file_info.get("duration", 0)
        icon = file_icon(name)

        txt = (
            f"{icon} **{name}**\n\n"
            f"📦 Size: `{humanbytes(size)}`\n"
        )
        if dur:
            txt += f"⏱ Duration: `{fmt_duration(dur)}`\n"

        base = f"{surl}|{fs_id}|{uk or ''}|{share_id or ''}"

        if is_video(name):
            txt += "\n\n**🎬 Quality Select Karo:**"
            btns = [
                [
                    InlineKeyboardButton("🎞 1080p", callback_data=f"qdl|M3U8_AUTO_1080|{base}"),
                    InlineKeyboardButton("🎞 720p",  callback_data=f"qdl|M3U8_FLV_264_720|{base}"),
                ],
                [
                    InlineKeyboardButton("🎞 480p",  callback_data=f"qdl|M3U8_FLV_264_480|{base}"),
                    InlineKeyboardButton("🎞 240p",  callback_data=f"qdl|M3U8_FLV_264_360|{base}"),
                ],
                [InlineKeyboardButton("⚡ Auto (Best)", callback_data=f"qdl|AUTO|{base}")],
                [InlineKeyboardButton("💿 Original",    callback_data=f"qdl|DIRECT|{base}")],
                [InlineKeyboardButton("◀️ Back", callback_data="cancel")],
            ]
        else:
            btns = [
                [InlineKeyboardButton(
                    f"📥 Download ({humanbytes(size)})",
                    callback_data=f"qdl|DIRECT|{base}"
                )],
                [InlineKeyboardButton("◀️ Back", callback_data="cancel")],
            ]

        await msg.edit(txt, reply_markup=InlineKeyboardMarkup(btns))

    except Exception as e:
        await msg.edit(f"❌ Error: `{e}`")


# ─────────────────────────────────────────────────────────────────────────────
#  DOWNLOAD ALL CALLBACK
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_callback_query(filters.regex(r"^dlall\|"))
async def cb_download_all(client: Client, cq: CallbackQuery):
    await cq.answer("📦 Starting bulk download...")
    parts = cq.data.split("|")
    surl = parts[1]
    fs_ids = parts[2].split("|") if parts[2] else []
    uk_str = parts[3] if len(parts) > 3 else ""
    sid_str = parts[4] if len(parts) > 4 else ""

    if not fs_ids:
        return await cq.answer("❌ No files found!", show_alert=True)

    user_id = cq.from_user.id
    if await check_banned(cq):
        return

    msg = cq.message
    await msg.edit(f"📦 **Bulk Download Started!**\nDownloading {len(fs_ids)} files...")

    api = await get_api()
    data = await db.cache_get(surl) or await api.get_file_list(surl)
    files = data.get("list", [])
    target_chat = await db.get_target_chat(user_id) or msg.chat.id
    bot_me = await client.get_me()

    success, failed = 0, 0
    for i, fid in enumerate(fs_ids, 1):
        file_info = next((f for f in files if str(f.get("fs_id")) == str(fid)), {})
        name = file_info.get("server_filename", f"file_{fid}")
        size = int(file_info.get("size", 0))
        uk = int(uk_str) if uk_str else None
        share_id = int(sid_str) if sid_str else None
        dl_path = f"{Config.DOWNLOAD_DIR}/{user_id}_{int(time.time())}_{name}"

        try:
            await msg.edit(
                f"📦 **Bulk Download** ({i}/{len(fs_ids)})\n\n"
                f"📄 `{name}`\n"
                f"📥 Downloading..."
            )
            # Auto quality for bulk
            dl_url, is_m3u8, quality = await api.resolve_download(
                surl, file_info, uk, share_id
            )
            if not dl_url:
                failed += 1
                continue

            await api.download(dl_url, dl_path, is_m3u8=is_m3u8)
            actual_size = os.path.getsize(dl_path)
            uploader = client if actual_size <= 2 * 1024**3 else (kuri or client)
            caption = (
                f"{file_icon(name)} **{name}**\n"
                f"📦 `{humanbytes(actual_size)}`\n"
                f"🤖 @{bot_me.username}"
            )
            sent = await send_file(uploader, target_chat, dl_path, file_info, caption)
            await db.add_download(user_id, name, actual_size)
            if Config.AUTO_DELETE and sent:
                asyncio.create_task(_auto_delete(sent, Config.AUTO_DELETE))
            success += 1
        except Exception:
            failed += 1
        finally:
            if os.path.exists(dl_path):
                os.remove(dl_path)

    await api.close()
    await msg.edit(
        f"✅ **Bulk Download Done!**\n\n"
        f"✅ Success: `{success}`\n"
        f"❌ Failed: `{failed}`"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  RENAME CALLBACK
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_callback_query(filters.regex(r"^rename\|"))
async def cb_rename(client: Client, cq: CallbackQuery):
    await cq.answer("✏️ Send new filename...")
    parts = cq.data.split("|")
    _, surl, fs_id = parts[0], parts[1], parts[2]
    uk_str = parts[3] if len(parts) > 3 else ""
    sid_str = parts[4] if len(parts) > 4 else ""
    uk = int(uk_str) if uk_str else None
    share_id = int(sid_str) if sid_str else None

    user_id = cq.from_user.id
    msg = cq.message

    rename_pending[user_id] = {
        "surl": surl, "fs_id": fs_id,
        "uk": uk, "share_id": share_id,
        "msg": msg,
    }

    await msg.edit(
        "✏️ **Rename File**\n\n"
        "Send the **new filename** (with extension):\n"
        "_Example: My Movie 2024.mkv_\n\n"
        "Or /cancel to cancel."
    )


@bot.on_message(filters.text & filters.private)
async def handle_rename_input(client: Client, msg: Message):
    user_id = msg.from_user.id
    if user_id not in rename_pending:
        return

    new_name = msg.text.strip()
    if new_name.lower() in ("/cancel", "cancel"):
        rename_pending.pop(user_id, None)
        return await msg.reply("❌ Rename cancelled.")

    state = rename_pending.pop(user_id)
    surl = state["surl"]
    fs_id = state["fs_id"]
    uk = state["uk"]
    share_id = state["share_id"]

    status = await msg.reply(f"🔍 **Fetching file...**\n✏️ Rename to: `{new_name}`")

    try:
        data = await db.cache_get(surl)
        if not data:
            api = await get_api()
            data = await api.get_file_list(surl)
            await api.close()

        files = data.get("list", [])
        file_info = next((f for f in files if str(f.get("fs_id")) == str(fs_id)), {})
        if not file_info:
            return await status.edit("❌ File info not found!")

        file_info = dict(file_info)
        orig_name = file_info.get("server_filename", "?")
        file_info["server_filename"] = new_name

        api = await get_api()
        dl_url, is_m3u8, quality = await api.resolve_download(surl, file_info, uk, share_id)
        if not dl_url:
            return await status.edit("❌ Could not get download URL!")

        dl_path = f"{Config.DOWNLOAD_DIR}/{user_id}_{int(time.time())}_{new_name}"
        dl_start = time.time()
        await status.edit(f"📥 **Downloading...**\n\n📄 `{new_name}`")

        ev = asyncio.Event()
        cancel_events[user_id] = ev
        task = asyncio.current_task()
        active_tasks[user_id].add(task)

        try:
            await api.download(
                dl_url, dl_path, is_m3u8=is_m3u8,
                progress=make_progress(status, "dl", new_name, dl_start),
                cancel_event=ev,
            )
            await api.close()
            dl_time = int(time.time() - dl_start)
            actual_size = os.path.getsize(dl_path)

            target_chat = await db.get_target_chat(user_id) or msg.chat.id
            bot_me = await client.get_me()

            up_start = time.time()
            sent_list = await send_file_or_split(
                client, target_chat, dl_path, file_info,
                caption="uploading...", msg_status=status, uploader_kuri=kuri
            )
            sent = sent_list[-1] if sent_list else None
            up_time = int(time.time() - up_start)

            def fmt_hms(s):
                h, r = divmod(int(s), 3600)
                m, sec = divmod(r, 60)
                return f"{h}:{m:02d}:{sec:02d}"

            caption = (
                f"{file_icon(new_name)} **{new_name}**\n\n"
                f"📦 **Size:** `{humanbytes(actual_size)}`\n"
                f"✏️ **Renamed from:** `{orig_name}`\n"
                f"⬇️ **Downloaded in:** `{fmt_hms(dl_time)}`\n"
                f"⬆️ **Uploaded in:** `{fmt_hms(up_time)}`\n"
                f"👤 **By:** `{user_id}`\n\n"
                f"❤️ Powered by @{bot_me.username}"
            )
            if sent:
                try:
                    await sent.edit_caption(caption)
                except Exception:
                    pass

            await db.add_download(user_id, new_name, actual_size)
            await status.delete()

        finally:
            active_tasks[user_id].discard(task)
            if os.path.exists(dl_path):
                os.remove(dl_path)

    except asyncio.CancelledError:
        await status.edit("⛔ Cancelled.")
    except Exception as e:
        await status.edit(f"❌ Error: `{e}`")


@bot.on_callback_query(filters.regex("^cancel$"))
async def cb_cancel(_, cq: CallbackQuery):
    await cq.message.delete()
    await cq.answer("Cancelled!")


@bot.on_callback_query(filters.regex("^help$"))
async def cb_help(_, cq: CallbackQuery):
    await cq.answer()
    await cq.message.reply("📖 Send me a TeraBox link!\nUse /help for full guide.")


# ─────────────────────────────────────────────────────────────────────────────
#  AUTO-DELETE
# ─────────────────────────────────────────────────────────────────────────────

async def _auto_delete(msg: Message, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  PREMIUM COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.command("addpremium") & filters.user(Config.ADMIN_IDS))
async def cmd_add_premium(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.reply("**Usage:** `/addpremium <user_id>`")
    try:
        uid = int(parts[1])
    except ValueError:
        return await msg.reply("❌ Invalid user ID!")
    premium_users.add(uid)
    await msg.reply(f"⭐ User `{uid}` is now **Premium**!\nLimit: `{Config.MAX_CONCURRENT_PREMIUM}`")


@bot.on_message(filters.command("rempremium") & filters.user(Config.ADMIN_IDS))
async def cmd_rem_premium(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.reply("**Usage:** `/rempremium <user_id>`")
    try:
        uid = int(parts[1])
    except ValueError:
        return await msg.reply("❌ Invalid user ID!")
    premium_users.discard(uid)
    await msg.reply(f"✅ User `{uid}` removed from Premium.")


@bot.on_message(filters.command("mylimit") & filters.private)
async def cmd_my_limit(_, msg: Message):
    uid = msg.from_user.id
    limit = get_user_limit(uid)
    status = "⭐ Premium" if uid in premium_users or uid in Config.ADMIN_IDS else "👤 Free"
    active = len(active_tasks.get(uid, set()))
    await msg.reply(
        f"**Your Download Limit**\n\n"
        f"🏷 Status: **{status}**\n"
        f"⚡ Parallel limit: `{limit}`\n"
        f"📥 Currently active: `{active}`"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  CDN CACHE COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.command("cdncache") & filters.user(Config.ADMIN_IDS))
async def cmd_cdn_cache(_, msg: Message):
    from terabox import _cdn_cache, CDN_CACHE_TTL, CDN_BACKENDS
    active = sum(
        1 for _, (__, ts) in _cdn_cache.items()
        if time.time() - ts < CDN_CACHE_TTL
    )
    txt = (
        f"🌐 **CDN Cache Stats**\n\n"
        f"📦 Cached URLs: `{active}`\n"
        f"⏱ TTL: `{CDN_CACHE_TTL // 60} minutes`\n\n"
        f"**CDN Backends:**\n"
    )
    for i, cdn in enumerate(CDN_BACKENDS, 1):
        txt += f"`{i}.` `{cdn}`\n"
    await msg.reply(txt)


@bot.on_message(filters.command("clearcache") & filters.user(Config.ADMIN_IDS))
async def cmd_clear_cache(_, msg: Message):
    from terabox import _cdn_cache
    count = len(_cdn_cache)
    _cdn_cache.clear()
    await msg.reply(f"🗑 Cleared `{count}` cached CDN URLs.")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    await db.connect()
    await bot.start()
    if kuri:
        await kuri.start()
        print("✅ Kurigram (4GB) ready!")
    me = await bot.get_me()
    print(f"✅ Bot started: @{me.username}")

    if Config.LOG_CHANNEL:
        try:
            await bot.send_message(Config.LOG_CHANNEL, "🚀 **TeraBox Bot v5.1 started!**")
        except Exception:
            pass

    await idle()
    await bot.stop()
    if kuri:
        await kuri.stop()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
