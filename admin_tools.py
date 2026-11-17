"""Панель продавца: подписка PRO ($1/мес), промокоды, статистика, CSV-экспорт, рассылка, настройки и супер-админка."""
import asyncio
import csv
import html
import io
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from config import ADMIN_IDS, REFERRAL_BONUS_DAYS
from db import (
    add_shop_admin,
    can_manage_shop,
    confirm_sub_payment,
    count_all_shops,
    count_orders_by_status,
    count_pending_sub_payments,
    count_shop_users,
    create_promocode,
    create_shop,
    ensure_user,
    create_sub_payment,
    create_sub_voucher,
    delete_promocode,
    delete_shop,
    delete_sub_voucher,
    extend_shop_subscription,
    get_all_orders_for_export,
    get_all_user_ids,
    get_managed_shop,
    get_platform_billing_config,
    get_shop,
    get_shop_notify_ids,
    get_shop_settings,
    get_shop_staff,
    get_shop_subscription_info,
    get_shop_user_ids,
    get_sub_payment,
    get_sub_plans,
    list_all_shops,
    list_promocodes,
    list_sub_payments,
    list_sub_vouchers,
    get_lang,
    get_order,
    get_order_items,
    mark_blocked,
    mark_order_paid_auto,
    platform_stats,
    redeem_sub_voucher,
    reject_sub_payment,
    remove_shop_admin,
    revoke_shop_subscription,
    set_platform_setting,
    stats_extra,
    stats_period,
    top_products,
    update_shop_field,
)
from admin_orders import admin_order_text
from keyboards import (
    STATUS_FILTERS,
    admin_order_kb,
    back_menu_kb,
    broadcast_audience_kb,
    broadcast_confirm_kb,
    cancel_kb,
    confirm_delete_shop_kb,
    channel_manager_kb,
    channel_showcase_kb,
    promocodes_menu_kb,
    settings_kb,
    staff_kb,
    stats_kb,
    sub_upsell_kb,
    subscription_menu_kb,
    subscription_pay_method_kb,
    superadmin_billing_kb,
    superadmin_menu_kb,
    superadmin_shop_detail_kb,
    superadmin_shops_kb,
    superadmin_sub_review_kb,
    superadmin_subs_list_kb,
    superadmin_vouchers_kb,
)
from texts import MENU_BUTTON_TEXTS, fmt_price, status_label, t
from utils import fmt_date, fmt_dt, get_bot_username, money, pages_count, reply, show

router = Router()

NOT_COMMAND = ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTON_TEXTS)
BROADCAST_DELAY = 0.06
PER_PAGE = 8


# ====================== СИСТЕМА ПОДПИСКИ PRO ($1 / МЕСЯЦ) ======================

class SubPaymentState(StatesGroup):
    receipt = State()
    voucher = State()


class PromoCreateState(StatesGroup):
    data = State()


class SuperBillingEdit(StatesGroup):
    value = State()
    voucher = State()



async def _ensure_user_shop(user) -> dict:
    await ensure_user(user.id, user.full_name, user.username)
    shop = await get_managed_shop(user.id)
    if not shop:
        default_title = f"Магазин {(user.first_name or user.full_name or 'Продавец')[:30]}"
        shop_id = await create_shop(owner_id=user.id, title=default_title)
        shop = await get_shop(shop_id)
    return shop


def render_sub_upsell_text(shop: dict, sub_info: dict, feature_name: str = "") -> str:
    billing = sub_info["billing"]
    p_usd = billing["price_usd"]
    usd_str = f"{int(p_usd)}$" if float(p_usd).is_integer() else f"{p_usd}$"
    uzs_str = fmt_price(billing["price_uzs"])
    reason_line = f"⚠️ <b>Для функции «{html.escape(feature_name)}» требуется подписка PRO.</b>\n\n" if feature_name else ""
    return (
        f"{reason_line}"
        f"💎 <b>Подписка ShopBot PRO для «{html.escape(shop['title'])}» — всего {usd_str} / месяц!</b>\n"
        f"<i>({uzs_str} сум — оплата переводом на карту)</i>\n\n"
        "<b>Что даёт подписка PRO владельцу канала:</b>\n"
        "• ♾ <b>Безлимит товаров</b> в каталоге и вашем канале (на бесплатном тарифе — до "
        f"{billing['free_max_products']} товаров)\n"
        "• ⚡️ <b>Авто-товары из постов и альбомов канала:</b> выкладывайте фото с ценой прямо в канале, "
        "а бот сам создаст карточку товара и прикрепит кнопку «🛒 Заказать»\n"
        "• 🎟 <b>Промокоды и скидки</b> для подписчиков вашего канала с публикацией в 1 клик\n"
        "• 📣 <b>Безлимитные рассылки</b> новинок и акций по базе ваших покупателей\n"
        "• ⭐️ <b>Приоритетное размещение и значок PRO</b> в общем каталоге магазинов\n"
        "• 📥 <b>Экспорт заказов в Excel/CSV</b> и подключение доп. менеджеров\n\n"
        "Окупается с первого же заказа! Выберите удобный тариф ниже:"
    )


async def subscription_dashboard_text(shop: dict) -> tuple[str, dict, dict]:
    sub_info = await get_shop_subscription_info(shop["id"])
    billing = sub_info["billing"]
    plans = get_sub_plans(billing)
    p_usd = billing["price_usd"]
    usd_str = f"{int(p_usd)}$" if float(p_usd).is_integer() else f"{p_usd}$"
    uzs_str = fmt_price(billing["price_uzs"])

    if sub_info["status"] == "vip":
        status_block = "👑 <b>Статус:</b> Бессрочный VIP PRO (Владелец платформы)"
    elif sub_info["status"] == "pro":
        status_block = (
            f"💎 <b>Статус:</b> PRO активна до <b>{fmt_dt(sub_info['sub_until'])}</b> "
            f"(осталось <b>{sub_info['days_left']} дн.</b>)"
        )
    elif sub_info["status"] == "trial":
        status_block = (
            f"🎁 <b>Статус:</b> Бесплатный пробный PRO до <b>{fmt_dt(sub_info['sub_until'])}</b> "
            f"(осталось <b>{sub_info['days_left']} дн.</b>)"
        )
    else:
        status_block = (
            f"⚠️ <b>Статус:</b> Базовый бесплатный тариф (до {billing['free_max_products']} товаров)\n"
            f"<i>Подключите PRO всего за {usd_str}/мес, чтобы снять все ограничения!</i>"
        )

    text = (
        f"💎 <b>Подписка PRO — «{html.escape(shop['title'])}»</b>\n\n"
        f"{status_block}\n\n"
        f"🔥 <b>Стоимость полного доступа: всего {usd_str} в месяц</b> ({uzs_str} сум)!\n\n"
        "<b>Преимущества PRO для вашего канала:</b>\n"
        "1️⃣ <b>Авто-парсер постов и альбомов канала</b> — публикуйте фото с ценой в канале, бот сам создаст товар и добавит кнопку заказа.\n"
        "2️⃣ <b>Безлимитный каталог товаров</b> и приоритет «⭐️ PRO» в списке магазинов.\n"
        "3️⃣ <b>Промокоды на скидку</b> для подписчиков канала и массовые рассылки.\n"
        "4️⃣ <b>Командная работа</b> (подключение менеджеров) и выгрузка отчётов в Excel/CSV.\n\n"
        "👇 <b>Выберите срок подписки для подключения или продления:</b>"
    )
    return text, plans, billing


@router.callback_query(F.data == "sub:menu")
async def sub_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    shop = await _ensure_user_shop(callback.from_user)
    text, plans, _ = await subscription_dashboard_text(shop)
    await show(callback, text, subscription_menu_kb(plans))
    await callback.answer()


