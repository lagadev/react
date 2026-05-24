import json
import re
import os
import asyncio
import logging
from typing import Optional, Dict, List, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from telethon import TelegramClient, sessions
from telethon import functions, types
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    AuthKeyUnregisteredError,
    PhoneNumberUnconfirmedError,
)

# ─── Logging Setup ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("reaction-api")

# ─── Configuration ──────────────────────────────────────────────────
ACCOUNT_FILE = "account.json"

# All positive reaction emojis available on Telegram
POSITIVE_REACTIONS = [
    "👍",   # thumbs up
    "❤️",   # red heart
    "🔥",   # fire
    "🥰",   # smiling face with hearts
    "😍",   # heart eyes
    "🤩",   # star struck
    "🎉",   # party popper
    "💯",   # hundred points
    "😎",   # smiling face with sunglasses
    "👏",   # clapping hands
    "🙏",   # folded hands
    "💪",   # flexed biceps
    "✨",   # sparkles
    "⭐",   # star
    "🌈",   # rainbow
    "🎊",   # confetti ball
    "💥",   # collision
    "🫶",   # heart hands
    "😊",   # smiling face
    "🤗",   # hugging face
]

DEFAULT_EMOJI = "👍"
MAX_RETRIES = 2


# ─── Load Accounts ──────────────────────────────────────────────────
def load_accounts() -> List[Dict[str, Any]]:
    """Load account list from account.json file."""
    if not os.path.exists(ACCOUNT_FILE):
        logger.error(f"Account file '{ACCOUNT_FILE}' not found!")
        return []
    try:
        with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        accounts = data.get("accounts", [])
        logger.info(f"✅ Loaded {len(accounts)} account(s) from {ACCOUNT_FILE}")
        return accounts
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {ACCOUNT_FILE}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error loading accounts: {e}")
        return []


# ─── Parse Telegram Link ────────────────────────────────────────────
def parse_telegram_link(link: str) -> Optional[Dict[str, Any]]:
    """
    Parse a Telegram message link.
    Supports:
      - https://t.me/username/12345
      - https://t.me/c/1234567890/12345
      - https://t.me/devlagabio/119
    """
    link = link.strip()
    
    # Public chat: t.me/username/message_id
    pattern_public = r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)"
    match = re.match(pattern_public, link)
    if match:
        return {
            "type": "public",
            "username": match.group(1),
            "message_id": int(match.group(2))
        }

    # Private/supergroup: t.me/c/chat_id/message_id
    pattern_private = r"https?://t\.me/c/(\d+)/(\d+)"
    match = re.match(pattern_private, link)
    if match:
        return {
            "type": "private",
            "chat_id": int("-100" + match.group(1)) if not match.group(1).startswith("-100") else int(match.group(1)),
            "message_id": int(match.group(2))
        }

    return None


# ─── Send Positive Reaction ─────────────────────────────────────────
async def send_positive_reaction(
    client: TelegramClient,
    peer: str,
    message_id: int,
    emoji: str = DEFAULT_EMOJI,
) -> bool:
    """
    Send a positive reaction to a Telegram message.
    Uses SendReactionRequest with ReactionEmoji.
    """
    try:
        # First, get the entity to ensure we have proper input peer
        try:
            entity = await client.get_entity(peer)
        except Exception as e:
            logger.warning(f"Could not get entity for '{peer}': {e}")
            entity = peer  # fallback - use as string

        # Send the reaction using Telethon's raw API
        result = await client(functions.messages.SendReactionRequest(
            peer=entity,
            msg_id=message_id,
            reaction=[types.ReactionEmoji(emoticon=emoji)],
            big=True,
            add_to_recent=True,
        ))
        
        logger.info(f"✅ Reaction '{emoji}' sent successfully to message {message_id}")
        return True

    except FloodWaitError as e:
        logger.warning(f"⏳ Flood wait required: {e.seconds} seconds")
        if e.seconds <= 25:
            logger.info(f"Waiting {e.seconds}s for flood limit...")
            await asyncio.sleep(e.seconds)
            try:
                entity = await client.get_entity(peer)
                await client(functions.messages.SendReactionRequest(
                    peer=entity,
                    msg_id=message_id,
                    reaction=[types.ReactionEmoji(emoticon=emoji)],
                    big=True,
                    add_to_recent=True,
                ))
                logger.info(f"✅ Reaction sent after flood wait")
                return True
            except Exception as e2:
                logger.error(f"Still failed after flood wait: {e2}")
                return False
        else:
            logger.warning(f"Flood too long ({e.seconds}s), skipping this account")
            return False

    except Exception as e:
        error_name = type(e).__name__
        error_msg = str(e)
        logger.error(f"❌ Reaction failed ({error_name}): {error_msg}")
        return False


