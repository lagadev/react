import json
import re
import os
import asyncio
import logging
import random
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
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reaction-api")

ACCOUNT_FILE = "account.json"

# 🔥 Multiple Positive Reactions
POSITIVE_REACTIONS = [
    "❤️",
    "👍",
    "🔥",
    "🥰",
    "👏",
    "😁",
    "🎉",
    "⚡",
    "😍",
    "💯",
]

MAX_RETRIES = 3


def load_accounts() -> List[Dict[str, Any]]:
    if not os.path.exists(ACCOUNT_FILE):
        logger.error(f"Account file '{ACCOUNT_FILE}' not found!")
        return []

    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    accounts = data.get("accounts", [])
    logger.info(f"Loaded {len(accounts)} account(s)")
    return accounts


def parse_telegram_link(link: str) -> Optional[Dict[str, Any]]:
    pattern_public = r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)"
    match = re.match(pattern_public, link.strip())

    if match:
        return {
            "username": match.group(1),
            "message_id": int(match.group(2))
        }

    pattern_private = r"https?://t\.me/c/(\d+)/(\d+)"
    match = re.match(pattern_private, link.strip())

    if match:
        return {
            "chat_id": int(match.group(1)),
            "message_id": int(match.group(2))
        }

    return None


async def send_reaction(
    client: TelegramClient,
    peer: str,
    message_id: int,
    emoji: str
) -> bool:

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
        logger.warning(f"Flood wait {e.seconds}s")

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


async def react_to_message(
    link: str,
    emoji: Optional[str] = None,
    max_accounts: Optional[int] = None
) -> Dict[str, Any]:

    parsed = parse_telegram_link(link)

    if not parsed:
        return {
            "status": "error",
            "detail": "Invalid Telegram message link"
        }

    peer_str = parsed.get("username", parsed.get("chat_id"))
    message_id = parsed["message_id"]

    accounts = load_accounts()

    if not accounts:
        return {
            "status": "error",
            "detail": "No accounts in account.json"
        }

    if max_accounts is not None:
        accounts = accounts[:max_accounts]

    results = []
    success_count = 0
    fail_count = 0

    for idx, acc in enumerate(accounts):

        session_str = acc.get("session", "")
        api_id = acc.get("api_id")
        api_hash = acc.get("api_hash")

        if not all([session_str, api_id, api_hash]):
            results.append({
                "account_index": idx,
                "status": "skipped",
                "reason": "Missing fields"
            })
            continue

        # 🔥 Random Positive Reaction
        selected_emoji = emoji if emoji else random.choice(POSITIVE_REACTIONS)

        string_session = sessions.StringSession(session_str)

        client = TelegramClient(
            string_session,
            api_id,
            api_hash
        )

        try:
            await client.start()

            logger.info(
                f"Account {idx}: connected with reaction {selected_emoji}"
            )

            success = False

            for attempt in range(MAX_RETRIES):

                success = await send_reaction(
                    client,
                    peer_str,
                    message_id,
                    selected_emoji
                )

                if success:
                    break

                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep((attempt + 1) * 2)

            if success:
                success_count += 1

                results.append({
                    "account_index": idx,
                    "status": "success",
                    "reaction": selected_emoji
                })

            else:
                fail_count += 1

                results.append({
                    "account_index": idx,
                    "status": "failed",
                    "reaction": selected_emoji
                })

        except SessionPasswordNeededError:
            fail_count += 1

            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "2FA required"
            })

        except AuthKeyUnregisteredError:
            fail_count += 1

            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": "Session expired"
            })

        except Exception as e:
            fail_count += 1

            results.append({
                "account_index": idx,
                "status": "failed",
                "reason": str(e)
            })

        finally:
            await client.disconnect()

    return {
        "status": "completed",
        "link": link,
        "peer": str(peer_str),
        "message_id": message_id,
        "total_accounts": len(accounts),
        "success": success_count,
        "failed": fail_count,
        "results": results,
    }


# ─── FastAPI App ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):

    if not os.path.exists(ACCOUNT_FILE):
        logger.warning(f"'{ACCOUNT_FILE}' not found")

    else:
        logger.info(
            f"API ready with {len(load_accounts())} accounts"
        )

    yield


app = FastAPI(
    title="Telegram Reaction API",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():

    return {
        "message": "Telegram Reaction API running",
        "accounts": len(load_accounts()),
        "available_reactions": POSITIVE_REACTIONS
    }


@app.get("/get")
async def get_reaction(
    link: str = Query(...),
    emoji: Optional[str] = Query(None),
    max_accounts: Optional[int] = Query(None),
):

    if not link or not link.startswith("https://t.me/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram link"
        )

    try:
        result = await react_to_message(
            link,
            emoji,
            max_accounts
        )

    except Exception as e:
        logger.exception("Unexpected error")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    return JSONResponse(content=result)


@app.get("/accounts")
async def list_accounts():

    accounts = load_accounts()

    return {
        "total": len(accounts)
    }


if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000
    )