@router.callback_query(F.data.startswith("sub:plan:") | F.data.startswith("sub:card:"))
async def sub_plan_select_cb(callback: CallbackQuery, state: FSMContext):
    shop = await _ensure_user_shop(callback.from_user)
    months = int(callback.data.split(":")[2])
    billing = await get_platform_billing_config()
    plans = get_sub_plans(billing)
    plan = plans.get(months, plans[1])

    usd_s = f"{int(plan['usd'])}$" if float(plan["usd"]).is_integer() else f"{plan['usd']}$"
    uzs_s = fmt_price(plan["uzs"])
    card_num = html.escape(billing["card_number"] or "Уточните у администратора")
    card_holder = html.escape(billing["card_holder"] or "—")

    await state.clear()
    await state.set_state(SubPaymentState.receipt)
    await state.update_data(
        shop_id=shop["id"],
        months=plan["months"],
        days=plan["days"],
        amount_uzs=plan["uzs"],
        amount_usd=plan["usd"],
    )
    text = (
        f"💳 <b>Оплата подписки PRO: {plan['title']} (+{plan['days']} дней)</b>\n"
        f"🏪 Магазин: <b>{html.escape(shop['title'])}</b>\n\n"
        f"💰 Сумма к переводу: <b>{uzs_s} сум</b> (<b>{usd_s}</b>)\n\n"
        f"💳 Переведите на карту:\n<code>{card_num}</code>\n"
        f"👤 Получатель: <b>{card_holder}</b>\n\n"
        "📸 <b>После перевода отправьте сюда скриншот чека</b> (как обычное фото).\n"
        "Администратор платформы проверит чек и сразу подключит вам подписку PRO!"
    )
    await show(callback, text, cancel_kb("sub:menu"))
    await callback.answer()


async def _unused_sub_card_start_cb(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    months = int(callback.data.split(":")[2])
    billing = await get_platform_billing_config()
    plans = get_sub_plans(billing)
    plan = plans.get(months, plans[1])

    usd_s = f"{int(plan['usd'])}$" if float(plan["usd"]).is_integer() else f"{plan['usd']}$"
    uzs_s = fmt_price(plan["uzs"])
    card_num = html.escape(billing["card_number"] or "Уточните у администратора")
    card_holder = html.escape(billing["card_holder"] or "—")

    await state.clear()
    await state.set_state(SubPaymentState.receipt)
    await state.update_data(
        shop_id=shop["id"],
        months=plan["months"],
        days=plan["days"],
        amount_uzs=plan["uzs"],
        amount_usd=plan["usd"],
    )
    text = (
        f"💳 <b>Оплата подписки PRO: {plan['title']}</b>\n"
        f"🏪 Магазин: <b>{html.escape(shop['title'])}</b>\n\n"
        f"💰 Сумма к переводу: <b>{uzs_s} сум</b> (эквивалент <b>{usd_s}</b>)\n\n"
        f"💳 Карта для перевода:\n<code>{card_num}</code>\n"
        f"👤 Получатель: <b>{card_holder}</b>\n\n"
        "📸 <b>После перевода отправьте сюда скриншот чека</b> (как обычное фото).\n"
        "После проверки подписка PRO будет сразу активирована!"
    )
    await show(callback, text, cancel_kb("sub:menu"))
    await callback.answer()


@router.message(SubPaymentState.receipt, F.photo)
async def sub_card_receipt_received(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(uid)
    if not shop:
        await message.answer("Магазин не найден.")
        return

    photo_id = message.photo[-1].file_id
    months = data.get("months", 1)
    days = data.get("days", 30)
    amount_uzs = data.get("amount_uzs", 12800)
    amount_usd = data.get("amount_usd", 1)

    sub_id = await create_sub_payment(
        shop_id=shop["id"],
        user_id=uid,
        months=months,
        days=days,
        amount=amount_uzs,
        currency="UZS",
        method="card",
        status="review",
        receipt_photo_id=photo_id,
    )

    await message.answer(
        f"✅ <b>Чек по заявке #{sub_id} получен!</b>\n"
        "Администратор платформы проверит оплату и активирует подписку PRO для вашего магазина. "
        "Вы получите уведомление прямо в этот чат.",
        reply_markup=back_menu_kb(),
    )

    uname = f" (@{html.escape(message.from_user.username)})" if message.from_user.username else ""
    admin_caption = (
        f"💎 <b>Новая оплата подписки PRO #{sub_id}</b>\n\n"
        f"🏪 Магазин: <b>{html.escape(shop['title'])}</b> (#{shop['id']})\n"
        f"👤 Владелец: <a href=\"tg://user?id={uid}\">{html.escape(message.from_user.full_name or str(uid))}</a>{uname}\n"
        f"📅 Тариф: <b>{months} мес. (+{days} дней)</b>\n"
        f"💰 Сумма: <b>{fmt_price(amount_uzs)} сум ({amount_usd}$)</b>"
    )
    for adm_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                adm_id,
                photo_id,
                caption=admin_caption,
                reply_markup=superadmin_sub_review_kb(sub_id, shop_id=shop['id']),
            )
        except TelegramAPIError:
            pass


@router.message(SubPaymentState.receipt)
async def sub_card_receipt_wrong(message: Message):
    await message.answer(
        "Пожалуйста, отправьте скриншот чека об оплате как обычное фото (или нажмите /cancel).",
        reply_markup=cancel_kb("sub:menu"),
    )


@router.callback_query(F.data.startswith("sub:stars:"))
async def sub_stars_invoice_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    months = int(callback.data.split(":")[2])
    billing = await get_platform_billing_config()
    plans = get_sub_plans(billing)
    plan = plans.get(months, plans[1])

    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"ShopBot PRO — {plan['title']}",
            description=(
                f"Подписка PRO на {plan['days']} дней для магазина «{shop['title']}»: "
                "безлимит товаров, авто-парсер постов канала, промокоды и рассылки."
            ),
            payload=f"sub:{shop['id']}:{plan['months']}:{plan['days']}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=plan["title"], amount=plan["stars"])],
        )
        await callback.answer()
    except TelegramAPIError as e:
        await callback.answer(
            f"Оплата через Telegram Stars сейчас недоступна ({e}). Выберите оплату по карте.",
            show_alert=True,
        )


@router.pre_checkout_query()
async def on_pre_checkout_query(query: PreCheckoutQuery):
    await query.answer(ok=True)



@router.callback_query(F.data.startswith("sub:click:") | F.data.startswith("sub:payme:"))
async def sub_uzs_invoice_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    parts = callback.data.split(":")
    provider = parts[1]  # click or payme
    months = int(parts[2])
    billing = await get_platform_billing_config()
    plans = get_sub_plans(billing)
    plan = plans.get(months, plans[1])

    token = billing["click_token"] if provider == "click" else billing["payme_token"]
    prov_name = "CLICK" if provider == "click" else "Payme"
    if not token:
        await callback.answer(
            f"⚠️ Авто-оплата {prov_name} ещё не подключена владельцем платформы в @BotFather.\n"
            "Пока вы можете оплатить переводом на карту или через Telegram Stars, "
            "а владелец платформы может добавить токен в разделе /superadmin → Настройки тарифа.",
            show_alert=True,
        )
        return

    amount_uzs = max(1000, int(plan["uzs"]))
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"ShopBot PRO ({prov_name}) — {plan['title']}",
            description=(
                f"Автоматическая оплата подписки PRO на {plan['days']} дней через {prov_name} "
                f"для магазина «{shop['title']}»."
            ),
            payload=f"sub:{shop['id']}:{plan['months']}:{plan['days']}:{provider}",
            provider_token=token,
            currency="UZS",
            prices=[LabeledPrice(label=f"{plan['title']} ({prov_name})", amount=amount_uzs * 100)],
        )
        await callback.answer()
    except TelegramAPIError as e:
        await callback.answer(
            f"Не удалось выставить счёт {prov_name}: {e}",
            show_alert=True,
        )


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot):
    sp = message.successful_payment
    payload = sp.invoice_payload or ""
    uid = message.from_user.id

    # 1. Авто-оплата подписки PRO владельцем канала (Click / Payme / Telegram Stars)
    if payload.startswith("sub:"):
        parts = payload.split(":")
        if len(parts) < 4:
            return
        shop_id = int(parts[1])
        months = int(parts[2])
        days = int(parts[3])
        method = parts[4] if len(parts) > 4 else ("stars" if sp.currency == "XTR" else "click")
        real_amount = sp.total_amount if sp.currency == "XTR" else (sp.total_amount // 100)

        new_until = await extend_shop_subscription(shop_id, days=days, is_paid=True)
        await create_sub_payment(
            shop_id=shop_id,
            user_id=uid,
            months=months,
            days=days,
            amount=real_amount,
            currency=sp.currency,
            method=method,
            status="confirmed",
            note=sp.provider_payment_charge_id or sp.telegram_payment_charge_id or "",
        )
        shop = await get_shop(shop_id)
        shop_title = html.escape(shop["title"]) if shop else f"#{shop_id}"
        method_label = {"click": "🟦 CLICK", "payme": "🟩 Payme", "stars": "⭐️ Telegram Stars"}.get(method, method.upper())
        cur_disp = "⭐️" if sp.currency == "XTR" else "сум"
        await message.answer(
            f"🎉 <b>Авто-оплата через {method_label} прошла успешно!</b>\n"
            f"💎 Подписка PRO для <b>«{shop_title}»</b> мгновенно активирована!\n\n"
            f"📅 Действует до: <b>{fmt_dt(new_until)}</b> (+{days} дней)\n"
            "Все PRO-функции вашего магазина и канала полностью открыты!",
            reply_markup=back_menu_kb(),
        )
        for adm_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    adm_id,
                    f"⚡️ <b>Авто-оплата подписки ({method_label})!</b>\n"
                    f"🏪 Магазин: <b>{shop_title}</b> (#{shop_id})\n"
                    f"💰 Сумма: <b>{fmt_price(real_amount)} {cur_disp}</b> (+{days} дн. до {fmt_dt(new_until)})",
                )
            except TelegramAPIError:
                pass
        return

    # 2. Авто-оплата заказа покупателем в магазине (Click / Payme)
    if payload.startswith("ord:"):
        parts = payload.split(":")
        if len(parts) < 3:
            return
        order_id = int(parts[1])
        provider = parts[2]
        charge_id = sp.provider_payment_charge_id or sp.telegram_payment_charge_id or ""
        await mark_order_paid_auto(order_id, provider, charge_id)
        order = await get_order(order_id)
        if not order:
            return
        items = await get_order_items(order_id)
        lang = await get_lang(uid) or "ru"
        shop_title = html.escape(order.get("shop_title") or "Магазин")
        prov_label = "CLICK" if provider == "click" else "Payme"

        await message.answer(
            t(lang, "notify_confirmed").format(id=order_id, shop=shop_title)
            + f"\n💳 Способ оплаты: <b>{prov_label} (автоматически)</b>"
        )
        notify_ids = await get_shop_notify_ids(order.get("shop_id"))
        details = (
            f"✅ <b>Заказ №{order_id} автоматически оплачен через {prov_label}!</b>\n\n"
            + admin_order_text(order, items, message.from_user.username)
        )
        kb = admin_order_kb(order)
        for admin_id in notify_ids:
            try:
                await bot.send_message(admin_id, details, reply_markup=kb)
            except TelegramAPIError:
                pass


