import html
import re

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

from db import get_cart_item_qty, get_shop
from keyboards import buy_kb, channel_kb
from texts import t
from utils import get_bot_username


def fmt_price(price: int) -> str:
    return f"{int(price or 0):,}".replace(",", " ")


def get_photos(p: dict) -> list:
    if p.get("photo_ids"):
        return [x for x in p["photo_ids"].split(",") if x]
    return [p["photo_id"]] if p.get("photo_id") else []


def get_channel_ids(p: dict) -> list:
    if p.get("channel_message_ids"):
        return [
            int(x)
            for x in str(p["channel_message_ids"]).split(",")
            if x.strip().lstrip("-").isdigit()
        ]
    return [int(p["channel_message_id"])] if p.get("channel_message_id") else []


def _clean_number(raw: str, is_thousand: bool = False) -> int | None:
    if not raw:
        return None
    s = raw.strip().replace(" ", "").replace("_", "")
    # Если формат вида 150.000 или 1,200,000 — убираем разделители тысяч
    if re.match(r"^\d{1,3}([.,]\d{3})+$", s):
        s = s.replace(".", "").replace(",", "")
    elif "." in s or "," in s:
        s = s.replace(",", ".")
        try:
            val = float(s)
            if is_thousand:
                val *= 1000
            return int(round(val)) if val > 0 else None
        except ValueError:
            return None
    if not s.isdigit():
        return None
    val = int(s)
    if is_thousand:
        val *= 1000
    return val if val > 0 else None


def parse_channel_post_caption(caption: str) -> dict | None:
    """Умный парсер постов канала: распознаёт название, описание и цену из подписи к фото/альбому.
    Поддерживает форматы:
    - '450 000 сум', '450.000 so'm', '450 ming so'm', '450 тыс сум', '450000 uzs'
    - '120 $', '$120', '120 usd', '120 у.е.' (включая эмодзи и текст в конце строки)
    """
    if not caption or not caption.strip():
        return None
    lines = [line.strip() for line in caption.strip().splitlines() if line.strip()]
    if not lines:
        return None

    price = None
    currency = "UZS"
    price_line_idx = None

    usd_pattern = re.compile(
        r"(?:💰|💵|🏷|цена|narxi|price|:|-)?\s*(?:\$\s*(\d[\d\s_.,]*)|(\d[\d\s_.,]*)\s*(?:\$|usd|у\.?\s*е\.?))(?:\b|[^0-9]|$)",
        re.IGNORECASE,
    )
    uzs_pattern = re.compile(
        r"(?:💰|💵|🏷|цена|narxi|price|:|-)?\s*(\d[\d\s_.,]*)\s*(ming|минг|тыс\.?)?\s*(?:сум|so'?m|som|uzs|сўм)\b",
        re.IGNORECASE,
    )

    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        # Пропускаем строки со ссылками на бота (если пост уже обработан)
        if "t.me/" in line and ("?start=p_" in line or "?start=s_" in line):
            continue

        m_uzs = uzs_pattern.search(line)
        if m_uzs:
            is_k = bool(m_uzs.group(2))
            parsed_val = _clean_number(m_uzs.group(1), is_thousand=is_k)
            if parsed_val:
                price = parsed_val
                currency = "UZS"
                price_line_idx = idx
                break

        m_usd = usd_pattern.search(line)
        if m_usd:
            raw_num = m_usd.group(1) or m_usd.group(2)
            parsed_val = _clean_number(raw_num, is_thousand=False)
            if parsed_val:
                price = parsed_val
                currency = "USD"
                price_line_idx = idx
                break

    if price is None:
        return None

    content_lines = [
        l for i, l in enumerate(lines)
        if i != price_line_idx and not ("t.me/" in l and "?start=" in l)
    ]
    if not content_lines:
        # Если весь пост состоял из одной строки вида "Кроссовки Nike - 450 000 сум"
        single_line = lines[price_line_idx]
        cleaned_title = uzs_pattern.sub("", usd_pattern.sub("", single_line)).strip(" -—:•|🔥⚡️💰💵🏷")
        if not cleaned_title:
            return None
        title = cleaned_title[:100]
        description = ""
    else:
        title = content_lines[0][:100]
        description = "\n".join(content_lines[1:])[:600] if len(content_lines) > 1 else ""

    return {
        "title": title,
        "description": description,
        "price": price,
        "currency": currency,
    }


def build_channel_text(p: dict, bot_username: str = None, shop_title: str = None) -> str:
    """Текст поста в канале (на двух языках). Для альбомов добавляется ссылка на бота."""
    cur = "$" if p["currency"] == "USD" else "сум / so'm"
    parts = [f"<b>{html.escape(p['title'])}</b>"]
    if p.get("description"):
        parts.append(html.escape(p["description"]))
    parts.append(f"💰 <b>{fmt_price(p['price'])} {cur}</b>")
    if p["in_stock"]:
        parts.append("✅ В наличии / Mavjud")
    else:
        parts.append("❌ Нет в наличии / Mavjud emas")
    if bot_username:
        order_link = f"https://t.me/{bot_username}?start=p_{p['id']}"
        links = [f'<a href="{order_link}">🛒 Заказать / Buyurtma berish</a>']
        if p.get("shop_id"):
            shop_link = f"https://t.me/{bot_username}?start=s_{p['shop_id']}"
            links.append(f'<a href="{shop_link}">🏪 Витрина / Do\'kon</a>')
        parts.append(" · ".join(links))
    return "\n\n".join(parts)


