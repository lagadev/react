import json
import re
import os
import asyncio
import logging
from typing import Optional, Dict, List, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from telethon import TelegramClient
from telethon import functions, types
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    FloodWaitError,
    AuthKeyUnregisteredError,
    RPCError,
)

# ─── Configure Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("reaction-api")

# ─── Configuration ────────────────────────────────────────────────────
ACCOUNT_FILE = "account.json"  # The JSON file with all accounts
REACTION_EMOJI = "❤️"           # Default positive reaction (❤️ = heart)
SESSION_DIR = "sessions"        # Directory to store .session files locally
MAX_RETRIES = 3                 # Max retries per account on failure

# ─── Load Accounts ────────────────────────────────────────────────────
def load_accounts() -> List[Dict[str, Any]]:
    """Load account list from account.json."""
    if not os.path.exists(ACCOUNT_FILE):
        logger.error(f"Account file '{ACCOUNT_FILE}' not found!")
        return []
    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    accounts = data.get("accounts", [])
    logger.info(f"Loaded {len(accounts)} account(s) from {ACCOUNT_FILE}")
    return accounts


# ─── Parse Telegram Link ──────────────────────────────────────────────
def parse_telegram_link(link: str) -> Optional[Dict[str, Any]]:
    """
    Parse a Telegram message link and return peer info.
    Supports formats:
      - https://t.me/username/12345
      - https://t.me/c/1234567890/12345
      - https://t.me/devlagabio/119
    """
    # Pattern for public chats: https://t.me/{username}/{message_id}
    pattern_public = r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)"
    match = re.match(pattern_public, link.strip())
    if match:
        return {"username": match.group(1), "message_id": int(match.group(2))}

    # Pattern for private/supergroups: https://t.me/c/{chat_id}/{message_id}
    pattern_private = r"https?://t\.me/c/(\d+)/(\d+)"
    match = re.match(pattern_private, link.strip())
    if match:
        return {"chat_id": int(match.group(1)), "message_id": int(match.group(2))}

    return None


# ─── Send Reaction via One Account ────────────────────────────────────
async def send_reaction(
    client: TelegramClient,
    peer: str,
    message_id: int,
    emoji: str = REACTION_EMOJI,
) -> bool:
    """
    Send a positive reaction to a message using the given Telegram client.
    Returns True if successful, False otherwise.
    """
    try:
        await client(functions.messages.SendReactionRequest(
            peer=peer,
            msg_id=message_id,
            reaction=[types.ReactionEmoji(emoticon=emoji)],
            big=True,
            add_to_recent=True,
        ))
        return True
    except FloodWaitError as e:
        logger.warning(f"Flood wait: need to wait {e.seconds}s")
        # If flood is short, wait; otherwise skip
        if e.seconds <= 30:
            await asyncio.sleep(e.seconds)
            try:
                await client(functions.messages.SendReactionRequest(
                    peer=peer,
                    msg_id=message_id,
                    reaction=[types.ReactionEmoji(emoticon=emoji)],
                    big=True,
                    add_to_recent=True,
                ))
                return True
            except Exception:
                return False
        return False
    except Exception as e:
        logger.error(f"Reaction failed: {type(e).__name__}: {e}")
        return False


