"""Панель продавца: заказы и клиенты конкретного магазина."""
import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from db import (
    can_manage_shop,
    change_order_status,
    count_customers,
    count_orders,
    count_orders_by_status,
    customer_spent,
    get_customer,
    get_lang,
    get_managed_shop,
    get_order,
    get_order_items,
    get_product,
    get_shop,
    get_user_orders,
    list_customers,
    list_orders,
    mark_blocked,
    mark_order_items_sold,
    set_managed_shop,
)
from keyboards import (
    STATUS_FILTERS,
    admin_order_kb,
    cancel_kb,
    confirm_payment_kb,
    customer_kb,
    customer_orders_kb,
    customers_kb,
    order_list_kb,
    orders_filter_kb,
    pay_again_kb,
)
from posts import refresh_channel_post
from texts import MENU_BUTTON_TEXTS, cur_label, fmt_price, status_label, t
from utils import fmt_dt, money, pages_count, reply, show

router = Router()

NOT_COMMAND = ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTON_TEXTS)
PER_PAGE = 8


class AdminText(StatesGroup):
    """Продавец вводит текст: причину отказа, номер отправки, сообщение клиенту."""
    text = State()


# ---------- тексты ----------

def admin_order_text(order: dict, items: list, username=None) -> str:
    cur = cur_label("ru", order["currency"])
    client = f'<a href="tg://user?id={order["user_id"]}">{html.escape(order["name"] or "—")}</a>'
    if username:
        client += f" (@{html.escape(username)})"
    shop_line = f"🏪 Магазин: <b>{html.escape(order.get('shop_title') or '—')}</b>\n" if order.get("shop_title") else ""
    lines = [
        f"📦 <b>Заказ №{order['id']}</b> · {status_label('ru', order['status'])}",
        shop_line + f"🕒 {fmt_dt(order['created_at'])}",
        "",
        f"👤 {client}",
        f"📱 {html.escape(order['phone'] or '—')}",
        f"📍 {html.escape(order['address'] or '—')}",
        "",
    ]
    for it in items:
        lines.append(
            f"• {html.escape(it['title'] or '')} × {it['qty']} — "
            f"{fmt_price(it['price'] * it['qty'])} {cur_label('ru', it['currency'])}"
        )
    lines.append("")
    if order.get("promo_code") and order.get("discount_amount"):
        lines.append(
            f"🎟 Промокод <code>{html.escape(order['promo_code'])}</code>: -{fmt_price(order['discount_amount'])} {cur}"
        )
    lines.append(f"💰 <b>Итого: {fmt_price(order['total'])} {cur}</b>")
    if order.get("tracking"):
        lines.append(f"🚚 Отправка: {html.escape(order['tracking'])}")
    if order.get("reason") and order["status"] in ("rejected", "cancelled"):
        label = "Причина отмены" if order["status"] == "cancelled" else "Причина отказа"
        lines.append(f"📝 {label}: {html.escape(order['reason'])}")
    return "\n".join(lines)


async def render_order(order_id: int):
    order = await get_order(order_id)
    if not order:
        return None
    items = await get_order_items(order_id)
    customer = await get_customer(order["user_id"], order.get("shop_id"))
    username = customer["username"] if customer else None
    return admin_order_text(order, items, username), admin_order_kb(order)


async def send_order_view(message: Message, order_id: int):
    rendered = await render_order(order_id)
    if rendered:
        await message.answer(rendered[0], reply_markup=rendered[1])


# ---------- уведомления клиенту ----------

async def send_to_user(bot: Bot, user_id: int, text: str, kb=None) -> bool:
    try:
        await bot.send_message(user_id, text, reply_markup=kb)
        return True
    except TelegramForbiddenError:
        await mark_blocked(user_id)
        return False
    except TelegramAPIError:
        return False


def buyer_text(lang: str, kind: str, order: dict, extra=None) -> str:
    order_id = order["id"]
    shop_title = html.escape(order.get("shop_title") or "Магазин")
    if kind == "confirmed":
        return t(lang, "notify_confirmed").format(id=order_id, shop=shop_title)
    if kind == "shipped":
        text = t(lang, "notify_shipped").format(id=order_id, shop=shop_title)
        if extra:
            text += "\n\n" + t(lang, "notify_note").format(note=html.escape(extra))
        return text
    if kind == "rejected":
        text = t(lang, "notify_rejected").format(id=order_id, shop=shop_title)
        if extra:
            text += "\n" + t(lang, "notify_reason").format(reason=html.escape(extra))
        return text + "\n\n" + t(lang, "notify_rejected_hint")
    if kind == "cancelled":
        text = t(lang, "notify_cancelled").format(id=order_id, shop=shop_title)
        if extra:
            text += "\n" + t(lang, "notify_reason").format(reason=html.escape(extra))
        return text
    raise ValueError(kind)


