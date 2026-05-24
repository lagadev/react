import re
import json
import asyncio
import logging
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# POSITIVE reactions only - Telegram's standard positive reaction set
POSITIVE_REACTIONS = ["👍", "❤️", "🔥", "🎉", "🤩", "😁", "🥰", "👏", "🤯", "🚀", "💯", "🎊", "✨", "💪", "🫡", "😎"]


def load_sessions():
    """Load all accounts from session.json"""
    session_path = os.path.join(os.path.dirname(__file__), '..', 'session.json')
    if not os.path.exists(session_path):
        return []
    with open(session_path, 'r') as f:
        data = json.load(f)
    return data.get('accounts', [])


def parse_telegram_link(link: str):
    """Parse a Telegram message link into peer and message_id"""
    patterns = [
        r'(?:https?://)?t\.me/([^/]+)/(\d+)',
        r'(?:https?://)?telegram\.me/([^/]+)/(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1), int(match.group(2))
    return None, None


async def send_reaction(client, peer, msg_id, emoji):
    """Send a single reaction to a message"""
    try:
        await client(functions.messages.SendReactionRequest(
            peer=peer,
            msg_id=msg_id,
            reaction=[types.ReactionEmoji(emoticon=emoji)]
        ))
        return True, None
    except Exception as e:
        return False, str(e)


async def process_account(account, peer, msg_id, reaction_index):
    """Initialize account and send assigned reaction"""
    phone = account.get('phone', 'unknown')
    api_id = account.get('api_id')
    api_hash = account.get('api_hash')
    session_string = account.get('session_string')
    assigned_emoji = account.get('reaction') or POSITIVE_REACTIONS[reaction_index % len(POSITIVE_REACTIONS)]

    if not api_id or not api_hash:
        return {'phone': phone, 'status': 'error', 'error': 'Missing api_id/api_hash'}

    try:
        if session_string:
            client = TelegramClient(StringSession(session_string), int(api_id), api_hash)
        else:
            client = TelegramClient(f'session_{phone}', int(api_id), api_hash)

        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return {'phone': phone, 'status': 'error', 'error': 'Not authorized'}

        success, err = await send_reaction(client, peer, msg_id, assigned_emoji)
        await client.disconnect()

        if success:
            return {'phone': phone, 'status': 'success', 'reaction': assigned_emoji}
        else:
            return {'phone': phone, 'status': 'error', 'error': err}

    except Exception as e:
        return {'phone': phone, 'status': 'error', 'error': str(e)}


async def handle_reactions(link, emoji=None):
    """React to a message with all accounts — each gets a positive reaction"""
    peer, msg_id = parse_telegram_link(link)
    if not peer or not msg_id:
        return {'error': 'Invalid Telegram link. Use: https://t.me/username/123'}

    accounts = load_sessions()
    if not accounts:
        return {'error': 'No accounts in session.json'}

    # If a specific reaction is given, use the same for all
    # Otherwise distribute positive reactions across accounts
    if emoji:
        tasks = [process_account(acc, peer, msg_id, 0) for acc in accounts]
        # Override with the user-specified emoji
        for i, acc in enumerate(accounts):
            accounts[i] = {**acc, 'reaction': emoji}
        tasks = [process_account(accounts[i], peer, msg_id, 0) for i in range(len(accounts))]
    else:
        tasks = [process_account(acc, peer, msg_id, i) for i, acc in enumerate(accounts)]

    results = await asyncio.gather(*tasks)

    total = len(results)
    success = sum(1 for r in results if r['status'] == 'success')
    failed = total - success

    return {
        'status': 'completed',
        'total_accounts': total,
        'success': success,
        'failed': failed,
        'results': results
    }


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function"""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        link = params.get('link', [None])[0]
        if not link:
            self._respond(400, {
                'error': 'Missing "link" parameter',
                'usage': '/get?link=https://t.me/username/message_id',
                'example': '/get?link=https://t.me/devlagabio/119'
            })
            return

        emoji = params.get('reaction', [None])[0]

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(handle_reactions(link, emoji))
        finally:
            loop.close()

        self._respond(200, result)

    def _respond(self, status_code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