# ---------- подарочный код и реферальная программа (+15 дней за друга) ----------

@router.callback_query(F.data == "sub:voucher")
async def sub_voucher_start_cb(callback: CallbackQuery, state: FSMContext):
    shop = await _ensure_user_shop(callback.from_user)
    await state.clear()
    await state.set_state(SubPaymentState.voucher)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        "🎁 <b>Активация подарочного кода PRO</b>\n\n"
        "Введите промокод на подписку, полученный от администрации платформы:\n\n"
        "Отмена: /cancel",
        cancel_kb("sub:menu"),
    )
    await callback.answer()


@router.message(SubPaymentState.voucher, F.text, NOT_COMMAND)
async def sub_voucher_apply_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(uid)
    if not shop:
        await message.answer("Магазин не найден.")
        return

    ok, info, days = await redeem_sub_voucher(shop["id"], uid, message.text)
    if not ok:
        await message.answer(info, reply_markup=cancel_kb("sub:menu"))
        return

    await message.answer(
        f"🎉 <b>Промокод активирован! Начислено +{days} дней PRO!</b>\n"
        f"🏪 Магазин: <b>{html.escape(shop['title'])}</b>\n"
        f"📅 Подписка PRO активна до: <b>{fmt_dt(info)}</b>",
        reply_markup=back_menu_kb(),
    )


@router.callback_query(F.data == "sub:ref")
async def sub_referral_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    shop = await _ensure_user_shop(callback.from_user)
    bot_username = await get_bot_username(bot)
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    text = (
        f"🤝 <b>Партнёрская программа: +{REFERRAL_BONUS_DAYS} дней PRO бесплатно!</b>\n\n"
        "Приглашайте друзей, у которых есть свои Telegram-каналы или магазины!\n"
        f"Когда друг перейдёт по вашей ссылке и подключит свой первый магазин, вашему магазину "
        f"<b>«{html.escape(shop['title'])}»</b> автоматически начислится <b>+{REFERRAL_BONUS_DAYS} дней подписки PRO бесплатно</b>!\n\n"
        f"🔗 <b>Ваша личная ссылка для приглашения:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "Отправьте её знакомым владельцам каналов или разместите в чате предпринимателей!"
    )
    await show(callback, text, cancel_kb("sub:menu"))
    await callback.answer()


# ====================== ПРОМОКОДЫ МАГАЗИНА ДЛЯ ПОКУПАТЕЛЕЙ (PRO) ======================

