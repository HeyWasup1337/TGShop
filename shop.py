"""Покупательская часть: каталог магазинов, витрина товаров, сортировка, промокоды, быстрый чекаут в 1 клик и оплата."""
import html
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message

from admin_orders import admin_order_text
from db import (
    add_to_cart,
    cancel_order,
    change_qty,
    clear_cart,
    compute_shop_sub_state,
    count_catalog,
    count_public_shops,
    create_order,
    get_active_shop,
    get_cart,
    get_catalog,
    get_lang,
    get_order,
    get_order_items,
    get_platform_billing_config,
    get_product,
    get_shop,
    get_shop_notify_ids,
    get_shop_settings,
    get_user_cart_shops,
    get_user_orders,
    get_user_saved_checkout,
    list_public_shops,
    remove_from_cart,
    search_products,
    search_public_shops,
    set_active_shop,
    set_order_payment,
    validate_promocode,
)
from keyboards import (
    admin_order_kb,
    buy_kb,
    buyer_order_pay_kb,
    cancel_reply_kb,
    cart_kb,
    catalog_browse_kb,
    fast_checkout_kb,
    main_menu,
    order_detail_kb,
    orders_kb,
    phone_kb,
    public_shops_kb,
)
from posts import send_card
from texts import MENU_BUTTON_TEXTS, TEXTS, cur_label, fmt_price, status_label, t
from utils import pages_count

router = Router()

NOT_COMMAND = ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTON_TEXTS)
MAX_CART_ITEMS = 20
PER_PAGE = 8
PAYABLE = ("pending_payment", "rejected")


class Checkout(StatesGroup):
    confirm_saved = State()
    name = State()
    phone = State()
    address = State()
    payment = State()
    promo = State()


class BuyerSearch(StatesGroup):
    shop_query = State()
    item_query = State()


def all_variants(key: str) -> set:
    return {TEXTS["ru"][key], TEXTS["uz"][key]}


async def user_lang(user_id: int) -> str:
    return await get_lang(user_id) or "ru"


async def safe_edit(message: Message, text: str, kb=None):
    try:
        await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramAPIError:
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


# ====================== ВЫБОР МАГАЗИНА И КАТАЛОГ ======================

async def render_shops_list(uid: int, lang: str, page: int = 0):
    total = await count_public_shops()
    if total == 0:
        return t(lang, "shops_empty"), public_shops_kb([], 0, 0, 1)
    pages = pages_count(total, PER_PAGE)
    page = min(max(page, 0), pages - 1)
    shops = await list_public_shops(PER_PAGE, page * PER_PAGE)
    active = await get_active_shop(uid)
    active_id = active["id"] if active else 0
    return t(lang, "shops_title"), public_shops_kb(shops, active_id, page, pages)


async def render_shop_catalog(shop: dict, lang: str, page: int = 0, sort: str = "new"):
    total = await count_catalog(shop["id"])
    pages = pages_count(total, PER_PAGE)
    page = min(max(page, 0), pages - 1)
    products = await get_catalog(shop["id"], PER_PAGE, page * PER_PAGE, sort=sort)

    sub = compute_shop_sub_state(shop)
    pro_badge = " ⭐️" if sub["is_pro"] else ""
    shop_title_esc = html.escape(shop["title"]) + pro_badge
    descr_line = f"\n{html.escape(shop['description'])}\n" if shop.get("description") else "\n"
    if total == 0:
        text = (
            f"🏪 <b>{shop_title_esc}</b>{descr_line}\n"
            + t(lang, "catalog_empty").format(shop=html.escape(shop["title"]))
        )
    else:
        text = (
            t(lang, "catalog_header").format(shop=shop_title_esc, count=total)
            + descr_line
        )
    kb = catalog_browse_kb(lang, products, shop, page, pages, sort=sort)
    return text, kb


