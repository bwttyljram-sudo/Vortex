Enter"""
سيرفر وهمي بسيط (Flask) - وظيفته الوحيدة إنه يخلي Render يشوف إن فيه
خدمة "Web Service" شغالة على بورت، ويقبل نبضات خارجية (من UptimeRobot
أو أي خدمة ping مجانية) تبقي البوت نشط وما ينام.

ملاحظة: البوت نفسه (bot.py) يشتغل عن طريق Polling بخيط منفصل،
وهذا الملف بس "واجهة" يشوفها Render من برّا.
"""

import os
import threading
from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Vortex Bot is alive and running ✅", 200


@app.route("/ping")
def ping():
    return "pong", 200


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """يشغّل السيرفر الوهمي بخيط منفصل عشان ما يوقف تشغيل البوت الأساسي"""
    t = threading.Thread(target=run)
    t.daemon = True
    t.start()
