import logging
import re
import json
import os
import asyncio
from datetime import time
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "284189682"))
REPORT_CHAT_ID = int(os.environ.get("REPORT_CHAT_ID", "-1003880750609"))
REPORT_THREAD_ID = int(os.environ.get("REPORT_THREAD_ID", "797"))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── STORAGE ──────────────────────────────────────────

DATA_FILE = '/tmp/counter_data.json'
groups = {}


def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump({str(k): v for k, v in groups.items()}, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Save error: {e}")


def load_data():
    global groups
    try:
        with open(DATA_FILE) as f:
            groups = {int(k): v for k, v in json.load(f).items()}
        logger.info(f"Loaded {len(groups)} groups")
    except FileNotFoundError:
        groups = {}
    except Exception as e:
        logger.error(f"Load error: {e}")
        groups = {}



# ─── AUTO-DETECT COMPANY ──────────────────────────────

COMPANY_MAP = {
    'happy': 'Happy Tours',
    'perfect': 'Your Perfect Travel',
}

async def detect_company(chat_id, ctx):
    """Check group admins and auto-detect company by account name/username."""
    try:
        admins = await ctx.bot.get_chat_administrators(chat_id)
        for admin in admins:
            user = admin.user
            if user.is_bot:
                continue
            check = ' '.join([
                user.username or '',
                user.first_name or '',
                user.last_name or ''
            ]).lower()
            for keyword, company in COMPANY_MAP.items():
                if keyword in check:
                    return company
    except Exception as e:
        logger.error(f"detect_company error: {e}")
    return ''

# ─── PARSE SEATS ──────────────────────────────────────

SKIP_WORDS = ['хотят', 'хочет', 'хочу', 'хотим', 'интересует', 'планирую', 'планируем']


def parse_seats(text):
    if not text:
        return 0
    text_lower = text.lower()
    for word in SKIP_WORDS:
        if word in text_lower:
            return 0
    m = re.search(r'\((\d+)\s*x\s*[\d.,]+\)', text)
    if m:
        return int(m.group(1))
    m = re.search(r'(-?\d+)\s*мест[аоу]?(?:\b|$|\s)', text_lower)
    if m:
        return int(m.group(1))
    return 0


# ─── VERIFY DELETED MESSAGES ─────────────────────────

async def verify_group(chat_id, ctx):
    g = groups.get(chat_id)
    if not g or not g['messages']:
        return False

    to_remove = []
    for msg_id_str in list(g['messages'].keys()):
        if msg_id_str == 'initial':
            continue
        try:
            fwd = await ctx.bot.forward_message(
                chat_id=ADMIN_ID, from_chat_id=chat_id,
                message_id=int(msg_id_str), disable_notification=True
            )
            await ctx.bot.delete_message(chat_id=ADMIN_ID, message_id=fwd.message_id)
        except Exception:
            to_remove.append(msg_id_str)
        await asyncio.sleep(0.05)

    for msg_id_str in to_remove:
        seats = g['messages'].pop(msg_id_str, 0)
        logger.info(f"[{g['name']}] Удалено: {msg_id_str} ({seats} мест)")

    if to_remove:
        g['total'] = sum(g['messages'].values())
        return True
    return False


# ─── UPDATE PIN ───────────────────────────────────────

async def update_pin(chat_id, ctx):
    g = groups.get(chat_id)
    if not g:
        return
    text = f"📊 Продано: {g['total']} мест"

    if g.get('pin_id'):
        try:
            await ctx.bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=g['pin_id']
            )
            return
        except Exception:
            g['pin_id'] = None

    try:
        sent = await ctx.bot.send_message(chat_id=chat_id, text=text)
        g['pin_id'] = sent.message_id
        try:
            await ctx.bot.pin_chat_message(
                chat_id=chat_id, message_id=sent.message_id,
                disable_notification=True
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Pin error {chat_id}: {e}")


# ─── GROUP MESSAGE HANDLER ────────────────────────────

async def handle_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id = msg.chat_id
    chat_name = msg.chat.title or 'Группа'
    seats = parse_seats(msg.text)

    if seats == 0:
        return

    if chat_id not in groups:
        company = await detect_company(chat_id, ctx)
        groups[chat_id] = {
            'name': chat_name, 'company': company, 'total': 0,
            'pin_id': None, 'messages': {}
        }
        if company:
            logger.info(f"[{chat_name}] Фирма определена: {company}")

    g = groups[chat_id]
    g['name'] = chat_name
    g['messages'][str(msg.message_id)] = seats

    await verify_group(chat_id, ctx)
    g['total'] = sum(g['messages'].values())

    await update_pin(chat_id, ctx)
    save_data()
    logger.info(f"[{chat_name}] +{seats} (итого: {g['total']})")


# ─── GROUP COMMANDS ───────────────────────────────────

async def set_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.chat.type == 'private':
        await msg.reply_text("Эту команду нужно писать в группе.")
        return
    try:
        count = int(ctx.args[0])
    except (IndexError, ValueError):
        await msg.reply_text("Формат: /set 15")
        return

    chat_id = msg.chat_id
    if chat_id not in groups:
        groups[chat_id] = {
            'name': msg.chat.title or 'Группа', 'company': '', 'total': 0,
            'pin_id': None, 'messages': {}
        }
    groups[chat_id]['messages']['initial'] = count
    groups[chat_id]['total'] = sum(groups[chat_id]['messages'].values())
    await update_pin(chat_id, ctx)
    save_data()
    await msg.reply_text(f"✅ Начальный счёт: {count}")


async def set_company(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/company Happy Tours — привязать группу к фирме."""
    msg = update.message
    if msg.chat.type == 'private':
        await msg.reply_text("Эту команду нужно писать в группе.")
        return

    company = ' '.join(ctx.args)
    if not company:
        await msg.reply_text("Формат: /company Happy Tours")
        return

    chat_id = msg.chat_id
    if chat_id not in groups:
        groups[chat_id] = {
            'name': msg.chat.title or 'Группа', 'company': '', 'total': 0,
            'pin_id': None, 'messages': {}
        }
    groups[chat_id]['company'] = company
    save_data()
    await msg.reply_text(f"✅ Фирма: {company}")


async def reset_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.chat.type == 'private':
        await msg.reply_text("Эту команду нужно писать в группе.")
        return
    chat_id = msg.chat_id
    if chat_id in groups:
        groups[chat_id]['messages'] = {}
        groups[chat_id]['total'] = 0
        await update_pin(chat_id, ctx)
        save_data()
    await msg.reply_text("✅ Счётчик сброшен.")



def build_stats_text_and_buttons():
    """Build stats message text and inline keyboard."""
    by_company = {}
    for cid, g in groups.items():
        company = g.get('company', '') or 'Без фирмы'
        if company not in by_company:
            by_company[company] = []
        by_company[company].append((cid, g))

    lines = ["📊 *Статистика:*\n"]
    buttons = []
    for company in sorted(by_company.keys()):
        lines.append(f"*{company}:*")
        for cid, g in sorted(by_company[company], key=lambda x: x[1]['name']):
            lines.append(f"• {g['name']} — *{g['total']}* мест")
            buttons.append([InlineKeyboardButton(
                f"🗑 {g['name']}", callback_data=f"del_trip_{cid}"
            )])
        lines.append("")
    return '\n'.join(lines), InlineKeyboardMarkup(buttons) if buttons else None

# ─── PRIVATE CHAT ─────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я считаю проданные места в группах поездок.\n\n"
        "Добавь меня в группу — я буду считать автоматически.\n\n"
        "Команды для группы:\n"
        "/set 15 — задать начальный счёт\n"
        "/company Happy Tours — вручную задать фирму\n"
        "/reset — сбросить счётчик\n\n"
        "Фирма определяется автоматически по аккаунту админа группы.",
        reply_markup=ReplyKeyboardMarkup(
            [['📊 Статистика']], resize_keyboard=True
        )
    )


async def handle_private(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text == '📊 Статистика':
        # Verify all groups
        for chat_id in list(groups.keys()):
            changed = await verify_group(chat_id, ctx)
            if changed:
                await update_pin(chat_id, ctx)
                save_data()

        if not groups:
            await update.message.reply_text("Пока нет данных.")
            return

        text, keyboard = build_stats_text_and_buttons()
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )


# ─── DELETE TRIP ──────────────────────────────────────

async def handle_delete_trip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith('del_trip_'):
        chat_id = int(data.replace('del_trip_', ''))
        g = groups.get(chat_id)
        name = g['name'] if g else '?'
        await query.edit_message_text(
            f"🗑 Удалить *{name}* из статистики?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton('✅ Да, удалить', callback_data=f'confirm_del_{chat_id}'),
                    InlineKeyboardButton('❌ Отмена', callback_data='cancel_del'),
                ]
            ])
        )

    elif data.startswith('confirm_del_'):
        chat_id = int(data.replace('confirm_del_', ''))
        g = groups.pop(chat_id, None)
        save_data()
        name = g['name'] if g else '?'
        await query.edit_message_text(f"✅ *{name}* удалена из статистики.", parse_mode=ParseMode.MARKDOWN)

    elif data == 'cancel_del':
        await query.edit_message_text("❌ Отменено.")


# ─── DAILY REPORT ─────────────────────────────────────

async def daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    if not groups:
        return

    # Verify all groups
    for chat_id in list(groups.keys()):
        changed = await verify_group(chat_id, ctx)
        if changed:
            await update_pin(chat_id, ctx)
            save_data()

    text, keyboard = build_stats_text_and_buttons()
    # Replace header for daily report
    text = text.replace("📊 *Статистика:*", "📊 *Ежедневная сводка:*")
    try:
        await ctx.bot.send_message(
            chat_id=REPORT_CHAT_ID,
            message_thread_id=REPORT_THREAD_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Report error: {e}")
        # Fallback to admin DM
        try:
            await ctx.bot.send_message(
                chat_id=ADMIN_ID, text=text,
                parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
            )
        except Exception:
            pass


# ─── MAIN ─────────────────────────────────────────────

def main():
    load_data()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_private
    ))
    app.add_handler(CallbackQueryHandler(handle_delete_trip, pattern=r'^(del_trip_|confirm_del_|cancel_del)'))
    app.add_handler(CommandHandler('set', set_count))
    app.add_handler(CommandHandler('company', set_company))
    app.add_handler(CommandHandler('reset', reset_count))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, handle_group_message
    ))

    app.job_queue.run_daily(daily_report, time=time(hour=20, minute=0))

    logger.info("Counter bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
