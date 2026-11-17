from aiogram.types import (
    ChatAdministratorRights,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)

from texts import cur_label, fmt_price, status_label, t


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:uz"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
            ]
        ]
    )


def main_menu(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "btn_catalog")), KeyboardButton(text=t(lang, "btn_cart"))],
            [KeyboardButton(text=t(lang, "btn_orders")), KeyboardButton(text=t(lang, "btn_shops"))],
            [KeyboardButton(text=t(lang, "btn_contact")), KeyboardButton(text=t(lang, "btn_lang"))],
            [KeyboardButton(text=t(lang, "btn_my_shop"))],
        ],
        resize_keyboard=True,
    )


def buy_kb(lang: str, product_id: int, shop_id: int = None, in_cart_qty: int = 0) -> InlineKeyboardMarkup:
    rows = []
    if in_cart_qty > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_in_cart_more").format(qty=in_cart_qty),
                    callback_data=f"buy:{product_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_go_cart").format(qty=in_cart_qty),
                    callback_data="cart:open",
                )
            ]
        )
    else:
        rows.append([InlineKeyboardButton(text=t(lang, "btn_buy"), callback_data=f"buy:{product_id}")])

    if shop_id:
        rows.append(
            [InlineKeyboardButton(text=t(lang, "btn_catalog"), callback_data=f"ushop:cat:{shop_id}:0")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_kb(bot_username: str, product_id: int, shop_id: int = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🛒 Заказать / Buyurtma berish",
                url=f"https://t.me/{bot_username}?start=p_{product_id}",
            )
        ]
    ]
    if shop_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🏪 Витрина / Do'kon",
                    url=f"https://t.me/{bot_username}?start=s_{shop_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_showcase_kb(bot_username: str, shop_id: int) -> InlineKeyboardMarkup:
    """Клавиатура под закреплённым постом-витриной или обычным постом в канале."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍 Открыть каталог / Katalog",
                    url=f"https://t.me/{bot_username}?start=s_{shop_id}",
                )
            ]
        ]
    )


def custom_url_kb(btn_text: str, btn_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=btn_url)]]
    )


# ---------- покупатель: магазины и каталог ----------

def pager_row(prefix: str, page: int, pages: int) -> list:
    """Строка «◀️ 2/5 ▶️». Пустая, если страница одна."""
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(_btn("◀️", f"{prefix}:{page - 1}"))
    row.append(_btn(f"{page + 1}/{pages}", "adm:noop"))
    if page < pages - 1:
        row.append(_btn("▶️", f"{prefix}:{page + 1}"))
    return [row]


def public_shops_kb(shops: list, active_shop_id: int, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for s in shops:
        pro_badge = "⭐️ " if s.get("is_pro") else ""
        mark = "🟢 " if s["id"] == active_shop_id else "🏪 "
        cnt = s.get("stock_count", 0)
        rows.append([_btn(f"{mark}{pro_badge}{s['title'][:28]} · 🛍 {cnt}", f"ushop:sel:{s['id']}")])
    rows += pager_row("ushop:page", page, pages)
    rows.append([_btn("🔎 Найти магазин / Do'kon qidirish", "ushop:find")])
    rows.append([_btn("📢 Подключить свой канал / магазин (PRO 1$/мес)", "adm:quick_channel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_browse_kb(
    lang: str, products: list, shop: dict, page: int, pages: int, sort: str = "new"
) -> InlineKeyboardMarkup:
    rows = []
    cur_map = {"USD": "$", "UZS": t(lang, "cur_uzs")}
    for p in products:
        cur = cur_map.get(p["currency"], p["currency"])
        rows.append(
            [
                _btn(
                    f"🛍 {p['title'][:28]} — {fmt_price(p['price'])} {cur}",
                    f"ucard:{p['id']}",
                )
            ]
        )
    rows += pager_row(f"ushop:cat:{shop['id']}:{sort}", page, pages)
    if len(products) > 1 or page > 0:
        s_new = ("• " if sort == "new" else "") + t(lang, "sort_new")
        s_chp = ("• " if sort == "cheap" else "") + t(lang, "sort_cheap")
        s_exp = ("• " if sort == "exp" else "") + t(lang, "sort_exp")
        rows.append(
            [
                _btn(s_new, f"ushop:sort:{shop['id']}:new"),
                _btn(s_chp, f"ushop:sort:{shop['id']}:cheap"),
                _btn(s_exp, f"ushop:sort:{shop['id']}:exp"),
            ]
        )
    action_row = [_btn(t(lang, "btn_search_catalog"), f"ushop:search:{shop['id']}")]
    if products:
        action_row.append(_btn(t(lang, "btn_all_items"), f"ushop:cards:{shop['id']}:{page}:{sort}"))
    rows.append(action_row)
    if shop.get("channel_username"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_go_channel"),
                    url=f"https://t.me/{shop['channel_username'].lstrip('@')}",
                )
            ]
        )
    rows.append([_btn(t(lang, "btn_shops"), "ushop:page:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- покупатель: корзина и заказы ----------

def cart_kb(lang: str, rows: list, other_shops: list = None, promo_code: str = None) -> InlineKeyboardMarkup:
    keyboard = []
    for n, r in enumerate(rows, 1):
        keyboard.append(
            [
                InlineKeyboardButton(text=f"➖ {n}", callback_data=f"cart:dec:{r['item_id']}"),
                InlineKeyboardButton(text=f"{r['qty']} шт.", callback_data="adm:noop"),
                InlineKeyboardButton(text=f"➕ {n}", callback_data=f"cart:inc:{r['item_id']}"),
                InlineKeyboardButton(text=f"🗑 {n}", callback_data=f"cart:del:{r['item_id']}"),
            ]
        )
    if rows:
        keyboard.append(
            [InlineKeyboardButton(text=t(lang, "btn_checkout"), callback_data="cart:checkout")]
        )
        promo_label = f"🎟 Промокод: {promo_code} (сменить)" if promo_code else t(lang, "btn_promo")
        keyboard.append(
            [
                InlineKeyboardButton(text=promo_label, callback_data="cart:promo"),
                InlineKeyboardButton(text=t(lang, "btn_clear"), callback_data="cart:clear"),
            ]
        )
    if other_shops:
        for s in other_shops:
            keyboard.append(
                [
                    _btn(
                        f"🛒 {s['title'][:28]} ({s['items_count']})",
                        f"cart:switch:{s['id']}",
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


def fast_checkout_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(t(lang, "btn_use_saved"), "chk:fast_yes")],
            [_btn(t(lang, "btn_edit_saved"), "chk:fast_edit")],
            [_btn(t(lang, "btn_cancel"), "chk:fast_cancel")],
        ]
    )


def cancel_reply_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_cancel"))]],
        resize_keyboard=True,
    )


def phone_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "btn_send_phone"), request_contact=True)],
            [KeyboardButton(text=t(lang, "btn_cancel"))],
        ],
        resize_keyboard=True,
    )


def orders_kb(lang: str, orders: list) -> InlineKeyboardMarkup:
    rows = []
    for o in orders:
        shop_tag = f" ({o['shop_title'][:12]})" if o.get("shop_title") else ""
        text = (
            f"№{o['id']}{shop_tag} · {fmt_price(o['total'])} {cur_label(lang, o['currency'])}"
            f" · {status_label(lang, o['status'])}"
        )
        rows.append([InlineKeyboardButton(text=text, callback_data=f"order:{o['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buyer_order_pay_kb(lang: str, order: dict, settings: dict = None) -> InlineKeyboardMarkup:
    rows = []
    oid = order["id"]
    if order["status"] in ("pending_payment", "rejected"):
        rows.append(
            [InlineKeyboardButton(text=t(lang, "btn_pay"), callback_data=f"pay:{oid}")]
        )
    if order["status"] == "pending_payment":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_cancel_order"), callback_data=f"ocancel:{oid}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def order_detail_kb(lang: str, order: dict):
    return buyer_order_pay_kb(lang, order)


def pay_again_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    """Кнопка под сообщением «оплата не подтверждена»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_pay"), callback_data=f"pay:{order_id}")]]
    )