async def render_promocodes_text(shop: dict) -> tuple[str, list, bool]:
    promos = await list_promocodes(shop["id"])
    has_channel = bool(shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    lines = [
        f"🎟 <b>Промокоды и скидки «{html.escape(shop['title'])}»</b>",
        "",
        "Создавайте промокоды для подписчиков вашего канала! Покупатели смогут вводить их в корзине перед оплатой.",
        "",
    ]
    if promos:
        lines.append("<b>Активные промокоды:</b>")
        for p in promos:
            lim = f"из {p['max_uses']}" if p["max_uses"] > 0 else "безлимит"
            lines.append(
                f"• <code>{html.escape(p['code'])}</code> — скидка <b>{p['discount_pct']}%</b> "
                f"(использован: {p['used_count']} / {lim})"
            )
        if has_channel:
            lines.append("\n💡 <i>Нажмите «📢 В канал», чтобы в 1 клик опубликовать красивый анонс промокода в вашем канале!</i>")
    else:
        lines.append("<i>У вас пока нет активных промокодов. Нажмите кнопку ниже, чтобы создать первый!</i>")
    return "\n".join(lines), promos, has_channel


@router.callback_query(F.data == "prm:menu")
async def promocodes_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Промокоды и скидки"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return
    text, promos, has_ch = await render_promocodes_text(shop)
    await show(callback, text, promocodes_menu_kb(promos, has_channel=has_ch))
    await callback.answer()


@router.callback_query(F.data == "prm:add")
async def promocode_add_start_cb(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Создание промокодов"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return
    await state.clear()
    await state.set_state(PromoCreateState.data)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        "🎟 <b>Создание промокода для покупателей</b>\n\n"
        "Отправьте код и процент скидки (от 1 до 90) через пробел.\n"
        "Примеры:\n"
        "• <code>SALE10 10</code> — промокод SALE10 на скидку 10% (безлимит)\n"
        "• <code>VIP20 20 50</code> — промокод VIP20 на скидку 20% на первые 50 заказов\n\n"
        "Отмена: /cancel",
        cancel_kb("prm:menu"),
    )
    await callback.answer()


@router.message(PromoCreateState.data, F.text, NOT_COMMAND)
async def promocode_add_save_msg(message: Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer(
            "Укажите код и процент скидки через пробел, например: <code>SALE10 10</code>"
        )
        return
    code = re.sub(r"[^A-Za-z0-9_-]", "", parts[0]).upper()[:20]
    pct_str = parts[1].rstrip("%")
    max_uses = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    if not code or not pct_str.isdigit() or not (1 <= int(pct_str) <= 90):
        await message.answer(
            "Код должен состоять из букв/цифр, а скидка — числом от 1 до 90%. Попробуйте снова:"
        )
        return

    data = await state.get_data()
    await state.clear()
    shop = await get_shop(data.get("shop_id")) or await get_managed_shop(message.from_user.id)
    if not shop:
        await message.answer("Магазин не найден.")
        return

    await create_promocode(shop["id"], code, int(pct_str), max_uses)
    text, promos, has_ch = await render_promocodes_text(shop)
    await message.answer(
        f"✅ Промокод <code>{code}</code> (-{pct_str}%) создан!\n\n" + text,
        reply_markup=promocodes_menu_kb(promos, has_channel=has_ch),
    )


@router.callback_query(F.data.startswith("prm:del:"))
async def promocode_del_cb(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    promo_id = int(callback.data.split(":")[2])
    await delete_promocode(shop["id"], promo_id)
    text, promos, has_ch = await render_promocodes_text(shop)
    await callback.answer("Промокод удалён ✅")
    await show(callback, text, promocodes_menu_kb(promos, has_channel=has_ch))


@router.callback_query(F.data.startswith("prm:pub:"))
async def promocode_pub_channel_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop or not shop.get("channel_id") or shop["channel_id"] in (0, -100):
        await callback.answer("Сначала привяжите Telegram-канал!", show_alert=True)
        return
    promo_id = int(callback.data.split(":")[2])
    promos = await list_promocodes(shop["id"])
    promo = next((p for p in promos if p["id"] == promo_id), None)
    if not promo:
        await callback.answer("Промокод не найден", show_alert=True)
        return

    bot_username = await get_bot_username(bot)
    lim_line = f"⏳ Количество активаций ограничено ({promo['max_uses']} шт.)!\n" if promo["max_uses"] > 0 else ""
    post_text = (
        f"🎁 <b>Специальная акция для подписчиков канала «{html.escape(shop['title'])}»!</b>\n\n"
        f"Используйте промокод <code>{html.escape(promo['code'])}</code> в корзине нашего бота "
        f"и получите скидку <b>-{promo['discount_pct']}%</b> на заказ!\n"
        f"{lim_line}\n"
        "👇 Нажмите кнопку ниже, чтобы открыть каталог и применить промокод:"
    )
    try:
        await bot.send_message(
            shop["channel_id"],
            post_text,
            reply_markup=channel_showcase_kb(bot_username, shop["id"]),
        )
        await callback.answer("📢 Пост с промокодом опубликован в вашем канале!", show_alert=True)
    except TelegramAPIError as e:
        await callback.answer(f"Ошибка публикации в канал: {e}", show_alert=True)


# ====================== СТАТИСТИКА И CSV-ЭКСПОРТ МАГАЗИНА ======================

async def stats_text(shop: dict) -> str:
    sid = shop["id"]
    today, week, month, total, counts, extra, top, sub_info = await asyncio.gather(
        stats_period(sid, 0),
        stats_period(sid, 7),
        stats_period(sid, 30),
        stats_period(sid, None),
        count_orders_by_status(sid),
        stats_extra(sid),
        top_products(sid, 5),
        get_shop_subscription_info(sid),
    )

    lines = [
        f"📊 <b>Статистика «{html.escape(shop['title'])}»</b>",
        f"💎 Тариф: <b>{sub_info['label']}</b>",
        "",
        "<b>Заказы и выручка</b>",
        f"Сегодня: {today['orders']} · {money(today['revenue'])}",
        f"7 дней: {week['orders']} · {money(week['revenue'])}",
        f"30 дней: {month['orders']} · {money(month['revenue'])}",
        f"Всего: {total['orders']} · {money(total['revenue'])}",
        "<i>Заказы без отменённых. Выручка: подтверждённые и отправленные.</i>",
        "",
        "<b>По статусам</b>",
    ]
    for status in STATUS_FILTERS:
        lines.append(f"{status_label('ru', status)}: {counts.get(status, 0)}")
    lines += [
        "",
        f"👥 Клиентов магазина: {extra['customers']} (за 7 дней: +{extra['customers_week']})",
        f"🛍 Товаров: {extra['products']}, в наличии: {extra['in_stock']}",
        f"🎟 Активных промокодов: {extra['promocodes']}",
    ]
    if top:
        lines += ["", "<b>Топ продаваемых товаров</b>"]
        for n, row in enumerate(top, 1):
            lines.append(f"{n}. {html.escape(row['title'] or '')} — {row['qty']} шт.")
    return "\n".join(lines)


@router.callback_query(F.data == "adm:stats")
async def stats(callback: CallbackQuery):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    await show(callback, await stats_text(shop), stats_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:export_orders")
async def export_orders_csv_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Экспорт заказов в Excel/CSV"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return

    orders = await get_all_orders_for_export(shop["id"])
    if not orders:
        await callback.answer("В вашем магазине пока нет заказов для выгрузки.", show_alert=True)
        return

    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM для корректного открытия кириллицы в Excel
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "ID заказа",
            "Дата",
            "Статус",
            "Покупатель",
            "Телефон",
            "Адрес",
            "Товары",
            "Промокод",
            "Скидка",
            "Итого",
            "Валюта",
            "Трек / отправка",
        ]
    )
    for o in orders:
        writer.writerow(
            [
                o["id"],
                fmt_dt(o["created_at"]),
                status_label("ru", o["status"]),
                o["name"] or "",
                o["phone"] or "",
                o["address"] or "",
                o["items_summary"] or "",
                o["promo_code"] or "",
                o["discount_amount"] or 0,
                o["total"] or 0,
                o["currency"] or "",
                o["tracking"] or "",
            ]
        )

    csv_bytes = buf.getvalue().encode("utf-8")
    doc = BufferedInputFile(csv_bytes, filename=f"orders_shop_{shop['id']}.csv")
    await bot.send_document(
        callback.from_user.id,
        document=doc,
        caption=f"📥 <b>Выгрузка заказов магазина «{html.escape(shop['title'])}»</b>\nВсего заказов: <b>{len(orders)}</b>",
    )
    await callback.answer("Файл отправлен ✅")


# ====================== РАССЫЛКА ПО КЛИЕНТАМ МАГАЗИНА ======================

class Broadcast(StatesGroup):
    content = State()
    confirm = State()


AUDIENCE_LANG = {"all": None, "ru": "ru", "uz": "uz"}
AUDIENCE_LABEL = {
    "all": "всем клиентам магазина",
    "ru": "клиентам магазина (Русский)",
    "uz": "клиентам магазина (O'zbekcha)",
    "sup_owners": "всем владельцам магазинов платформы",
    "sup_all": "всем пользователям платформы",
}


@router.callback_query(F.data == "adm:bc")
async def bc_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Массовая рассылка клиентам"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return

    sid = shop["id"]
    total, ru, uz = await asyncio.gather(
        count_shop_users(sid),
        count_shop_users(sid, "ru"),
        count_shop_users(sid, "uz"),
    )
    await show(
        callback,
        f"📣 <b>Рассылка по клиентам «{html.escape(shop['title'])}»</b>\n"
        "Кому отправить сообщение?\n"
        "<i>Учитываются только активные клиенты вашего магазина, не заблокировавшие бота.</i>",
        broadcast_audience_kb(total, ru, uz),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bc:a:"))
async def bc_audience(callback: CallbackQuery, state: FSMContext):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    audience = callback.data.split(":")[2]
    if audience not in AUDIENCE_LANG:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(Broadcast.content)
    await state.update_data(audience=audience, shop_id=shop["id"], mode="shop")
    await show(
        callback,
        f"✏️ Отправьте сообщение для рассылки {AUDIENCE_LABEL[audience]}.\n\n"
        "Это может быть текст, фото, видео или пост с подписью.\n\nОтмена: /cancel",
        cancel_kb(),
    )
    await callback.answer()


async def _resolve_broadcast_ids(data: dict) -> list:
    mode = data.get("mode", "shop")
    audience = data.get("audience", "all")
    if mode == "super":
        return await get_all_user_ids(only_owners=(audience == "sup_owners"))
    shop_id = data.get("shop_id")
    return await get_shop_user_ids(shop_id, AUDIENCE_LANG.get(audience))


@router.message(Broadcast.content)
async def bc_content(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        await message.answer("Сейчас вы готовите рассылку. Отправьте сообщение или нажмите «Отмена» (/cancel).")
        return
    data = await state.get_data()
    ids = await _resolve_broadcast_ids(data)
    if not ids:
        await state.clear()
        await message.answer(
            "Получателей пока нет (в базе вашего магазина ещё нет активных клиентов).",
            reply_markup=back_menu_kb(),
        )
        return
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    await state.set_state(Broadcast.confirm)
    label = AUDIENCE_LABEL.get(data["audience"], "выбранной аудитории")
    await message.reply(
        f"Отправить это сообщение {label}?\nПолучателей: <b>{len(ids)}</b>",
        reply_markup=broadcast_confirm_kb(),
    )


async def copy_one(bot: Bot, user_id: int, src_chat: int, src_msg: int) -> str:
    for _ in range(2):
        try:
            await bot.copy_message(user_id, src_chat, src_msg)
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            await mark_blocked(user_id)
            return "blocked"
        except TelegramAPIError:
            return "failed"
    return "failed"


@router.callback_query(Broadcast.confirm, F.data == "bc:go")
async def bc_go(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    ids = await _resolve_broadcast_ids(data)
    await show(callback, f"⏳ Выполняется рассылка ({len(ids)} получателей)... Пожалуйста, подождите.")
    await callback.answer()

    sent = blocked = failed = 0
    for user_id in ids:
        result = await copy_one(bot, user_id, data["src_chat"], data["src_msg"])
        if result == "ok":
            sent += 1
        elif result == "blocked":
            blocked += 1
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    await reply(
        callback,
        "✅ <b>Рассылка завершена!</b>\n\n"
        f"📬 Доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок отправки: <b>{failed}</b>",
        back_menu_kb(),
    )


# ====================== НАСТРОЙКИ МАГАЗИНА И МЕНЕДЖЕРЫ ======================

class SettingEdit(StatesGroup):
    value = State()


class StaffAdd(StatesGroup):
    user_id = State()


FIELDS = {
    "title": ("title", "✏️ Отправьте новое название магазина (от 2 до 60 символов):"),
    "descr": ("description", "📝 Отправьте новое описание магазина (до 300 символов, или «-» чтобы очистить):"),
    "card": ("card_number", "💳 Отправьте новый номер карты для приёма оплаты (16 цифр):"),
    "holder": ("card_holder", "👤 Отправьте имя получателя на карте (например, ALI VALIYEV):"),
    "phone": ("shop_phone", "☎️ Отправьте контактный телефон магазина (например, +998 90 123 45 67):"),
    "click": ("click_token", "🟦 Отправьте <b>Click Provider Token</b> из @BotFather (раздел Payments → Click), или «-» чтобы отключить:"),
    "payme": ("payme_token", "🟩 Отправьте <b>Payme Provider Token</b> из @BotFather (раздел Payments → Payme), или «-» чтобы отключить:"),
}


async def settings_text(shop: dict, bot: Bot = None) -> str:
    s = await get_shop_settings(shop["id"])
    sub_info = await get_shop_subscription_info(shop["id"])
    empty = "<i>не указано</i>"
    card = f"<code>{html.escape(s['card_number'])}</code>" if s["card_number"] else empty
    click_st = "✅ Подключён" if s.get("click_token") else "⚪️ не задан"
    payme_st = "✅ Подключён" if s.get("payme_token") else "⚪️ не задан"
    holder = html.escape(s["card_holder"]) if s["card_holder"] else empty
    phone = html.escape(s["shop_phone"]) if s["shop_phone"] else empty
    descr = html.escape(s["description"]) if s["description"] else empty

    if s["channel_id"] and s["channel_id"] not in (0, -100):
        ch_name = html.escape(s["channel_title"] or str(s["channel_id"]))
        ch_user = f" (@{html.escape(s['channel_username'])})" if s["channel_username"] else ""
        channel_str = f"<b>{ch_name}</b>{ch_user} [<code>{s['channel_id']}</code>]"
    else:
        channel_str = "⚠️ <i>не привязан</i>"

    status_str = "🟢 Активен (виден покупателям)" if s["is_active"] else "🔴 На паузе (скрыт из витрины)"
    sub_line = (
        f"{sub_info['label']} (до {fmt_date(sub_info['sub_until'])})"
        if sub_info.get("sub_until") and sub_info["is_pro"]
        else sub_info["label"]
    )
    staff = await get_shop_staff(shop["id"])

    bot_link = ""
    if bot:
        bot_username = await get_bot_username(bot)
        if bot_username:
            bot_link = f"\n🔗 Прямая ссылка: <code>https://t.me/{bot_username}?start=s_{shop['id']}</code>"

    return (
        f"⚙️ <b>Настройки магазина «{html.escape(s['title'])}»</b>\n\n"
        f"📊 Статус: {status_str}\n"
        f"💎 Подписка: <b>{sub_line}</b>\n"
        f"📢 Канал: {channel_str}\n"
        f"📝 Описание: {descr}\n\n"
        f"💳 Карта: {card}\n"
        f"👤 Получатель: {holder}\n"
        f"☎️ Телефон: {phone}\n"
        f"👨‍💼 Доп. менеджеров: {len(staff)}"
        f"{bot_link}\n\n"
        "Выберите параметр для изменения:"
    )


@router.callback_query(F.data == "adm:settings")
async def settings_menu(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    await show(callback, await settings_text(shop, bot), settings_kb(shop))
    await callback.answer()



@router.callback_query(F.data == "set:del_ask")
async def delete_shop_ask_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    shop = await get_managed_shop(uid)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    if shop["owner_id"] != uid and uid not in ADMIN_IDS:
        await callback.answer("Удалить магазин может только его владелец", show_alert=True)
        return
    await show(
        callback,
        f"⚠️ <b>Вы действительно хотите удалить магазин «{html.escape(shop['title'])}»?</b>\n\n"
        "Будут безвозвратно удалены все товары, заказы, промокоды и настройки этого магазина.\n"
        "Это действие нельзя отменить!",
        confirm_delete_shop_kb(shop["id"], is_super=False),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set:del_yes:"))
async def delete_shop_confirm_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    uid = callback.from_user.id
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer("Магазин уже удалён", show_alert=True)
        return
    if shop["owner_id"] != uid and uid not in ADMIN_IDS:
        await callback.answer("Удалить магазин может только его владелец", show_alert=True)
        return
    title = shop["title"]
    await delete_shop(shop_id)
    await callback.answer(f"🗑 Магазин «{title}» удалён!", show_alert=True)
    import admin as admin_mod
    text, kb = await admin_mod.menu_view(uid, bot)
    await show(callback, f"🗑 <b>Магазин «{html.escape(title)}» был удалён.</b>\n\n" + text, kb)


@router.callback_query(F.data == "set:toggle_active")
async def toggle_shop_active_cb(callback: CallbackQuery, bot: Bot):
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    new_val = 0 if shop.get("is_active", 1) else 1
    await update_shop_field(shop["id"], "is_active", new_val)
    shop = await get_shop(shop["id"])
    await callback.answer("🟢 Магазин включён!" if new_val else "🔴 Магазин поставлен на паузу")
    await show(callback, await settings_text(shop, bot), settings_kb(shop))


@router.callback_query(F.data == "set:staff")
async def staff_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    shop = await get_managed_shop(uid)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    sub_info = await get_shop_subscription_info(shop["id"])
    if not sub_info["is_pro"]:
        await show(
            callback,
            render_sub_upsell_text(shop, sub_info, "Подключение менеджеров магазина"),
            sub_upsell_kb(sub_info["billing"]["price_usd"]),
        )
        await callback.answer()
        return

    staff = await get_shop_staff(shop["id"])
    is_owner = (shop["owner_id"] == uid) or (uid in ADMIN_IDS)
    lines = [
        f"👨‍💼 <b>Менеджеры магазина «{html.escape(shop['title'])}»</b>",
        f"👑 Владелец: <code>{shop['owner_id']}</code>",
        "",
    ]
    if staff:
        lines.append("<b>Добавленные менеджеры:</b>")
        for n, s in enumerate(staff, 1):
            uname = f" (@{html.escape(s['username'])})" if s.get("username") else ""
            lines.append(f"{n}. {html.escape(s.get('full_name') or 'Менеджер')}{uname} — ID <code>{s['user_id']}</code>")
    else:
        lines.append("<i>Дополнительных менеджеров пока нет.</i>")
    lines.append(
        "\nМенеджеры получают уведомления о заказах и чеках, могут добавлять товары и обрабатывать заказы вашего магазина."
    )
    await show(callback, "\n".join(lines), staff_kb(staff, is_owner=is_owner))
    await callback.answer()


@router.callback_query(F.data == "staff:add")
async def staff_add_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    shop = await get_managed_shop(uid)
    if not shop or (shop["owner_id"] != uid and uid not in ADMIN_IDS):
        await callback.answer("Добавлять менеджеров может только владелец магазина", show_alert=True)
        return
    await state.clear()
    await state.set_state(StaffAdd.user_id)
    await state.update_data(shop_id=shop["id"])
    await show(
        callback,
        "👨‍💼 Отправьте <b>Telegram ID</b> пользователя (числом), которого хотите назначить менеджером вашего магазина, "
        "или перешлите сюда любое сообщение от него:\n\nОтмена: /cancel",
        cancel_kb("set:staff"),
    )
    await callback.answer()


@router.message(StaffAdd.user_id, NOT_COMMAND)
async def staff_add_save(message: Message, state: FSMContext):
    data = await state.get_data()
    shop_id = data.get("shop_id")
    uid = message.from_user.id
    shop = await get_shop(shop_id) if shop_id else await get_managed_shop(uid)
    if not shop or (shop["owner_id"] != uid and uid not in ADMIN_IDS):
        await state.clear()
        await message.answer("Нет прав для добавления менеджера.")
        return

    target_id = None
    if message.forward_from:
        target_id = message.forward_from.id
    elif message.text and message.text.strip().isdigit():
        target_id = int(message.text.strip())

    if not target_id:
        await message.answer("Пожалуйста, отправьте цифровой Telegram ID пользователя (например, <code>123456789</code>):")
        return

    if target_id == shop["owner_id"]:
        await message.answer("Этот пользователь уже является владельцем магазина.")
        return

    await state.clear()
    added = await add_shop_admin(shop["id"], target_id)
    staff = await get_shop_staff(shop["id"])
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> назначен менеджером магазина!"
        if added
        else f"ℹ️ Пользователь <code>{target_id}</code> уже в списке менеджеров.",
        reply_markup=staff_kb(staff, is_owner=True),
    )


@router.callback_query(F.data.startswith("staff:del:"))
async def staff_del_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    shop = await get_managed_shop(uid)
    if not shop or (shop["owner_id"] != uid and uid not in ADMIN_IDS):
        await callback.answer("Удалять менеджеров может только владелец магазина", show_alert=True)
        return
    target_id = int(callback.data.split(":")[2])
    await remove_shop_admin(shop["id"], target_id)
    staff = await get_shop_staff(shop["id"])
    await callback.answer("Менеджер удалён ✅")
    await show(
        callback,
        f"👨‍💼 <b>Менеджеры магазина «{html.escape(shop['title'])}»</b>\nМенеджер <code>{target_id}</code> удалён.",
        staff_kb(staff, is_owner=True),
    )


@router.callback_query(F.data.startswith("set:"))
async def settings_ask(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":")[1]
    if field not in FIELDS:
        await callback.answer()
        return
    shop = await get_managed_shop(callback.from_user.id)
    if not shop:
        await callback.answer("Сначала создайте магазин", show_alert=True)
        return
    await state.clear()
    await state.set_state(SettingEdit.value)
    await state.update_data(field=field, shop_id=shop["id"])
    await show(callback, FIELDS[field][1] + "\n\nОтмена: /cancel", cancel_kb("adm:settings"))
    await callback.answer()


def normalize_card(raw: str):
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 16:
        return None
    return " ".join(digits[i:i + 4] for i in range(0, 16, 4))


def normalize_phone_pretty(raw: str):
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) != 12 or not digits.startswith("998"):
        return None
    return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:12]}"


@router.message(SettingEdit.value, F.text, NOT_COMMAND)
async def settings_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    field = data["field"]
    shop_id = data.get("shop_id")
    shop = await get_shop(shop_id) if shop_id else await get_managed_shop(message.from_user.id)
    if not shop or not await can_manage_shop(message.from_user.id, shop["id"]):
        await state.clear()
        await message.answer("Магазин не найден.")
        return

    raw = message.text.strip()
    if field == "card":
        value = normalize_card(raw)
        error = "Номер карты должен состоять из 16 цифр. Попробуйте ещё раз:"
    elif field == "phone":
        value = normalize_phone_pretty(raw)
        error = "Номер не распознан. Отправьте в формате +998 90 123 45 67:"
    elif field == "title":
        value = raw if 2 <= len(raw) <= 60 else None
        error = "Название магазина должно быть от 2 до 60 символов:"
    elif field == "descr":
        if raw == "-":
            value = ""
        else:
            value = raw if len(raw) <= 300 else None
        error = "Описание должно быть до 300 символов:"
    elif field in ("click", "payme"):
        if raw == "-":
            value = ""
        else:
            value = raw if (":" in raw and len(raw) >= 10) else None
        error = "Токен провайдера из @BotFather должен содержать двоеточие (например, <code>398062629:TEST:...</code>) или отправьте «-» для очистки:"
    else:
        value = raw if 2 <= len(raw) <= 50 else None
        error = "Имя получателя должно быть от 2 до 50 символов:"

    if value is None:
        await message.answer(error)
        return

    await update_shop_field(shop["id"], FIELDS[field][0], value)
    await state.clear()
    shop = await get_shop(shop["id"])
    await message.answer("✅ Сохранено!")
    await message.answer(
        await settings_text(shop, bot),
        reply_markup=settings_kb(shop),
        disable_web_page_preview=True,
    )


# ====================== СУПЕР-АДМИН ПЛАТФОРМЫ И БИЛЛИНГ ПОДПИСОК ======================

async def superadmin_text() -> tuple[str, int]:
    st = await platform_stats()
    billing = await get_platform_billing_config()
    text = (
        "👑 <b>Панель владельца платформы (Super-Admin)</b>\n\n"
        f"🏪 Всего магазинов: <b>{st['total_shops']}</b> (активных: <b>{st['active_shops']}</b>)\n"
        f"💎 С активным PRO: <b>{st['pro_shops']}</b> (оплаченных: <b>{st['paid_pro_shops']}</b>)\n"
        f"📢 Привязанных каналов: <b>{st['bound_channels']}</b>\n"
        f"👥 Всего пользователей в боте: <b>{st['total_users']}</b>\n"
        f"🛍 Всего товаров: <b>{st['total_items']}</b>\n"
        f"📦 Всего заказов: <b>{st['total_orders']}</b>\n"
        f"💰 Оборот магазинов: <b>{money(st['revenue'])}</b>\n\n"
        f"💵 <b>Доход с подписок PRO:</b> <b>{money(st['sub_revenue'])}</b>\n"
        f"🏷 Текущий тариф PRO: <b>{billing['price_usd']}$ / мес</b> ({fmt_price(billing['price_uzs'])} сум)"
    )
    return text, st["pending_subs"]


@router.message(Command("superadmin"))
async def superadmin_cmd(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    text, pending = await superadmin_text()
    await message.answer(text, reply_markup=superadmin_menu_kb(pending))


@router.callback_query(F.data == "sup:menu")
async def superadmin_menu_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступно только администратору платформы", show_alert=True)
        return
    await state.clear()
    text, pending = await superadmin_text()
    await show(callback, text, superadmin_menu_kb(pending))
    await callback.answer()


# ---------- управление заявками на подписку PRO ----------

@router.callback_query(F.data.startswith("sup:subs:"))
async def superadmin_subs_list_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    page = int(callback.data.split(":")[2])
    all_subs = await list_sub_payments(limit=50, offset=0)
    pending_cnt = await count_pending_sub_payments()
    pages = pages_count(len(all_subs), PER_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = all_subs[page * PER_PAGE:(page + 1) * PER_PAGE]
    text = (
        f"💳 <b>Платежи и заявки по подписке PRO</b>\n"
        f"🔎 Ожидают проверки: <b>{pending_cnt}</b> · Всего записей: <b>{len(all_subs)}</b>"
    )
    if not chunk:
        text += "\n\nЗаявок на оплату подписки пока нет."
    await show(callback, text, superadmin_subs_list_kb(chunk, page, pages))
    await callback.answer()


@router.callback_query(F.data.startswith("sup:sub_v:"))
async def superadmin_sub_view_cb(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    sub_id = int(callback.data.split(":")[2])
    sub = await get_sub_payment(sub_id)
    if not sub:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    st_map = {"review": "🔎 На проверке", "confirmed": "✅ Подтверждена", "rejected": "❌ Отклонена"}
    caption = (
        f"💳 <b>Заявка на подписку #{sub['id']}</b>\n"
        f"Статус: <b>{st_map.get(sub['status'], sub['status'])}</b>\n"
        f"🏪 Магазин: <b>{html.escape(sub.get('shop_title') or str(sub['shop_id']))}</b> (#{sub['shop_id']})\n"
        f"👤 Пользователь: <a href=\"tg://user?id={sub['user_id']}\">{html.escape(sub.get('full_name') or str(sub['user_id']))}</a>\n"
        f"📅 Срок: <b>{sub['months']} мес. (+{sub['days']} дн.)</b>\n"
        f"💰 Сумма: <b>{fmt_price(sub['amount'])} {sub['currency']}</b> ({sub['method']})\n"
        f"🕒 Создана: {fmt_dt(sub['created_at'])}"
    )
    kb = superadmin_sub_review_kb(sub_id, shop_id=sub["shop_id"]) if sub["status"] == "review" else cancel_kb("sup:subs:0")
    if sub.get("receipt_photo_id"):
        await bot.send_photo(callback.from_user.id, sub["receipt_photo_id"], caption=caption, reply_markup=kb)
    else:
        await show(callback, caption, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sup:sub_ok:"))
async def superadmin_sub_approve_cb(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    sub_id = int(callback.data.split(":")[2])
    sub = await get_sub_payment(sub_id)
    if not sub:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    ok, new_until = await confirm_sub_payment(sub_id)
    if not ok:
        await callback.answer("Заявка уже была обработана ранее", show_alert=True)
        return

    await callback.answer(f"✅ Подписка активирована до {fmt_date(new_until)}!", show_alert=True)
    notify_ids = await get_shop_notify_ids(sub["shop_id"])
    notify_ids.add(sub["user_id"])
    for uid in notify_ids:
        try:
            await bot.send_message(
                uid,
                f"🎉 <b>Ваша оплата подтверждена! Подписка PRO активирована!</b>\n\n"
                f"🏪 Магазин: <b>{html.escape(sub.get('shop_title') or 'Ваш магазин')}</b>\n"
                f"📅 Действует до: <b>{fmt_dt(new_until)}</b> (+{sub['days']} дней)\n\n"
                "✅ <b>Теперь вы можете подключить свой Telegram-канал!</b>",
                reply_markup=channel_manager_kb({"channel_id": 0}),
            )
        except TelegramAPIError:
            pass

    text, pending = await superadmin_text()
    await show(callback, text, superadmin_menu_kb(pending))


@router.callback_query(F.data.startswith("sup:sub_no:"))
async def superadmin_sub_reject_cb(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    sub_id = int(callback.data.split(":")[2])
    sub = await get_sub_payment(sub_id)
    if not sub:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    changed = await reject_sub_payment(sub_id, "Чек не подтверждён")
    if not changed:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    await callback.answer("❌ Заявка отклонена", show_alert=True)
    try:
        await bot.send_message(
            sub["user_id"],
            f"❌ <b>Оплата по заявке #{sub_id} на подписку PRO не подтверждена.</b>\n"
            "Пожалуйста, проверьте реквизиты и отправьте корректный скриншот чека в разделе «💎 Подписка PRO».",
        )
    except TelegramAPIError:
        pass
    text, pending = await superadmin_text()
    await show(callback, text, superadmin_menu_kb(pending))


# ---------- настройка тарифа ($1/мес) и реквизитов платформы ----------

async def render_super_billing_text() -> tuple[str, dict]:
    billing = await get_platform_billing_config()
    lock_str = (
        "🔒 Строгий (при окончании подписки витрина ставится на паузу)"
        if billing["strict_lock"]
        else f"🔓 Мягкий Freemium (при окончании подписки доступно до {billing['free_max_products']} товаров)"
    )
    text = (
        "⚙️ <b>Настройки тарифа PRO и реквизитов платформы</b>\n\n"
        f"💵 Базовая цена (1 мес.): <b>{billing['price_usd']} $</b>\n"
        f"🇺🇿 Цена в сумах (1 мес.): <b>{fmt_price(billing['price_uzs'])} сум</b>\n"
        f"🎁 Бесплатный пробный период (Trial): <b>{billing['trial_days']} дней</b>\n"
        f"📦 Бесплатный лимит товаров (без PRO): <b>{billing['free_max_products']} шт.</b>\n"
        f"🛡 Режим после окончания PRO: {lock_str}\n\n"
        f"💳 Карта платформы: <code>{html.escape(billing['card_number'] or '—')}</code>\n"
        f"👤 Получатель: <b>{html.escape(billing['card_holder'] or '—')}</b>\n\n"
        "<i>Цены на 3 мес. (-17%) и 12 мес. (-25%) рассчитываются автоматически от базовой цены 1 месяца.</i>"
    )
    return text, billing


@router.callback_query(F.data == "sup:billing")
async def superadmin_billing_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    text, billing = await render_super_billing_text()
    await show(callback, text, superadmin_billing_kb(billing))
    await callback.answer()


@router.callback_query(F.data == "sup:btoggle_lock")
async def superadmin_toggle_lock_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    billing = await get_platform_billing_config()
    new_val = "0" if billing["strict_lock"] else "1"
    await set_platform_setting("sub_strict_lock", new_val)
    text, billing = await render_super_billing_text()
    await callback.answer("Режим обновлён ✅")
    await show(callback, text, superadmin_billing_kb(billing))


BILLING_PROMPTS = {
    "sub_price_usd": "💵 Введите базовую цену подписки за 1 месяц в долларах (например, <code>1</code> или <code>1.5</code>):",
    "sub_price_uzs": "🇺🇿 Введите цену подписки за 1 месяц в сумах (например, <code>12800</code>):",
    "sub_price_stars": "⭐️ Введите цену подписки за 1 месяц в Telegram Stars (например, <code>50</code>):",
    "sub_trial_days": "🎁 Введите длительность бесплатного пробного периода для новых каналов в днях (например, <code>14</code>):",
    "free_max_products": "📦 Введите лимит товаров на бесплатном тарифе (например, <code>5</code>):",
    "platform_card_number": "💳 Введите номер карты владельца платформы (16 цифр) для приёма оплаты за подписку:",
    "platform_card_holder": "👤 Введите имя и фамилию владельца карты платформы (например, <code>SARDOR TASHMATOV</code>):",
    "platform_click_token": "🟦 Отправьте <b>Click Provider Token</b> из @BotFather (Payments → Click) для авто-оплаты подписок (или «-» для очистки):",
    "platform_payme_token": "🟩 Отправьте <b>Payme Provider Token</b> из @BotFather (Payments → Payme) для авто-оплаты подписок (или «-» для очистки):",
}


@router.callback_query(F.data.startswith("sup:bset:"))
async def superadmin_bset_start_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":")[2]
    if key not in BILLING_PROMPTS:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(SuperBillingEdit.value)
    await state.update_data(bkey=key)
    await show(callback, BILLING_PROMPTS[key] + "\n\nОтмена: /cancel", cancel_kb("sup:billing"))
    await callback.answer()


@router.message(SuperBillingEdit.value, F.text, NOT_COMMAND)
async def superadmin_bset_save_msg(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    key = data.get("bkey")
    raw = message.text.strip()

    if key == "sub_price_usd":
        try:
            val = float(raw.replace(",", "."))
            if val <= 0 or val > 1000:
                raise ValueError
            clean_val = str(int(val)) if val.is_integer() else str(round(val, 2))
        except ValueError:
            await message.answer("Введите положительное число (например, <code>1</code>):")
            return
    elif key in ("sub_price_uzs", "sub_price_stars", "sub_trial_days", "free_max_products"):
        digits = raw.replace(" ", "").replace("_", "")
        if not digits.isdigit():
            await message.answer("Введите целое число:")
            return
        clean_val = str(int(digits))
    elif key in ("platform_click_token", "platform_payme_token"):
        if raw == "-":
            clean_val = ""
        elif ":" in raw and len(raw) >= 10:
            clean_val = raw
        else:
            await message.answer("Токен из @BotFather должен содержать двоеточие (например, <code>398062629:TEST:...</code>) или отправьте «-»:")
            return
    elif key == "platform_card_number":
        clean_val = normalize_card(raw)
        if not clean_val:
            await message.answer("Номер карты должен состоять из 16 цифр:")
            return
    else:
        if not (2 <= len(raw) <= 60):
            await message.answer("Введите от 2 до 60 символов:")
            return
        clean_val = raw

    await set_platform_setting(key, clean_val)
    await state.clear()
    text, billing = await render_super_billing_text()
    await message.answer("✅ Параметр обновлён!\n\n" + text, reply_markup=superadmin_billing_kb(billing))


# ---------- подарочные коды (ваучеры PRO) в супер-админке ----------

@router.callback_query(F.data == "sup:vouchers")
async def superadmin_vouchers_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    vouchers = await list_sub_vouchers()
    lines = [
        "🎁 <b>Подарочные коды (ваучеры) на подписку PRO</b>",
        "",
        "Создавайте коды для партнёров и владельцев каналов — они смогут активировать их в меню «💎 Подписка PRO».",
        "",
    ]
    if vouchers:
        for v in vouchers:
            lines.append(
                f"• <code>{html.escape(v['code'])}</code> — <b>+{v['days']} дн. PRO</b> "
                f"(активаций: {v['used_count']}/{v['max_uses']})"
            )
    else:
        lines.append("<i>Активных подарочных кодов пока нет.</i>")
    await show(callback, "\n".join(lines), superadmin_vouchers_kb(vouchers))
    await callback.answer()


@router.callback_query(F.data == "sup:vadd")
async def superadmin_voucher_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(SuperBillingEdit.voucher)
    await show(
        callback,
        "🎁 <b>Создание подарочного кода PRO</b>\n\n"
        "Отправьте код, количество дней и лимит активаций через пробел.\n"
        "Примеры:\n"
        "• <code>PRO30 30 10</code> — код PRO30 на 30 дней для 10 магазинов\n"
        "• <code>VIP365 365 1</code> — код VIP365 на 1 год для 1 магазина\n\n"
        "Отмена: /cancel",
        cancel_kb("sup:vouchers"),
    )
    await callback.answer()


@router.message(SuperBillingEdit.voucher, F.text, NOT_COMMAND)
async def superadmin_voucher_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Введите код и число дней через пробел, например: <code>PRO30 30 5</code>")
        return
    code = re.sub(r"[^A-Za-z0-9_-]", "", parts[0]).upper()[:24]
    days = int(parts[1]) if parts[1].isdigit() else 0
    max_uses = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    if not code or days <= 0 or max_uses <= 0:
        await message.answer("Проверьте формат: КОД ДНИ ЛИМИТ (например, <code>START30 30 10</code>):")
        return

    await state.clear()
    await create_sub_voucher(code, days, max_uses)
    vouchers = await list_sub_vouchers()
    await message.answer(
        f"✅ Подарочный код <code>{code}</code> на +{days} дн. (лимит: {max_uses}) создан!",
        reply_markup=superadmin_vouchers_kb(vouchers),
    )


@router.callback_query(F.data.startswith("sup:vdel:"))
async def superadmin_voucher_del_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    code = callback.data.split(":")[2]
    await delete_sub_voucher(code)
    vouchers = await list_sub_vouchers()
    await callback.answer("Код удалён ✅")
    await show(callback, "🎁 Подарочный код удалён.", superadmin_vouchers_kb(vouchers))


# ---------- управление магазинами в супер-админке ----------

@router.callback_query(F.data.startswith("sup:shops:"))
async def superadmin_shops_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    page = int(callback.data.split(":")[2])
    total = await count_all_shops()
    pages = pages_count(total, PER_PAGE)
    page = min(max(page, 0), pages - 1)
    shops = await list_all_shops(PER_PAGE, page * PER_PAGE)
    await show(
        callback,
        f"🏪 <b>Все магазины платформы</b> · {total}\n💎 — активна подписка PRO",
        superadmin_shops_kb(shops, page, pages),
    )
    await callback.answer()


async def _super_shop_detail_text(shop: dict) -> str:
    st = "🟢 Активен" if shop["is_active"] else "🔴 Заблокирован / На паузе"
    sub_info = await get_shop_subscription_info(shop["id"])
    sub_str = (
        f"{sub_info['label']} (до {fmt_dt(sub_info['sub_until'])})"
        if sub_info.get("sub_until")
        else sub_info["label"]
    )
    ch = (
        f"{html.escape(shop['channel_title'] or '')} (<code>{shop['channel_id']}</code>)"
        if shop.get("channel_id")
        else "не привязан"
    )
    return (
        f"🏪 <b>Магазин #{shop['id']}: {html.escape(shop['title'])}</b>\n"
        f"Статус: {st}\n"
        f"💎 Подписка: <b>{sub_str}</b>\n"
        f"👤 Владелец ID: <a href=\"tg://user?id={shop['owner_id']}\">{shop['owner_id']}</a>\n"
        f"📢 Канал: {ch}\n"
        f"💳 Карта: <code>{html.escape(shop.get('card_number') or '—')}</code>\n"
        f"☎️ Телефон: {html.escape(shop.get('shop_phone') or '—')}\n"
        f"🕒 Создан: {fmt_dt(shop.get('created_at'))}"
    )


@router.callback_query(F.data.startswith("sup:shop:"))
async def superadmin_shop_view_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    await show(callback, await _super_shop_detail_text(shop), superadmin_shop_detail_kb(shop))
    await callback.answer()


@router.callback_query(F.data.startswith("sup:sub_add:"))
async def superadmin_shop_sub_add_cb(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    shop_id = int(parts[2])
    days = int(parts[3])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    new_until = await extend_shop_subscription(shop_id, days=days, is_paid=True)
    shop = await get_shop(shop_id)
    await callback.answer(f"💎 Начислено +{days} дн. PRO (до {fmt_date(new_until)})!", show_alert=True)
    try:
        await bot.send_message(
            shop["owner_id"],
            f"🎁 <b>Администратор платформы подключил вашему магазину «{html.escape(shop['title'])}» подписку PRO (+{days} дн.)!</b>\n"
            f"📅 Подписка активна до: <b>{fmt_dt(new_until)}</b>\n\n"
            "✅ Теперь вы можете привязать и настроить свой Telegram-канал:",
            reply_markup=channel_manager_kb(shop),
        )
    except TelegramAPIError:
        pass
    await show(callback, await _super_shop_detail_text(shop), superadmin_shop_detail_kb(shop))


@router.callback_query(F.data.startswith("sup:sub_rev:"))
async def superadmin_shop_sub_revoke_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    shop_id = int(callback.data.split(":")[2])
    await revoke_shop_subscription(shop_id)
    shop = await get_shop(shop_id)
    await callback.answer("Подписка PRO сброшена", show_alert=True)
    await show(callback, await _super_shop_detail_text(shop), superadmin_shop_detail_kb(shop))



@router.callback_query(F.data.startswith("sup:del_ask:"))
async def superadmin_shop_del_ask_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    await show(
        callback,
        f"⚠️ <b>Удалить магазин #{shop['id']} «{html.escape(shop['title'])}» навсегда?</b>\n\n"
        "Все товары, заказы и привязка канала этого магазина будут полностью удалены из базы.",
        confirm_delete_shop_kb(shop_id, is_super=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sup:del_yes:"))
async def superadmin_shop_del_yes_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer("Магазин уже удалён", show_alert=True)
        return
    title = shop["title"]
    await delete_shop(shop_id)
    await callback.answer(f"🗑 Магазин «{title}» удалён!", show_alert=True)
    total = await count_all_shops()
    pages = pages_count(total, PER_PAGE)
    shops = await list_all_shops(PER_PAGE, 0)
    await show(
        callback,
        f"🗑 Магазин <b>«{html.escape(title)}»</b> удалён.\n\n🏪 <b>Все магазины платформы</b> · {total}",
        superadmin_shops_kb(shops, 0, pages),
    )


@router.callback_query(F.data.startswith("sup:toggle:"))
async def superadmin_shop_toggle_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer("Магазин не найден", show_alert=True)
        return
    new_val = 0 if shop["is_active"] else 1
    await update_shop_field(shop_id, "is_active", new_val)
    shop = await get_shop(shop_id)
    await callback.answer("Статус магазина обновлён ✅")
    await show(callback, await _super_shop_detail_text(shop), superadmin_shop_detail_kb(shop))


@router.callback_query(F.data.startswith("sup:bc:"))
async def superadmin_bc_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    kind = callback.data.split(":")[2]
    aud_key = "sup_owners" if kind == "owners" else "sup_all"
    await state.clear()
    await state.set_state(Broadcast.content)
    await state.update_data(audience=aud_key, mode="super")
    await show(
        callback,
        f"👑 Отправьте сообщение для глобальной рассылки {AUDIENCE_LABEL[aud_key]}:\n\nОтмена: /cancel",
        cancel_kb("sup:menu"),
    )
    await callback.answer()
