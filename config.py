import os


class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    API_ID           = int(os.getenv("API_ID", "0"))
    API_HASH         = os.getenv("API_HASH", "")
    BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
    SESSION_STRING   = os.getenv("SESSION_STRING", "")   # Kurigram 4GB uploads

    # ── MongoDB ───────────────────────────────────────────────────────────────
    MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    DB_NAME          = os.getenv("DB_NAME", "terabox_bot")

    # ── TeraBox cookie (ndus) — fallback, also updateable via /addcookie ─────
    NDUS_COOKIE      = os.getenv("NDUS_COOKIE", "")

    # ── Bot settings ──────────────────────────────────────────────────────────
    ADMIN_IDS        = list(map(int, os.getenv("ADMIN_IDS", "0").split()))
    LOG_CHANNEL      = int(os.getenv("LOG_CHANNEL", "0"))
    UPDATE_CHANNEL   = os.getenv("UPDATE_CHANNEL", "")
    SUPPORT_GROUP    = os.getenv("SUPPORT_GROUP", "")

    # Force join channel (leave blank to disable)
    FORCE_JOIN       = os.getenv("FORCE_JOIN", "")       # e.g. "mychannel"

    # Auto delete after X seconds (default 1 hour). 0 = disabled
    AUTO_DELETE      = int(os.getenv("AUTO_DELETE", str(60 * 60)))

    # Max concurrent downloads per user (10 free, premium can go higher)
    MAX_CONCURRENT         = int(os.getenv("MAX_CONCURRENT", "10"))
    MAX_CONCURRENT_PREMIUM = int(os.getenv("MAX_CONCURRENT_PREMIUM", "25"))

    DOWNLOAD_DIR     = "/tmp/terabox"