# ====================== КАБИНЕТ ВЛАДЕЛЬЦА КАНАЛА / МАГАЗИНА ======================

def onboarding_kb(
    bot_username: str = None, shops: list = None, is_superadmin: bool = False
) -> InlineKeyboardMarkup:
    rows = [
        [_btn("📢 Подключить мой Telegram-канал", "adm:quick_channel")],
        [_btn("💎 Купить подписку PRO (1$ / мес)", "sub:menu")],
        [_btn("🏪 Создать магазин вручную", "adm:create_start")],
    ]
    if shops:
        for s in shops:
            rows.append([_btn(f"🛠 Управлять «{s['title'][:28]}»", f"adm:sel_shop:{s['id']}")])
    if is_superadmin:
        rows.append([_btn("👑 Панель владельца платформы", "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_shops_kb(shops: list, current_shop_id: int) -> InlineKeyboardMarkup:
    rows = []
    for s in shops:
        mark = "🟢 " if s["id"] == current_shop_id else "🏪 "
        pro = "⭐️ " if s.get("is_pro") else ""
        rev = f" · 🔎 {s['review_count']}" if s.get("review_count") else ""
        rows.append(
            [
                _btn(
                    f"{mark}{pro}{s['title'][:24]} (🛍 {s.get('items_count', 0)}{rev})",
                    f"adm:sel_shop:{s['id']}",
                )
            ]
        )
    rows.append([_btn("📢 Подключить ещё один канал", "adm:quick_channel")])
    rows.append([_btn("➕ Создать магазин вручную", "adm:create_start")])
    rows.append([_btn("⬅️ В панель управления", "adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu(
    review_count: int = 0,
    has_channel: bool = True,
    shops_count: int = 1,
    is_superadmin: bool = False,
    sub_state: dict = None,
    price_usd: float = 1.0,
) -> InlineKeyboardMarkup:
    orders_text = "📦 Заказы"
    if review_count:
        orders_text += f" · 🔎 {review_count} на проверке"

    p_str = f"{int(price_usd)}$" if float(price_usd).is_integer() else f"{price_usd}$"
    if sub_state and not sub_state.get("is_pro"):
        sub_btn_text = f"🔥 Включить PRO ({p_str}/мес) — Безлимит!"
    elif sub_state and sub_state.get("status") == "trial":
        sub_btn_text = f"🎁 Пробный PRO ({sub_state.get('days_left', 0)} дн.) · Тариф {p_str}/мес"
    else:
        sub_btn_text = f"💎 Подписка PRO ({p_str}/мес) и Бонусы"

    rows = []
    if has_channel:
        rows.append([_btn("📢 Управление каналом", "chm:menu"), _btn("➕ Выложить товар", "adm:add")])
    else:
        rows.append([_btn("📢 Подключить Telegram-канал (1 клик)", "set:channel")])
        rows.append([_btn("➕ Добавить товар", "adm:add")])
    rows.append([_btn(orders_text, "adm:orders")])
    rows.append([_btn("🛍 Все товары", "adm:products"), _btn("🎟 Промокоды", "prm:menu")])
    rows.append([_btn("👥 Клиенты", "cus:l:0"), _btn("📣 Рассылка", "adm:bc")])
    rows.append([_btn("📊 Статистика и CSV", "adm:stats"), _btn("⚙️ Настройки", "adm:settings")])
    rows.append([_btn(sub_btn_text, "sub:menu")])
    if shops_count > 1:
        rows.append([_btn(f"🔄 Мои каналы и магазины ({shops_count})", "adm:my_shops")])
    else:
        rows.append([_btn("➕ Подключить ещё один канал / магазин", "adm:quick_channel")])
    if is_superadmin:
        rows.append([_btn("👑 Супер-админ платформы", "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- система подписки PRO ($1/месяц) ----------

def subscription_menu_kb(plans: dict, has_stars: bool = True) -> InlineKeyboardMarkup:
    rows = []
    for m, p in plans.items():
        usd_s = f"{int(p['usd'])}$" if float(p["usd"]).is_integer() else f"{p['usd']}$"
        uzs_s = fmt_price(p["uzs"])
        rows.append(
            [
                _btn(
                    f"{p['badge']} · {p['title']} — {usd_s} ({uzs_s} сум)",
                    f"sub:plan:{m}",
                )
            ]
        )
    rows.append(
        [
            _btn("🎁 Ввести подарочный код", "sub:voucher"),
            _btn("🤝 +15 дней за друга", "sub:ref"),
        ]
    )
    rows.append([_btn("⬅️ В меню магазина", "adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_pay_method_kb(months: int, plan: dict, billing: dict = None) -> InlineKeyboardMarkup:
    usd_s = f"{int(plan['usd'])}$" if float(plan["usd"]).is_integer() else f"{plan['usd']}$"
    uzs_s = fmt_price(plan["uzs"])
    rows = [
        [
            _btn(f"🟦 Авто-оплата CLICK ({uzs_s} сум)", f"sub:click:{months}"),
        ],
        [
            _btn(f"🟩 Авто-оплата Payme ({uzs_s} сум)", f"sub:payme:{months}"),
        ],
        [
            _btn(
                f"⭐️ Авто-оплата Telegram Stars ({plan['stars']} ⭐️)",
                f"sub:stars:{months}",
            )
        ],
        [
            _btn(
                f"💳 Перевод на карту ({usd_s} / {uzs_s} сум) + чек",
                f"sub:card:{months}",
            )
        ],
        [_btn("⬅️ К выбору тарифа", "sub:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sub_upsell_kb(price_usd: float = 1.0) -> InlineKeyboardMarkup:
    p_str = f"{int(price_usd)}$" if float(price_usd).is_integer() else f"{price_usd}$"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"💎 Подключить PRO за {p_str}/месяц", "sub:menu")],
            [_btn("🎁 У меня есть промокод", "sub:voucher")],
            [_btn("⬅️ В меню магазина", "adm:menu")],
        ]
    )


def superadmin_sub_review_kb(sub_id: int, shop_id: int = 0) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn("✅ Подтвердить и включить PRO", f"sup:sub_ok:{sub_id}"),
            _btn("❌ Отклонить", f"sup:sub_no:{sub_id}"),
        ]
    ]
    if shop_id:
        rows.append([_btn("🏪 Открыть магазин в панели", f"sup:shop:{shop_id}")])
    rows.append([_btn("👑 В панель Супер-Админа", "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- промокоды магазина ----------

def promocodes_menu_kb(promos: list, has_channel: bool = False) -> InlineKeyboardMarkup:
    rows = [[_btn("➕ Создать промокод на скидку", "prm:add")]]
    for p in promos:
        lim = f"/{p['max_uses']}" if p["max_uses"] > 0 else ""
        row = [
            _btn(
                f"🎟 {p['code']} (-{p['discount_pct']}%) · исп: {p['used_count']}{lim}",
                "adm:noop",
            ),
            _btn("🗑", f"prm:del:{p['id']}"),
        ]
        if has_channel:
            row.insert(1, _btn("📢 В канал", f"prm:pub:{p['id']}"))
        rows.append(row)
    rows.append([_btn("⬅️ В меню магазина", "adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_manager_kb(shop: dict) -> InlineKeyboardMarkup:
    """Расширенное меню управления привязанным Telegram-каналом через бота."""
    has_ch = bool(shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    if not has_ch:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_btn("📢 Привязать Telegram-канал", "set:channel")],
                [_btn("⬅️ В меню магазина", "adm:menu")],
            ]
        )
    auto_on = bool(shop.get("auto_parse_posts", 1))
    auto_label = (
        "⚡️ Авто-товары из постов и альбомов: ВКЛ"
        if auto_on
        else "⚡️ Авто-товары из постов и альбомов: ВЫКЛ"
    )
    rows = [
        [_btn("📝 Конструктор поста в канал", "chm:post"), _btn("➕ Выложить товар", "adm:add")],
        [_btn("📌 Закрепить пост-витрину в канале", "chm:pin_showcase")],
        [_btn("🎨 Оформление канала (имя / описание / фото)", "chm:profile")],
        [_btn(auto_label, "chm:toggle_auto")],
        [_btn("🔄 Обновить все посты товаров", "chm:sync_all"), _btn("🔍 Права бота", "chm:check")],
        [_btn("🔗 Сменить привязанный канал", "set:channel")],
        [_btn("⬅️ В меню магазина", "adm:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_profile_kb() -> InlineKeyboardMarkup:
    """Клавиатура управления профилем самого Telegram-канала через бота."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✏️ Изменить название канала в Telegram", "chm:ed_title")],
            [_btn("📝 Изменить описание (Bio) канала", "chm:ed_bio")],
            [_btn("🖼 Установить новое фото (аватар) канала", "chm:ed_photo")],
            [_btn("🔗 Вставить ссылку на витрину в описание канала", "chm:auto_bio")],
            [_btn("⬅️ Назад к управлению каналом", "chm:menu")],
        ]
    )


def channel_post_options_kb() -> InlineKeyboardMarkup:
    """Варианты публикации подготовленного поста в канал."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🛍 Опубликовать с кнопкой «Открыть каталог»", "chm:pub:shop")],
            [_btn("📌 Опубликовать с кнопкой и ЗАКРЕПИТЬ", "chm:pub:pin")],
            [_btn("🔗 Опубликовать со своей кнопкой-ссылкой", "chm:pub:custom")],
            [_btn("📢 Опубликовать без кнопок", "chm:pub:plain")],
            [_btn("❌ Отмена", "chm:menu")],
        ]
    )


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ В меню магазина", "adm:menu")]])


def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📥 Скачать заказы (CSV / Excel)", "adm:export_orders")],
            [_btn("⬅️ В меню магазина", "adm:menu")],
        ]
    )


def products_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("➕ Добавить товар", "adm:add")],
            [_btn("📋 Список товаров", "adm:list:0"), _btn("🔎 Найти", "adm:find")],
            [_btn("⬅️ В меню магазина", "adm:menu")],
        ]
    )


def cancel_kb(back_data: str = "adm:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("❌ Отмена", back_data)]])


def skip_or_cancel_kb(skip_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("⏭ Пропустить (настроить позже)", skip_data)],
            [_btn("❌ Отмена", "adm:cancel")],
        ]
    )


def channel_bind_kb(bot_username: str = None, allow_skip: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if bot_username:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить бота в админы канала (1 клик)",
                    url=(
                        f"https://t.me/{bot_username}?startchannel=true"
                        "&admin=post_messages+edit_messages+delete_messages+pin_messages+change_info"
                    ),
                )
            ]
        )
    if allow_skip:
        rows.append([_btn("⏭ Пропустить (привязать позже)", "adm:skip_channel")])
    rows.append([_btn("⬅️ Назад", "adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_request_reply_kb() -> ReplyKeyboardMarkup:
    """Нативная кнопка Telegram для выбора своего канала с автоматической выдачей прав боту."""
    rights_kwargs = {
        "is_anonymous": False,
        "can_manage_chat": True,
        "can_delete_messages": True,
        "can_manage_video_chats": False,
        "can_restrict_members": False,
        "can_promote_members": False,
        "can_change_info": True,
        "can_invite_users": False,
        "can_post_stories": False,
        "can_edit_stories": False,
        "can_delete_stories": False,
        "can_send_welcome_messages": False,
        "can_post_messages": True,
        "can_edit_messages": True,
        "can_pin_messages": True,
    }
    model_fields = getattr(ChatAdministratorRights, "model_fields", None)
    if isinstance(model_fields, dict):
        for fname, finfo in model_fields.items():
            if fname not in rights_kwargs and getattr(finfo, "is_required", lambda: False)():
                rights_kwargs[fname] = False
        rights_kwargs = {k: v for k, v in rights_kwargs.items() if k in model_fields}
    rights = ChatAdministratorRights(**rights_kwargs)
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📢 Выбрать мой канал в Telegram",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=777,
                        chat_is_channel=True,
                        bot_administrator_rights=rights,
                        user_administrator_rights=rights,
                    ),
                )
            ],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )


def detected_channel_kb(channel_id: int, has_shop: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_shop:
        rows.append([_btn("🔗 Привязать к текущему магазину", f"adm:bind_det:{channel_id}:cur")])
    rows.append([_btn("🆕 Создать новый магазин для этого канала", f"adm:bind_det:{channel_id}:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def photos_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Готово", "adm:photos_done")],
            [_btn("❌ Отмена", "adm:cancel")],
        ]
    )


def currency_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("сум (UZS)", "adm:cur:UZS"), _btn("$ (USD)", "adm:cur:USD")],
            [_btn("❌ Отмена", "adm:cancel")],
        ]
    )


