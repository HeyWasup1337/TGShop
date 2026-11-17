from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from config import TZ_OFFSET_HOURS
from texts import fmt_price

_BOT_USERNAME_CACHE: dict[int, str] = {}


async def get_bot_username(bot: Bot) -> str:
    """Возвращает username бота с кэшированием в памяти (без лишних сетевых запросов)."""
    if not bot:
        return ""
    bot_id = getattr(bot, "id", 0) or 0
    if bot_id not in _BOT_USERNAME_CACHE:
        try:
            me = await bot.get_me()
            _BOT_USERNAME_CACHE[bot_id] = me.username or ""
        except TelegramAPIError:
            return ""
    return _BOT_USERNAME_CACHE.get(bot_id, "")


def fmt_dt(value) -> str:
    """Дата из базы (UTC) -> местное время, например 13.10.2026 17:13."""
    if not value:
        return "—"
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)
    return (dt + timedelta(hours=TZ_OFFSET_HOURS)).strftime("%d.%m.%Y %H:%M")


def fmt_date(value) -> str:
    """Дата из базы (UTC) -> местная дата без времени, например 26.11.2026."""
    if not value:
        return "—"
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)[:10]
    return (dt + timedelta(hours=TZ_OFFSET_HOURS)).strftime("%d.%m.%Y")


def money(by_currency: dict) -> str:
    """{'UZS': 1200000, 'USD': 50, 'XTR': 100} -> '1 200 000 сум + 50 $'."""
    if not by_currency:
        return "0"
    parts = []
    if by_currency.get("UZS"):
        parts.append(f"{fmt_price(by_currency['UZS'])} сум")
    if by_currency.get("USD"):
        val = by_currency["USD"]
        if isinstance(val, float) and not val.is_integer():
            parts.append(f"{val:.2f} $")
        else:
            parts.append(f"{fmt_price(int(val))} $")
    if by_currency.get("XTR"):
        parts.append(f"{fmt_price(by_currency['XTR'])} ⭐️")
    return " + ".join(parts) if parts else "0"


def pages_count(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)


async def show(callback: CallbackQuery, text: str, kb=None):
    """Показывает экран в одном окне: правит текущее сообщение или заменяет фото-карточку."""
    msg = callback.message
    if isinstance(msg, Message):
        if msg.text:
            try:
                await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
                return
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    return
            except TelegramAPIError:
                pass
        elif msg.photo or msg.document or msg.video:
            try:
                await msg.delete()
            except TelegramAPIError:
                pass
    try:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=kb, disable_web_page_preview=True
        )
    except TelegramAPIError:
        pass


async def reply(callback: CallbackQuery, text: str, kb=None):
    """Отправляет новое сообщение тому, кто нажал кнопку."""
    try:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=kb, disable_web_page_preview=True
        )
    except TelegramAPIError:
        pass
