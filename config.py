import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0") or "0")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip() and x.strip().lstrip("-").isdigit()
}

CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()
CARD_HOLDER = os.getenv("CARD_HOLDER", "").strip()
SHOP_PHONE = os.getenv("SHOP_PHONE", "+998 99 979 34 06").strip()

# Токены авто-оплаты Click и Payme из @BotFather -> Payments (можно также задать прямо в Супер-Админке бота)
CLICK_TOKEN = os.getenv("CLICK_TOKEN", "").strip()
PAYME_TOKEN = os.getenv("PAYME_TOKEN", "").strip()

# Сдвиг времени относительно UTC (Ташкент = +5). Нужен для дат в админке, подписок и статистики.
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "5") or "5")

# Настройки системы подписки PRO для владельцев каналов (можно также менять прямо из Супер-Админки бота)
SUB_PRICE_USD = float(os.getenv("SUB_PRICE_USD", "1") or "1")
SUB_PRICE_UZS = int(os.getenv("SUB_PRICE_UZS", "12800") or "12800")
SUB_PRICE_STARS = int(os.getenv("SUB_PRICE_STARS", "50") or "50")
SUB_TRIAL_DAYS = int(os.getenv("SUB_TRIAL_DAYS", "0") or "0")
FREE_MAX_PRODUCTS = int(os.getenv("FREE_MAX_PRODUCTS", "5") or "5")
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "15") or "15")