async def notify_buyer(bot: Bot, order: dict, kind: str, extra=None) -> bool:
    lang = await get_lang(order["user_id"]) or "ru"
    kb = pay_again_kb(lang, order["id"]) if kind == "rejected" else None
    return await send_to_user(bot, order["user_id"], buyer_text(lang, kind, order, extra), kb)


def delivery_note(delivered: bool) -> str:
    return "" if delivered else "\n⚠️ Клиент не получил уведомление (он заблокировал бота)."


# ---------- заказы: списки ----------

@router.callback_query(F.data == "adm:orders")
async def orders_menu(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    counts = await count_orders_by_status(shop["id"])
    await show(
        callback,
        f"📦 <b>Заказы магазина «{html.escape(shop['title'])}»</b>\nВыберите категорию:",
        orders_filter_kb(counts),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ord:l:"))
async def orders_list(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    parts = callback.data.split(":")
    status = parts[2]
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    if status != "all" and status not in STATUS_FILTERS:
        await callback.answer()
        return
    status_filter = None if status == "all" else status
    total = await count_orders(shop["id"], status_filter)
    pages = pages_count(total, PER_PAGE)
    page = min(max(page, 0), pages - 1)
    orders = await list_orders(shop["id"], status_filter, PER_PAGE, page * PER_PAGE)
    title = "📋 Все заказы" if status == "all" else status_label("ru", status)
    text = f"🏪 <b>{html.escape(shop['title'])}</b>\n<b>{title}</b> · {total}"
    if not orders:
        text += "\n\nВ этой категории пока нет заказов."
    await show(callback, text, order_list_kb(orders, status, page, pages))
    await callback.answer()


@router.callback_query(F.data.startswith("ord:v:"))
async def order_view(callback: CallbackQuery):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]):
        await callback.answer("Заказ не найден или нет доступа", show_alert=True)
        return
    await set_managed_shop(uid, order["shop_id"])
    rendered = await render_order(order_id)
    await show(callback, rendered[0], rendered[1])
    await callback.answer()


@router.callback_query(F.data.startswith("ord:rc:"))
async def order_receipt(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    order = await get_order(int(callback.data.split(":")[2]))
    if not order or not await can_manage_shop(uid, order["shop_id"]):
        await callback.answer("Заказ не найден или нет доступа", show_alert=True)
        return
    await set_managed_shop(uid, order["shop_id"])
    if not order["payment_photo_id"]:
        await callback.answer("Чек не загружен", show_alert=True)
        return
    await bot.send_photo(
        uid,
        order["payment_photo_id"],
        caption=f"🧾 Чек по заказу №{order['id']}\n💰 {fmt_price(order['total'])} {cur_label('ru', order['currency'])}",
    )
    await callback.answer()


# ---------- заказы: подтверждение оплаты ----------

CONFIRM_FROM = ("review", "pending_payment", "rejected")
CANCEL_FROM = ("pending_payment", "review", "rejected", "confirmed")


@router.callback_query(F.data.startswith("ord:ok:"))
async def confirm_ask(callback: CallbackQuery):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]):
        await callback.answer("Заказ не найден или нет доступа", show_alert=True)
        return
    await set_managed_shop(uid, order["shop_id"])
    if order["status"] not in CONFIRM_FROM:
        await callback.answer("Статус заказа уже изменён", show_alert=True)
        return
    await show(
        callback,
        f"Подтвердить оплату по заказу №{order_id} на сумму "
        f"<b>{fmt_price(order['total'])} {cur_label('ru', order['currency'])}</b>?\n\n"
        "Покупатель сразу получит уведомление.",
        confirm_payment_kb(order_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ord:ok2:"))
async def confirm_do(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]):
        await callback.answer("Заказ не найден или нет доступа", show_alert=True)
        return
    await set_managed_shop(uid, order["shop_id"])
    if not await change_order_status(order_id, CONFIRM_FROM, "confirmed"):
        await callback.answer("Статус заказа уже изменён", show_alert=True)
    else:
        order = await get_order(order_id)
        delivered = await notify_buyer(bot, order, "confirmed")
        await callback.answer(
            "✅ Оплата подтверждена, клиент уведомлён"
            if delivered
            else "✅ Подтверждено (клиент заблокировал бота)",
            show_alert=not delivered,
        )
    rendered = await render_order(order_id)
    if rendered:
        await show(callback, rendered[0], rendered[1])


# ---------- заказы: действия с вводом текста ----------

PROMPTS = {
    "reject": "❌ Укажите причину отказа, её увидит покупатель. Если причина не нужна, отправьте «-»:",
    "ship": "🚚 Напишите информацию об отправке (трек-номер, курьер, время доставки). Если нечего добавить, отправьте «-»:",
    "cancel": "🚫 Укажите причину отмены заказа. Если причина не нужна, отправьте «-»:",
    "msg": "✉️ Напишите сообщение покупателю. Он получит его от имени вашего магазина:",
    "cmsg": "✉️ Напишите сообщение клиенту. Он получит его от имени вашего магазина:",
}


async def ask_text(callback: CallbackQuery, state: FSMContext, action: str, **data):
    await state.clear()
    await state.set_state(AdminText.text)
    await state.update_data(action=action, **data)
    await reply(callback, PROMPTS[action] + "\n\nОтмена: /cancel", cancel_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("ord:no:"))
async def reject_ask(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]) or order["status"] != "review":
        await callback.answer("Статус заказа уже изменён или нет доступа", show_alert=True)
        return
    await ask_text(callback, state, "reject", order_id=order_id, shop_id=order["shop_id"])


