# 📦 TeraBox Downloader Bot v5.0

Fast, async TeraBox / Nephobox file downloader bot for Telegram.

## ✨ What's New in v5.1

| Feature | v5.0 | v5.1 |
|---------|------|------|
| Improved Quality Selection UI | ❌ | ✅ |
| 240p Quality Option | ❌ | ✅ |
| Auto (Best) — Full Width Button | ❌ | ✅ |
| Original Download Button | ❌ | ✅ |

## ✨ What's New in v5.0

| Feature | v4.4 | v5.0 |
|---------|------|------|
| `tera.backend.live` API | ❌ | ✅ (no cookie needed!) |
| Force Subscribe Guard | ❌ | ✅ |
| User Ban / Unban | ❌ | ✅ |
| Download Queue | ❌ | ✅ |
| `/cancel` active download | ❌ | ✅ |
| `/ping` latency check | ❌ | ✅ |
| Download All (folder) | ❌ | ✅ |
| Better thumbnails | ❌ | ✅ |
| Total data stats | ❌ | ✅ |
| Auto-delete (non-blocking) | ❌ | ✅ |

## 📂 Project Structure

```
terabox-bot/
├── bot.py           # Main bot — all handlers
├── terabox.py       # TeraBox API (backend + web fallback)
├── database.py      # MongoDB (Motor) — users, bans, cache
├── config.py        # Config from env vars
├── utils.py         # Helpers (humanbytes, progress, icons)
├── gen_session.py   # Generate Kurigram session string
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 🚀 Setup

### 1. Clone & configure

```bash
git clone https://github.com/youruser/terabox-bot
cd terabox-bot
cp .env.example .env
nano .env   # fill in your values
```

### 2. Get credentials

| Variable | Where to get |
|----------|-------------|
| `API_ID` / `API_HASH` | https://my.telegram.org |
| `BOT_TOKEN` | @BotFather |
| `MONGO_URI` | MongoDB Atlas or local |
| `NDUS_COOKIE` | terabox.app → F12 → Cookies → `ndus` (optional in v5) |
| `SESSION_STRING` | Run `python3 gen_session.py` |

### 3. Run

**Local:**
```bash
pip install -r requirements.txt
python3 bot.py
```

**Docker:**
```bash
docker-compose up -d
```

**Termux:**
```bash
pkg install python mongodb
pip install -r requirements.txt
python bot.py
```

**Railway / Render / Koyeb:**
- Add all `.env` variables in dashboard
- Deploy from GitHub

## 🤖 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Home menu |
| `/help` | Usage guide |
| `/setchat <id>` | Set upload target channel/group |
| `/clearchat` | Remove custom target |
| `/mychat` | Show current target |
| `/stats` | Bot statistics |
| `/cancel` | Cancel active download |
| `/ping` | Check bot latency |

**Admin only:**

| Command | Description |
|---------|-------------|
| `/addcookie ndus=xxx` | Update TeraBox cookie |
| `/broadcast` | Broadcast to all users |
| `/users` | Show user + download count |
| `/ban <id> [reason]` | Ban a user |
| `/unban <id>` | Unban a user |
| `/banned` | Show total banned count |

## 🌐 Supported Domains

- terabox.app
- nephobox.com
- freeterabox.com
- 1024terabox.com
- teraboxapp.com
- terabox.com
- terasharelink.com

## 📏 Upload Limits

| Size | Method |
|------|--------|
| < 50 MB | Bot API |
| 50 MB – 2 GB | Bot API (large) |
| 2 GB – 4 GB | Kurigram MTProto (needs SESSION_STRING) |

## 🔧 API Priority Chain

For every link, v5 tries in this order (fastest first):

1. `tera.backend.live/ch-android` → file list
2. `tera.backend.live/dlink` → direct download link
3. `tera.backend.live/stream-app` → M3U8 stream
4. `terabox.app/share/list` → fallback list
5. `terabox.app/share/streaming` → fallback M3U8

This means **most links work without any cookie** now!

## 🎬 Quality Selection UI

Video files ke liye bot ye buttons dikhata hai:

```
[🎞 1080p]  [🎞 720p]
[🎞 480p]   [🎞 240p]
[⚡ Auto (Best)]
[💿 Original]
[✏️ Rename & Download]
[❌ Cancel]
```

- **1080p / 720p / 480p / 240p** — Specific quality choose karo
- **Auto (Best)** — Best available quality automatically select hoti hai
- **Original** — TeraBox ka original file directly download
- **Rename & Download** — File ka naam change karke download karo

Ye UI dono jagah kaam karta hai:
- Direct file link paste karne par
- Folder ke andar se file select karne par

---
Dev: @anujedits76

## ⚡ Parallel Downloads & Premium System

### Free Users
- **10 parallel downloads** by default (`MAX_CONCURRENT=10` in `.env`)

### Premium Users
- **25 parallel downloads** (`MAX_CONCURRENT_PREMIUM=25` in `.env`)
- Admin adds/removes premium manually

### Premium Admin Commands

| Command | Description |
|---------|-------------|
| `/addpremium <user_id>` | Give user premium (25 parallel) |
| `/rempremium <user_id>` | Remove premium from user |
| `/premiumlist` | Show all premium users |

### User Commands

| Command | Description |
|---------|-------------|
| `/mylimit` | Check your current parallel download limit |

### `.env` settings
```env
MAX_CONCURRENT=10          # Free user limit
MAX_CONCURRENT_PREMIUM=25  # Premium user limit
```
