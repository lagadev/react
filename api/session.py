import json
import os
import asyncio
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

SESSION_FILE = os.path.join(os.path.dirname(__file__), '..', 'session.json')


def _load():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            return json.load(f)
    return {'accounts': []}


def _save(data):
    with open(SESSION_FILE, 'w') as f:
        json.dump(data, f, indent=2)


async def create_session(api_id, api_hash, phone, code=None, password=None):
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        if code:
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                if password:
                    await client.sign_in(password=password)
                else:
                    await client.disconnect()
                    return {'status': 'password_needed', 'message': '2FA password required'}
            except Exception as e:
                await client.disconnect()
                return {'status': 'error', 'error': str(e)}
        else:
            await client.send_code_request(phone)
            await client.disconnect()
            return {'status': 'code_sent', 'message': 'Code sent. Call again with &code=XXXXX'}

    session_string = client.session.save()
    me = await client.get_me()
    await client.disconnect()

    return {
        'status': 'success',
        'phone': phone,
        'username': me.username,
        'first_name': me.first_name,
        'session_string': session_string
    }


async def add_session(api_id, api_hash, phone, code=None, password=None):
    result = await create_session(api_id, api_hash, phone, code, password)
    if result['status'] == 'success':
        data = _load()
        data['accounts'] = [a for a in data['accounts'] if a.get('phone') != phone]
        data['accounts'].append({
            'phone': phone,
            'api_id': int(api_id),
            'api_hash': api_hash,
            'session_string': result['session_string'],
            'username': result.get('username', ''),
            'first_name': result.get('first_name', '')
        })
        _save(data)
        result.pop('session_string', None)  # don't leak in response
    return result


async def list_sessions():
    data = _load()
    accounts = []
    for acc in data.get('accounts', []):
        accounts.append({
            'phone': acc.get('phone'),
            'username': acc.get('username'),
            'first_name': acc.get('first_name'),
            'status': 'active'
        })
    return {'total': len(accounts), 'accounts': accounts}


async def delete_session(phone):
    data = _load()
    before = len(data['accounts'])
    data['accounts'] = [a for a in data['accounts'] if a.get('phone') != phone]
    _save(data)
    return {'status': 'deleted' if before > len(data['accounts']) else 'not_found', 'phone': phone}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        action = params.get('action', ['list'])[0]

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if action == 'list':
                result = loop.run_until_complete(list_sessions())
            elif action == 'add':
                api_id = params.get('api_id', [None])[0]
                api_hash = params.get('api_hash', [None])[0]
                phone = params.get('phone', [None])[0]
                code = params.get('code', [None])[0]
                pw = params.get('password', [None])[0]
                if not all([api_id, api_hash, phone]):
                    result = {
                        'error': 'Missing api_id, api_hash, phone',
                        'step1': '/session?action=add&api_id=X&api_hash=Y&phone=+123...',
                        'step2': '/session?action=add&api_id=X&api_hash=Y&phone=+123...&code=12345',
                        'step3': '/session?action=add&api_id=X&api_hash=Y&phone=+123...&code=12345&password=YOUR_2FA'
                    }
                else:
                    result = loop.run_until_complete(add_session(api_id, api_hash, phone, code, pw))
            elif action == 'delete':
                phone = params.get('phone', [None])[0]
                result = {'error': 'Missing phone'} if not phone else loop.run_until_complete(delete_session(phone))
            else:
                result = {'error': f'Unknown action: {action}', 'available': ['list', 'add', 'delete']}
        finally:
            loop.close()

        self._respond(200, result)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = json.loads(self.rfile.read(length)) if length else {}
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(add_session(
                data.get('api_id'), data.get('api_hash'), data.get('phone'),
                data.get('code'), data.get('password')
            ))
        finally:
            loop.close()
        self._respond(200, result)

    def do_DELETE(self):
        phone = parse_qs(urlparse(self.path).query).get('phone', [None])[0]
        if not phone:
            self._respond(400, {'error': 'Missing phone'})
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(delete_session(phone))
        finally:
            loop.close()
        self._respond(200, result)

    def _respond(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
