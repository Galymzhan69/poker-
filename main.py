import asyncio
import os
import secrets
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from poker import new_deck, cards_text, winner_ids

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not TOKEN:
    raise RuntimeError("Укажите BOT_TOKEN в файле .env")

bot = Bot(TOKEN)
dp = Dispatcher()
rooms = {}
room_seq = 100

def is_admin(uid):
    return uid in ADMIN_IDS

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

def main_menu():
    return kb([
        [("♠️ Все столы", "tables"), ("➕ Создать стол", "create")],
        [("👤 Профиль", "profile"), ("🏆 Рейтинг", "top")],
        [("📖 Правила", "rules")]
    ])

def user_label(uid):
    u = db.get_user(uid)
    return (u["display_name"] if u else f"Игрок {uid}")[:24]

def room_status(room):
    return "Игра идёт" if room["state"] == "playing" else "Ожидание игроков"

def room_card(room):
    return (
        f"♠️ <b>Стол №{room['id']}</b>\n"
        f"Статус: {room_status(room)}\n"
        f"Игроки: {len(room['players'])}/6\n"
        f"Минимальный игровой баланс для входа: 20 очков\n"
        f"Стартовый стек за столом: {room['stack']} фишек\n"
        f"Карты игроков скрыты."
    )

def room_buttons(room):
    rows = [[("👁 Посмотреть стол", f"view:{room['id']}")]]
    if room["state"] == "waiting":
        rows.append([("➕ Присоединиться", f"join:{room['id']}")])
        if room["owner"] in room["players"]:
            rows.append([("▶️ Начать игру", f"start:{room['id']}")])
    return kb(rows)

def get_room(room_id):
    try:
        return rooms.get(int(room_id))
    except ValueError:
        return None

def current_player(room):
    if not room or room["state"] != "playing":
        return None
    active = [p for p in room["players"] if not p["folded"] and not p["allin"]]
    if not active:
        return None
    return room["players"][room["turn"]]

def next_turn(room):
    n = len(room["players"])
    for _ in range(n):
        room["turn"] = (room["turn"] + 1) % n
        p = room["players"][room["turn"]]
        if not p["folded"] and not p["allin"]:
            return
    room["turn"] = 0

def round_complete(room):
    active = [p for p in room["players"] if not p["folded"] and not p["allin"]]
    if len(active) <= 1:
        return True
    target = max(p["street_bet"] for p in room["players"])
    return all(p["acted"] and p["street_bet"] == target for p in active)

async def send_table_state(room):
    board = " ".join(room["board"]) if room["board"] else "—"
    lines = [
        f"♠️ <b>Стол №{room['id']}</b>",
        f"Улица: {room['street_name']}",
        f"Общие карты: {board}",
        f"Банк раунда: {room['pot']} фишек",
        ""
    ]
    for p in room["players"]:
        status = "сбросил карты" if p["folded"] else ("All-in" if p["allin"] else "в игре")
        lines.append(f"• {user_label(p['uid'])}: стек {p['chips']} | {status}")
    actor = current_player(room)
    if actor:
        lines += ["", f"Ход: {user_label(actor['uid'])}"]
        text = "\n".join(lines)
        actions = []
        if actor["street_bet"] == max(p["street_bet"] for p in room["players"]):
            actions.append([("Check", f"act:{room['id']}:check")])
        else:
            actions.append([("Call", f"act:{room['id']}:call")])
        actions += [
            [("Raise +2", f"act:{room['id']}:raise")],
            [("Fold", f"act:{room['id']}:fold")]
        ]
        for uid in room["players"]:
            if uid["uid"] == actor["uid"]:
                try:
                    await bot.send_message(uid["uid"], text, reply_markup=kb(actions))
                except Exception:
                    pass
    else:
        await announce(room, "\n".join(lines))

async def announce(room, text):
    for p in room["players"]:
        try:
            await bot.send_message(p["uid"], text)
        except Exception:
            pass