@router.message(StateFilter("*"), F.text.in_(all_variants("btn_shops")))
async def show_shops_menu(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    lang = await user_lang(uid)
    text, kb = await render_shops_list(uid, lang, 0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ushop:page:"))
async def shops_page_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    lang = await user_lang(uid)
    page = int(callback.data.split(":")[2])
    text, kb = await render_shops_list(uid, lang, page)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data == "ushop:find")
async def search_shops_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    await state.clear()
    await state.set_state(BuyerSearch.shop_query)
    prompt = (
        "🔎 Введите название магазина или канала для поиска:"
        if lang == "ru"
        else "🔎 Do'kon yoki kanal nomini kiriting:"
    )
    await callback.message.answer(prompt, reply_markup=cancel_reply_kb(lang))
    await callback.answer()


@router.message(
    StateFilter(BuyerSearch.shop_query, BuyerSearch.item_query, Checkout.promo),
    F.text.in_(all_variants("btn_cancel")),
)
async def buyer_search_cancel(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    await state.clear()
    await message.answer(t(lang, "checkout_cancelled"), reply_markup=main_menu(lang))


@router.message(BuyerSearch.shop_query, F.text, NOT_COMMAND)
async def search_shops_do(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await user_lang(uid)
    await state.clear()
    found = await search_public_shops(message.text)
    await message.answer("🔎", reply_markup=main_menu(lang))
    if not found:
        await message.answer(t(lang, "catalog_search_empty"))
        return
    active = await get_active_shop(uid)
    active_id = active["id"] if active else 0
    await message.answer(
        t(lang, "shops_title"),
        reply_markup=public_shops_kb(found, active_id, 0, 1),
    )


@router.callback_query(F.data.startswith("ushop:sel:"))
async def select_shop_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    lang = await user_lang(uid)
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop or not shop["is_active"]:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    await set_active_shop(uid, shop_id)
    await callback.answer(t(lang, "shop_selected").format(title=shop["title"]))
    text, kb = await render_shop_catalog(shop, lang, 0)
    await safe_edit(callback.message, text, kb)


@router.message(StateFilter("*"), F.text.in_(all_variants("btn_catalog")))
async def show_catalog_msg(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    lang = await user_lang(uid)
    shop = await get_active_shop(uid)
    if not shop:
        text, kb = await render_shops_list(uid, lang, 0)
        await message.answer(text, reply_markup=kb)
        return
    text, kb = await render_shop_catalog(shop, lang, 0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ushop:cat:"))
async def shop_catalog_page_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    parts = callback.data.split(":")
    shop_id = int(parts[2])
    sort = "new"
    page = 0
    if len(parts) == 4:
        if parts[3].isdigit():
            page = int(parts[3])
        else:
            sort = parts[3]
    elif len(parts) >= 5:
        sort = parts[3] if parts[3] in ("new", "cheap", "exp") else "new"
        page = int(parts[4]) if parts[4].isdigit() else 0

    shop = await get_shop(shop_id)
    if not shop or not shop["is_active"]:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    await set_active_shop(uid, shop_id)
    text, kb = await render_shop_catalog(shop, lang, page, sort=sort)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ushop:sort:"))
async def shop_catalog_sort_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    parts = callback.data.split(":")
    shop_id = int(parts[2])
    sort = parts[3] if len(parts) > 3 and parts[3] in ("new", "cheap", "exp") else "new"
    shop = await get_shop(shop_id)
    if not shop or not shop["is_active"]:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    text, kb = await render_shop_catalog(shop, lang, 0, sort=sort)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ushop:cards:"))
async def shop_catalog_cards_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    parts = callback.data.split(":")
    shop_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    sort = parts[4] if len(parts) > 4 and parts[4] in ("new", "cheap", "exp") else "new"
    shop = await get_shop(shop_id)
    if not shop or not shop["is_active"]:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    products = await get_catalog(shop_id, PER_PAGE, page * PER_PAGE, sort=sort)
    if not products:
        await callback.answer(
            t(lang, "catalog_empty").format(shop=shop["title"]), show_alert=True
        )
        return
    await callback.answer()
    for p in products:
        await send_card(callback.message, lang, p, shop_title=shop["title"], user_id=uid)


@router.callback_query(F.data.startswith("ucard:"))
async def user_view_card_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    product_id = int(callback.data.split(":")[1])
    product = await get_product(product_id)
    if not product or product["hidden"]:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    shop = await get_shop(product["shop_id"])
    if shop:
        await set_active_shop(uid, shop["id"])
    await callback.answer()
    await send_card(
        callback.message,
        lang,
        product,
        shop_title=shop["title"] if shop else None,
        user_id=uid,
    )


@router.callback_query(F.data.startswith("ushop:search:"))
async def search_catalog_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    shop_id = int(callback.data.split(":")[2])
    shop = await get_shop(shop_id)
    if not shop:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    await state.clear()
    await state.set_state(BuyerSearch.item_query)
    await state.update_data(shop_id=shop_id)
    await callback.message.answer(
        t(lang, "catalog_search_prompt").format(shop=html.escape(shop["title"])),
        reply_markup=cancel_reply_kb(lang),
    )
    await callback.answer()


@router.message(BuyerSearch.item_query, F.text, NOT_COMMAND)
async def search_catalog_do(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await user_lang(uid)
    data = await state.get_data()
    await state.clear()
    shop_id = data.get("shop_id")
    shop = await get_shop(shop_id) if shop_id else await get_active_shop(uid)
    await message.answer("🔎", reply_markup=main_menu(lang))
    if not shop:
        await message.answer(t(lang, "choose_shop_first"))
        return
    found = await search_products(shop["id"], message.text, limit=10, only_in_stock=True)
    if not found:
        await message.answer(t(lang, "catalog_search_empty"))
        return
    await message.answer(
        t(lang, "catalog_header").format(shop=html.escape(shop["title"]), count=len(found)),
        reply_markup=catalog_browse_kb(lang, found, shop, 0, 1),
    )


# ====================== КОРЗИНА И ПРОМОКОДЫ ======================

def cart_text(lang: str, shop_title: str, rows: list, promo: dict = None) -> str:
    lines = [t(lang, "cart_title").format(shop=html.escape(shop_title)), ""]
    subtotal = 0
    for n, r in enumerate(rows, 1):
        cur = cur_label(lang, r["currency"])
        item_sub = r["price"] * r["qty"]
        subtotal += item_sub
        lines.append(
            f"{n}. {html.escape(r['title'])}\n"
            f"   {fmt_price(r['price'])} {cur} × {r['qty']} = {fmt_price(item_sub)} {cur}"
        )
    lines.append("")
    cur_str = cur_label(lang, rows[0]["currency"])
    if promo:
        discount = int(round(subtotal * (promo["discount_pct"] / 100.0)))
        if discount >= subtotal:
            discount = max(0, subtotal - 1)
        total = max(1, subtotal - discount)
        lines.append(t(lang, "cart_subtotal").format(subtotal=fmt_price(subtotal), currency=cur_str))
        lines.append(
            t(lang, "cart_discount").format(
                code=html.escape(promo["code"]),
                pct=promo["discount_pct"],
                discount=fmt_price(discount),
                currency=cur_str,
            )
        )
        lines.append(t(lang, "cart_total").format(total=fmt_price(total), currency=cur_str))
    else:
        lines.append(t(lang, "cart_total").format(total=fmt_price(subtotal), currency=cur_str))
    return "\n".join(lines)


async def render_cart(user_id: int, lang: str, state: FSMContext = None):
    shop = await get_active_shop(user_id)
    cart_shops = await get_user_cart_shops(user_id)

    if (not shop or not await get_cart(user_id, shop["id"])) and cart_shops:
        first_cart_shop_id = cart_shops[0]["id"]
        await set_active_shop(user_id, first_cart_shop_id)
        shop = await get_shop(first_cart_shop_id)

    if not shop:
        return t(lang, "cart_empty"), None

    rows = await get_cart(user_id, shop["id"])
    other_shops = [s for s in cart_shops if s["id"] != shop["id"]]

    if not rows:
        text = t(lang, "cart_empty")
        if other_shops:
            text += t(lang, "cart_other_shops")
        return text, cart_kb(lang, [], other_shops=other_shops)

    promo = None
    if state:
        data = await state.get_data()
        promo_code = data.get(f"promo_{shop['id']}")
        if promo_code:
            promo = await validate_promocode(shop["id"], promo_code)

    text = cart_text(lang, shop["title"], rows, promo=promo)
    if other_shops:
        text += t(lang, "cart_other_shops")
    return text, cart_kb(lang, rows, other_shops=other_shops, promo_code=promo["code"] if promo else None)


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    product_id = int(callback.data.split(":")[1])
    product = await get_product(product_id)
    if not product or product["hidden"] or not product["in_stock"]:
        await callback.answer(t(lang, "unavailable"), show_alert=True)
        return

    shop_id = product.get("shop_id") or 1
    shop = await get_shop(shop_id)
    if not shop or not shop["is_active"]:
        await callback.answer(t(lang, "unavailable"), show_alert=True)
        return

    await set_active_shop(uid, shop_id)
    rows = await get_cart(uid, shop_id)
    in_cart_ids = {r["item_id"] for r in rows}
    if product_id not in in_cart_ids and len(rows) >= MAX_CART_ITEMS:
        await callback.answer(t(lang, "cart_full"), show_alert=True)
        return
    if rows and rows[0]["currency"] != product["currency"]:
        await callback.answer(t(lang, "currency_mismatch"), show_alert=True)
        return

    is_new, new_qty = await add_to_cart(uid, product_id)
    if is_new:
        await callback.answer(t(lang, "added"))
    else:
        await callback.answer(t(lang, "added_more").format(qty=new_qty))

    try:
        await callback.message.edit_reply_markup(
            reply_markup=buy_kb(lang, product_id, shop_id, in_cart_qty=new_qty)
        )
    except TelegramAPIError:
        pass


@router.callback_query(F.data == "cart:open")
async def open_cart_cb(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    text, kb = await render_cart(uid, lang, state)
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.message(StateFilter("*"), F.text.in_(all_variants("btn_cart")))
async def show_cart(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    text, kb = await render_cart(message.from_user.id, lang, state)
    await message.answer(text, reply_markup=kb)


async def start_checkout(callback: CallbackQuery, state: FSMContext, lang: str):
    uid = callback.from_user.id
    shop = await get_active_shop(uid)
    if not shop:
        await callback.answer(t(lang, "cart_empty"), show_alert=True)
        return
    rows = await get_cart(uid, shop["id"])
    if not rows:
        await callback.answer(t(lang, "cart_empty"), show_alert=True)
        return
    bad = [r for r in rows if r["hidden"] or not r["in_stock"]]
    if bad:
        for r in bad:
            await remove_from_cart(uid, r["item_id"])
        await callback.message.answer(t(lang, "cart_removed_unavailable"))
        text, kb = await render_cart(uid, lang, state)
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return

    data = await state.get_data()
    promo_code = data.get(f"promo_{shop['id']}")

    saved = await get_user_saved_checkout(uid)
    if saved:
        await state.set_state(Checkout.confirm_saved)
        await state.update_data(
            shop_id=shop["id"],
            promo_code=promo_code,
            name=saved["name"],
            phone=saved["phone"],
            address=saved["address"],
        )
        await callback.message.answer(
            t(lang, "fast_checkout_prompt").format(
                name=html.escape(saved["name"]),
                phone=html.escape(saved["phone"]),
                address=html.escape(saved["address"]),
            ),
            reply_markup=fast_checkout_kb(lang),
        )
        await callback.answer()
        return

    await state.set_state(Checkout.name)
    await state.update_data(shop_id=shop["id"], promo_code=promo_code)
    await callback.message.answer(t(lang, "ask_name"), reply_markup=cancel_reply_kb(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("cart:"))
async def cart_actions(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    parts = callback.data.split(":")
    action = parts[1]

    if action == "checkout":
        await start_checkout(callback, state, lang)
        return

    if action == "promo":
        shop = await get_active_shop(uid)
        if not shop:
            await callback.answer()
            return
        await state.set_state(Checkout.promo)
        await state.update_data(promo_shop_id=shop["id"])
        await callback.message.answer(t(lang, "promo_ask"), reply_markup=cancel_reply_kb(lang))
        await callback.answer()
        return

    if action == "switch" and len(parts) > 2:
        await set_active_shop(uid, int(parts[2]))
    elif action in ("inc", "dec", "del") and len(parts) > 2:
        item_id = int(parts[2])
        if action == "inc":
            await change_qty(uid, item_id, 1)
        elif action == "dec":
            await change_qty(uid, item_id, -1)
        else:
            await remove_from_cart(uid, item_id)
    elif action == "clear":
        shop = await get_active_shop(uid)
        await clear_cart(uid, shop["id"] if shop else None)

    text, kb = await render_cart(uid, lang, state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@router.message(Checkout.promo, F.text, NOT_COMMAND)
async def apply_promo_msg(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await user_lang(uid)
    data = await state.get_data()
    shop_id = data.get("promo_shop_id")
    if not shop_id:
        shop = await get_active_shop(uid)
        shop_id = shop["id"] if shop else 0

    code = message.text.strip().upper()
    promo = await validate_promocode(shop_id, code)
    await state.set_state(None)
    if not promo:
        await message.answer(t(lang, "promo_invalid"), reply_markup=main_menu(lang))
    else:
        await state.update_data({f"promo_{shop_id}": promo["code"]})
        await message.answer(
            t(lang, "promo_applied").format(code=html.escape(promo["code"]), pct=promo["discount_pct"]),
            reply_markup=main_menu(lang),
        )
    text, kb = await render_cart(uid, lang, state)
    await message.answer(text, reply_markup=kb)


# ====================== ОФОРМЛЕНИЕ ЗАКАЗА (ОБЫЧНОЕ И В 1 КЛИК) ======================

@router.callback_query(Checkout.confirm_saved, F.data.startswith("chk:fast_"))
async def fast_checkout_decision_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    choice = callback.data.split("_")[1]
    data = await state.get_data()

    if choice == "cancel":
        await state.set_state(None)
        await safe_edit(callback.message, t(lang, "checkout_cancelled"))
        await callback.answer()
        return

    if choice == "edit":
        await state.set_state(Checkout.name)
        await callback.message.answer(t(lang, "ask_name"), reply_markup=cancel_reply_kb(lang))
        await callback.answer()
        return

    # 1-Click Checkout!
    await callback.answer("⚡️ Оформляем заказ...")
    await finalize_order_creation(
        callback.message,
        uid,
        lang,
        state,
        bot,
        user_obj=callback.from_user,
        name=data["name"],
        phone=data["phone"],
        address=data["address"],
    )


@router.message(
    StateFilter(Checkout.name, Checkout.phone, Checkout.address),
    F.text.in_(all_variants("btn_cancel")),
)
async def checkout_cancel(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    await state.set_state(None)
    await message.answer(t(lang, "checkout_cancelled"), reply_markup=main_menu(lang))


@router.message(Checkout.name, F.text, NOT_COMMAND)
async def checkout_name(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    name = message.text.strip()
    if not 2 <= len(name) <= 60:
        await message.answer(t(lang, "bad_name"))
        return
    await state.update_data(name=name)
    await state.set_state(Checkout.phone)
    await message.answer(t(lang, "ask_phone"), reply_markup=phone_kb(lang))


def normalize_phone(raw: str):
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None


async def ask_address(message: Message, state: FSMContext, lang: str, phone: str):
    await state.update_data(phone=phone)
    await state.set_state(Checkout.address)
    await message.answer(t(lang, "ask_address"), reply_markup=cancel_reply_kb(lang))


@router.message(Checkout.phone, F.contact)
async def checkout_phone_contact(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    digits = re.sub(r"\D", "", message.contact.phone_number or "")
    if len(digits) < 9:
        await message.answer(t(lang, "bad_phone"))
        return
    await ask_address(message, state, lang, "+" + digits)


@router.message(Checkout.phone, F.text, NOT_COMMAND)
async def checkout_phone_text(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    phone = normalize_phone(message.text)
    if phone is None:
        await message.answer(t(lang, "bad_phone"))
        return
    await ask_address(message, state, lang, phone)


async def payment_text(lang: str, order: dict) -> str:
    settings = await get_shop_settings(order.get("shop_id"))
    shop_name = html.escape(order.get("shop_title") or settings["title"] or "Магазин")
    promo_line = ""
    if order.get("promo_code") and order.get("discount_amount"):
        cur_s = cur_label(lang, order["currency"])
        promo_line = f"🎟 Промокод <code>{html.escape(order['promo_code'])}</code>: -{fmt_price(order['discount_amount'])} {cur_s}\n"

    if settings["card_number"]:
        return t(lang, "pay_instructions").format(
            id=order["id"],
            shop=shop_name,
            promo_line=promo_line,
            total=fmt_price(order["total"]),
            currency=cur_label(lang, order["currency"]),
            card=f"<code>{html.escape(settings['card_number'])}</code>",
            holder=html.escape(settings["card_holder"] or "—"),
        )
    return t(lang, "pay_no_card").format(
        id=order["id"],
        shop=shop_name,
        promo_line=promo_line,
        total=fmt_price(order["total"]),
        currency=cur_label(lang, order["currency"]),
        phone=html.escape(settings["shop_phone"] or "—"),
    )


async def notify_shop_new_order(bot: Bot, order: dict, items: list, user):
    """Уведомляет владельца и менеджеров магазина о новом созданном заказе."""
    notify_ids = await get_shop_notify_ids(order.get("shop_id"))
    details = "🆕 <b>Новый заказ!</b>\n\n" + admin_order_text(order, items, user.username)
    kb = admin_order_kb(order)
    for admin_id in notify_ids:
        try:
            await bot.send_message(admin_id, details, reply_markup=kb)
        except TelegramAPIError:
            pass


async def finalize_order_creation(
    message: Message,
    uid: int,
    lang: str,
    state: FSMContext,
    bot: Bot,
    user_obj,
    name: str,
    phone: str,
    address: str,
):
    data = await state.get_data()
    shop_id = data.get("shop_id")
    if not shop_id:
        shop = await get_active_shop(uid)
        shop_id = shop["id"] if shop else None

    rows = await get_cart(uid, shop_id) if shop_id else []
    if not rows:
        await state.clear()
        await message.answer(t(lang, "cart_empty"), reply_markup=main_menu(lang))
        return

    bad = [r for r in rows if r["hidden"] or not r["in_stock"]]
    if bad:
        for r in bad:
            await remove_from_cart(uid, r["item_id"])
        await state.clear()
        await message.answer(t(lang, "cart_removed_unavailable"), reply_markup=main_menu(lang))
        text, kb = await render_cart(uid, lang, state)
        await message.answer(text, reply_markup=kb)
        return

    promo_code = data.get("promo_code") or data.get(f"promo_{shop_id}")
    order_id = await create_order(uid, shop_id, name, phone, address, rows, promo_code=promo_code)
    order = await get_order(order_id)
    items = await get_order_items(order_id)
    await state.clear()
    await state.set_state(Checkout.payment)
    await state.update_data(order_id=order_id)
    await message.answer("✅", reply_markup=main_menu(lang))
    await message.answer(await payment_text(lang, order), reply_markup=buyer_order_pay_kb(lang, order))
    await notify_shop_new_order(bot, order, items, user_obj)


@router.message(Checkout.address, F.text, NOT_COMMAND)
async def checkout_address(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    lang = await user_lang(uid)
    address = message.text.strip()
    if not 5 <= len(address) <= 200:
        await message.answer(t(lang, "bad_address"))
        return

    data = await state.get_data()
    await finalize_order_creation(
        message,
        uid,
        lang,
        state,
        bot,
        user_obj=message.from_user,
        name=data["name"],
        phone=data["phone"],
        address=address,
    )


# ---------- оплата: скриншот чека ----------

async def notify_admins_receipt(bot: Bot, order: dict, items: list, photo_id: str, user):
    cur = cur_label("ru", order["currency"])
    shop_name = html.escape(order.get("shop_title") or "Магазин")
    short = (
        f"💳 <b>Чек по заказу №{order['id']}</b> ({shop_name})\n"
        f"💰 Сумма: {fmt_price(order['total'])} {cur}"
    )
    details = admin_order_text(order, items, user.username)
    kb = admin_order_kb(order)

    notify_ids = await get_shop_notify_ids(order.get("shop_id"))
    for admin_id in notify_ids:
        try:
            await bot.send_photo(admin_id, photo_id, caption=short)
            await bot.send_message(admin_id, details, reply_markup=kb)
        except TelegramAPIError:
            pass


@router.message(Checkout.payment, F.photo)
async def receive_receipt(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    lang = await user_lang(uid)
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await get_order(order_id) if order_id else None
    if not order or order["user_id"] != uid or order["status"] not in PAYABLE:
        await state.clear()
        await message.answer(t(lang, "order_not_payable"), reply_markup=main_menu(lang))
        return

    photo_id = message.photo[-1].file_id
    await set_order_payment(order_id, photo_id)
    await state.clear()
    await message.answer(t(lang, "receipt_ok"), reply_markup=main_menu(lang))

    order = await get_order(order_id)
    items = await get_order_items(order_id)
    await notify_admins_receipt(bot, order, items, photo_id, message.from_user)


@router.message(Checkout.payment, F.document)
async def receipt_as_file(message: Message):
    lang = await user_lang(message.from_user.id)
    await message.answer(t(lang, "send_receipt_photo"))


# ====================== МОИ ЗАКАЗЫ ======================

def order_text(lang: str, order: dict, items: list) -> str:
    lines = [
        f"• {html.escape(i['title'])} × {i['qty']} — "
        f"{fmt_price(i['price'] * i['qty'])} {cur_label(lang, i['currency'])}"
        for i in items
    ]
    promo_line = ""
    if order.get("promo_code") and order.get("discount_amount"):
        cur_s = cur_label(lang, order["currency"])
        promo_line = f"\n🎟 Промокод <code>{html.escape(order['promo_code'])}</code>: -{fmt_price(order['discount_amount'])} {cur_s}"
    return t(lang, "order_detail").format(
        id=order["id"],
        shop=html.escape(order.get("shop_title") or "Магазин"),
        status=status_label(lang, order["status"]),
        items="\n".join(lines),
        promo_line=promo_line,
        total=fmt_price(order["total"]),
        currency=cur_label(lang, order["currency"]),
        address=html.escape(order["address"]),
    )


@router.message(StateFilter("*"), F.text.in_(all_variants("btn_orders")))
async def my_orders(message: Message, state: FSMContext):
    await state.clear()
    lang = await user_lang(message.from_user.id)
    orders = await get_user_orders(message.from_user.id)
    if not orders:
        await message.answer(t(lang, "orders_empty"))
        return
    await message.answer(t(lang, "orders_title"), reply_markup=orders_kb(lang, orders))


@router.callback_query(F.data.startswith("order:"))
async def order_view(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    order_id = int(callback.data.split(":")[1])
    order = await get_order(order_id)
    if not order or order["user_id"] != uid:
        await callback.answer(t(lang, "order_not_found"), show_alert=True)
        return
    items = await get_order_items(order_id)
    await callback.message.answer(
        order_text(lang, order, items), reply_markup=order_detail_kb(lang, order)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"))
async def pay_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    order_id = int(callback.data.split(":")[1])
    order = await get_order(order_id)
    if not order or order["user_id"] != uid:
        await callback.answer(t(lang, "order_not_found"), show_alert=True)
        return
    if order["status"] not in PAYABLE:
        await callback.answer(t(lang, "order_not_payable"), show_alert=True)
        return
    await state.clear()
    await state.set_state(Checkout.payment)
    await state.update_data(order_id=order_id)
    await callback.message.answer(await payment_text(lang, order), reply_markup=buyer_order_pay_kb(lang, order))
    await callback.answer()



@router.callback_query(F.data.startswith("opay:"))
async def order_auto_pay_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    parts = callback.data.split(":")
    provider = parts[1]  # click or payme
    order_id = int(parts[2])
    order = await get_order(order_id)
    if not order or order["user_id"] != uid or order["status"] not in PAYABLE:
        await callback.answer(t(lang, "order_not_payable"), show_alert=True)
        return
    if order.get("currency") != "UZS":
        await callback.answer("CLICK / Payme поддерживают оплату только в сумах (UZS).", show_alert=True)
        return

    settings = await get_shop_settings(order.get("shop_id"))
    billing = await get_platform_billing_config()
    if provider == "click":
        token = settings.get("click_token") or billing.get("click_token") or ""
        prov_name = "CLICK"
    else:
        token = settings.get("payme_token") or billing.get("payme_token") or ""
        prov_name = "Payme"

    if not token:
        msg = (
            f"⚠️ Авто-оплата {prov_name} в этом магазине ещё не подключена продавцом. "
            "Пожалуйста, оплатите переводом по карте и отправьте скриншот чека."
            if lang == "ru"
            else f"⚠️ Ushbu do'konda {prov_name} avto-to'lovi hali ulanmagan. "
                 "Iltimos, karta orqali to'lab chek skrinshotini yuboring."
        )
        await callback.answer(msg, show_alert=True)
        return

    amount_uzs = max(1000, int(order["total"]))
    shop_name = order.get("shop_title") or settings.get("title") or "Магазин"
    try:
        await bot.send_invoice(
            chat_id=uid,
            title=f"Заказ №{order['id']} — {shop_name}",
            description=f"Автоматическая оплата заказа №{order['id']} через {prov_name}",
            payload=f"ord:{order['id']}:{provider}",
            provider_token=token,
            currency="UZS",
            prices=[LabeledPrice(label=f"Заказ №{order['id']}", amount=amount_uzs * 100)],
        )
        await callback.answer()
    except TelegramAPIError as e:
        await callback.answer(f"Ошибка выставления счёта {prov_name}: {e}", show_alert=True)


@router.callback_query(F.data.startswith("ocancel:"))
async def order_cancel(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await user_lang(uid)
    order_id = int(callback.data.split(":")[1])
    if await cancel_order(order_id, uid):
        await safe_edit(callback.message, t(lang, "order_cancelled"))
    else:
        await callback.answer(t(lang, "order_not_payable"), show_alert=True)
        return
    await callback.answer()


# ====================== КОНТАКТЫ МАГАЗИНА ======================

@router.message(StateFilter("*"), F.text.in_(all_variants("btn_contact")))
async def contacts(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    lang = await user_lang(uid)
    shop = await get_active_shop(uid)
    settings = await get_shop_settings(shop["id"] if shop else None)
    descr = f"{html.escape(settings['description'])}\n\n" if settings.get("description") else ""
    channel_line = (
        f"📢 Канал: @{html.escape(settings['channel_username'])}\n"
        if settings.get("channel_username")
        else ""
    )
    phone = html.escape(settings["shop_phone"] or "—")
    await message.answer(
        t(lang, "contact_text").format(
            shop=html.escape(settings["title"]),
            descr=descr,
            phone=phone,
            channel_line=channel_line,
        )
    )