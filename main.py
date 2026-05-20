import json
import random
import asyncio
import time
import os
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from pyrogram import Client, errors
from pyrogram.enums import ChatAction

app = Flask(__name__)

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
REACTIONS = ["🔥", "❤️", "⚡", "😍", "👍", "👏", "😁", "😮"]
ACCOUNT_FILE = "account.json"
RATE_LIMIT_DELAY_MIN = 1.0   # seconds between reactions from same account
RATE_LIMIT_DELAY_MAX = 3.0
DAILY_REACTION_LIMIT_PER_ACCOUNT = 800  # safety buffer below Telegram's ~1000/day

clients = []
daily_counters = {}  # {client_idx: {"date": "YYYY-MM-DD", "count": int}}

# ─────────────────────────────────────────────
# LOAD ACCOUNTS
# ─────────────────────────────────────────────
def load_accounts():
    if not os.path.exists(ACCOUNT_FILE):
        print(f"❌ {ACCOUNT_FILE} not found!")
        sys.exit(1)

    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        accounts = json.load(f)
    
    # Normalise keys: some entries may use "session_string" instead of "session"
    for acc in accounts:
        if "session_string" in acc and "session" not in acc:
            acc["session"] = acc.pop("session_string")
    
    return accounts

# ─────────────────────────────────────────────
# INITIALISE CLIENTS (Pyrogram)
# ─────────────────────────────────────────────
async def start_clients():
    accounts = load_accounts()
    
    for idx, acc in enumerate(accounts):
        try:
            if not acc.get("connected", False):
                print(f"⏭️  Skipping (not connected): {acc.get('username', acc.get('phone', 'unknown'))}")
                continue

            # Use phone as session name fallback
            session_name = acc.get("phone", acc.get("username", f"account_{idx}"))
            # Sanitise for filesystem
            session_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_name)

            client = Client(
                name=session_name,
                api_id=int(acc["api_id"]),
                api_hash=acc["api_hash"],
                session_string=acc["session"],
                in_memory=True   # don't write .session files to disk
            )

            await client.start()
            
            me = await client.get_me()
            print(f"✅ Connected: @{me.username or me.first_name or acc['username']} (ID: {me.id})")
            
            clients.append(client)
            daily_counters[idx] = {"date": datetime.now().strftime("%Y-%m-%d"), "count": 0}

        except errors.Unauthorized:
            print(f"❌ Unauthorised / session expired: {acc.get('username', 'unknown')}")
        except Exception as e:
            print(f"❌ Error connecting {acc.get('username', 'unknown')}: {e}")

    print(f"\n🔗 Total connected clients: {len(clients)}")

# ─────────────────────────────────────────────
# PARSE TELEGRAM LINK
# ─────────────────────────────────────────────
def parse_link(link):
    link = link.strip()
    if "t.me/" not in link:
        return None, None

    data = link.split("t.me/")[1]
    parts = data.split("/")

    if len(parts) < 2:
        return None, None

    username = parts[0]
    # Remove query params from msg_id part
    msg_part = parts[1].split("?")[0]

    try:
        msg_id = int(msg_part)
    except ValueError:
        return None, None

    return username, msg_id

# ─────────────────────────────────────────────
# RESET DAILY COUNTERS IF NEW DAY
# ─────────────────────────────────────────────
def check_daily_reset():
    today = datetime.now().strftime("%Y-%m-%d")
    for idx in daily_counters:
        if daily_counters[idx]["date"] != today:
            daily_counters[idx] = {"date": today, "count": 0}

