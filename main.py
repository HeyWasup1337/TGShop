import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, Message

import admin
import admin_orders
import admin_tools
import shop
from config import BOT_TOKEN
from db import (
    close_db,
    ensure_user,
    get_lang,
    get_platform_billing_config,
    get_product,
    get_shop,
    get_shops_for_sub_notifications,
    init_db,
    mark_sub_notification,
    set_active_shop,
    set_lang,
)
from keyboards import lang_keyboard, main_menu, sub_upsell_kb
from posts import send_card
from shop import render_shop_catalog
from texts import TEXTS, fmt_price, t
from utils import fmt_dt

user = Router()


def all_variants(key: str) -> set:
    """Текст кнопки на обоих языках."""
    return {TEXTS["ru"][key], TEXTS["uz"][key]}


async def handle_start_payload(message: Message, uid: int, lang: str, payload: str, bot: Bot = None):
    """Обрабатывает переходы по deep-link ссылкам (?start=p_<id>, ?start=s_<shop_id>, ?start=ref_<uid>)."""
    if not payload:
        await message.answer(t(lang, "welcome"), reply_markup=main_menu(lang))
        return

    if payload.startswith("ref_") and payload[4:].isdigit():
        await message.answer(t(lang, "welcome"), reply_markup=main_menu(lang))
        text, kb = await admin.menu_view(uid, bot or message.bot)
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
        return

    if payload.startswith("p_") and payload[2:].isdigit():
        product_id = int(payload[2:])
        product = await get_product(product_id)
        if not product or product["hidden"]:
            await message.answer(t(lang, "not_found"), reply_markup=main_menu(lang))
            return
        target_shop = await get_shop(product.get("shop_id"))
        if target_shop:
            await set_active_shop(uid, target_shop["id"])
        await message.answer(
            t(lang, "shop_selected").format(
                title=target_shop["title"] if target_shop else "Магазин"
            ),
            reply_markup=main_menu(lang),
        )
        await send_card(
            message,
            lang,
            product,
            shop_title=target_shop["title"] if target_shop else None,
            user_id=uid,
        )
        return

    if payload.startswith("s_") and payload[2:].isdigit():
        shop_id = int(payload[2:])
        target_shop = await get_shop(shop_id)
        if not target_shop or not target_shop["is_active"]:
            await message.answer(t(lang, "not_found"), reply_markup=main_menu(lang))
            return
        await set_active_shop(uid, target_shop["id"])
        await message.answer(
            t(lang, "shop_selected").format(title=target_shop["title"]),
            reply_markup=main_menu(lang),
        )
        text, kb = await render_shop_catalog(target_shop, lang, 0)
        await message.answer(text, reply_markup=kb)
        return

    await message.answer(t(lang, "welcome"), reply_markup=main_menu(lang))


@user.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    u = message.from_user
    args = (command.args or "").strip()

    referrer_id = int(args[4:]) if args.startswith("ref_") and args[4:].isdigit() else 0
    lang = await get_lang(u.id)
    if lang is None:
        if args:
            await state.update_data(pending_payload=args, referrer_id=referrer_id)
        await message.answer(t("ru", "choose_lang"), reply_markup=lang_keyboard())
        return

    await ensure_user(u.id, u.full_name, u.username, default_lang=lang, referrer_id=referrer_id)
    await handle_start_payload(message, u.id, lang, args, bot=bot)


@user.callback_query(F.data.startswith("lang:"))
async def choose_lang(callback: CallbackQuery, state: FSMContext, bot: Bot):
    lang = callback.data.split(":")[1]
    if lang not in ("ru", "uz"):
        await callback.answer()
        return
    u = callback.from_user
    data = await state.get_data()
    referrer_id = data.get("referrer_id") or 0
    await set_lang(u.id, lang, u.full_name, u.username)
    if referrer_id:
        await ensure_user(u.id, u.full_name, u.username, default_lang=lang, referrer_id=referrer_id)
    payload = data.get("pending_payload")
    if payload:
        await state.update_data(pending_payload=None, referrer_id=0)
    await handle_start_payload(callback.message, u.id, lang, payload, bot=bot)
    await callback.answer()


