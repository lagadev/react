"""
LagahReact — Telegram Reaction Sender API
Pyrogram-based. No API_ID/API_HASH needed in environment.
All credentials stored inside session strings.
"""

import os
import re
import json
import asyncio
import logging
from http.server import BaseHTTPRequestHandler
from pyrogram import Client
from pyrogram.errors import RPCError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lagahreact")

# ─── Load sessions from session.json ───────────────────────
SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "session.json")
SESSIONS = {}

if os.path.exists(SESSIONS_FILE):
    try:
        with open(SESSIONS_FILE, "r") as f:
            SESSIONS = json.load(f)
        logger.info(f"Loaded {len(SESSIONS)} session(s) from session.json")
    except Exception as e:
        logger.error(f"Failed to load session.json: {e}")
else:
    logger.warning("session.json not found! Create it with phone:session pairs.")

# ─── Telegram link parser ──────────────────────────────────
LINK_PATTERNS = [
    re.compile(r"t\.me/([a-zA-Z0-9_]{5,})/(\d+)"),
    re.compile(r"t\.me/c/(-?\d+)/(\d+)"),
    re.compile(r"t\.me/c/(\d+)/(\d+)"),
]


def parse_link(link: str):
    """Parse Telegram message link to get target and message ID."""
    for pattern in LINK_PATTERNS:
        match = pattern.search(link)
        if match:
            groups = match.groups()
            if link.find("/c/") != -1:
                peer = int(groups[0])
                # Private supergroups need -100 prefix
                if peer > 0 and str(peer)[0] != "-":
                    peer = int(f"-100{peer}")
                return peer, int(groups[1])
            else:
                return groups[0], int(groups[1])
    return None, None


async def send_reaction(session_str: str, target, msg_id: int, emoji: str):
    """
    Send a reaction to a Telegram message using Pyrogram.
    Pyrogram's export_session_string() contains API_ID/API_HASH internally.
    """
    app = Client(
        name="reaction_bot",
        session_string=session_str,
        in_memory=True
    )
    try:
        await app.start()
        me = await app.get_me()

        # Resolve peer
        if isinstance(target, int):
            chat = await app.get_chat(target)
        else:
            chat = await app.get_chat(target)

        # Send reaction using Pyrogram's message.react()
        # Pyrogram uses sendReaction under the hood
        await app.send_reaction(
            chat_id=chat.id,
            message_id=msg_id,
            emoji=emoji
        )

        await app.stop()
        return {
            "ok": True,
            "message": f"Reacted {emoji} as {me.first_name or me.username or 'Unknown'}",
            "account": me.first_name or str(me.id)
        }

    except RPCError as e:
        try:
            await app.stop()
        except:
            pass
        return {"ok": False, "error": f"Telegram error: {e.MESSAGE or str(e)}"}
    except Exception as e:
        try:
            await app.stop()
        except:
            pass
        return {"ok": False, "error": str(e)}


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            # Logging
            logger.info(f"Request: {self.path}")

            # Endpoint check
            if parsed.path not in ("/get", "/reaction", "/"):
                self.wfile.write(json.dumps({
                    "ok": False,
                    "error": "Invalid endpoint. Use /get?link=...&emoji=...&account=..."
                }).encode())
                return

            # ─── List accounts if no link provided ───
            link = params.get("link", [None])[0]
            if not link:
                if SESSIONS:
                    account_list = list(SESSIONS.keys())
                    self.wfile.write(json.dumps({
                        "ok": True,
                        "message": "Available accounts",
                        "accounts": account_list,
                        "usage": "/get?link=https://t.me/username/123&emoji=👍&account=+8801829507129"
                    }, indent=2).encode())
                else:
                    self.wfile.write(json.dumps({
                        "ok": False,
                        "error": "No sessions loaded. Check session.json"
                    }).encode())
                return

            # ─── Get emoji ───
            emoji = params.get("emoji", ["👍"])[0]

            # ─── Get account (phone number) ───
            account = params.get("account", [None])[0]
            session_str = None

            if account:
                # Specific account requested
                if account in SESSIONS:
                    session_str = SESSIONS[account]
                    logger.info(f"Using account: {account}")
                else:
                    self.wfile.write(json.dumps({
                        "ok": False,
                        "error": f"Account '{account}' not found. Available: {list(SESSIONS.keys())}"
                    }).encode())
                    return
            else:
                # Use first available session
                if SESSIONS:
                    first_key = list(SESSIONS.keys())[0]
                    session_str = SESSIONS[first_key]
                    account = first_key
                    logger.info(f"No account specified, using: {account}")
                else:
                    self.wfile.write(json.dumps({
                        "ok": False,
                        "error": "No sessions loaded"
                    }).encode())
                    return

            # ─── Parse link ───
            target, msg_id = parse_link(link)
            if not target or not msg_id:
                self.wfile.write(json.dumps({
                    "ok": False,
                    "error": f"Invalid Telegram link: {link}. Format: https://t.me/username/123"
                }).encode())
                return

            # ─── Send reaction ───
            logger.info(f"Sending {emoji} to {target}/{msg_id} using {account}")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(send_reaction(session_str, target, msg_id, emoji))
            loop.close()

            # ─── Build response ───
            response = {
                "ok": result["ok"],
                "emoji": emoji,
                "target": str(target),
                "message_id": msg_id,
                "link": link,
                "account_used": account,
            }
            if result.get("message"):
                response["message"] = result["message"]
            if result.get("error"):
                response["error"] = result["error"]

            self.wfile.write(json.dumps(response, indent=2, ensure_ascii=False).encode())

        except Exception as e:
            logger.error(f"Unhandled error: {e}")
            self.wfile.write(json.dumps({
                "ok": False,
                "error": f"Server error: {str(e)}"
            }).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
