"""Панель продавца: мгновенное подключение канала, авто-парсер фото и альбомов, управление каналом и товарами."""
import asyncio
import html
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, ChatMemberUpdated, Message

from admin_tools import render_sub_upsell_text
from config import ADMIN_IDS
from db import (
    add_product,
    bind_shop_channel,
    can_manage_shop,
    compute_shop_sub_state,
    count_orders_by_status,
    count_products,
    create_shop,
    duplicate_product,
    ensure_user,
    get_all_products,
    get_lang,
    get_managed_shop,
    get_platform_billing_config,
    get_product,
    get_shop,
    get_shop_by_channel,
    get_shop_notify_ids,
    get_shop_subscription_info,
    get_sub_plans,
    get_user_shops,
    search_products,
    set_channel_messages,
    set_currency,
    set_description,
    set_hidden,
    set_in_stock,
    set_managed_shop,
    set_photos,
    set_price,
    set_title,
    update_shop_field,
)
from keyboards import (
    admin_menu,
    cancel_kb,
    channel_bind_kb,
    channel_kb,
    channel_manager_kb,
    channel_post_options_kb,
    channel_profile_kb,
    channel_request_reply_kb,
    channel_showcase_kb,
    confirm_kb,
    currency_kb,
    custom_url_kb,
    detected_channel_kb,
    main_menu,
    my_shops_kb,
    onboarding_kb,
    photos_done_kb,
    product_admin_kb,
    product_list_kb,
    products_menu_kb,
    skip_or_cancel_kb,
    sub_upsell_kb,
    subscription_menu_kb,
)
from posts import (
    build_channel_text,
    build_media,
    build_pinned_showcase_text,
    get_channel_ids,
    get_photos,
    parse_channel_post_caption,
    publish_product_to_channel,
    refresh_channel_post,
)
from texts import MENU_BUTTON_TEXTS, TEXTS, fmt_price, t
from utils import fmt_date, fmt_dt, get_bot_username, pages_count, reply, show

router = Router()

NOT_COMMAND = ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTON_TEXTS)
MAX_PHOTOS = 10
PER_PAGE = 8
photo_lock = asyncio.Lock()

# Буфер медиагрупп (альбомов), выложенных прямо в Telegram-канале
_CHANNEL_ALBUM_BUFFER: dict[str, dict] = {}
_CHANNEL_ALBUM_LOCK = asyncio.Lock()


class CreateShop(StatesGroup):
    title = State()
    description = State()
    card = State()
    holder = State()
    phone = State()


class BindChannel(StatesGroup):
    waiting = State()


class ChannelPostCompose(StatesGroup):
    waiting = State()
    choose_btn = State()
    custom_btn = State()


class ChannelProfileEdit(StatesGroup):
    title = State()
    bio = State()
    photo = State()


class AddProduct(StatesGroup):
    photo = State()
    title = State()
    description = State()
    price = State()
    currency = State()
    confirm = State()


class EditPrice(StatesGroup):
    value = State()


class EditField(StatesGroup):
    value = State()


class EditPhotos(StatesGroup):
    photo = State()


class FindProduct(StatesGroup):
    query = State()


def parse_price(text: str):
    digits = text.replace(" ", "").replace("_", "").replace(".", "").replace(",", "")
    if not digits.isdigit() or int(digits) <= 0:
        return None
    return int(digits)


# ====================== ГЛАВНОЕ МЕНЮ МАГАЗИНА И КАНАЛА ======================