def start_hand(room):
    deck = new_deck()
    room["deck"] = deck
    room["board"] = []
    room["pot"] = 0
    room["street"] = 0
    room["street_name"] = "Префлоп"
    room["turn"] = 0
    room["players"] = [
        {
            "uid": uid, "hole": [deck.pop(), deck.pop()],
            "chips": room["stack"], "street_bet": 0,
            "total_bet": 0, "folded": False, "allin": False,
            "acted": False
        } for uid in room["players"]
    ]
    room["state"] = "playing"

def reset_street(room):
    for p in room["players"]:
        p["street_bet"] = 0
        p["acted"] = False
    room["turn"] = next((i for i,p in enumerate(room["players"]) if not p["folded"] and not p["allin"]), 0)

async def advance_street(room):
    if len([p for p in room["players"] if not p["folded"]]) <= 1:
        await finish_hand(room)
        return
    if room["street"] == 0:
        room["board"] += [room["deck"].pop() for _ in range(3)]
        room["street_name"] = "Флоп"
    elif room["street"] == 1:
        room["board"].append(room["deck"].pop())
        room["street_name"] = "Тёрн"
    elif room["street"] == 2:
        room["board"].append(room["deck"].pop())
        room["street_name"] = "Ривер"
    else:
        await finish_hand(room)
        return
    room["street"] += 1
    reset_street(room)
    if all(p["folded"] or p["allin"] for p in room["players"]):
        while len(room["board"]) < 5:
            room["board"].append(room["deck"].pop())
        await finish_hand(room)
        return
    await send_table_state(room)

async def finish_hand(room):
    alive = [p for p in room["players"] if not p["folded"]]
    if len(alive) == 1:
        winners = [alive[0]["uid"]]
        details = {}
        desc = "Остальные игроки сбросили карты."
    else:
        winners, details = winner_ids(room["players"], room["board"])
        desc = "\n".join(
            f"{user_label(uid)} — {details[uid][1]}"
            for uid in winners if uid in details
        )
    db.add_game_result([p["uid"] for p in room["players"]], winners)
    await announce(
        room,
        f"🏆 <b>Раздача завершена</b>\n"
        f"Общие карты: {cards_text(room['board'])}\n"
        f"Победитель(и): {', '.join(user_label(x) for x in winners)}\n"
        f"{desc}\n\n"
        f"Игровые фишки этой раздачи не конвертируются в баланс и не имеют денежной ценности."
    )
    room["state"] = "waiting"
    room["players"] = [p["uid"] for p in room["players"]]
    room["board"] = []
    room["pot"] = 0

@dp.message(Command("start"))
async def start(message: Message):
    db.ensure_user(message.from_user)
    if db.is_banned(message.from_user.id):
        return await message.answer("Доступ к боту ограничен.")
    await message.answer(
        "♠️ <b>POKER CLUB</b>\n\n"
        "Добро пожаловать!\n"
        "Игровые очки не являются деньгами, не продаются и не выводятся.",
        reply_markup=main_menu()
    )

@dp.message(Command("tables"))
async def cmd_tables(message: Message):
    await show_tables(message)

async def show_tables(target):
    waiting = [r for r in rooms.values() if r["state"] == "waiting"]
    if not waiting:
        text = "Сейчас нет открытых столов. Нажмите «Создать стол»."
        if isinstance(target, Message):
            await target.answer(text, reply_markup=main_menu())
        else:
            await target.message.answer(text, reply_markup=main_menu())
        return
    for room in waiting:
        if isinstance(target, Message):
            await target.answer(room_card(room), reply_markup=room_buttons(room), parse_mode="HTML")
        else:
            await target.message.answer(room_card(room), reply_markup=room_buttons(room), parse_mode="HTML")

@dp.callback_query(F.data == "tables")
async def cb_tables(call: CallbackQuery):
    await show_tables(call)
    await call.answer()

