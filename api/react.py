import json
import time
import random
import requests
from http.server import BaseHTTPRequestHandler

# 🔐 আপনার bot tokens এখানে বসান (env use করলে ভালো)
BOT_TOKENS = [
    "BOT_TOKEN_1",
    "BOT_TOKEN_2",
    "BOT_TOKEN_3",
    "BOT_TOKEN_4"
]

# 😊 Default emoji list
EMOJIS = [
    "🌟", "💖", "🔥", "🎉", "✨", "😍", "👏",
    "💯", "⚡", "🏆", "💎", "🙌", "🤝", "😎"
]


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # Request body read
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length)
            data = json.loads(body)

            chat_id = data.get("chat_id")
            message_id = data.get("message_id")
            count = data.get("count", len(BOT_TOKENS))

            if not chat_id or not message_id:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing chat_id or message_id')
                return

            success = 0
            failed = 0

            # Limit count
            tokens = BOT_TOKENS[:count]

            for token in tokens:
                try:
                    emoji = random.choice(EMOJIS)

                    url = f"https://api.telegram.org/bot{token}/setMessageReaction"

                    payload = {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "reaction": [
                            {
                                "type": "emoji",
                                "emoji": emoji,
                                "is_big": True
                            }
                        ]
                    }

                    r = requests.post(url, json=payload, timeout=5)

                    if r.status_code == 200:
                        success += 1
                    else:
                        failed += 1

                    # ⏱️ ছোট delay (rate limit avoid)
                    time.sleep(0.2)

                except:
                    failed += 1

            # Response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = {
                "status": "done",
                "success": success,
                "failed": failed
            }

            self.wfile.write(json.dumps(response).encode())

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())
