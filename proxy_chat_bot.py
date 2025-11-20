import os
import random
import sqlite3
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from flask import Flask, request

# ================== 自动从 Render 环境变量读取 ==================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
# ============================================================

# Flask + Gunicorn 用于 Render webhook
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# 数据库（Render 免费层直接用本地 SQLite，足够）
db = sqlite3.connect("users.db", check_same_thread=False)
cur = db.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS passed (user_id INTEGER PRIMARY KEY)")
db.commit()

SPAM = ["贷款","加微信","私信","包装","刷单","合作","赚钱","投资","t.me/joinchat","http","https","@","频道","群","wx","WX"]

def is_spam(message) -> bool:
    text = ""
    if message.text: text += message.text
    if message.caption: text += message.caption
    if message.document and message.document.file_name: text += message.document.file_name
    return any(word in text.lower() for word in SPAM)

def gen_captcha():
    a, b = random.randint(10, 40), random.randint(10, 40)
    return a, b, a+b, f"{a} + {b} = ?"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur.execute("SELECT 1 FROM passed WHERE user_id=?", (user_id,))
    if cur.fetchone():
        await update.message.reply_text("你已通过验证，直接发消息吧～")
        return
    a, b, ans, q = gen_captcha()
    opts = [ans-9, ans, ans+13]
    random.shuffle(opts)
    keyboard = [[InlineKeyboardButton(str(x), callback_data=f"c_{x}_{user_id}") for x in opts]]
    await update.message.reply_text(
        f"首次使用需要过个小验证（防广告机器人）\n\n{q}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["captcha"] = ans

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("_")
    answer = int(data[1])
    user_id = int(data[2])
    if answer == context.user_data.get("captcha"):
        cur.execute("INSERT OR IGNORE INTO passed VALUES (?)", (user_id,))
        db.commit()
        await q.edit_message_text("验证成功！现在可以正常聊天了～")
    else:
        await q.edit_message_text("答案错误，已限制30天")
        await application.bot.ban_chat_member(q.message.chat_id, user_id,
            until_date=datetime.now() + timedelta(days=30))

async def forward_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id

    # 管理员（你）发的 → 转发给最近联系的用户
    if user_id == ADMIN_ID:
        last = context.bot_data.get("last_user")
        if last:
            await msg.forward(chat_id=last)
            await msg.reply_text(f"已转发给 {last}")
        else:
            await msg.reply_text("暂时没人找你～")
        return

    # 普通用户
    cur.execute("SELECT 1 FROM passed WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        await msg.reply_text("请先完成验证")
        return
    if is_spam(msg):
        await msg.reply_text("检测到广告，已拦截")
        return

    context.bot_data["last_user"] = user_id
    await msg.forward(chat_id=ADMIN_ID)      # 纯转发
    await msg.reply_text("消息已收到，我会尽快回复～")

# ==================== 注册所有处理器 ====================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, forward_to_user))

# ==================== Flask webhook 路由 ====================
@app.route("/", methods=["GET", "POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), application.bot)
        if update:
            application.process_update(update)
        return "ok", 200
    else:
        # GET 请求直接返回 200，防止 Telegram 认为 404
        return "Telegram Proxy Bot is running!", 200

# Render 第一次部署时自动设置 webhook
if os.environ.get("RENDER") == "true":                     # Render 新规范
    # Render 现在用 RENDER_EXTERNAL_HOSTNAME 或直接从请求头取
    domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not domain:
        # 极少数情况下还没准备好，跳过自动设置（后面手动一次就行）
        print("RENDER_EXTERNAL_HOSTNAME 未就绪，跳过自动 setWebhook")
    else:
        webhook_url = f"https://{domain}/{TOKEN}"
        try:
            application.bot.set_webhook(url=webhook_url)
            print(f"Webhook 自动设置成功：{webhook_url}")
        except Exception as e:
            print(f"Webhook 设置失败（首次部署正常）：{e}")

# Gunicorn 入口
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
