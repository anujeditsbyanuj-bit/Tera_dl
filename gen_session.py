"""
Pyrogram session string generate karne ke liye
Run: python3 gen_session.py
"""
from pyrogram import Client

api_id  = int(input("API_ID: "))
api_hash = input("API_HASH: ")

with Client("session_gen", api_id=api_id, api_hash=api_hash) as app:
    print("\n✅ SESSION_STRING:")
    print(app.export_session_string())
    print("\nCopy karo aur .env me paste karo!")