@dp.callback_query(F.data == "create")
async def cb_create(call: CallbackQuery):
    global room_seq
    uid = call.from_user.id
    db.ensure_user(call.from_user)
    if db.is_banned(uid):
        return await call.answer("Доступ ограничен.", show_alert=True)
    if db.get_user(uid)["points"] < 20:
        return await call.answer("Для создания/входа требуется 20 игровых очков.", show_alert=True)
    if any(uid in [p["uid"] if isinstance(p, dict) else p for p in r["players"]] for r in rooms.values()):
        return await call.answer("Вы уже находитесь за другим столом.", show_alert=True)
    room_seq += 1
    room = {
        "id": room_seq, "owner": uid, "players": [uid],
        "state": "waiting", "stack": 100, "deck": [],
        "board": [], "pot": 0, "street": 0,
        "street_name": "Ожидание", "turn": 0
    }
    rooms[room_seq] = room
    await call.message.answer("Стол создан!\n\n" + room_card(room), reply_markup=room_buttons(room), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("view:"))
async def cb_view(call: CallbackQuery):
    room = get_room(call.data.split(":")[1])
    if not room:
        return await call.answer("Стол не найден.", show_alert=True)
    await call.message.answer(room_card(room), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("join:"))
async def cb_join(call: CallbackQuery):
    uid = call.from_user.id
    db.ensure_user(call.from_user)
    room = get_room(call.data.split(":")[1])
    if not room or room["state"] != "waiting":
        return await call.answer("Стол недоступен.", show_alert=True)
    if db.is_banned(uid):
        return await call.answer("Доступ ограничен.", show_alert=True)
    if db.get_user(uid)["points"] < 20:
        return await call.answer("Для входа нужно минимум 20 игровых очков.", show_alert=True)
    if uid in room["players"]:
        return await call.answer("Вы уже за этим столом.")
    if len(room["players"]) >= 6:
        return await call.answer("Стол заполнен.", show_alert=True)
    if any(uid in [p["uid"] if isinstance(p, dict) else p for p in r["players"]] for r in rooms.values()):
        return await call.answer("Сначала выйдите из другого стола.", show_alert=True)
    room["players"].append(uid)
    await announce(room, f"➕ {user_label(uid)} присоединился к столу №{room['id']} ({len(room['players'])}/6).")
    await call.answer("Вы присоединились!")

@dp.callback_query(F.data.startswith("start:"))
async def cb_start_game(call: CallbackQuery):
    room = get_room(call.data.split(":")[1])
    if not room or room["owner"] != call.from_user.id:
        return await call.answer("Только создатель стола может начать игру.", show_alert=True)
    if len(room["players"]) < 2:
        return await call.answer("Нужно минимум 2 игрока.", show_alert=True)
    start_hand(room)
    await announce(room, "♠️ Игра началась! Ваши карты отправлены в личные сообщения.")
    for p in room["players"]:
        await bot.send_message(p["uid"], f"Ваши карты: {cards_text(p['hole'])}")
    await send_table_state(room)
    await call.answer("Игра началась!")

