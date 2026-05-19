import json
import random
import asyncio
from flask import Flask, request, jsonify
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

app = Flask(__name__)

REACTIONS = [
    "🔥",
    "❤️",
    "⚡",
    "😍",
    "👍",
    "👏",
    "😁",
    "😮"
]


# =========================================
# LOAD ACCOUNTS
# =========================================

with open("account.json", "r", encoding="utf-8") as f:
    ACCOUNTS = json.load(f)


# =========================================
# CREATE TELETHON CLIENTS
# =========================================

clients = []


async def start_clients():
    for acc in ACCOUNTS:
        try:
            if not acc.get("connected"):
                continue

            client = TelegramClient(
                StringSession(acc["session"]),
                int(acc["api_id"]),
                acc["api_hash"]
            )

            await client.connect()

            if await client.is_user_authorized():
                clients.append(client)
                print(f"Connected: {acc['username']}")
            else:
                print(f"Unauthorized: {acc['username']}")

        except Exception as e:
            print(f"Error {acc['username']}: {e}")


# =========================================
# PARSE TELEGRAM LINK
# =========================================


def parse_link(link):
    link = link.strip()

    if "t.me/" not in link:
        return None, None

    data = link.split("t.me/")[1]

    parts = data.split("/")

    if len(parts) < 2:
        return None, None

    username = parts[0]

    try:
        msg_id = int(parts[1])
    except:
        return None, None

    return username, msg_id


# =========================================
# SEND REACTIONS
# =========================================


async def react_to_post(channel, msg_id):
    success = 0
    failed = 0

    for client in clients:
        try:
            emoji = random.choice(REACTIONS)

            await client(
                SendReactionRequest(
                    peer=channel,
                    msg_id=msg_id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                    big=True
                )
            )

            success += 1
            print(f"Reaction sent: {emoji}")

            await asyncio.sleep(random.uniform(1, 3))

        except Exception as e:
            failed += 1
            print(e)

    return {
        "success": success,
        "failed": failed,
        "total": len(clients)
    }


# =========================================
# API ENDPOINT
# =========================================


@app.route("/get")
def api():
    link = request.args.get("link")

    if not link:
        return jsonify({
            "status": False,
            "message": "No Telegram link provided"
        })

    channel, msg_id = parse_link(link)

    if not channel:
        return jsonify({
            "status": False,
            "message": "Invalid Telegram link"
        })

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            react_to_post(channel, msg_id)
        )

        return jsonify({
            "status": True,
            "channel": channel,
            "message_id": msg_id,
            "result": result
        })

    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e)
        })


# =========================================
# START SERVER
# =========================================


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(start_clients())

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