@router.callback_query(F.data.startswith("ord:sh:"))
async def ship_ask(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]) or order["status"] != "confirmed":
        await callback.answer("Отправить можно только оплаченный заказ", show_alert=True)
        return
    await ask_text(callback, state, "ship", order_id=order_id, shop_id=order["shop_id"])


@router.callback_query(F.data.startswith("ord:cx:"))
async def cancel_ask(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]) or order["status"] not in CANCEL_FROM:
        await callback.answer("Этот заказ нельзя отменить", show_alert=True)
        return
    await ask_text(callback, state, "cancel", order_id=order_id, shop_id=order["shop_id"])


@router.callback_query(F.data.startswith("ord:msg:"))
async def msg_ask(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]):
        await callback.answer("Заказ не найден или нет доступа", show_alert=True)
        return
    await set_managed_shop(uid, order["shop_id"])
    await ask_text(callback, state, "msg", order_id=order_id, shop_id=order["shop_id"])


@router.callback_query(F.data.startswith("cus:msg:"))
async def customer_msg_ask(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    user_id = int(callback.data.split(":")[2])
    if not await get_customer(user_id, shop["id"]):
        await callback.answer("Клиент не найден", show_alert=True)
        return
    await ask_text(callback, state, "cmsg", user_id=user_id, shop_id=shop["id"])


@router.message(AdminText.text, F.text, NOT_COMMAND)
async def text_received(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    action = data.get("action")
    shop_id = data.get("shop_id")
    raw = message.text.strip()
    note = None if raw == "-" else raw[:500]

    if action in ("msg", "cmsg"):
        if raw == "-":
            await message.answer("Напишите текст сообщения (не «-»). Отмена: /cancel")
            return
        await state.clear()
        if action == "msg":
            order = await get_order(data["order_id"])
            user_id = order["user_id"] if order else None
            shop_title = order.get("shop_title") if order else "Магазин"
        else:
            user_id = data["user_id"]
            shop = await get_shop(shop_id) if shop_id else await get_managed_shop(message.from_user.id)
            shop_title = shop["title"] if shop else "Магазин"
        if user_id is None:
            await message.answer("Получатель не найден.")
            return
        lang = await get_lang(user_id) or "ru"
        text = t(lang, "msg_from_shop").format(
            shop=html.escape(shop_title or "Магазин"),
            text=html.escape(raw[:3000]),
        )
        delivered = await send_to_user(bot, user_id, text)
        await message.answer(
            "✅ Сообщение отправлено клиенту."
            if delivered
            else "⚠️ Не удалось отправить: клиент заблокировал бота или недоступен."
        )
        return

    order_id = data.get("order_id")
    await state.clear()
    order = await get_order(order_id)
    if not order or not await can_manage_shop(message.from_user.id, order["shop_id"]):
        await message.answer("Заказ не найден или нет доступа.")
        return

    if action == "reject":
        changed = await change_order_status(order_id, ("review",), "rejected", reason=note)
        done = "❌ Оплата отклонена."
        kind = "rejected"
    elif action == "ship":
        changed = await change_order_status(order_id, ("confirmed",), "shipped", tracking=note)
        done = "🚚 Заказ отмечен как отправленный."
        kind = "shipped"
    elif action == "cancel":
        changed = await change_order_status(order_id, CANCEL_FROM, "cancelled", reason=note)
        done = "🚫 Заказ отменён."
        kind = "cancelled"
    else:
        await message.answer("Действие не распознано. Откройте заказ заново.")
        return

    if not changed:
        await message.answer("⚠️ Статус заказа уже изменился, действие не выполнено.")
    else:
        order = await get_order(order_id)
        delivered = await notify_buyer(bot, order, kind, note)
        await message.answer(done + delivery_note(delivered))
    await send_order_view(message, order_id)


@router.message(AdminText.text, ~F.text)
async def text_expected(message: Message):
    await message.answer("Нужно отправить текстом. Отмена: /cancel")


# ---------- заказы: проданные товары ----------

@router.callback_query(F.data.startswith("ord:sold:"))
async def mark_sold(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or not await can_manage_shop(uid, order["shop_id"]):
        await callback.answer("Заказ не найден или нет доступа", show_alert=True)
        return
    await set_managed_shop(uid, order["shop_id"])
    if order["status"] not in ("confirmed", "shipped"):
        await callback.answer("Доступно только для оплаченных заказов", show_alert=True)
        return
    shop = await get_shop(order["shop_id"])
    channel_id = shop["channel_id"] if shop else 0
    ids = await mark_order_items_sold(order_id)
    failed = 0
    for item_id in ids:
        product = await get_product(item_id)
        if not product:
            continue
        try:
            await refresh_channel_post(bot, product, channel_id)
        except TelegramAPIError:
            failed += 1
    text = f"🏷 Отмечено проданными товаров: {len(ids)}. Посты в канале обновлены."
    if failed:
        text = f"🏷 Отмечено проданными: {len(ids)}. Посты не обновились: {failed} (проверьте права бота в канале)."
    await callback.answer(text, show_alert=True)


# ---------- клиенты магазина ----------

LANG_LABEL = {"ru": "🇷🇺 Русский", "uz": "🇺🇿 O'zbekcha"}


@router.callback_query(F.data.startswith("cus:l:"))
async def customers_list(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    total = await count_customers(shop["id"])
    pages = pages_count(total, PER_PAGE)
    page = min(max(page, 0), pages - 1)
    customers = await list_customers(shop["id"], PER_PAGE, page * PER_PAGE)
    text = (
        f"👥 <b>Клиенты магазина «{html.escape(shop['title'])}»</b> · всего {total}\n"
        "В кнопке указано число заказов клиента в вашем магазине."
    )
    if not customers:
        text = f"👥 <b>Клиенты магазина «{html.escape(shop['title'])}»</b>\n\nПока ни один покупатель не открывал витрину вашего магазина."
    await show(callback, text, customers_kb(customers, page, pages))
    await callback.answer()


@router.callback_query(F.data.startswith("cus:v:"))
async def customer_view(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    user_id = int(callback.data.split(":")[2])
    c = await get_customer(user_id, shop["id"])
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    spent = await customer_spent(user_id, shop["id"])
    lines = [
        f'👤 <a href="tg://user?id={user_id}">{html.escape(c["full_name"] or str(user_id))}</a>',
    ]
    if c["username"]:
        lines.append(f"@{html.escape(c['username'])}")
    lines += [
        f"ID: <code>{user_id}</code>",
        f"Язык: {LANG_LABEL.get(c['lang'], '—')}",
        f"В боте с: {fmt_dt(c['created_at'])}",
        "",
        f"📦 Заказов в «{html.escape(shop['title'])}»: {c['orders_count']} (оплачено: {c['paid_count']})",
        f"💰 Куплено на: {money(spent)}",
    ]
    if c["blocked"]:
        lines += ["", "🚫 Клиент заблокировал бота: сообщения ему не доходят."]
    await show(callback, "\n".join(lines), customer_kb(user_id))
    await callback.answer()


@router.callback_query(F.data.startswith("cus:o:"))
async def customer_orders(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    user_id = int(callback.data.split(":")[2])
    orders = await get_user_orders(user_id, 15, shop_id=shop["id"])
    text = (
        f"📦 <b>Заказы клиента в «{html.escape(shop['title'])}»</b>"
        if orders
        else "📦 У клиента пока нет заказов в вашем магазине."
    )
    await show(callback, text, customer_orders_kb(orders, user_id))
    await callback.answer()