async def menu_view(user_id: int, bot: Bot = None):
    shop = await get_managed_shop(user_id)
    shops = await get_user_shops(user_id)
    is_super = user_id in ADMIN_IDS
    bot_username = await get_bot_username(bot) if bot else ""
    billing = await get_platform_billing_config()
    p_usd = billing["price_usd"]
    usd_str = f"{int(p_usd)}$" if float(p_usd).is_integer() else f"{p_usd}$"

    if not shop:
        text = (
            "🚀 <b>Подключите свой Telegram-канал и превратите его в автоматизированный магазин!</b>\n\n"
            f"💎 <b>Подключение своего Telegram-канала и полная автоматизация продаж — по подписке PRO ({usd_str} / месяц)!</b>\n\n"
            "Что умеет бот для вашего канала:\n"
            "• 📢 <b>Подключение в 1 клик</b> — бот сам считает название и описание вашего канала\n"
            "• ⚡️ <b>Авто-распознавание товаров и альбомов</b> — публикуйте фото с ценой прямо в канале, и бот сам добавит кнопку <b>«🛒 Заказать»</b>\n"
            "• 🎟 <b>Промокоды и рассылки</b> — удерживайте подписчиков канала скидками и акциями\n"
            "• 🎨 <b>Полное управление каналом</b> — меняйте профиль канала, закрепляйте витрину и публикуйте посты с кнопками прямо из бота\n"
            "• 💳 <b>Оплата на вашу карту</b>, учёт заказов в 1 клик, CRM и аналитика\n\n"
            "👇 Нажмите <b>«📢 Подключить мой канал»</b> ниже:"
        )
        return text, onboarding_kb(bot_username, shops, is_superadmin=is_super)

    counts = await count_orders_by_status(shop["id"])
    sub_state = compute_shop_sub_state(shop, free_limit=billing["free_max_products"])
    has_channel = bool(shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    if has_channel:
        ch_name = shop.get("channel_title") or str(shop["channel_id"])
        if shop.get("channel_username"):
            ch_line = f"📢 Канал: <b>{html.escape(ch_name)}</b> (@{html.escape(shop['channel_username'])})"
        else:
            ch_line = f"📢 Канал: <b>{html.escape(ch_name)}</b> (<code>{shop['channel_id']}</code>)"
        auto_str = "ВКЛ ⚡️" if (shop.get("auto_parse_posts", 1) and sub_state["is_pro"]) else "ВЫКЛ"
        ch_line += f"\n🤖 Авто-товары из постов и альбомов: <b>{auto_str}</b>"
    else:
        ch_line = "📢 Канал: ⚠️ <i>не подключён (нажмите кнопку ниже, чтобы привязать)</i>"

    card_line = (
        f"💳 Карта: <code>{html.escape(shop['card_number'])}</code>"
        if shop.get("card_number")
        else "💳 Карта: ⚠️ <i>не указана (укажите в Настройках)</i>"
    )
    if sub_state["status"] == "vip":
        sub_line = "💎 Тариф: <b>👑 VIP PRO (Владелец платформы)</b>"
    elif sub_state["status"] == "pro":
        sub_line = f"💎 Тариф: <b>PRO до {fmt_date(sub_state['sub_until'])}</b> ({sub_state['days_left']} дн.)"
    elif sub_state["status"] == "trial":
        sub_line = f"🎁 Тариф: <b>Пробный PRO до {fmt_date(sub_state['sub_until'])}</b> ({sub_state['days_left']} дн. бесплатно)"
    else:
        sub_line = f"⚠️ Тариф: <b>Базовый (до {billing['free_max_products']} тов.)</b> — <i>PRO всего {usd_str}/мес!</i>"

    status_line = "🟢 Активен" if shop.get("is_active", 1) else "🔴 На паузе"
    link_line = (
        f"\n🔗 Витрина: <code>https://t.me/{bot_username}?start=s_{shop['id']}</code>"
        if bot_username
        else ""
    )

    text = (
        f"🛠 <b>Управление: «{html.escape(shop['title'])}»</b> ({status_line})\n\n"
        f"{sub_line}\n"
        f"{ch_line}\n"
        f"{card_line}"
        f"{link_line}"
    )
    kb = admin_menu(
        review_count=counts.get("review", 0),
        has_channel=has_channel,
        shops_count=len(shops),
        is_superadmin=is_super,
        sub_state=sub_state,
        price_usd=p_usd,
    )
    return text, kb


@router.message(StateFilter("*"), Command("admin", "myshop", "seller", "channel", "pro"))
@router.message(StateFilter("*"), F.text.in_({TEXTS["ru"]["btn_my_shop"], TEXTS["uz"]["btn_my_shop"]}))
async def admin_cmd(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    u = message.from_user
    await ensure_user(u.id, u.full_name, u.username)
    text, kb = await menu_view(u.id, bot)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.message(StateFilter("*"), Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    lang = await get_lang(message.from_user.id) or "ru"
    await message.answer("Действие отменено.", reply_markup=main_menu(lang))
    text, kb = await menu_view(message.from_user.id, bot)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "adm:cancel")
async def cancel_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    text, kb = await menu_view(callback.from_user.id, bot)
    await show(callback, "Отменено.\n\n" + text, kb)
    await callback.answer()


@router.callback_query(F.data == "adm:menu")
async def menu_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    text, kb = await menu_view(callback.from_user.id, bot)
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == "adm:noop")
async def noop_cb(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "adm:my_shops")
async def my_shops_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    shops = await get_user_shops(uid)
    cur = await get_managed_shop(uid)
    cur_id = cur["id"] if cur else 0
    await show(
        callback,
        "🏪 <b>Ваши подключённые каналы и магазины</b>\n"
        "Выберите нужный для управления или подключите ещё один канал:",
        my_shops_kb(shops, cur_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sel_shop:"))
async def select_managed_shop_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    uid = callback.from_user.id
    shop_id = int(callback.data.split(":")[2])
    if not await can_manage_shop(uid, shop_id):
        await callback.answer("У вас нет доступа к этому магазину", show_alert=True)
        return
    await set_managed_shop(uid, shop_id)
    shop = await get_shop(shop_id)
    await callback.answer(f"Выбран «{shop['title']}» ✅")
    text, kb = await menu_view(uid, bot)
    await show(callback, text, kb)


# ====================== БЫСТРОЕ ПОДКЛЮЧЕНИЕ КАНАЛА В 1 ШАГ ======================

async def _require_pro_for_channel(user, callback_or_msg, bot: Bot, shop_id: int = 0) -> tuple[bool, int]:
    """Проверяет, есть ли у пользователя активная подписка PRO для подключения канала.
    Если нет — создаёт черновик магазина (если его ещё нет) и предлагает купить подписку PRO.
    """
    uid = user.id
    await ensure_user(uid, user.full_name, user.username)
    shop = await get_shop(shop_id) if shop_id else await get_managed_shop(uid)
    if not shop:
        default_title = f"Магазин {(user.first_name or user.full_name or 'Продавец')[:30]}"
        new_sid = await create_shop(owner_id=uid, title=default_title)
        shop = await get_shop(new_sid)
    sub_info = await get_shop_subscription_info(shop["id"])
    if sub_info["is_pro"]:
        return True, shop["id"]

    plans = get_sub_plans(sub_info["billing"])
    upsell_text = (
        "🔒 <b>Для подключения Telegram-канала необходима подписка PRO!</b>\n\n"
        + render_sub_upsell_text(shop, sub_info, "Подключение Telegram-канала")
    )
    kb = subscription_menu_kb(plans)
    if isinstance(callback_or_msg, CallbackQuery):
        await show(callback_or_msg, upsell_text, kb)
    else:
        await bot.send_message(uid, upsell_text, reply_markup=kb, disable_web_page_preview=True)
    return False, shop["id"]


@router.callback_query(F.data == "adm:quick_channel")
async def quick_channel_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Мгновенное подключение нового канала (доступно при активной подписке PRO)."""
    ok_pro, shop_id = await _require_pro_for_channel(callback.from_user, callback, bot, shop_id=0)
    await callback.answer()
    if not ok_pro:
        return
    await prompt_bind_channel(callback.message, callback.from_user.id, state, bot, shop_id=shop_id, is_new=True)


# ====================== УПРАВЛЕНИЕ ПРИВЯЗАННЫМ КАНАЛОМ (chm:) ======================

async def channel_manager_text(shop: dict, bot: Bot) -> str:
    has_ch = bool(shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    if not has_ch:
        return (
            f"📢 <b>Канал для «{html.escape(shop['title'])}» ещё не подключён</b>\n\n"
            "Привяжите ваш Telegram-канал, чтобы публиковать товары, менять оформление канала, "
            "закреплять пост-витрину и автоматически добавлять кнопку «🛒 Заказать» к постам в канале."
        )
    ch_name = html.escape(shop.get("channel_title") or str(shop["channel_id"]))
    ch_user = f" (@{html.escape(shop['channel_username'])})" if shop.get("channel_username") else ""
    auto_on = bool(shop.get("auto_parse_posts", 1))
    bot_username = await get_bot_username(bot)

    return (
        f"📢 <b>Управление каналом {ch_name}{ch_user}</b>\n"
        f"ID канала: <code>{shop['channel_id']}</code>\n\n"
        f"⚡️ <b>Авто-товары из постов и альбомов канала:</b> {'ВКЛ ✅' if auto_on else 'ВЫКЛ ❌'}\n"
        "<i>Когда включено: просто опубликуйте в своём канале 1 фото или целый альбом (до 10 фото) с подписью и ценой "
        "(например: <code>Кроссовки Nike\\nРазмеры 41-44\\n450 000 сум</code>), и бот сам создаст товар и "
        "прикрепит к посту кнопку «🛒 Заказать»!</i>\n\n"
        f"🔗 Ссылка на витрину для описания канала:\n"
        f"<code>https://t.me/{bot_username}?start=s_{shop['id']}</code>"
    )


@router.callback_query(F.data == "chm:menu")
async def channel_manager_menu_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала подключите канал или создайте магазин", show_alert=True)
        return
    await show(callback, await channel_manager_text(shop, bot), channel_manager_kb(shop))
    await callback.answer()


@router.callback_query(F.data == "chm:toggle_auto")
async def channel_toggle_auto_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Авто-товары из постов канала"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return

    new_val = 0 if shop.get("auto_parse_posts", 1) else 1
    await update_shop_field(shop["id"], "auto_parse_posts", new_val)
    shop = await get_shop(shop["id"])
    await callback.answer(
        "⚡️ Авто-товары из постов и альбомов канала ВКЛЮЧЕНЫ!"
        if new_val
        else "Авто-товары из постов канала выключены"
    )
    await show(callback, await channel_manager_text(shop, bot), channel_manager_kb(shop))


@router.callback_query(F.data == "chm:pin_showcase")
async def channel_pin_showcase_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите канал!", show_alert=True)
        return
    bot_username = await get_bot_username(bot)
    text = build_pinned_showcase_text(shop)
    kb = channel_showcase_kb(bot_username, shop["id"])
    try:
        sent = await bot.send_message(
            shop["channel_id"], text, reply_markup=kb, disable_web_page_preview=True
        )
        pinned_ok = False
        try:
            await bot.pin_chat_message(shop["channel_id"], sent.message_id, disable_notification=True)
            pinned_ok = True
        except TelegramAPIError:
            pass
        await update_shop_field(shop["id"], "pinned_message_id", sent.message_id)
        msg = (
            "📌 Пост-витрина опубликован и закреплён в вашем канале!"
            if pinned_ok
            else "✅ Пост-витрина опубликован в канале! (Чтобы бот мог закреплять посты, включите ему право «Закрепление сообщений»)."
        )
        await callback.answer(msg, show_alert=True)
    except TelegramAPIError as e:
        await callback.answer(f"Ошибка публикации в канал: {e}", show_alert=True)


# ---------- конструктор постов в канал ----------

@router.callback_query(F.data == "chm:post")
async def channel_post_start_cb(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите канал!", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Конструктор постов в канал"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return

    await state.clear()
    await state.set_state(ChannelPostCompose.waiting)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        f"📝 <b>Конструктор поста в канал «{html.escape(shop['channel_title'] or shop['title'])}»</b>\n\n"
        "Отправьте сюда сообщение, которое хотите выложить в канал (текст, фото, видео, голосовое или кружочек).\n"
        "На следующем шаге вы выберете кнопку под постом и закрепление!\n\n"
        "Отмена: /cancel",
        cancel_kb("chm:menu"),
    )
    await callback.answer()


@router.message(ChannelPostCompose.waiting, NOT_COMMAND)
async def channel_post_receive_content(message: Message, state: FSMContext):
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    await state.set_state(ChannelPostCompose.choose_btn)
    await message.reply(
        "✅ Пост принят! Как опубликовать его в вашем канале?",
        reply_markup=channel_post_options_kb(),
    )


@router.callback_query(ChannelPostCompose.choose_btn, F.data.startswith("chm:pub:"))
async def channel_post_publish_option(callback: CallbackQuery, state: FSMContext, bot: Bot):
    mode = callback.data.split(":")[2]
    data = await state.get_data()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await state.clear()
        await callback.answer("Канал не привязан", show_alert=True)
        return

    if mode == "custom":
        await state.set_state(ChannelPostCompose.custom_btn)
        await show(
            callback,
            "🔗 Введите текст кнопки и ссылку через дефис или вертикальную черту, например:\n"
            "<code>Написать менеджеру | https://t.me/username</code>",
            cancel_kb("chm:menu"),
        )
        await callback.answer()
        return

    await state.clear()
    bot_username = await get_bot_username(bot)
    kb = channel_showcase_kb(bot_username, shop["id"]) if mode in ("shop", "pin") else None
    try:
        sent = await bot.copy_message(
            chat_id=shop["channel_id"],
            from_chat_id=data["src_chat"],
            message_id=data["src_msg"],
            reply_markup=kb,
        )
        pin_note = ""
        if mode == "pin":
            try:
                await bot.pin_chat_message(shop["channel_id"], sent.message_id, disable_notification=True)
                pin_note = " и закреплён в шапке канала"
            except TelegramAPIError:
                pin_note = " (не удалось закрепить — проверьте право «Закрепление сообщений»)"
        await show(
            callback,
            f"✅ <b>Пост успешно опубликован в канале{pin_note}!</b>\n\n"
            + await channel_manager_text(shop, bot),
            channel_manager_kb(shop),
        )
    except TelegramAPIError as e:
        await show(
            callback,
            f"❌ Не удалось опубликовать пост в канале: {html.escape(str(e))}",
            channel_manager_kb(shop),
        )
    await callback.answer()


@router.message(ChannelPostCompose.custom_btn, F.text, NOT_COMMAND)
async def channel_post_custom_btn_send(message: Message, state: FSMContext, bot: Bot):
    raw = message.text.strip()
    sep = "|" if "|" in raw else (" - " if " - " in raw else None)
    if not sep:
        await message.answer(
            "Формат не распознан. Отправьте в виде:\n<code>Текст кнопки | https://t.me/...</code>"
        )
        return
    btn_text, btn_url = [x.strip() for x in raw.split(sep, 1)]
    if btn_url.startswith("@"):
        btn_url = "https://t.me/" + btn_url.lstrip("@")
    elif btn_url.startswith("t.me/"):
        btn_url = "https://" + btn_url
    if not (btn_url.startswith("http://") or btn_url.startswith("https://") or btn_url.startswith("tg://")):
        await message.answer("Ссылка должна начинаться с https:// или @username. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    await state.clear()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(message.from_user.id)
    if not shop or not shop.get("channel_id"):
        await message.answer("Канал не привязан.")
        return

    try:
        await bot.copy_message(
            chat_id=shop["channel_id"],
            from_chat_id=data["src_chat"],
            message_id=data["src_msg"],
            reply_markup=custom_url_kb(btn_text[:40], btn_url),
        )
        await message.answer(
            "✅ <b>Пост со своей кнопкой опубликован в канале!</b>",
            reply_markup=channel_manager_kb(shop),
        )
    except TelegramAPIError as e:
        await message.answer(
            f"❌ Ошибка публикации: {html.escape(str(e))}",
            reply_markup=channel_manager_kb(shop),
        )


# ---------- управление профилем самого Telegram-канала из бота ----------

@router.callback_query(F.data == "chm:profile")
async def channel_profile_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите канал!", show_alert=True)
        return
    await show(
        callback,
        f"🎨 <b>Оформление Telegram-канала «{html.escape(shop['channel_title'] or shop['title'])}»</b>\n\n"
        "Вы можете прямо из бота менять название, описание (Bio) и аватарку вашего канала в Telegram "
        "(для этого у бота в канале должно быть включено право <i>«Изменение профиля канала»</i>):",
        channel_profile_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "chm:auto_bio")
async def channel_auto_bio_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите канал!", show_alert=True)
        return
    bot_username = await get_bot_username(bot)
    link = f"https://t.me/{bot_username}?start=s_{shop['id']}"
    base_descr = (shop.get("description") or "").strip()
    new_bio = f"{base_descr}\n🛍 Витрина и заказ: {link}".strip()[:255]
    try:
        await bot.set_chat_description(shop["channel_id"], new_bio)
        await callback.answer("✅ Ссылка на витрину добавлена в описание вашего канала!", show_alert=True)
    except TelegramAPIError as e:
        await callback.answer(
            f"Не удалось изменить описание канала (включите боту право «Изменение профиля канала»): {e}",
            show_alert=True,
        )


@router.callback_query(F.data == "chm:ed_title")
async def channel_edit_title_start(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id"):
        await callback.answer("Канал не привязан", show_alert=True)
        return
    await state.clear()
    await state.set_state(ChannelProfileEdit.title)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        "✏️ Введите <b>новое название для вашего Telegram-канала</b> (оно сразу обновится в Telegram и в магазине):",
        cancel_kb("chm:profile"),
    )
    await callback.answer()


@router.message(ChannelProfileEdit.title, F.text, NOT_COMMAND)
async def channel_edit_title_save(message: Message, state: FSMContext, bot: Bot):
    new_title = message.text.strip()
    if not 2 <= len(new_title) <= 100:
        await message.answer("Название должно быть от 2 до 100 символов:")
        return
    data = await state.get_data()
    await state.clear()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(message.from_user.id)
    if not shop or not shop.get("channel_id"):
        await message.answer("Канал не привязан.")
        return
    try:
        await bot.set_chat_title(shop["channel_id"], new_title)
        await bind_shop_channel(
            shop["id"],
            shop["channel_id"],
            new_title,
            shop.get("channel_username") or "",
            sync_title=True,
        )
        await message.answer(
            f"✅ Название канала и магазина изменено на <b>«{html.escape(new_title)}»</b>!",
            reply_markup=channel_profile_kb(),
        )
    except TelegramAPIError as e:
        await message.answer(
            f"❌ Не удалось изменить название канала (включите боту право «Изменение профиля канала»): {html.escape(str(e))}",
            reply_markup=channel_profile_kb(),
        )


@router.callback_query(F.data == "chm:ed_bio")
async def channel_edit_bio_start(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id"):
        await callback.answer("Канал не привязан", show_alert=True)
        return
    await state.clear()
    await state.set_state(ChannelProfileEdit.bio)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        "📝 Введите <b>новое описание (Bio) для вашего Telegram-канала</b> (до 255 символов):",
        cancel_kb("chm:profile"),
    )
    await callback.answer()


@router.message(ChannelProfileEdit.bio, F.text, NOT_COMMAND)
async def channel_edit_bio_save(message: Message, state: FSMContext, bot: Bot):
    new_bio = message.text.strip()
    if len(new_bio) > 255:
        await message.answer("Описание канала в Telegram не может превышать 255 символов. Сократите его:")
        return
    data = await state.get_data()
    await state.clear()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(message.from_user.id)
    if not shop or not shop.get("channel_id"):
        await message.answer("Канал не привязан.")
        return
    try:
        await bot.set_chat_description(shop["channel_id"], new_bio)
        await update_shop_field(shop["id"], "description", new_bio)
        await message.answer(
            "✅ Описание канала и магазина успешно обновлено!",
            reply_markup=channel_profile_kb(),
        )
    except TelegramAPIError as e:
        await message.answer(
            f"❌ Не удалось изменить описание канала (проверьте право «Изменение профиля канала»): {html.escape(str(e))}",
            reply_markup=channel_profile_kb(),
        )


@router.callback_query(F.data == "chm:ed_photo")
async def channel_edit_photo_start(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id"):
        await callback.answer("Канал не привязан", show_alert=True)
        return
    await state.clear()
    await state.set_state(ChannelProfileEdit.photo)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        "🖼 Отправьте <b>новую картинку (фото)</b>, которую нужно установить на аватарку вашего Telegram-канала:",
        cancel_kb("chm:profile"),
    )
    await callback.answer()


@router.message(ChannelProfileEdit.photo, F.photo)
async def channel_edit_photo_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(message.from_user.id)
    if not shop or not shop.get("channel_id"):
        await message.answer("Канал не привязан.")
        return
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        downloaded = await bot.download_file(file_info.file_path)
        input_photo = BufferedInputFile(downloaded.read(), filename="avatar.jpg")
        await bot.set_chat_photo(shop["channel_id"], photo=input_photo)
        await message.answer(
            "✅ Новая аватарка канала успешно установлена!",
            reply_markup=channel_profile_kb(),
        )
    except TelegramAPIError as e:
        await message.answer(
            f"❌ Не удалось обновить аватарку канала (проверьте право «Изменение профиля канала»): {html.escape(str(e))}",
            reply_markup=channel_profile_kb(),
        )


@router.message(ChannelProfileEdit.photo, NOT_COMMAND)
async def channel_edit_photo_wrong(message: Message):
    await message.answer("Пожалуйста, отправьте картинку как обычное фото (или нажмите /cancel).")


@router.callback_query(F.data == "chm:sync_all")
async def channel_sync_all_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите канал!", show_alert=True)
        return
    products = await get_all_products(shop["id"], limit=50, offset=0)
    updated = 0
    for p in products:
        if get_channel_ids(p):
            try:
                await refresh_channel_post(bot, p, shop["channel_id"])
                updated += 1
            except TelegramAPIError:
                pass
    await callback.answer(f"🔄 Синхронизировано постов в канале: {updated}", show_alert=True)


@router.callback_query(F.data == "chm:check")
async def channel_check_rights_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Канал не привязан", show_alert=True)
        return
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(shop["channel_id"], me.id)
        if m.status != "administrator":
            await callback.answer("⚠️ Бот НЕ является администратором канала!", show_alert=True)
            return
        post_ok = "✅" if getattr(m, "can_post_messages", False) else "❌"
        edit_ok = "✅" if getattr(m, "can_edit_messages", False) else "❌"
        del_ok = "✅" if getattr(m, "can_delete_messages", False) else "❌"
        pin_ok = "✅" if getattr(m, "can_pin_messages", False) else "➖"
        info_ok = "✅" if getattr(m, "can_change_info", False) else "➖"
        await callback.answer(
            f"Права бота в канале:\n"
            f"{post_ok} Публикация постов\n"
            f"{edit_ok} Редактирование постов\n"
            f"{del_ok} Удаление постов\n"
            f"{pin_ok} Закрепление сообщений\n"
            f"{info_ok} Изменение профиля канала",
            show_alert=True,
        )
    except TelegramAPIError as e:
        await callback.answer(f"Ошибка доступа к каналу: {e}", show_alert=True)


# ====================== АВТО-ПАРСЕР ПОСТОВ И АЛЬБОМОВ В ПРИВЯЗАННОМ КАНАЛЕ ======================

async def _finalize_auto_parsed_product(
    bot: Bot, shop: dict, parsed: dict, photo_ids: list, message_ids: list
):
    product_id = await add_product(
        shop_id=shop["id"],
        title=parsed["title"],
        description=parsed["description"],
        price=parsed["price"],
        currency=parsed["currency"],
        photo_ids=photo_ids[:MAX_PHOTOS],
    )
    await set_channel_messages(product_id, message_ids)
    product = await get_product(product_id)

    try:
        await refresh_channel_post(bot, product, shop["channel_id"])
    except TelegramAPIError:
        pass

    cur_str = "$" if parsed["currency"] == "USD" else "сум"
    photos_note = f" ({len(photo_ids)} фото)" if len(photo_ids) > 1 else ""
    notify_ids = await get_shop_notify_ids(shop["id"])
    for uid in notify_ids:
        try:
            await bot.send_message(
                uid,
                f"⚡️ <b>Пост в канале «{html.escape(shop['title'])}» автоматически добавлен в каталог!</b>\n\n"
                f"🛍 Товар: <b>{html.escape(parsed['title'])}</b>{photos_note}\n"
                f"💰 Цена: <b>{fmt_price(parsed['price'])} {cur_str}</b>\n"
                "К посту в канале автоматически прикреплена ссылка/кнопка «🛒 Заказать».",
                reply_markup=product_admin_kb(product, has_channel=True),
            )
        except TelegramAPIError:
            pass


async def _flush_channel_album(group_key: str, bot: Bot):
    await asyncio.sleep(1.5)
    async with _CHANNEL_ALBUM_LOCK:
        entry = _CHANNEL_ALBUM_BUFFER.pop(group_key, None)
    if not entry:
        return
    parsed = parse_channel_post_caption(entry["caption"])
    if not parsed or not entry["photos"]:
        return
    await _finalize_auto_parsed_product(
        bot=bot,
        shop=entry["shop"],
        parsed=parsed,
        photo_ids=entry["photos"],
        message_ids=entry["mids"],
    )


@router.channel_post(F.photo)
async def on_bound_channel_photo_post(message: Message, bot: Bot):
    """Автоматически распознаёт как одиночные фото, так и альбомы (media_group_id) с ценой в канале!"""
    shop = await get_shop_by_channel(message.chat.id)
    if not shop or not shop.get("is_active", 1) or not shop.get("auto_parse_posts", 1):
        return

    sub_state = compute_shop_sub_state(shop)
    if not sub_state["is_pro"]:
        return

    photo_id = message.photo[-1].file_id

    if message.media_group_id:
        group_key = f"{message.chat.id}:{message.media_group_id}"
        async with _CHANNEL_ALBUM_LOCK:
            if group_key not in _CHANNEL_ALBUM_BUFFER:
                _CHANNEL_ALBUM_BUFFER[group_key] = {
                    "shop": shop,
                    "caption": message.caption or "",
                    "photos": [photo_id],
                    "mids": [message.message_id],
                }
                asyncio.create_task(_flush_channel_album(group_key, bot))
            else:
                buf = _CHANNEL_ALBUM_BUFFER[group_key]
                if not buf["caption"] and message.caption:
                    buf["caption"] = message.caption
                if len(buf["photos"]) < MAX_PHOTOS:
                    buf["photos"].append(photo_id)
                buf["mids"].append(message.message_id)
        return

    parsed = parse_channel_post_caption(message.caption or "")
    if not parsed:
        return

    await _finalize_auto_parsed_product(
        bot=bot,
        shop=shop,
        parsed=parsed,
        photo_ids=[photo_id],
        message_ids=[message.message_id],
    )


# ====================== МАСТЕР СОЗДАНИЯ МАГАЗИНА ВРУЧНУЮ ======================

@router.callback_query(F.data == "adm:create_start")
async def create_shop_start(callback: CallbackQuery, state: FSMContext):
    u = callback.from_user
    await ensure_user(u.id, u.full_name, u.username)
    await state.clear()
    await state.set_state(CreateShop.title)
    await reply(
        callback,
        "🏪 <b>Создание нового магазина (Шаг 1 из 3)</b>\n\n"
        "✏️ Введите <b>название вашего магазина</b> (например, <i>Tashkent Brand Shop</i>):",
        cancel_kb(),
    )
    await callback.answer()


@router.message(CreateShop.title, F.text, NOT_COMMAND)
async def create_shop_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if title == "❌ Отмена":
        await state.clear()
        lang = await get_lang(message.from_user.id) or "ru"
        await message.answer("Отменено.", reply_markup=main_menu(lang))
        return
    if not 2 <= len(title) <= 60:
        await message.answer("Название должно быть от 2 до 60 символов. Попробуйте ещё раз:")
        return
    await state.update_data(title=title)
    await state.set_state(CreateShop.description)
    await message.answer(
        "📝 <b>Шаг 2 из 3: Описание магазина</b>\n\n"
        "Введите краткое описание вашего магазина (что продаёте, условия доставки и т. д., до 300 символов) "
        "или нажмите «Пропустить»:",
        reply_markup=skip_or_cancel_kb("adm:skip_descr"),
    )


@router.callback_query(CreateShop.description, F.data == "adm:skip_descr")
async def create_shop_skip_descr(callback: CallbackQuery, state: FSMContext):
    await state.update_data(description="")
    await state.set_state(CreateShop.card)
    await reply(
        callback,
        "💳 <b>Шаг 3 из 3: Карта для приёма оплаты</b>\n\n"
        "Введите номер вашей карты (16 цифр, например <code>8600 0000 0000 0000</code>), "
        "на которую покупатели будут переводить оплату, или нажмите «Пропустить»:",
        skip_or_cancel_kb("adm:skip_card"),
    )
    await callback.answer()


@router.message(CreateShop.description, F.text, NOT_COMMAND)
async def create_shop_descr(message: Message, state: FSMContext):
    descr = message.text.strip()
    if descr == "-":
        descr = ""
    if len(descr) > 300:
        await message.answer("Описание слишком длинное (максимум 300 символов). Сократите его:")
        return
    await state.update_data(description=descr)
    await state.set_state(CreateShop.card)
    await message.answer(
        "💳 <b>Шаг 3 из 3: Карта для приёма оплаты</b>\n\n"
        "Введите номер вашей карты (16 цифр, например <code>8600 0000 0000 0000</code>), "
        "на которую покупатели будут переводить оплату, или нажмите «Пропустить»:",
        reply_markup=skip_or_cancel_kb("adm:skip_card"),
    )


def _normalize_card(raw: str):
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 16:
        return None
    return " ".join(digits[i:i + 4] for i in range(0, 16, 4))


def _normalize_phone_pretty(raw: str):
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) != 12 or not digits.startswith("998"):
        return None
    return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:12]}"


@router.callback_query(CreateShop.card, F.data == "adm:skip_card")
async def create_shop_skip_card(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    shop_id = await create_shop(
        owner_id=callback.from_user.id,
        title=data["title"],
        description=data.get("description", ""),
    )
    await callback.answer("Магазин создан! 🎉")
    sub_info = await get_shop_subscription_info(shop_id)
    if not sub_info["is_pro"]:
        text, kb = await menu_view(callback.from_user.id, bot)
        await show(
            callback,
            "✅ <b>Магазин успешно создан!</b>\n"
            "🔒 <i>Чтобы привязать к нему ваш Telegram-канал, купите подписку PRO (1$/мес) в меню ниже:</i>\n\n" + text,
            kb,
        )
        return
    await prompt_bind_channel(callback.message, callback.from_user.id, state, bot, shop_id, is_new=True)


@router.message(CreateShop.card, F.text, NOT_COMMAND)
async def create_shop_card(message: Message, state: FSMContext):
    card = _normalize_card(message.text)
    if not card:
        await message.answer(
            "Номер карты должен состоять из 16 цифр. Введите снова или нажмите «Пропустить»:",
            reply_markup=skip_or_cancel_kb("adm:skip_card"),
        )
        return
    await state.update_data(card_number=card)
    await state.set_state(CreateShop.holder)
    await message.answer(
        "👤 Введите <b>имя и фамилию получателя карты</b> (например, <code>ALI VALIYEV</code>):",
        reply_markup=skip_or_cancel_kb("adm:skip_holder"),
    )


@router.callback_query(CreateShop.holder, F.data == "adm:skip_holder")
async def create_shop_skip_holder(callback: CallbackQuery, state: FSMContext):
    await state.update_data(card_holder="")
    await state.set_state(CreateShop.phone)
    await reply(
        callback,
        "☎️ Введите <b>контактный телефон магазина</b> (например, <code>+998 90 123 45 67</code>) или пропустите:",
        skip_or_cancel_kb("adm:skip_phone"),
    )
    await callback.answer()


@router.message(CreateShop.holder, F.text, NOT_COMMAND)
async def create_shop_holder(message: Message, state: FSMContext):
    holder = message.text.strip()
    if not 2 <= len(holder) <= 50:
        await message.answer("Введите имя получателя (от 2 до 50 символов):")
        return
    await state.update_data(card_holder=holder)
    await state.set_state(CreateShop.phone)
    await message.answer(
        "☎️ Введите <b>контактный телефон магазина</b> (например, <code>+998 90 123 45 67</code>) или пропустите:",
        reply_markup=skip_or_cancel_kb("adm:skip_phone"),
    )


@router.callback_query(CreateShop.phone, F.data == "adm:skip_phone")
async def create_shop_skip_phone(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    shop_id = await create_shop(
        owner_id=callback.from_user.id,
        title=data["title"],
        description=data.get("description", ""),
        card_number=data.get("card_number", ""),
        card_holder=data.get("card_holder", ""),
    )
    await callback.answer("Магазин создан! 🎉")
    sub_info = await get_shop_subscription_info(shop_id)
    if not sub_info["is_pro"]:
        text, kb = await menu_view(callback.from_user.id, bot)
        await show(
            callback,
            "✅ <b>Магазин успешно создан!</b>\n"
            "🔒 <i>Чтобы привязать к нему ваш Telegram-канал, купите подписку PRO (1$/мес) в меню ниже:</i>\n\n" + text,
            kb,
        )
        return
    await prompt_bind_channel(callback.message, callback.from_user.id, state, bot, shop_id, is_new=True)


@router.message(CreateShop.phone, F.text, NOT_COMMAND)
async def create_shop_phone(message: Message, state: FSMContext, bot: Bot):
    phone = _normalize_phone_pretty(message.text)
    if not phone:
        await message.answer(
            "Номер не распознан. Введите в формате +998 90 123 45 67 или нажмите «Пропустить»:",
            reply_markup=skip_or_cancel_kb("adm:skip_phone"),
        )
        return
    data = await state.get_data()
    await state.clear()
    shop_id = await create_shop(
        owner_id=message.from_user.id,
        title=data["title"],
        description=data.get("description", ""),
        card_number=data.get("card_number", ""),
        card_holder=data.get("card_holder", ""),
        shop_phone=phone,
    )
    sub_info = await get_shop_subscription_info(shop_id)
    if not sub_info["is_pro"]:
        text, kb = await menu_view(message.from_user.id, bot)
        await message.answer(
            "✅ <b>Магазин успешно создан!</b>\n"
            "🔒 <i>Чтобы привязать к нему ваш Telegram-канал, купите подписку PRO (1$/мес) в меню ниже:</i>\n\n" + text,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        return
    await prompt_bind_channel(message, message.from_user.id, state, bot, shop_id, is_new=True)


# ====================== ПРИВЯЗКА TELEGRAM-КАНАЛА (4 СПОСОБА + АВТОИМПОРТ ДАННЫХ) ======================

async def prompt_bind_channel(
    message: Message, user_id: int, state: FSMContext, bot: Bot, shop_id: int = 0, is_new: bool = False
):
    await state.clear()
    await state.set_state(BindChannel.waiting)
    await state.update_data(shop_id=shop_id)
    shop = await get_shop(shop_id) if shop_id else None
    bot_username = await get_bot_username(bot)

    if shop:
        header = f"📢 <b>Привязка канала к магазину «{html.escape(shop['title'])}»</b>\n\n"
    else:
        header = "📢 <b>Быстрое подключение вашего Telegram-канала</b>\n\n"

    text = (
        header
        + "Выберите любой удобный способ подключения:\n\n"
        + "1️⃣ <b>Кнопкой выбора канала (рекомендуется):</b> нажмите кнопку <b>«📢 Выбрать мой канал в Telegram»</b> внизу экрана — Telegram сам выдаст боту нужные права и подключит канал.\n"
        + "2️⃣ <b>Через инлайн-кнопку ниже:</b> нажмите «➕ Добавить бота в админы канала (1 клик)» и выберите ваш канал.\n"
        + "3️⃣ <b>Пересылкой поста:</b> добавьте бота администратором в канал и <b>перешлите сюда любой пост</b> из канала.\n"
        + "4️⃣ <b>По @username или ID:</b> отправьте <code>@username</code> канала, ссылку <code>https://t.me/...</code> или ID (<code>-100...</code>)."
    )
    await bot.send_message(
        user_id,
        "👇 Нажмите кнопку внизу, чтобы выбрать ваш канал из списка Telegram:",
        reply_markup=channel_request_reply_kb(),
    )
    await bot.send_message(
        user_id,
        text,
        reply_markup=channel_bind_kb(bot_username, allow_skip=bool(shop and is_new)),
        disable_web_page_preview=True,
    )


async def verify_and_bind_channel(
    bot: Bot, user_id: int, shop_id: int, chat_identifier
) -> tuple[bool, str, int]:
    """Проверяет наличие подписки PRO, права бота и владельца в канале и привязывает канал."""
    target_shop = await get_shop(shop_id) if shop_id else await get_managed_shop(user_id)
    if not target_shop and user_id not in ADMIN_IDS:
        new_sid = await create_shop(owner_id=user_id, title="Мой магазин")
        target_shop = await get_shop(new_sid)
        shop_id = new_sid
    if target_shop:
        sub_check = await get_shop_subscription_info(target_shop["id"])
        if not sub_check["is_pro"]:
            return (
                False,
                "🔒 <b>Для подключения Telegram-канала необходима подписка PRO (1$ / месяц)!</b>\n\n"
                "Откройте раздел <b>«💎 Подписка PRO (1$/мес)»</b> в меню магазина, переведите оплату на карту и отправьте скриншот чека. "
                "Сразу после подтверждения вы сможете подключить ваш канал!",
                target_shop["id"],
            )
    try:
        chat = await bot.get_chat(chat_identifier)
    except TelegramAPIError:
        return (
            False,
            "❌ Не удалось найти этот канал. Убедитесь, что вы сначала добавили бота в администраторы канала.",
            shop_id,
        )

    if chat.type != "channel":
        return (
            False,
            "❌ Это не Telegram-канал. Пожалуйста, выберите именно канал (а не группу или личный чат).",
            shop_id,
        )

    me = await bot.get_me()
    try:
        bot_member = await bot.get_chat_member(chat.id, me.id)
    except TelegramAPIError:
        return (
            False,
            f"❌ Бот не является администратором канала «{html.escape(chat.title or '')}».\n"
            "Сначала добавьте бота в администраторы канала.",
            shop_id,
        )

    if bot_member.status != "administrator":
        return (
            False,
            f"⚠️ Бот найден в канале «{html.escape(chat.title or '')}», но не назначен <b>администратором</b>.\n"
            "Выдайте боту права администратора (публикация и редактирование сообщений) и повторите попытку.",
            shop_id,
        )

    if not getattr(bot_member, "can_post_messages", True):
        return (
            False,
            f"⚠️ У бота в канале «{html.escape(chat.title or '')}» отключено право <b>«Публикация сообщений»</b>.\n"
            "Включите это право в настройках администраторов канала.",
            shop_id,
        )

    if user_id not in ADMIN_IDS:
        try:
            user_member = await bot.get_chat_member(chat.id, user_id)
            if user_member.status not in ("creator", "administrator"):
                return (
                    False,
                    "❌ Вы не являетесь владельцем или администратором этого канала.",
                    shop_id,
                )
        except TelegramAPIError:
            return (
                False,
                "❌ Не удалось подтвердить ваши права администратора в этом канале.",
                shop_id,
            )

    ch_title = (chat.title or "Мой магазин").strip()
    ch_user = (chat.username or "").strip()
    ch_descr = (getattr(chat, "description", "") or "").strip()[:300]

    if not shop_id:
        existing_for_ch = await get_shop_by_channel(chat.id)
        if existing_for_ch and await can_manage_shop(user_id, existing_for_ch["id"]):
            shop_id = existing_for_ch["id"]
            await set_managed_shop(user_id, shop_id)
            await bind_shop_channel(shop_id, chat.id, ch_title, ch_user)
        else:
            shop_id = await create_shop(
                owner_id=user_id,
                title=ch_title[:60],
                description=ch_descr,
                channel_id=chat.id,
                channel_title=ch_title,
                channel_username=ch_user,
            )
    else:
        await bind_shop_channel(shop_id, chat.id, ch_title, ch_user)

    sub_info = await get_shop_subscription_info(shop_id)
    ch_display = f"<b>{html.escape(ch_title)}</b>"
    if ch_user:
        ch_display += f" (@{html.escape(ch_user)})"
    trial_note = (
        f"\n🎁 <b>Активирован бесплатный PRO-доступ до {fmt_date(sub_info['sub_until'])}!</b>"
        if sub_info["is_pro"] and sub_info.get("sub_until")
        else ""
    )
    return (
        True,
        f"✅ Канал {ch_display} успешно подключён!{trial_note}\n\n"
        "• Теперь вы можете управлять каналом через раздел <b>«📢 Управление каналом»</b>\n"
        "• Публикуйте товары через бота или прямо в канале (с фото/альбомом и ценой в подписи) — бот сам добавит кнопку «🛒 Заказать»!",
        shop_id,
    )


@router.callback_query(F.data == "set:channel")
async def start_bind_channel_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    shop_id = shop["id"] if shop else 0
    ok_pro, resolved_sid = await _require_pro_for_channel(callback.from_user, callback, bot, shop_id=shop_id)
    await callback.answer()
    if not ok_pro:
        return
    await prompt_bind_channel(callback.message, callback.from_user.id, state, bot, resolved_sid, is_new=False)


@router.callback_query(F.data == "adm:skip_channel")
async def skip_channel_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    uid = callback.from_user.id
    lang = await get_lang(uid) or "ru"
    await bot.send_message(
        uid,
        "Вы можете подключить канал в любой момент в меню управления магазином.",
        reply_markup=main_menu(lang),
    )
    text, kb = await menu_view(uid, bot)
    await bot.send_message(uid, text, reply_markup=kb, disable_web_page_preview=True)
    await callback.answer()


@router.message(F.chat_shared)
async def on_chat_shared(message: Message, state: FSMContext, bot: Bot):
    """Обработка нативной кнопки выбора канала (KeyboardButtonRequestChat)."""
    uid = message.from_user.id
    await ensure_user(uid, message.from_user.full_name, message.from_user.username)
    lang = await get_lang(uid) or "ru"
    shared_chat_id = message.chat_shared.chat_id
    data = await state.get_data()
    shop_id = data.get("shop_id") or 0

    ok, msg_text, _ = await verify_and_bind_channel(bot, uid, shop_id, shared_chat_id)
    await state.clear()
    await message.answer(msg_text, reply_markup=main_menu(lang))
    text, kb = await menu_view(uid, bot)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.message(BindChannel.waiting, F.forward_from_chat)
async def bind_channel_forwarded(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    lang = await get_lang(uid) or "ru"
    data = await state.get_data()
    shop_id = data.get("shop_id") or 0

    ok, msg_text, _ = await verify_and_bind_channel(bot, uid, shop_id, message.forward_from_chat.id)
    if not ok:
        await message.answer(msg_text)
        return
    await state.clear()
    await message.answer(msg_text, reply_markup=main_menu(lang))
    text, kb = await menu_view(uid, bot)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


def _parse_channel_input(raw: str):
    s = raw.strip()
    if s.lstrip("-").isdigit():
        return int(s)
    m = re.search(r"(?:t\.me/|@)([a-zA-Z0-9_]{4,})", s)
    if m:
        return "@" + m.group(1)
    if re.match(r"^[a-zA-Z0-9_]{4,}$", s):
        return "@" + s
    return None


@router.message(BindChannel.waiting, F.text, NOT_COMMAND)
async def bind_channel_text(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    lang = await get_lang(uid) or "ru"
    raw = message.text.strip()
    if raw in ("❌ Отмена", t("ru", "btn_cancel"), t("uz", "btn_cancel")):
        await state.clear()
        await message.answer("Подключение канала отменено.", reply_markup=main_menu(lang))
        text, kb = await menu_view(uid, bot)
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
        return

    if getattr(message, "forward_origin", None) and getattr(message.forward_origin, "chat", None):
        target = message.forward_origin.chat.id
    else:
        target = _parse_channel_input(raw)

    if target is None:
        await message.answer(
            "⚠️ Не удалось распознать канал.\n"
            "Нажмите кнопку <b>«📢 Выбрать мой канал в Telegram»</b> внизу, "
            "перешлите пост из канала или отправьте его <code>@username</code> / ID (<code>-100...</code>)."
        )
        return

    data = await state.get_data()
    shop_id = data.get("shop_id") or 0

    ok, msg_text, _ = await verify_and_bind_channel(bot, uid, shop_id, target)
    if not ok:
        await message.answer(msg_text)
        return
    await state.clear()
    await message.answer(msg_text, reply_markup=main_menu(lang))
    text, kb = await menu_view(uid, bot)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.message(BindChannel.waiting)
async def bind_channel_other(message: Message, state: FSMContext, bot: Bot):
    """Если переслано фото/медиа из канала (без текста)."""
    fchat = getattr(message, "forward_from_chat", None)
    if not fchat and getattr(message, "forward_origin", None):
        fchat = getattr(message.forward_origin, "chat", None)
    if not fchat:
        await message.answer(
            "Перешлите сообщение из вашего канала, нажмите кнопку «📢 Выбрать мой канал» или введите @username канала."
        )
        return
    uid = message.from_user.id
    lang = await get_lang(uid) or "ru"
    data = await state.get_data()
    shop_id = data.get("shop_id") or 0
    ok, msg_text, _ = await verify_and_bind_channel(bot, uid, shop_id, fchat.id)
    if not ok:
        await message.answer(msg_text)
        return
    await state.clear()
    await message.answer(msg_text, reply_markup=main_menu(lang))
    text, kb = await menu_view(uid, bot)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.my_chat_member()
async def on_bot_channel_status_change(event: ChatMemberUpdated, bot: Bot):
    """Автоматическое обнаружение, когда владелец добавляет бота администратором в свой канал!"""
    if event.chat.type != "channel":
        return
    new_status = event.new_chat_member.status
    if new_status != "administrator":
        return

    actor = event.from_user
    if not actor or actor.is_bot:
        return

    await ensure_user(actor.id, actor.full_name, actor.username)
    shop = await get_managed_shop(actor.id)
    ch_title = html.escape(event.chat.title or str(event.chat.id))
    lang = await get_lang(actor.id) or "ru"

    if not shop and actor.id not in ADMIN_IDS:
        sid = await create_shop(owner_id=actor.id, title=(event.chat.title or "Мой магазин")[:60])
        shop = await get_shop(sid)

    if shop:
        sub_info = await get_shop_subscription_info(shop["id"])
        if not sub_info["is_pro"]:
            try:
                plans = get_sub_plans(sub_info["billing"])
                await bot.send_message(
                    actor.id,
                    f"👋 Вы добавили бота администратором в канал <b>«{ch_title}»</b>!\n\n"
                    "🔒 <b>Для подключения канала к магазину необходима подписка PRO (1$ / месяц).</b>\n"
                    "Выберите тариф ниже, переведите оплату на карту и отправьте чек — после активации PRO ваш канал сразу подключится!",
                    reply_markup=subscription_menu_kb(plans),
                )
            except TelegramAPIError:
                pass
            return

    try:
        if not shop:
            shop_id = await create_shop(
                owner_id=actor.id,
                title=(event.chat.title or "Мой магазин")[:60],
                channel_id=event.chat.id,
                channel_title=event.chat.title or "",
                channel_username=event.chat.username or "",
            )
            sub_info = await get_shop_subscription_info(shop_id)
            trial_msg = (
                f"\n🎁 Вам начислен <b>бесплатный PRO-период ({sub_info['days_left']} дн.)</b>!"
                if sub_info["is_pro"]
                else ""
            )
            await bot.send_message(
                actor.id,
                f"🎉 Вы добавили меня администратором в канал <b>«{ch_title}»</b>!\n"
                f"✅ Ваш канал автоматически подключён, и для него создан магазин!{trial_msg}",
                reply_markup=main_menu(lang),
            )
            text, kb = await menu_view(actor.id, bot)
            await bot.send_message(actor.id, text, reply_markup=kb, disable_web_page_preview=True)
        elif not shop.get("channel_id") or shop["channel_id"] in (0, -100):
            await bind_shop_channel(
                shop["id"],
                event.chat.id,
                event.chat.title or "",
                event.chat.username or "",
            )
            await bot.send_message(
                actor.id,
                f"🎉 Вы добавили меня администратором в канал <b>«{ch_title}»</b>!\n"
                f"✅ Канал автоматически привязан к вашему магазину <b>«{html.escape(shop['title'])}»</b>.",
                reply_markup=main_menu(lang),
            )
            text, kb = await menu_view(actor.id, bot)
            await bot.send_message(actor.id, text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await bot.send_message(
                actor.id,
                f"🎉 Вы добавили меня администратором в канал <b>«{ch_title}»</b> (<code>{event.chat.id}</code>)!\n\n"
                "Выберите действие:",
                reply_markup=detected_channel_kb(event.chat.id, has_shop=True),
            )
    except TelegramAPIError:
        pass


@router.callback_query(F.data.startswith("adm:bind_det:"))
async def bind_detected_channel_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split(":")
    channel_id = int(parts[2])
    mode = parts[3]
    uid = callback.from_user.id

    if mode == "cur":
        shop = await get_managed_shop(uid)
        shop_id = shop["id"] if shop else 0
    else:
        shop_id = 0

    ok, msg_text, _ = await verify_and_bind_channel(bot, uid, shop_id, channel_id)
    await callback.answer("Готово! 🎉" if ok else "Ошибка")
    text, kb = await menu_view(uid, bot)
    await show(callback, msg_text + "\n\n" + text, kb)


# ====================== ТОВАРЫ МАГАЗИНА ======================

@router.callback_query(F.data == "adm:products")
async def products_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин или подключите канал", show_alert=True)
        return
    total = await count_products(shop["id"])
    sub_info = await get_shop_subscription_info(shop["id"])
    limit_str = "безлимит (PRO 💎)" if sub_info["is_pro"] else f"{total} из {sub_info['free_limit']} (Базовый тариф)"
    await show(
        callback,
        f"🛍 <b>Товары магазина «{html.escape(shop['title'])}»</b> · {total}\n"
        f"Лимит каталога: <b>{limit_str}</b>",
        products_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:add")
async def add_start(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала подключите канал или создайте магазин", show_alert=True)
        return

    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        cur_count = await count_products(shop["id"])
        if cur_count >= sub_info["free_limit"]:
            await show(
                callback,
                render_sub_upsell_text(
                    shop,
                    sub_info,
                    f"Добавление более {sub_info['free_limit']} товаров в каталог",
                ),
                sub_upsell_kb(sub_info["billing"]["price_usd"]),
            )
            await callback.answer()
            return

    await state.clear()
    await state.set_state(AddProduct.photo)
    await state.update_data(shop_id=shop["id"], photos=[], last_group=None)
    await show(
        callback,
        f"📷 <b>Добавление товара в «{html.escape(shop['title'])}»</b>\n\n"
        "Отправьте фото товара (можно сразу до 10 штук альбомом или по одному, как обычное фото).\n"
        "<i>💡 Совет: если вы перешлёте готовый пост с фото и ценой из вашего канала, бот сам распознает название, описание и цену!</i>\n\n"
        "Когда все фото загружены, нажмите «✅ Готово».",
        cancel_kb(),
    )
    await callback.answer()


@router.message(AddProduct.photo, F.photo)
async def add_photo(message: Message, state: FSMContext):
    async with photo_lock:
        data = await state.get_data()
        photos = data.get("photos", [])
        group = message.media_group_id
        notify = group is None or data.get("last_group") != group
        if len(photos) < MAX_PHOTOS:
            photos.append(message.photo[-1].file_id)

        parsed = parse_channel_post_caption(message.caption or "") if len(photos) == 1 and message.caption else None
        await state.update_data(photos=photos, last_group=group, parsed_caption=parsed)
        full = len(photos) >= MAX_PHOTOS

    if notify:
        if parsed and group is None:
            await state.update_data(
                title=parsed["title"],
                description=parsed["description"],
                price=parsed["price"],
                currency=parsed["currency"],
            )
            await state.set_state(AddProduct.confirm)
            shop = await get_shop(data.get("shop_id")) or await get_managed_shop(message.from_user.id)
            has_channel = bool(shop and shop.get("channel_id") and shop["channel_id"] not in (0, -100))
            preview = {
                "title": parsed["title"],
                "description": parsed["description"],
                "price": parsed["price"],
                "currency": parsed["currency"],
                "in_stock": 1,
                "shop_id": shop["id"] if shop else 1,
            }
            caption = "⚡️ <b>Текст и цена распознаны автоматически!</b>\n\n" + build_channel_text(preview)
            await message.answer_photo(
                photos[0], caption=caption, reply_markup=confirm_kb(has_channel=has_channel)
            )
            return

        text = f"📷 Фото принято (всего: {len(photos)}). Отправьте ещё или нажмите «✅ Готово»."
        if full:
            text = "📷 Достигнут максимум (10 фото). Нажмите «✅ Готово»."
        await message.answer(text, reply_markup=photos_done_kb())


@router.message(AddProduct.photo)
async def add_photo_wrong(message: Message):
    await message.answer("Нужно отправить именно фото (не файлом). Для отмены: /cancel")


@router.callback_query(AddProduct.photo, F.data == "adm:photos_done")
async def photos_done(callback: CallbackQuery, state: FSMContext):
    async with photo_lock:
        data = await state.get_data()
        photos = data.get("photos", [])
        if not photos:
            await callback.answer("Сначала отправьте хотя бы одно фото", show_alert=True)
            return
        await state.set_state(AddProduct.title)
    await show(
        callback,
        f"✅ Принято фото: {len(photos)}.\n\n✏️ Введите <b>название товара</b> (до 100 символов):",
        cancel_kb(),
    )
    await callback.answer()


@router.message(AddProduct.title, F.text, NOT_COMMAND)
async def add_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not 1 <= len(title) <= 100:
        await message.answer("Название должно быть от 1 до 100 символов. Сократите его:")
        return
    await state.update_data(title=title)
    await state.set_state(AddProduct.description)
    await message.answer(
        "📝 Введите <b>описание товара</b> (размеры, характеристики, цвет, состояние).\n"
        "Если описание не нужно, отправьте «-»:",
        reply_markup=cancel_kb(),
    )


@router.message(AddProduct.description, F.text, NOT_COMMAND)
async def add_description(message: Message, state: FSMContext):
    description = message.text.strip()
    if description == "-":
        description = ""
    if len(description) > 600:
        await message.answer("Описание слишком длинное (максимум 600 символов). Сократите его:")
        return
    await state.update_data(description=description)
    await state.set_state(AddProduct.price)
    await message.answer(
        "💰 Введите <b>цену товара</b> (только цифры, например <code>250000</code>):",
        reply_markup=cancel_kb(),
    )


@router.message(AddProduct.price, F.text, NOT_COMMAND)
async def add_price(message: Message, state: FSMContext):
    price = parse_price(message.text)
    if price is None:
        await message.answer("Введите цену положительным числом, например 250000:")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.currency)
    await message.answer("Выберите валюту цены:", reply_markup=currency_kb())


@router.callback_query(AddProduct.currency, F.data.startswith("adm:cur:"))
async def add_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split(":")[2]
    if currency not in ("UZS", "USD"):
        await callback.answer()
        return
    await state.update_data(currency=currency)
    await state.set_state(AddProduct.confirm)
    data = await state.get_data()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(callback.from_user.id)
    has_channel = bool(shop and shop.get("channel_id") and shop["channel_id"] not in (0, -100))

    photos = data["photos"]
    preview = {
        "title": data["title"],
        "description": data["description"],
        "price": data["price"],
        "currency": data["currency"],
        "in_stock": 1,
        "shop_id": shop["id"] if shop else 1,
    }
    caption = "👀 <b>Предпросмотр карточки товара:</b>\n\n" + build_channel_text(preview)
    chat_id = callback.from_user.id
    kb = confirm_kb(has_channel=has_channel)
    if len(photos) == 1:
        await callback.bot.send_photo(chat_id, photos[0], caption=caption, reply_markup=kb)
    else:
        await callback.bot.send_media_group(chat_id, media=build_media(photos, caption))
        await callback.bot.send_message(
            chat_id, "Выберите, куда опубликовать товар:", reply_markup=kb
        )
    await callback.answer()


@router.callback_query(AddProduct.confirm, F.data.startswith("adm:pub"))
async def publish(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split(":")
    mode = parts[2] if len(parts) > 2 else "ch"
    data = await state.get_data()
    await state.clear()

    uid = callback.from_user.id
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(uid)
    if not shop or not await can_manage_shop(uid, shop["id"]):
        await callback.answer("Магазин не найден", show_alert=True)
        return

    photos = data["photos"]
    product_id = await add_product(
        shop["id"], data["title"], data["description"], data["price"], data["currency"], photos
    )
    product = await get_product(product_id)

    has_channel = bool(shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    if mode == "ch" and has_channel:
        try:
            message_ids = await publish_product_to_channel(bot, product, shop["channel_id"])
            await set_channel_messages(product_id, message_ids)
            msg_done = "✅ Товар добавлен в каталог и опубликован в вашем Telegram-канале!"
        except TelegramAPIError as e:
            msg_done = (
                "✅ Товар сохранён в каталоге бота, но опубликовать в канал не удалось.\n"
                f"Проверьте права бота в канале ({html.escape(str(e))}).\n"
                "Вы сможете опубликовать его позже кнопкой в карточке товара."
            )
    else:
        msg_done = "✅ Товар сохранён в каталоге вашего магазина!"

    text, kb = await menu_view(uid, bot)
    await show(callback, msg_done + "\n\n" + text, kb)
    await callback.answer()


# ---------- список, поиск и карточка товара ----------

@router.callback_query(F.data.startswith("adm:list"))
async def list_products(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    total = await count_products(shop["id"])
    if total == 0:
        await show(callback, "В вашем магазине пока нет товаров. Добавьте первый!", products_menu_kb())
        await callback.answer()
        return
    pages = pages_count(total, PER_PAGE)
    page = min(max(page, 0), pages - 1)
    products = await get_all_products(shop["id"], PER_PAGE, page * PER_PAGE)
    await show(
        callback,
        f"📋 <b>Товары «{html.escape(shop['title'])}»</b> · {total}\n"
        "✅ в наличии · ❌ нет в наличии · 📢 выложен в канал",
        product_list_kb(products, page, pages),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:find")
async def find_start(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    await state.clear()
    await state.set_state(FindProduct.query)
    await show(callback, "🔎 Введите часть названия или описания товара:", cancel_kb())
    await callback.answer()


@router.message(FindProduct.query, F.text, NOT_COMMAND)
async def find_query(message: Message, state: FSMContext):
    shop = await get_managed_shop(message.from_user.id)
    await state.clear()
    if not shop:
        await message.answer("Магазин не найден.")
        return
    found = await search_products(shop["id"], message.text)
    if not found:
        await message.answer("Ничего не найдено.", reply_markup=products_menu_kb())
        return
    await message.answer(
        f"🔎 Найдено товаров: {len(found)}", reply_markup=product_list_kb(found, 0, 1)
    )


def card_caption(product: dict) -> str:
    caption = build_channel_text(product)
    photos = get_photos(product)
    if len(photos) > 1:
        caption += f"\n\n📷 Фото в альбоме: {len(photos)}"
    return caption


async def send_admin_card(bot: Bot, chat_id: int, product: dict, shop: dict = None):
    if not shop:
        shop = await get_shop(product["shop_id"])
    has_channel = bool(shop and shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    photos = get_photos(product)
    kb = product_admin_kb(product, has_channel=has_channel)
    if photos:
        await bot.send_photo(chat_id, photos[0], caption=card_caption(product), reply_markup=kb)
    else:
        await bot.send_message(chat_id, card_caption(product), reply_markup=kb)


@router.callback_query(F.data.startswith("adm:p:"))
async def show_card(callback: CallbackQuery):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден или нет доступа", show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    if isinstance(callback.message, Message):
        try:
            await callback.message.delete()
        except TelegramAPIError:
            pass
    await send_admin_card(callback.bot, uid, product, shop)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:cur_sw:"))
async def switch_product_currency_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    new_cur = "USD" if product["currency"] == "UZS" else "UZS"
    await set_currency(product_id, new_cur)
    product = await get_product(product_id)
    shop = await get_shop(product["shop_id"])
    try:
        await refresh_channel_post(bot, product, shop["channel_id"] if shop else None)
    except TelegramAPIError:
        pass
    has_channel = bool(shop and shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    if isinstance(callback.message, Message) and callback.message.photo:
        try:
            await callback.message.edit_caption(
                caption=card_caption(product),
                reply_markup=product_admin_kb(product, has_channel=has_channel),
            )
        except TelegramAPIError:
            pass
    else:
        await send_admin_card(bot, uid, product, shop)
    await callback.answer(f"Валюта изменена на {new_cur} ✅")


@router.callback_query(F.data.startswith("adm:dup:"))
async def duplicate_product_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        cur_count = await count_products(shop["id"])
        if cur_count >= sub_info["free_limit"]:
            await show(
                callback,
                render_sub_upsell_text(shop, sub_info, "Дублирование сверх бесплатного лимита"),
                sub_upsell_kb(sub_info["billing"]["price_usd"]),
            )
            await callback.answer()
            return

    new_id = await duplicate_product(product_id)
    new_prod = await get_product(new_id)
    await callback.answer("📑 Создана копия товара!")
    await send_admin_card(bot, uid, new_prod, shop)


@router.callback_query(F.data.startswith("adm:photos:"))
async def edit_photos_start_cb(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    await state.clear()
    await state.set_state(EditPhotos.photo)
    await state.update_data(product_id=product_id, photos=[], last_group=None)
    await reply(
        callback,
        f"📷 <b>Замена фото товара «{html.escape(product['title'])}»</b>\n\n"
        "Отправьте новое фото (или альбом до 10 фото), затем нажмите «✅ Готово»:",
        cancel_kb(),
    )
    await callback.answer()


@router.message(EditPhotos.photo, F.photo)
async def edit_photos_receive(message: Message, state: FSMContext):
    async with photo_lock:
        data = await state.get_data()
        photos = data.get("photos", [])
        group = message.media_group_id
        notify = group is None or data.get("last_group") != group
        if len(photos) < MAX_PHOTOS:
            photos.append(message.photo[-1].file_id)
        await state.update_data(photos=photos, last_group=group)
    if notify:
        await message.answer(
            f"📷 Принято фото: {len(photos)}. Отправьте ещё или нажмите «✅ Готово».",
            reply_markup=photos_done_kb(),
        )


@router.callback_query(EditPhotos.photo, F.data == "adm:photos_done")
async def edit_photos_done_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    photos = data.get("photos", [])
    product_id = data.get("product_id")
    if not photos:
        await callback.answer("Сначала отправьте хотя бы одно фото", show_alert=True)
        return
    await state.clear()
    product = await get_product(product_id)
    if not product or not await can_manage_shop(callback.from_user.id, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    await set_photos(product_id, photos)
    product = await get_product(product_id)
    shop = await get_shop(product["shop_id"])
    await callback.answer("✅ Фото товара обновлены!")
    await send_admin_card(bot, callback.from_user.id, product, shop)


@router.callback_query(F.data.startswith("adm:pinp:"))
async def pin_product_in_channel_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Канал не привязан", show_alert=True)
        return
    mids = get_channel_ids(product)
    if not mids:
        await callback.answer("Этот товар ещё не опубликован в канале", show_alert=True)
        return
    try:
        await bot.pin_chat_message(shop["channel_id"], mids[0], disable_notification=True)
        await callback.answer("📌 Пост с этим товаром закреплён в вашем канале!", show_alert=True)
    except TelegramAPIError as e:
        await callback.answer(f"Не удалось закрепить (проверьте право «Закрепление сообщений»): {e}", show_alert=True)


@router.callback_query(F.data.startswith("adm:repub:"))
async def republish_product_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите Telegram-канал в настройках магазина!", show_alert=True)
        return

    for mid in get_channel_ids(product):
        try:
            await bot.delete_message(shop["channel_id"], mid)
        except TelegramAPIError:
            pass

    try:
        mids = await publish_product_to_channel(bot, product, shop["channel_id"])
        await set_channel_messages(product_id, mids)
        await callback.answer("📢 Товар опубликован в канале!", show_alert=True)
    except TelegramAPIError as e:
        await callback.answer(f"Ошибка публикации: {e}", show_alert=True)


@router.callback_query(F.data.startswith("adm:stock:"))
async def toggle_stock(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    await set_in_stock(product_id, not product["in_stock"])
    product = await get_product(product_id)
    try:
        await refresh_channel_post(bot, product, shop["channel_id"] if shop else None)
    except TelegramAPIError as e:
        await reply(callback, f"⚠️ Не удалось обновить пост в канале: {html.escape(str(e))}")

    has_channel = bool(shop and shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    msg = callback.message
    if isinstance(msg, Message) and msg.photo:
        try:
            await msg.edit_caption(
                caption=card_caption(product),
                reply_markup=product_admin_kb(product, has_channel=has_channel),
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
    else:
        await send_admin_card(bot, uid, product, shop)
    await callback.answer("Статус наличия обновлён ✅")


@router.callback_query(F.data.startswith("adm:price:"))
async def price_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    await state.clear()
    await state.set_state(EditPrice.value)
    await state.update_data(product_id=product_id)
    await reply(callback, "💰 Введите новую цену (только цифры):", cancel_kb())
    await callback.answer()


@router.message(EditPrice.value, F.text, NOT_COMMAND)
async def price_save(message: Message, state: FSMContext, bot: Bot):
    price = parse_price(message.text)
    if price is None:
        await message.answer("Введите цену положительным числом, например 450000:")
        return
    data = await state.get_data()
    product_id = data["product_id"]
    await state.clear()
    product = await get_product(product_id)
    if not product or not await can_manage_shop(message.from_user.id, product["shop_id"]):
        await message.answer("Товар не найден.")
        return

    await set_price(product_id, price)
    product = await get_product(product_id)
    shop = await get_shop(product["shop_id"])
    try:
        await refresh_channel_post(bot, product, shop["channel_id"] if shop else None)
        await message.answer("✅ Цена обновлена!")
    except TelegramAPIError as e:
        await message.answer(
            f"⚠️ Цена сохранена, но пост в канале обновить не удалось: {html.escape(str(e))}"
        )
    await send_admin_card(bot, message.chat.id, product, shop)


EDIT_PROMPTS = {
    "title": "✏️ Введите новое название товара (до 100 символов):",
    "descr": "📝 Введите новое описание (до 600 символов). Чтобы убрать описание, отправьте «-»:",
}


@router.callback_query(F.data.startswith("adm:title:") | F.data.startswith("adm:descr:"))
async def field_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    _, field, raw_id = callback.data.split(":")
    product = await get_product(int(raw_id))
    if not product or product["hidden"] or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    await state.clear()
    await state.set_state(EditField.value)
    await state.update_data(product_id=product["id"], field=field)
    await reply(callback, EDIT_PROMPTS[field], cancel_kb())
    await callback.answer()


@router.message(EditField.value, F.text, NOT_COMMAND)
async def field_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    product_id, field = data["product_id"], data["field"]
    product = await get_product(product_id)
    if not product or not await can_manage_shop(message.from_user.id, product["shop_id"]):
        await state.clear()
        await message.answer("Товар не найден.")
        return

    text = message.text.strip()
    if field == "title":
        if not text or len(text) > 100:
            await message.answer("Название должно быть от 1 до 100 символов. Попробуйте ещё раз:")
            return
        await set_title(product_id, text)
    else:
        if text == "-":
            text = ""
        if len(text) > 600:
            await message.answer("Описание слишком длинное (максимум 600 символов). Сократите его:")
            return
        await set_description(product_id, text)
    await state.clear()
    product = await get_product(product_id)
    shop = await get_shop(product["shop_id"])
    try:
        await refresh_channel_post(bot, product, shop["channel_id"] if shop else None)
        await message.answer("✅ Изменения сохранены!")
    except TelegramAPIError as e:
        await message.answer(
            f"⚠️ Сохранено, но пост в канале обновить не удалось: {html.escape(str(e))}"
        )
    await send_admin_card(bot, message.chat.id, product, shop)


@router.callback_query(F.data.startswith("adm:del:"))
async def delete_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product or not await can_manage_shop(uid, product["shop_id"]):
        await callback.answer("Товар не найден", show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    channel_id = shop["channel_id"] if shop else 0
    note = ""
    if channel_id and channel_id not in (0, -100):
        for message_id in get_channel_ids(product):
            try:
                await bot.delete_message(channel_id, message_id)
            except TelegramAPIError:
                note = "\n⚠️ Пост в канале удалить не удалось, при необходимости удалите его вручную."
    await set_hidden(product_id, True)
    text, kb = await menu_view(uid, bot)
    await show(callback, "🗑 Товар удалён из каталога." + note + "\n\n" + text, kb)
    await callback.answer()