@user.message(StateFilter("*"), F.text.in_(all_variants("btn_lang")))
async def change_lang(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t("ru", "choose_lang"), reply_markup=lang_keyboard())


async def subscription_monitor_loop(bot: Bot):
    """Фоновая задача: проверяет сроки подписок магазинов и отправляет вежливые напоминания владельцам каналов."""
    while True:
        try:
            billing = await get_platform_billing_config()
            p_usd = billing["price_usd"]
            usd_str = f"{int(p_usd)}$" if float(p_usd).is_integer() else f"{p_usd}$"
            uzs_str = fmt_price(billing["price_uzs"])
            kb = sub_upsell_kb(p_usd)

            alerts = await get_shops_for_sub_notifications()
            for s in alerts["remind_3d"]:
                await mark_sub_notification(s["id"], "3d")
                if s["owner_id"]:
                    try:
                        await bot.send_message(
                            s["owner_id"],
                            f"⏳ <b>Напоминание: подписка PRO для «{html.escape(s['title'])}» заканчивается через 3 дня</b> "
                            f"({fmt_dt(s['sub_until'])}).\n\n"
                            f"Продлите полный доступ всего за <b>{usd_str}/месяц ({uzs_str} сум)</b>, "
                            "чтобы авто-товары из постов канала, промокоды и рассылки продолжали работать без перерыва!",
                            reply_markup=kb,
                        )
                    except TelegramAPIError:
                        pass

            for s in alerts["remind_1d"]:
                await mark_sub_notification(s["id"], "1d")
                if s["owner_id"]:
                    try:
                        await bot.send_message(
                            s["owner_id"],
                            f"⚡️ <b>До окончания подписки PRO для «{html.escape(s['title'])}» осталось меньше 24 часов!</b>\n\n"
                            f"Продлите подписку в 1 клик всего за <b>{usd_str}/мес</b>:",
                            reply_markup=kb,
                        )
                    except TelegramAPIError:
                        pass

            for s in alerts["expired"]:
                await mark_sub_notification(s["id"], "expired", pause_shop=billing["strict_lock"])
                if s["owner_id"]:
                    mode_note = (
                        "Витрина магазина временно поставлена на паузу до продления подписки."
                        if billing["strict_lock"]
                        else f"Ваш магазин переведён на бесплатный базовый тариф (до {billing['free_max_products']} товаров). "
                             "Все ваши товары и заказы сохранены!"
                    )
                    try:
                        await bot.send_message(
                            s["owner_id"],
                            f"⚠️ <b>Срок подписки PRO для магазина «{html.escape(s['title'])}» истёк.</b>\n\n"
                            f"{mode_note}\n\n"
                            f"💎 Подключите PRO всего за <b>{usd_str} в месяц ({uzs_str} сум)</b>, "
                            "чтобы вернуть авто-парсинг постов канала, безлимит товаров, промокоды и рассылки!",
                            reply_markup=kb,
                        )
                    except TelegramAPIError:
                        pass
        except Exception as e:
            logging.warning("Subscription monitor warning: %s", e)

        await asyncio.sleep(300)


def build_dispatcher() -> Dispatcher:
    """Создаёт и настраивает Dispatcher со всеми роутерами."""
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(admin_orders.router)
    dp.include_router(admin_tools.router)
    dp.include_router(shop.router)
    dp.include_router(user)
    return dp


async def setup_bot_commands(bot: Bot):
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню / Asosiy menyu"),
                BotCommand(command="channel", description="📢 Мои каналы и магазин"),
                BotCommand(command="pro", description="💎 Подписка PRO ($1/мес)"),
                BotCommand(command="admin", description="🛠 Панель управления"),
                BotCommand(command="cancel", description="❌ Отменить текущее действие"),
            ]
        )
    except Exception:
        pass


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    await setup_bot_commands(bot)
    monitor_task = asyncio.create_task(subscription_monitor_loop(bot))
    logging.info("Платформа каналов и магазинов ShopBot (с системой подписки PRO $1/мес) запущена!")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "channel_post",
                "callback_query",
                "my_chat_member",
                "pre_checkout_query",
            ],
        )
    finally:
        monitor_task.cancel()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