# ─────────────────────────────────────────────
# SEND REACTIONS
# ─────────────────────────────────────────────
async def react_to_post(channel, msg_id):
    check_daily_reset()
    success = 0
    failed = 0
    details = []

    for idx, client in enumerate(clients):
        try:
            # Check daily limit
            if daily_counters[idx]["count"] >= DAILY_REACTION_LIMIT_PER_ACCOUNT:
                msg = f"⏸️  Account {idx} reached daily limit ({DAILY_REACTION_LIMIT_PER_ACCOUNT})"
                print(msg)
                details.append({"account": idx, "status": "daily_limit_skipped", "reason": msg})
                continue

            emoji = random.choice(REACTIONS)

            # Resolve channel entity
            try:
                chat = await client.get_chat(f"@{channel}")
            except errors.UsernameNotOccupied:
                msg = f"❌ Channel @{channel} does not exist"
                print(msg)
                details.append({"account": idx, "status": "failed", "reason": msg})
                failed += 1
                continue
            except errors.UsernameInvalid:
                msg = f"❌ Invalid username: @{channel}"
                print(msg)
                details.append({"account": idx, "status": "failed", "reason": msg})
                failed += 1
                continue

            # Send reaction
            await client.send_reaction(
                chat_id=chat.id,
                message_id=msg_id,
                emoji=emoji,
                big=True
            )

            daily_counters[idx]["count"] += 1
            success += 1
            today_total = daily_counters[idx]["count"]
            print(f"✅ [Account {idx}] Reaction '{emoji}' → @{channel}/{msg_id} (today: {today_total})")
            details.append({
                "account": idx,
                "status": "success",
                "emoji": emoji,
                "daily_count": today_total
            })

            # Rate limit delay
            delay = random.uniform(RATE_LIMIT_DELAY_MIN, RATE_LIMIT_DELAY_MAX)
            await asyncio.sleep(delay)

        except errors.FloodWait as e:
            wait_secs = e.value
            msg = f"⏳ FloodWait on account {idx}: waiting {wait_secs}s"
            print(msg)
            details.append({"account": idx, "status": "flood_wait", "wait_seconds": wait_secs, "reason": msg})
            failed += 1
            # Still sleep a bit so we don't hammer
            await asyncio.sleep(min(wait_secs, 5))

        except errors.ReactionInvalid:
            msg = f"❌ Invalid reaction for account {idx} (maybe premium-only emoji)"
            print(msg)
            details.append({"account": idx, "status": "failed", "reason": msg})
            failed += 1

        except errors.MsgIdInvalid:
            msg = f"❌ Message ID {msg_id} not found in @{channel}"
            print(msg)
            details.append({"account": idx, "status": "failed", "reason": msg})
            failed += 1

        except errors.ChatWriteForbidden:
            msg = f"❌ Cannot react in @{channel} (no permission or restricted)"
            print(msg)
            details.append({"account": idx, "status": "failed", "reason": msg})
            failed += 1

        except Exception as e:
            msg = f"❌ Unexpected error on account {idx}: {e}"
            print(msg)
            details.append({"account": idx, "status": "error", "error": str(e)})
            failed += 1

    return {
        "success": success,
        "failed": failed,
        "total": len(clients),
        "details": details
    }

# ─────────────────────────────────────────────
# FLASK ENDPOINT
# ─────────────────────────────────────────────
@app.route("/get")
def api():
    link = request.args.get("link")

    if not link:
        return jsonify({
            "status": False,
            "message": "No Telegram link provided. Use ?link=https://t.me/channel/123"
        })

    channel, msg_id = parse_link(link)

    if not channel:
        return jsonify({
            "status": False,
            "message": "Invalid Telegram link format. Expected: https://t.me/username/message_id"
        })

    # Check if clients are loaded
    if len(clients) == 0:
        return jsonify({
            "status": False,
            "message": "No connected accounts available. Check account.json and restart."
        })

    try:
        # Run async reaction task in a fresh event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(react_to_post(channel, msg_id))
        loop.close()

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

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "connected_accounts": len(clients),
        "accounts_online": [f"Account {i}" for i in range(len(clients))],
        "daily_limits": f"{DAILY_REACTION_LIMIT_PER_ACCOUNT}/day per account"
    })

@app.route("/status")
def status():
    check_daily_reset()
    accounts_info = []
    for idx, client in enumerate(clients):
        try:
            me = client.get_me()
            accounts_info.append({
                "account_idx": idx,
                "username": me.username if hasattr(me, 'username') else "unknown",
                "today_reactions": daily_counters.get(idx, {}).get("count", 0),
                "daily_limit": DAILY_REACTION_LIMIT_PER_ACCOUNT
            })
        except:
            accounts_info.append({
                "account_idx": idx,
                "status": "error fetching info"
            })

    return jsonify({
        "total_accounts": len(clients),
        "accounts": accounts_info
    })

# ─────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Starting Telegram Reaction Bot...")
    print(f"📁 Loading accounts from {ACCOUNT_FILE}")

    # Initialise event loop and start clients
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_clients())

    # Start Flask server
    print("\n🌐 Starting API server on http://0.0.0.0:5000")
    print("📌 Endpoints:")
    print("   GET /         → Health check")
    print("   GET /status   → Account status & daily counters")
    print("   GET /get?link=https://t.me/channel/123 → React to a post\n")
    
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False   # prevents double-initialisation
    )
