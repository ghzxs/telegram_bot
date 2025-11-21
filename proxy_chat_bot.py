#!/root/venv/bin/python3
import random
import sqlite3
from datetime import datetime, timedelta
import os
import pathlib

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== 请修改这三行 ====================
# 从环境变量读取，Render 上在 Service -> Environment 设置 TG_BOT_TOKEN 和 TG_ADMIN_ID
TOKEN = os.environ.get("TG_BOT_TOKEN")
ADMIN_ID = int(os.environ.get("TG_ADMIN_ID", "804926209"))
if not TOKEN:
    raise SystemExit("请在环境变量 TG_BOT_TOKEN 中设置 Bot Token（不要将 token 写入源码）")
# ====================================================

# 将 sqlite 放在脚本所在目录，避免相对路径问题
base_dir = pathlib.Path(__file__).parent
db_path = base_dir / "proxy_users.db"
db = sqlite3.connect(str(db_path), check_same_thread=False)
cur = db.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS passed (user_id INTEGER PRIMARY KEY)")
db.commit()

# 广告关键词（检测文字、caption、文件名）
# 全部小写，便于统一匹配
SPAM = ["贷款","加微信","私信","包装","刷单","合作","赚钱","投资","t.me/joinchat","http","https","@","频道","群","wx"]

def is_spam(message) -> bool:
    text = ""
    if message.text:
        text += message.text
    if message.caption:
        text += message.caption
    if message.document and message.document.file_name:
        text += message.document.file_name
    text = text.lower()
    return any(word in text for word in SPAM)

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
    opts = [ans-7, ans, ans+11]
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

    # 仅允许发起验证的用户本人点击按钮
    if q.from_user.id != user_id:
        await q.answer("请使用发送 /start 的账号点击以完成验证。", show_alert=True)
        return

    if answer == context.user_data.get("captcha"):
        cur.execute("INSERT OR IGNORE INTO passed VALUES (?)", (user_id,))
        db.commit()
        await q.edit_message_text("验证成功！现在可以正常聊天了～")
    else:
        await q.edit_message_text("答案错误，已限制使用7天。")
        await context.bot.ban_chat_member(
            chat_id=q.message.chat_id,
            user_id=user_id,
            until_date=datetime.utcnow() + timedelta(days=7)
        )

async def forward_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id

    # 1. 管理员（你）发消息 → 转发给最近联系的用户
    if user_id == ADMIN_ID:
        last = context.bot_data.get("last_user")
        if not last:
            await msg.reply_text("暂时没人找你哦～")
            return
        # 直接转发，不复制
        await msg.forward(chat_id=last)
        await msg.reply_text(f"已转发给用户 {last}")
        return

    # 2. 普通用户发消息
    # 检查是否通过验证
    cur.execute("SELECT 1 FROM passed WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        await msg.reply_text("请先完成验证")
        return

    # 广告过滤
    if is_spam(msg):
        await msg.reply_text("检测到广告，已被拦截。")
        return

    # 正常转发给你（管理员）
    context.bot_data["last_user"] = user_id
    await msg.forward(chat_id=ADMIN_ID)          # ← 重点：纯转发

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    # 支持几乎所有消息类型（文字、图片、文件、语音、视频、贴纸、投票等）
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,   # 所有消息，除了命令
        forward_to_user
    ))

    print("【纯转发版】双向聊天 + CAPTCHA + 防广告机器人已启动！")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
