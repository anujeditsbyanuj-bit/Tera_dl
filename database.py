"""
MongoDB handler — users, downloads, user settings, bans, queue
"""

from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
from config import Config


class Database:
    def __init__(self):
        self.client = None
        self.db = None

    async def connect(self):
        self.client = AsyncIOMotorClient(Config.MONGO_URI)
        self.db = self.client[Config.DB_NAME]
        await self.db.users.create_index("user_id", unique=True)
        await self.db.downloads.create_index([("user_id", 1), ("date", -1)])
        await self.db.cache.create_index("expires", expireAfterSeconds=0)
        await self.db.bans.create_index("user_id", unique=True)
        print("✅ MongoDB connected!")

    # ── Users ─────────────────────────────────────────────────────────────────
    async def add_user(self, user_id: int, name: str, username: str = None):
        await self.db.users.update_one(
            {"user_id": user_id},
            {
                "$set":          {"name": name, "username": username, "last_seen": datetime.utcnow()},
                "$setOnInsert":  {"user_id": user_id, "joined": datetime.utcnow(),
                                  "target_chat": None},
            },
            upsert=True
        )

    async def get_user(self, user_id: int) -> dict:
        return await self.db.users.find_one({"user_id": user_id}) or {}

    async def total_users(self) -> int:
        return await self.db.users.count_documents({})

    async def get_all_user_ids(self) -> list[int]:
        return [u["user_id"] async for u in self.db.users.find({}, {"user_id": 1})]

    # ── Ban / Unban ───────────────────────────────────────────────────────────
    async def ban_user(self, user_id: int, reason: str = ""):
        await self.db.bans.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "reason": reason, "date": datetime.utcnow()}},
            upsert=True
        )

    async def unban_user(self, user_id: int):
        await self.db.bans.delete_one({"user_id": user_id})

    async def is_banned(self, user_id: int) -> bool:
        return bool(await self.db.bans.find_one({"user_id": user_id}))

    async def get_ban_reason(self, user_id: int) -> str:
        doc = await self.db.bans.find_one({"user_id": user_id})
        return doc.get("reason", "") if doc else ""

    async def total_banned(self) -> int:
        return await self.db.bans.count_documents({})

    # ── User custom target chat ───────────────────────────────────────────────
    async def set_target_chat(self, user_id: int, chat_id: int):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"target_chat": chat_id}}
        )

    async def get_target_chat(self, user_id: int) -> int | None:
        u = await self.db.users.find_one({"user_id": user_id}, {"target_chat": 1})
        return u.get("target_chat") if u else None

    async def clear_target_chat(self, user_id: int):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"target_chat": None}}
        )

    # ── Downloads ─────────────────────────────────────────────────────────────
    async def add_download(self, user_id: int, filename: str, size: int):
        await self.db.downloads.insert_one({
            "user_id": user_id, "filename": filename,
            "size": size, "date": datetime.utcnow(),
        })

    async def total_downloads(self) -> int:
        return await self.db.downloads.count_documents({})

    async def user_dl_count(self, user_id: int) -> int:
        return await self.db.downloads.count_documents({"user_id": user_id})

    async def total_data_transferred(self) -> int:
        """Total bytes transferred across all downloads"""
        pipeline = [{"$group": {"_id": None, "total": {"$sum": "$size"}}}]
        async for doc in self.db.downloads.aggregate(pipeline):
            return doc.get("total", 0)
        return 0

    # ── Config (cookie, etc.) ─────────────────────────────────────────────────
    async def set_config(self, key: str, value: str):
        await self.db.config.update_one(
            {"key": key}, {"$set": {"key": key, "value": value}}, upsert=True
        )

    async def get_config(self, key: str) -> str | None:
        doc = await self.db.config.find_one({"key": key})
        return doc["value"] if doc else None

    # ── Cache (surl → file info, TTL 1h) ─────────────────────────────────────
    async def cache_set(self, surl: str, data: dict):
        await self.db.cache.update_one(
            {"surl": surl},
            {"$set": {"surl": surl, "data": data,
                      "expires": datetime.utcnow() + timedelta(hours=1)}},
            upsert=True
        )

    async def cache_get(self, surl: str) -> dict | None:
        doc = await self.db.cache.find_one({"surl": surl})
        if doc and doc["expires"] > datetime.utcnow():
            return doc["data"]
        return None

    async def close(self):
        if self.client:
            self.client.close()


db = Database()
