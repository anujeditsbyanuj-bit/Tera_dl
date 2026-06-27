import os


class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    API_ID           = int(os.getenv("API_ID", "37476811"))
    API_HASH         = os.getenv("API_HASH", "7aa60670b871050820086c6267371ee6")
    BOT_TOKEN        = os.getenv("BOT_TOKEN", "8741784728:AAHN0kLZpFhJgQgIazAql9TQrwMFqPsM8fo")
    SESSION_STRING   = os.getenv("SESSION_STRING", "")   # Kurigram 4GB uploads

    # ── MongoDB ───────────────────────────────────────────────────────────────
    MONGO_URI        = os.getenv("MONGO_URI", "mongodb+srv://Anujedit:Anujedit@cluster0.7cs2nhd.mongodb.net/?appName=Cluster0")
    DB_NAME          = os.getenv("DB_NAME", "terabox_bot")

    # ── TeraBox cookie (ndus) — fallback, also updateable via /addcookie ─────
    NDUS_COOKIE      = os.getenv("NDUS_COOKIE", "YfM5cX8peHuiSPsOc57-6f_BhA7POchpE3H2Rz6K")

    # ── Bot settings ──────────────────────────────────────────────────────────
    ADMIN_IDS        = list(map(int, os.getenv("ADMIN_IDS", "8730393744").split()))
    LOG_CHANNEL      = int(os.getenv("LOG_CHANNEL", "-1003824246703"))
    UPDATE_CHANNEL   = os.getenv("UPDATE_CHANNEL", "-1003824246703")
    SUPPORT_GROUP    = os.getenv("SUPPORT_GROUP", "-1003824246703")

    # Force join channel (leave blank to disable)
    FORCE_JOIN       = os.getenv("FORCE_JOIN", "")       # e.g. "mychannel"

    # Auto delete after X seconds (default 1 hour). 0 = disabled
    AUTO_DELETE      = int(os.getenv("AUTO_DELETE", str(60 * 60)))

    # Max concurrent downloads per user (10 free, premium can go higher)
    MAX_CONCURRENT         = int(os.getenv("MAX_CONCURRENT", "10"))
    MAX_CONCURRENT_PREMIUM = int(os.getenv("MAX_CONCURRENT_PREMIUM", "25"))

    DOWNLOAD_DIR     = "/tmp/terabox"