def confirm_kb(has_channel: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if has_channel:
        rows.append([_btn("📢 Опубликовать в канал и каталог", "adm:pub:ch")])
        rows.append([_btn("📥 Сохранить только в каталог бота", "adm:pub:bot")])
    else:
        rows.append([_btn("✅ Сохранить в каталог бота", "adm:pub:bot")])
    rows.append([_btn("❌ Отмена", "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_list_kb(products: list, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        mark = "✅" if p["in_stock"] else "❌"
        ch_mark = "📢" if p.get("channel_message_id") or p.get("channel_message_ids") else ""
        rows.append([_btn(f"{mark}{ch_mark} {p['title'][:38]}", f"adm:p:{p['id']}")])
    rows += pager_row("adm:list", page, pages)
    rows.append([_btn("⬅️ К разделу товаров", "adm:products")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_admin_kb(product: dict, has_channel: bool = False) -> InlineKeyboardMarkup:
    pid = product["id"]
    stock_text = "❌ Отметить «нет в наличии»" if product["in_stock"] else "✅ Вернуть «в наличии»"
    next_cur = "USD ($)" if product.get("currency") == "UZS" else "UZS (сум)"
    rows = [
        [_btn("💰 Цена", f"adm:price:{pid}"), _btn("✏️ Название", f"adm:title:{pid}")],
        [_btn("📝 Описание", f"adm:descr:{pid}"), _btn("📷 Фото", f"adm:photos:{pid}")],
        [_btn(f"💱 В {next_cur}", f"adm:cur_sw:{pid}"), _btn("📑 Дублировать", f"adm:dup:{pid}")],
        [_btn(stock_text, f"adm:stock:{pid}")],
    ]
    if has_channel:
        has_post = bool(product.get("channel_message_id") or product.get("channel_message_ids"))
        pub_label = "🔄 Выложить в канал заново" if has_post else "📢 Опубликовать в канал"
        row_ch = [_btn(pub_label, f"adm:repub:{pid}")]
        if has_post:
            row_ch.append(_btn("📌 Закрепить в канале", f"adm:pinp:{pid}"))
        rows.append(row_ch)
    rows.append([_btn("🗑 Удалить товар", f"adm:del:{pid}")])
    rows.append([_btn("⬅️ К списку товаров", "adm:list:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- заказы ----------

STATUS_FILTERS = ("review", "pending_payment", "confirmed", "shipped", "rejected", "cancelled")


def orders_filter_kb(counts: dict) -> InlineKeyboardMarkup:
    rows = []
    for status in STATUS_FILTERS:
        label = status_label("ru", status)
        n = counts.get(status, 0)
        rows.append([_btn(f"{label} · {n}", f"ord:l:{status}:0")])
    total = sum(counts.values())
    rows.append([_btn(f"📋 Все заказы · {total}", "ord:l:all:0")])
    rows.append([_btn("📥 Скачать заказы (CSV)", "adm:export_orders")])
    rows.append([_btn("⬅️ В меню магазина", "adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_list_kb(orders: list, status: str, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for o in orders:
        emoji = status_label("ru", o["status"]).split()[0]
        text = (
            f"{emoji} №{o['id']} · {fmt_price(o['total'])} {cur_label('ru', o['currency'])}"
            f" · {(o['name'] or '')[:14]}"
        )
        rows.append([_btn(text, f"ord:v:{o['id']}")])
    rows += pager_row(f"ord:l:{status}", page, pages)
    rows.append([_btn("⬅️ К заказам", "adm:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_kb(order: dict) -> InlineKeyboardMarkup:
    oid, st = order["id"], order["status"]
    rows = []
    if st in ("review", "pending_payment", "rejected"):
        rows.append([_btn("✅ Подтвердить оплату", f"ord:ok:{oid}")])
    if st == "review":
        rows.append([_btn("❌ Отклонить чек", f"ord:no:{oid}")])
    if st == "confirmed":
        rows.append([_btn("🚚 Отправить заказ", f"ord:sh:{oid}")])
    if st in ("confirmed", "shipped"):
        rows.append([_btn("🏷 Отметить товары проданными", f"ord:sold:{oid}")])
    if order.get("payment_photo_id"):
        rows.append([_btn("🧾 Показать чек", f"ord:rc:{oid}")])
    rows.append(
        [
            _btn("✉️ Написать клиенту", f"ord:msg:{oid}"),
            _btn("👤 Карточка клиента", f"cus:v:{order['user_id']}"),
        ]
    )
    if st in ("pending_payment", "review", "rejected", "confirmed"):
        rows.append([_btn("🚫 Отменить заказ", f"ord:cx:{oid}")])
    rows.append([_btn("⬅️ К заказам", "adm:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_payment_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Да, подтвердить оплату", f"ord:ok2:{order_id}")],
            [_btn("⬅️ Назад к заказу", f"ord:v:{order_id}")],
        ]
    )


# ---------- клиенты ----------

def customers_kb(customers: list, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for c in customers:
        name = (c["full_name"] or str(c["user_id"]))[:22]
        tag = f" @{c['username']}" if c["username"] else ""
        rows.append([_btn(f"👤 {name}{tag} · 📦 {c['orders_count']}", f"cus:v:{c['user_id']}")])
    rows += pager_row("cus:l", page, pages)
    rows.append([_btn("⬅️ В меню магазина", "adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def customer_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📦 Заказы клиента", f"cus:o:{user_id}"), _btn("✉️ Написать", f"cus:msg:{user_id}")],
            [_btn("⬅️ К списку клиентов", "cus:l:0")],
        ]
    )


def customer_orders_kb(orders: list, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for o in orders:
        emoji = status_label("ru", o["status"]).split()[0]
        rows.append(
            [
                _btn(
                    f"{emoji} №{o['id']} · {fmt_price(o['total'])} {cur_label('ru', o['currency'])}",
                    f"ord:v:{o['id']}",
                )
            ]
        )
    rows.append([_btn("⬅️ К клиенту", f"cus:v:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- рассылка и настройки магазина ----------

def broadcast_audience_kb(total: int, ru: int, uz: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"👥 Всем клиентам магазина · {total}", "bc:a:all")],
            [_btn(f"🇷🇺 Русский · {ru}", "bc:a:ru"), _btn(f"🇺🇿 O'zbekcha · {uz}", "bc:a:uz")],
            [_btn("⬅️ В меню магазина", "adm:menu")],
        ]
    )


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("✅ Запустить рассылку", "bc:go"), _btn("❌ Отмена", "adm:cancel")]]
    )


def settings_kb(shop: dict) -> InlineKeyboardMarkup:
    has_ch = bool(shop.get("channel_id") and shop["channel_id"] not in (0, -100))
    ch_text = "📢 Управление каналом" if has_ch else "📢 Привязать Telegram-канал"
    ch_cb = "chm:menu" if has_ch else "set:channel"
    active_text = (
        "🟢 Витрина включена (поставить на паузу)"
        if shop.get("is_active", 1)
        else "🔴 На паузе (включить витрину)"
    )
    rows = [
        [_btn(ch_text, ch_cb)],
        [_btn("✏️ Название магазина", "set:title"), _btn("📝 Описание", "set:descr")],
        [_btn("💳 Номер карты", "set:card"), _btn("👤 Получатель", "set:holder")],
        [_btn("☎️ Телефон магазина", "set:phone"), _btn("👨‍💼 Менеджеры", "set:staff")],
        [_btn("💎 Подписка PRO ($1/мес)", "sub:menu")],
        [_btn(active_text, "set:toggle_active")],
        [_btn("🗑 Удалить магазин", "set:del_ask")],
        [_btn("⬅️ В меню магазина", "adm:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_shop_kb(shop_id: int, is_super: bool = False) -> InlineKeyboardMarkup:
    yes_cb = f"sup:del_yes:{shop_id}" if is_super else f"set:del_yes:{shop_id}"
    no_cb = f"sup:shop:{shop_id}" if is_super else "adm:settings"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🗑 Да, удалить магазин навсегда", yes_cb)],
            [_btn("⬅️ Отмена", no_cb)],
        ]
    )


def staff_kb(staff: list, is_owner: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_owner:
        for s in staff:
            name = s.get("full_name") or str(s["user_id"])
            rows.append([_btn(f"❌ Удалить {name[:20]} ({s['user_id']})", f"staff:del:{s['user_id']}")])
        rows.append([_btn("➕ Добавить менеджера по ID", "staff:add")])
    rows.append([_btn("⬅️ К настройкам", "adm:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- супер-админка платформы ----------

def superadmin_menu_kb(pending_subs: int = 0) -> InlineKeyboardMarkup:
    sub_label = f"💳 Чеки за подписку · 🔎 {pending_subs}" if pending_subs else "💳 Подписки и тарифы ($1/мес)"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(sub_label, "sup:subs:0")],
            [_btn("⚙️ Настройки тарифа ($1/мес) и реквизитов", "sup:billing")],
            [_btn("🎁 Подарочные коды (ваучеры PRO)", "sup:vouchers")],
            [_btn("🏪 Все магазины на платформе", "sup:shops:0")],
            [_btn("📣 Рассылка всем владельцам магазинов", "sup:bc:owners")],
            [_btn("📣 Рассылка всем пользователям бота", "sup:bc:all")],
            [_btn("⬅️ В мою панель магазина", "adm:menu")],
        ]
    )


def superadmin_billing_kb(billing: dict) -> InlineKeyboardMarkup:
    lock_label = (
        "🔒 Истёкшие магазины: Пауза витрины"
        if billing.get("strict_lock")
        else f"🔓 Истёкшие магазины: Базовый тариф (до {billing.get('free_max_products', 5)} тов.)"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(f"💵 Цена USD ({billing['price_usd']}$)", "sup:bset:sub_price_usd"),
                _btn(f"🇺🇿 Цена сум ({fmt_price(billing['price_uzs'])})", "sup:bset:sub_price_uzs"),
            ],
            [
                _btn(f"🎁 Триал ({billing['trial_days']} дн.)", "sup:bset:sub_trial_days"),
                _btn(f"📦 Лимит Free ({billing['free_max_products']} тов.)", "sup:bset:free_max_products"),
            ],
            [
                _btn("💳 Карта платформы", "sup:bset:platform_card_number"),
                _btn("👤 Получатель карты", "sup:bset:platform_card_holder"),
            ],
            [_btn(lock_label, "sup:btoggle_lock")],
            [_btn("⬅️ Назад в Супер-Админку", "sup:menu")],
        ]
    )


def superadmin_vouchers_kb(vouchers: list) -> InlineKeyboardMarkup:
    rows = [[_btn("➕ Создать подарочный код PRO", "sup:vadd")]]
    for v in vouchers:
        rows.append(
            [
                _btn(
                    f"🎁 {v['code']} (+{v['days']} дн.) · исп: {v['used_count']}/{v['max_uses']}",
                    "adm:noop",
                ),
                _btn("🗑", f"sup:vdel:{v['code']}"),
            ]
        )
    rows.append([_btn("⬅️ Назад в Супер-Админку", "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def superadmin_subs_list_kb(subs: list, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for s in subs:
        st_icon = {"review": "🔎", "confirmed": "✅", "rejected": "❌"}.get(s["status"], "•")
        shop_t = (s.get("shop_title") or f"Shop #{s['shop_id']}")[:18]
        rows.append(
            [
                _btn(
                    f"{st_icon} #{s['id']} {shop_t} · {s['months']} мес. ({fmt_price(s['amount'])} {s['currency']})",
                    f"sup:sub_v:{s['id']}",
                )
            ]
        )
    rows += pager_row("sup:subs", page, pages)
    rows.append([_btn("⬅️ Назад в Супер-Админку", "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def superadmin_shops_kb(shops: list, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for s in shops:
        st = "🟢" if s["is_active"] else "🔴"
        pro = "💎" if s.get("is_pro") else "⚪️"
        rows.append(
            [
                _btn(
                    f"{st}{pro} #{s['id']} {s['title'][:20]} · 🛍{s['items_count']} 📦{s['orders_count']}",
                    f"sup:shop:{s['id']}",
                )
            ]
        )
    rows += pager_row("sup:shops", page, pages)
    rows.append([_btn("⬅️ Назад", "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def superadmin_shop_detail_kb(shop: dict) -> InlineKeyboardMarkup:
    sid = shop["id"]
    toggle_text = "🔴 Заблокировать / скрыть магазин" if shop["is_active"] else "🟢 Разблокировать магазин"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🛠 Войти в управление этим магазином", f"adm:sel_shop:{sid}")],
            [
                _btn("💎 +30 дней PRO", f"sup:sub_add:{sid}:30"),
                _btn("💎 +90 дней PRO", f"sup:sub_add:{sid}:90"),
                _btn("👑 +365 дней", f"sup:sub_add:{sid}:365"),
            ],
            [_btn("⚠️ Сбросить подписку PRO", f"sup:sub_rev:{sid}")],
            [_btn(toggle_text, f"sup:toggle:{sid}")],
            [_btn("🗑 Удалить магазин навсегда", f"sup:del_ask:{sid}")],
            [_btn("⬅️ К списку магазинов", "sup:shops:0")],
        ]
    )