# ─── Process Message with All Accounts ────────────────────────────────
async def react_to_message(
    link: str,
    emoji: str = REACTION_EMOJI,
    max_accounts: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Parse the link, then use all (or up to max_accounts) accounts to react.
    Returns a summary dict.
    """
    # 1. Parse link
    parsed = parse_telegram_link(link)
    if not parsed:
        return {"status": "error", "detail": "Invalid Telegram message link format"}

    # 2. Determine peer string
    if "username" in parsed:
        peer_str = parsed["username"]
    else:
        peer_str = parsed["chat_id"]
    message_id = parsed["message_id"]

    logger.info(f"Target: peer={peer_str}, message_id={message_id}")

    # 3. Load accounts
    accounts = load_accounts()
    if not accounts:
        return {"status": "error", "detail": "No accounts found in account.json"}

    if max_accounts is not None:
        accounts = accounts[:max_accounts]

    # 4. Create session directory if needed
    os.makedirs(SESSION_DIR, exist_ok=True)

    results = []
    success_count = 0
    fail_count = 0

    # 5. Process with each account
    for idx, acc in enumerate(accounts):
        session_name = acc.get("session", "")
        api_id = acc.get("api_id")
        api_hash = acc.get("api_hash")

        if not all([session_name, api_id, api_hash]):
            logger.warning(f"Account {idx}: missing fields, skipping")
            results.append({
                "account_index": idx,
                "status": "skipped",
                "reason": "Missing session/api_id/api_hash",
            })
            continue

        # Session file path (we use a hash of the session string as filename)
        session_file = os.path.join(SESSION_DIR, f"acc_{idx}")

        client = TelegramClient(session_file, api_id, api_hash)

        try:
            await client.start()
            logger.info(f"Account {idx}: connected successfully")

            success = False
            for attempt in range(MAX_RETRIES):
                success = await send_reaction(client, peer_str, message_id, emoji)
                if success:
                    break
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 2
                    logger.info(f"Account {idx}: retrying in {wait}s...")
                    await asyncio.sleep(wait)

            if success:
                success_count += 1
                results.append({
                    "account_index": idx,
                    "status": "success",
                })
            else:
                fail_count += 1
                results.append({
                    "account_index": idx,
                    "status": "failed",
                })

        except SessionPasswordNeededError:
            logger.error(f"Account {idx}: 2FA password required!")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "2FA required - cannot proceed",
            })
        except AuthKeyUnregisteredError:
            logger.error(f"Account {idx}: session key invalid/expired")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "Session expired",
            })
        except Exception as e:
            logger.error(f"Account {idx}: connection error - {e}")
            fail_count += 1
            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": str(e),
            })
        finally:
            await client.disconnect()

    # 6. Return summary
    return {
        "status": "completed",
        "link": link,
        "peer": peer_str,
        "message_id": message_id,
        "emoji": emoji,
        "total_accounts": len(accounts),
        "success": success_count,
        "failed": fail_count,
        "results": results,
    }


# ─── FastAPI App ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify accounts file exists
    if not os.path.exists(ACCOUNT_FILE):
        logger.warning(
            f"'{ACCOUNT_FILE}' not found at startup. "
            "Create it with valid accounts before using the API."
        )
    else:
        accs = load_accounts()
        logger.info(f"API ready with {len(accs)} accounts loaded")
    yield
    # Shutdown: nothing special needed
    logger.info("API shutting down")


app = FastAPI(
    title="Telegram Reaction API",
    description="Send positive reactions to Telegram messages using multiple accounts",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "message": "Telegram Reaction API is running",
        "usage": "GET /get?link=https://t.me/username/12345",
        "accounts": len(load_accounts()),
    }


@app.get("/get")
async def get_reaction(
    link: str = Query(..., description="Telegram message link to react to"),
    emoji: str = Query(
        REACTION_EMOJI,
        description="Emoji to use as reaction (default: ❤️)",
    ),
    max_accounts: Optional[int] = Query(
        None,
        description="Maximum number of accounts to use (default: all)",
    ),
):
    """
    Send positive reactions to a Telegram message using all available accounts.

    Example:
      /get?link=https://t.me/devlagabio/119
      /get?link=https://t.me/devlagabio/119&emoji=👍
      /get?link=https://t.me/devlagabio/119&max_accounts=3
    """
    # Validate link
    if not link or not link.startswith("https://t.me/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid link. Must be a Telegram message link like https://t.me/username/12345",
        )

    # Parse first to catch errors early
    parsed = parse_telegram_link(link)
    if not parsed:
        raise HTTPException(
            status_code=400,
            detail="Could not parse the Telegram link. Expected format: https://t.me/username/message_id",
        )

    # Execute reactions
    try:
        result = await react_to_message(link, emoji, max_accounts)
    except Exception as e:
        logger.exception("Unexpected error during reaction processing")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

    # If all failed, still return 200 but with details
    return JSONResponse(content=result)


@app.get("/accounts")
async def list_accounts():
    """List how many accounts are configured (without exposing secrets)."""
    accounts = load_accounts()
    return {
        "total": len(accounts),
        "accounts": [
            {"index": i, "api_id": acc.get("api_id"), "has_session": bool(acc.get("session"))}
            for i, acc in enumerate(accounts)
        ],
    }


# ─── Direct Run (for local testing) ───────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