@dp.callback_query(F.data.startswith("act:"))
async def cb_action(call: CallbackQuery):
    _, rid, action = call.data.split(":")
    room = get_room(rid)
    if not room or room["state"] != "playing":
        return await call.answer("Раздача не активна.", show_alert=True)
    actor = current_player(room)
    if not actor or actor["uid"] != call.from_user.id:
        return await call.answer("Сейчас не ваш ход.", show_alert=True)
    target = max(p["street_bet"] for p in room["players"])
    if action == "fold":
        actor["folded"] = True
        actor["acted"] = True
    elif action == "check":
        if actor["street_bet"] != target:
            return await call.answer("Нельзя сделать Check: сначала уравняйте ставку.", show_alert=True)
        actor["acted"] = True
    elif action == "call":
        diff = target - actor["street_bet"]
        paid = min(diff, actor["chips"])
        actor["chips"] -= paid
        actor["street_bet"] += paid
        actor["total_bet"] += paid
        room["pot"] += paid
        actor["allin"] = actor["chips"] == 0
        actor["acted"] = True
    elif action == "raise":
        # Fixed-limit practice action: raise by 2 practice chips.
        diff = target - actor["street_bet"] + 2
        paid = min(diff, actor["chips"])
        if paid <= target - actor["street_bet"]:
            return await call.answer("Недостаточно фишек для повышения.", show_alert=True)
        actor["chips"] -= paid
        actor["street_bet"] += paid
        actor["total_bet"] += paid
        room["pot"] += paid
        actor["allin"] = actor["chips"] == 0
        for p in room["players"]:
            if p is not actor and not p["folded"] and not p["allin"]:
                p["acted"] = False
        actor["acted"] = True
    if len([p for p in room["players"] if not p["folded"]]) <= 1:
        await finish_hand(room)
    elif round_complete(room):
        await advance_street(room)
    else:
        next_turn(room)
        await send_table_state(room)
    await call.answer("Ход принят.")

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if not u:
        db.ensure_user(call.from_user)
        u = db.get_user(call.from_user.id)
    await call.message.answer(
        f"👤 <b>Профиль</b>\nИмя: {u['display_name']}\n"
        f"Игровые очки: {u['points']}\nИгр: {u['games']}\nПобед: {u['wins']}",
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    rows = db.top_users()
    text = "🏆 <b>Рейтинг игроков</b>\n\n"
    text += "\n".join(f"{i}. {r['display_name']} — побед: {r['wins']}, очки: {r['points']}" for i,r in enumerate(rows,1)) or "Пока нет игроков."
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "rules")
async def cb_rules(call: CallbackQuery):
    await call.message.answer(
        "📖 <b>Texas Hold'em</b>\n\n"
        "Каждому игроку раздаются 2 закрытые карты. На стол выкладываются 5 общих карт: "
        "флоп (3), тёрн (1), ривер (1). Побеждает лучшая комбинация из 5 карт.\n\n"
        "Check — пропустить ход без повышения.\nCall — уравнять текущую ставку игровыми фишками.\n"
        "Raise — повысить игровую ставку.\nFold — сбросить карты.\n\n"
        "Фишки используются только для тренировки внутри раздачи и не имеют денежной ценности.",
        parse_mode="HTML"
    )
    await call.answer()

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Команда недоступна.")
    await message.answer(
        "🛡 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        f"Пользователей: {db.user_count()}\n"
        f"Открытых столов: {len(rooms)}\n\n"
        "Команды:\n"
        "/addpoints USER_ID AMOUNT — начислить игровые очки\n"
        "/ban USER_ID — блокировка\n"
        "/unban USER_ID — разблокировка\n"
        "/closeall — закрыть все столы",
        parse_mode="HTML"
    )

@dp.message(Command("addpoints"))
async def admin_addpoints(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        _, uid, amount = message.text.split()
        uid, amount = int(uid), int(amount)
        if amount <= 0 or amount > 100000:
            return await message.answer("Укажите положительное число до 100000.")
        ok, result = db.change_points(message.from_user.id, uid, amount)
        await message.answer(("✅ " if ok else "❌ ") + result)
    except ValueError:
        await message.answer("Формат: /addpoints USER_ID AMOUNT")

@dp.message(Command("ban"))
async def admin_ban(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await message.answer("Пользователь заблокирован." if db.set_ban(uid, True) else "Пользователь не найден.")
    except (ValueError, IndexError):
        await message.answer("Формат: /ban USER_ID")

@dp.message(Command("unban"))
async def admin_unban(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await message.answer("Блокировка снята." if db.set_ban(uid, False) else "Пользователь не найден.")
    except (ValueError, IndexError):
        await message.answer("Формат: /unban USER_ID")

@dp.message(Command("closeall"))
async def admin_closeall(message: Message):
    if not is_admin(message.from_user.id):
        return
    rooms.clear()
    await message.answer("Все столы закрыты.")

async def main():
    db.init_db()
    print("POKER CLUB bot is running")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