# ─── Main Reaction Logic ────────────────────────────────────────────
async def react_to_message(
    link: str,
    emoji: str = DEFAULT_EMOJI,
    max_accounts: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Core function: parse link, iterate through all accounts,
    send positive reaction from each account.
    """
    start_time = asyncio.get_event_loop().time()
    
    # Step 1: Parse the link
    parsed = parse_telegram_link(link)
    if not parsed:
        return {
            "status": "error",
            "detail": "Invalid Telegram message link format. Expected: https://t.me/username/message_id",
        }

    # Determine peer string
    if parsed["type"] == "public":
        peer_str = parsed["username"]
    else:
        peer_str = parsed["chat_id"]
    
    message_id = parsed["message_id"]
    logger.info(f"🎯 Target: peer={peer_str}, message_id={message_id}, emoji={emoji}")

    # Step 2: Validate emoji
    if emoji not in POSITIVE_REACTIONS:
        logger.warning(f"Emoji '{emoji}' not in known positive reactions list, but attempting anyway")

    # Step 3: Load accounts
    accounts = load_accounts()
    if not accounts:
        return {"status": "error", "detail": "No accounts found in account.json"}

    if max_accounts is not None and max_accounts > 0:
        accounts = accounts[:max_accounts]
        logger.info(f"Using {max_accounts} account(s) (limited by max_accounts parameter)")
    else:
        logger.info(f"Using all {len(accounts)} account(s)")

    # Step 4: Process each account
    results = []
    success_count = 0
    fail_count = 0
    already_reacted = 0

    for idx, acc in enumerate(accounts):
        session_str = acc.get("session", "")
        api_id = acc.get("api_id")
        api_hash = acc.get("api_hash")

        # Validate account fields
        if not session_str:
            logger.warning(f"Account {idx}: missing 'session' field, skipping")
            results.append({
                "account_index": idx,
                "status": "skipped",
                "reason": "Missing session string",
            })
            continue
        
        if not api_id or not api_hash:
            logger.warning(f"Account {idx}: missing api_id or api_hash, skipping")
            results.append({
                "account_index": idx,
                "status": "skipped",
                "reason": "Missing api_id or api_hash",
            })
            continue

        # Use StringSession (no filesystem writes - Vercel compatible!)
        try:
            string_session = sessions.StringSession(session_str)
            client = TelegramClient(string_session, api_id, api_hash)
        except Exception as e:
            logger.error(f"Account {idx}: invalid session string - {e}")
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": f"Invalid session string: {str(e)[:50]}",
            })
            fail_count += 1
            continue

        # Connect and send reaction
        try:
            logger.info(f"📡 Account {idx}: connecting...")
            await client.start()
            
            # Check if connected properly
            me = await client.get_me()
            logger.info(f"✅ Account {idx}: connected as {me.first_name or me.username or 'user'}")

            # Attempt to send reaction with retries
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                logger.info(f"Account {idx}: sending reaction (attempt {attempt}/{MAX_RETRIES})")
                success = await send_positive_reaction(client, peer_str, message_id, emoji)
                if success:
                    break
                if attempt < MAX_RETRIES:
                    wait_time = attempt * 3
                    logger.info(f"Account {idx}: retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)

            if success:
                success_count += 1
                results.append({
                    "account_index": idx,
                    "status": "success",
                    "user": me.first_name or "unknown",
                })
            else:
                fail_count += 1
                results.append({
                    "account_index": idx,
                    "status": "failed",
                    "reason": "All retry attempts exhausted",
                })

        except SessionPasswordNeededError:
            logger.error(f"Account {idx}: ❌ Two-factor authentication (2FA) is enabled!")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "2FA password required - account cannot be used automatically",
            })

        except AuthKeyUnregisteredError:
            logger.error(f"Account {idx}: ❌ Session expired/invalid. Need new login.")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "Session expired - generate a new session string",
            })

        except PhoneNumberUnconfirmedError:
            logger.error(f"Account {idx}: ❌ Phone number not confirmed.")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "Phone number unconfirmed",
            })

        except ConnectionError as e:
            logger.error(f"Account {idx}: ❌ Connection error - {e}")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": f"Connection error: {str(e)[:60]}",
            })

        except Exception as e:
            error_name = type(e).__name__
            logger.error(f"Account {idx}: ❌ {error_name} - {str(e)[:100]}")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": f"{error_name}: {str(e)[:80]}",
            })

        finally:
            try:
                await client.disconnect()
                logger.info(f"Account {idx}: disconnected")
            except Exception:
                pass

    # Step 5: Calculate timing
    elapsed_time = round(asyncio.get_event_loop().time() - start_time, 2)

    # Step 6: Build summary
    summary = {
        "status": "completed",
        "link": link,
        "peer": str(peer_str),
        "message_id": message_id,
        "emoji": emoji,
        "total_accounts": len(accounts),
        "success": success_count,
        "failed": fail_count,
        "already_reacted": already_reacted,
        "time_taken_seconds": elapsed_time,
        "results": results,
    }

    logger.info(
        f"📊 Summary: {success_count} success, {fail_count} failed, "
        f"{already_reacted} already reacted in {elapsed_time}s"
    )

    return summary


# ─── FastAPI Application ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    if not os.path.exists(ACCOUNT_FILE):
        logger.warning(
            f"⚠️  '{ACCOUNT_FILE}' not found! "
            "Create it with valid Telegram accounts before using the API."
        )
    else:
        accs = load_accounts()
        logger.info(
            f"🚀 API ready with {len(accs)} Telegram account(s). "
            f"Available positive reactions: {len(POSITIVE_REACTIONS)}"
        )
    yield
    # Shutdown
    logger.info("👋 API shutting down")


app = FastAPI(
    title="Telegram Positive Reaction API",
    description="""
    Send positive reactions to Telegram messages using multiple user accounts.
    
    ## Positive Reactions Available
    👍 ❤️ 🔥 🥰 😍 🤩 🎉 💯 😎 👏 🙏 💪 ✨ ⭐ 🌈 🎊 💥 🫶 😊 🤗
    
    ## Usage
    ```
    GET /get?link=https://t.me/username/12345
    GET /get?link=https://t.me/username/12345&emoji=🔥
    GET /get?link=https://t.me/username/12345&emoji=🔥&max_accounts=3
    ```
    """,
    version="2.0.0",
    lifespan=lifespan,
)


# ─── API Endpoints ────────────────────────────────────────────────────

@app.get("/")
async def root():
    """API root - shows status and available reactions."""
    accounts = load_accounts()
    return {
        "app": "Telegram Positive Reaction API",
        "version": "2.0.0",
        "status": "running",
        "accounts_configured": len(accounts),
        "available_reactions": POSITIVE_REACTIONS,
        "endpoints": {
            "send_reaction": "/get?link=<telegram_message_link>",
            "list_accounts": "/accounts",
            "help": "/help",
        },
        "examples": {
            "basic": "/get?link=https://t.me/devlagabio/119",
            "custom_emoji": "/get?link=https://t.me/devlagabio/119&emoji=🔥",
            "limit_accounts": "/get?link=https://t.me/devlagabio/119&emoji=❤️&max_accounts=3",
        },
    }


@app.get("/help")
async def help_endpoint():
    """Detailed help page."""
    return {
        "title": "📖 How to Use the Reaction API",
        "steps": [
            "1. Get a Telegram message link (public channel/group message)",
            "2. Send a GET request to /get with the link parameter",
            "3. The API will react from ALL configured accounts",
        ],
        "link_formats": [
            "https://t.me/username/12345",
            "https://t.me/c/1234567890/12345",
        ],
        "parameters": {
            "link": {
                "required": True,
                "description": "Full Telegram message URL",
                "example": "https://t.me/devlagabio/119",
            },
            "emoji": {
                "required": False,
                "default": "👍",
                "description": "Reaction emoji from positive list",
                "example": "🔥",
                "available": POSITIVE_REACTIONS,
            },
            "max_accounts": {
                "required": False,
                "default": "all",
                "description": "Limit how many accounts to use (1 = first account only)",
                "example": "3",
            },
        },
        "response_fields": {
            "status": "completed or error",
            "success": "Number of accounts that reacted successfully",
            "failed": "Number of accounts that failed",
            "time_taken_seconds": "Total time in seconds",
            "results": "Detailed per-account results",
        },
        "note": "All reactions are POSITIVE (👍, ❤️, 🔥 etc.). Use responsibly.",
    }


@app.get("/get")
async def get_reaction(
    link: str = Query(
        ...,
        description="Telegram message link to react to",
        example="https://t.me/devlagabio/119",
    ),
    emoji: str = Query(
        DEFAULT_EMOJI,
        description="Reaction emoji (positive only)",
        example="🔥",
    ),
    max_accounts: Optional[int] = Query(
        None,
        description="Maximum number of accounts to use (default: use all)",
        ge=1,
        le=100,
        example=5,
    ),
):
    """
    Send a positive reaction to a Telegram message.
    
    All configured accounts will react to the specified message.
    Returns a detailed report of which accounts succeeded or failed.
    """
    # Validate link
    if not link:
        raise HTTPException(status_code=400, detail="Missing 'link' parameter")
    
    if not link.startswith("https://t.me/") and not
