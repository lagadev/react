import json
import time
import random
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

# 🔐 Bot Tokens (env use করা better)
BOT_TOKENS = [
    "8640658980:AAHNEh-ZHU5CwwiS5_Ak3ywrsEeWgrQsfP4",
    "8320276149:AAFznZ1LQqeXjfTYoxc8SFYr1yNCDfu7EmY",
    "8564269804:AAEa_v9pipRUt2YRjVXeAZ3ExzDOfd6cahE",
    "8548195103:AAHXuYSKaur4oDoOVjcQ0LzvwfsjN4Ha-Yc"
]

# 😊 Emoji list
EMOJIS = [
    "🌟", "💖", "🔥", "🎉", "✨", "😍", "👏",
    "💯", "⚡", "🏆", "💎", "🙌", "🤝", "😎"
]


class Handler(BaseHTTPRequestHandler):

    # ✅ GET request (browser test এর জন্য)
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        response = {
            "status": "running",
            "message": "Server is working. Use POST to send reactions."
        }

        self.wfile.write(json.dumps(response).encode())

    # ✅ POST request (main logic)
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
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
            tokens = BOT_TOKENS[:min(count, len(BOT_TOKENS))]

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

                    # ছোট delay (rate limit avoid)
                    time.sleep(0.2)

                except Exception as e:
                    failed += 1

            # ✅ Response
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


# ✅ Server run
def run(server_class=HTTPServer, handler_class=Handler, port=8000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"🚀 Server running on port {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