def build_pinned_showcase_text(shop: dict) -> str:
    """Текст закреплённого поста-витрины для шапки Telegram-канала."""
    lines = [f"🏪 <b>Добро пожаловать в «{html.escape(shop['title'])}»!</b>"]
    if shop.get("description"):
        lines.append(html.escape(shop["description"]))
    lines.append(
        "\n🛍 В нашем официальном боте вы можете:\n"
        "• Посмотреть весь каталог товаров в наличии\n"
        "• Использовать промокоды и оформить заказ в 1 клик\n"
        "• Отслеживать статус доставки\n\n"
        "🇺🇿 <b>Xush kelibsiz!</b> Barcha mavjud tovarlarni ko'rish va buyurtma berish uchun quyidagi tugmani bosing:"
    )
    if shop.get("shop_phone"):
        lines.append(f"\n☎️ <b>Телефон / Aloqa:</b> {html.escape(shop['shop_phone'])}")
    return "\n".join(lines)


def build_user_card(lang: str, p: dict, shop_title: str = None) -> str:
    """Карточка товара для клиента на его языке."""
    cur = t(lang, "cur_usd" if p["currency"] == "USD" else "cur_uzs")
    parts = []
    if shop_title:
        parts.append(f"🏪 <i>{html.escape(shop_title)}</i>")
    parts.append(f"<b>{html.escape(p['title'])}</b>")
    if p.get("description"):
        parts.append(html.escape(p["description"]))
    parts.append(t(lang, "price").format(price=fmt_price(p["price"]), currency=cur))
    parts.append(t(lang, "in_stock") if p["in_stock"] else t(lang, "sold_out"))
    return "\n\n".join(parts)


def build_media(photos: list, caption: str) -> list:
    media = []
    for i, file_id in enumerate(photos):
        if i == 0:
            media.append(InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML"))
        else:
            media.append(InputMediaPhoto(media=file_id))
    return media


async def send_card(message: Message, lang: str, p: dict, shop_title: str = None, user_id: int = None):
    """Отправляет клиенту карточку товара (одно фото или альбом)."""
    if not shop_title and p.get("shop_id"):
        shop = await get_shop(p["shop_id"])
        if shop:
            shop_title = shop["title"]
    uid = user_id or (message.chat.id if message.chat else 0)
    in_cart_qty = await get_cart_item_qty(uid, p["id"]) if uid and p.get("in_stock") else 0
    photos = get_photos(p)
    caption = build_user_card(lang, p, shop_title=shop_title)
    kb = buy_kb(lang, p["id"], p.get("shop_id"), in_cart_qty=in_cart_qty) if p["in_stock"] else None
    if not photos:
        await message.answer(caption, reply_markup=kb)
    elif len(photos) == 1:
        await message.answer_photo(photos[0], caption=caption, reply_markup=kb)
    else:
        await message.answer_media_group(media=build_media(photos, caption))
        if kb:
            await message.answer(t(lang, "order_prompt"), reply_markup=kb)


async def publish_product_to_channel(bot: Bot, p: dict, channel_id: int) -> list:
    """Публикует товар в указанный Telegram-канал и возвращает список message_id."""
    photos = get_photos(p)
    bot_username = await get_bot_username(bot)
    if len(photos) == 1:
        sent = await bot.send_photo(
            channel_id,
            photos[0],
            caption=build_channel_text(p),
            reply_markup=channel_kb(bot_username, p["id"], p.get("shop_id")),
        )
        return [sent.message_id]
    else:
        sent = await bot.send_media_group(
            channel_id,
            media=build_media(photos, build_channel_text(p, bot_username)),
        )
        return [m.message_id for m in sent]


async def refresh_channel_post(bot: Bot, p: dict, channel_id: int = None):
    """Обновляет пост в канале магазина после смены цены, описания или наличия."""
    ids = get_channel_ids(p)
    if not ids:
        return
    if not channel_id and p.get("shop_id"):
        shop = await get_shop(p["shop_id"])
        channel_id = shop["channel_id"] if shop else 0
    if not channel_id or channel_id in (0, -100):
        return

    bot_username = await get_bot_username(bot)
    if len(ids) == 1:
        text = build_channel_text(p)
        markup = channel_kb(bot_username, p["id"], p.get("shop_id"))
    else:
        text = build_channel_text(p, bot_username)
        markup = None
    try:
        await bot.edit_message_caption(
            chat_id=channel_id,
            message_id=ids[0],
            caption=text,
            reply_markup=markup,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise