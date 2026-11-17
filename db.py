from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from config import (
    ADMIN_IDS,
    CLICK_TOKEN,
    PAYME_TOKEN,
    BASE_DIR,
    CARD_HOLDER,
    CARD_NUMBER,
    CHANNEL_ID,
    FREE_MAX_PRODUCTS,
    REFERRAL_BONUS_DAYS,
    SHOP_PHONE,
    SUB_PRICE_STARS,
    SUB_PRICE_USD,
    SUB_PRICE_UZS,
    SUB_TRIAL_DAYS,
    TZ_OFFSET_HOURS,
)

DB_PATH = str(BASE_DIR / "shop.db")

# Статусы заказа: pending_payment -> review -> confirmed -> shipped (или rejected / cancelled)
ORDER_STATUSES = ("review", "pending_payment", "confirmed", "shipped", "rejected", "cancelled")
PAID_STATUSES = ("confirmed", "shipped")

SHOP_UPDATABLE_FIELDS = {
    "title",
    "description",
    "channel_id",
    "channel_title",
    "channel_username",
    "card_number",
    "card_holder",
    "shop_phone",
    "is_active",
    "auto_parse_posts",
    "pinned_message_id",
    "sub_until",
    "trial_used",
    "sub_reminded_3d",
    "sub_reminded_1d",
    "sub_expired_notified",
    "referred_by",
    "click_token",
    "payme_token",
}

# Пул постоянных соединений по пути к файлу БД
_CONNECTIONS: dict[str, aiosqlite.Connection] = {}


def _utc_now() -> datetime:
    return datetime.utcnow()


def _fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_utc(val) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


async def get_db() -> aiosqlite.Connection:
    conn = _CONNECTIONS.get(DB_PATH)
    if conn is None or not getattr(conn, "_running", False):
        conn_obj = aiosqlite.connect(DB_PATH)
        if hasattr(conn_obj, "_thread") and conn_obj._thread is not None:
            try:
                conn_obj._thread.daemon = True
            except RuntimeError:
                pass
        conn = await conn_obj
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA cache_size=-16000")
        await conn.commit()
        _CONNECTIONS[DB_PATH] = conn
    return conn


async def close_db():
    for path, conn in list(_CONNECTIONS.items()):
        _CONNECTIONS.pop(path, None)
        try:
            await conn.close()
        except Exception:
            pass


async def _add_column_if_missing(db, table: str, column: str, ddl: str):
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def init_db():
    db = await get_db()

    # Таблица магазинов и привязанных каналов
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            channel_id INTEGER DEFAULT 0,
            channel_title TEXT DEFAULT '',
            channel_username TEXT DEFAULT '',
            card_number TEXT DEFAULT '',
            card_holder TEXT DEFAULT '',
            shop_phone TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            auto_parse_posts INTEGER DEFAULT 1,
            pinned_message_id INTEGER DEFAULT 0,
            sub_until TEXT DEFAULT NULL,
            trial_used INTEGER DEFAULT 0,
            sub_reminded_3d INTEGER DEFAULT 0,
            sub_reminded_1d INTEGER DEFAULT 0,
            sub_expired_notified INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await _add_column_if_missing(db, "shops", "auto_parse_posts", "INTEGER DEFAULT 1")
    await _add_column_if_missing(db, "shops", "pinned_message_id", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "shops", "sub_until", "TEXT DEFAULT NULL")
    await _add_column_if_missing(db, "shops", "trial_used", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "shops", "sub_reminded_3d", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "shops", "sub_reminded_1d", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "shops", "sub_expired_notified", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "shops", "referred_by", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "shops", "click_token", "TEXT DEFAULT ''")
    await _add_column_if_missing(db, "shops", "payme_token", "TEXT DEFAULT ''")
    await _add_column_if_missing(db, "orders", "payment_method", "TEXT DEFAULT 'card'")

    # Таблица платежей и заявок по подписке ($1/месяц)
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            months INTEGER DEFAULT 1,
            days INTEGER DEFAULT 30,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            method TEXT DEFAULT 'card',
            status TEXT DEFAULT 'review',
            receipt_photo_id TEXT,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        )
        """
    )

    # Подарочные коды (ваучеры) на PRO-подписку от владельца платформы
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS sub_vouchers (
            code TEXT PRIMARY KEY,
            days INTEGER NOT NULL DEFAULT 30,
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS sub_voucher_uses (
            code TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            used_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (code, shop_id)
        )
        """
    )

    # Промокоды магазинов для покупателей (скидки в %)
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS promocodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            discount_pct INTEGER NOT NULL DEFAULT 10,
            max_uses INTEGER NOT NULL DEFAULT 0,
            used_count INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(shop_id, code)
        )
        """
    )

    # Дополнительные менеджеры магазина
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_admins (
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (shop_id, user_id)
        )
        """
    )

    # База клиентов конкретного магазина
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_customers (
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (shop_id, user_id)
        )
        """
    )

    # Таблица пользователей
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            lang TEXT,
            full_name TEXT,
            username TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await _add_column_if_missing(db, "users", "blocked", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "users", "active_shop_id", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "users", "managed_shop_id", "INTEGER DEFAULT 0")
    await _add_column_if_missing(db, "users", "saved_name", "TEXT DEFAULT NULL")
    await _add_column_if_missing(db, "users", "saved_phone", "TEXT DEFAULT NULL")
    await _add_column_if_missing(db, "users", "saved_address", "TEXT DEFAULT NULL")
    await _add_column_if_missing(db, "users", "referrer_id", "INTEGER DEFAULT 0")

    # Таблица товаров
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER DEFAULT 1,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            price INTEGER NOT NULL,
            currency TEXT DEFAULT 'UZS',
            in_stock INTEGER DEFAULT 1,
            hidden INTEGER DEFAULT 0,
            photo_id TEXT,
            channel_message_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await _add_column_if_missing(db, "items", "shop_id", "INTEGER DEFAULT 1")
    await _add_column_if_missing(db, "items", "photo_ids", "TEXT")
    await _add_column_if_missing(db, "items", "channel_message_ids", "TEXT")

    # Корзина
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS cart (
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            qty INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, item_id)
        )
        """
    )

    # Заказы
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER DEFAULT 1,
            user_id INTEGER NOT NULL,
            name TEXT,
            phone TEXT,
            address TEXT,
            total INTEGER,
            currency TEXT,
            status TEXT DEFAULT 'pending_payment',
            payment_photo_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await _add_column_if_missing(db, "orders", "shop_id", "INTEGER DEFAULT 1")
    await _add_column_if_missing(db, "orders", "tracking", "TEXT")
    await _add_column_if_missing(db, "orders", "reason", "TEXT")
    await _add_column_if_missing(db, "orders", "updated_at", "TEXT")
    await _add_column_if_missing(db, "orders", "promo_code", "TEXT DEFAULT NULL")
    await _add_column_if_missing(db, "orders", "discount_amount", "INTEGER DEFAULT 0")

    # Позиции заказа
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            item_id INTEGER,
            title TEXT,
            price INTEGER,
            currency TEXT,
            qty INTEGER
        )
        """
    )

    # Глобальные настройки платформы и тарифов
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    # Индексы для мгновенной выборки при большом числе магазинов, товаров и заказов
    await db.execute("CREATE INDEX IF NOT EXISTS idx_shops_owner ON shops(owner_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_shops_channel ON shops(channel_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_shops_active ON shops(is_active)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_shops_sub ON shops(sub_until)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_items_shop_stock ON items(shop_id, hidden, in_stock)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_shop_status ON orders(shop_id, status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cart_user ON cart(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_shop_customers_shop ON shop_customers(shop_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_shop_admins_user ON shop_admins(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status, shop_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_promocodes_shop ON promocodes(shop_id, code)")

    # Автоматическая миграция старых данных в Магазин №1
    async with db.execute("SELECT COUNT(*) AS n FROM shops") as cur:
        shops_count = (await cur.fetchone())["n"]
    if shops_count == 0:
        async with db.execute("SELECT COUNT(*) AS n FROM items") as cur_items:
            items_count = (await cur_items.fetchone())["n"]
        async with db.execute("SELECT COUNT(*) AS n FROM orders") as cur_orders:
            orders_count = (await cur_orders.fetchone())["n"]

        if items_count > 0 or orders_count > 0 or ADMIN_IDS or (CHANNEL_ID and CHANNEL_ID != -100):
            async with db.execute("SELECT key, value FROM settings") as cur_set:
                legacy_settings = {r["key"]: r["value"] for r in await cur_set.fetchall()}
            card_num = legacy_settings.get("card_number") or CARD_NUMBER or ""
            card_hld = legacy_settings.get("card_holder") or CARD_HOLDER or ""
            phone = legacy_settings.get("shop_phone") or SHOP_PHONE or ""
            owner = min(ADMIN_IDS) if ADMIN_IDS else 0
            ch_id = CHANNEL_ID if CHANNEL_ID not in (0, -100) else 0
            initial_sub = _fmt_utc(_utc_now() + timedelta(days=3650 if owner in ADMIN_IDS else SUB_TRIAL_DAYS))

            async with db.execute(
                """
                INSERT INTO shops (
                    owner_id, title, description, channel_id,
                    card_number, card_holder, shop_phone, is_active, auto_parse_posts,
                    sub_until, trial_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, 1)
                """,
                (
                    owner,
                    "Основной магазин",
                    "Добро пожаловать в наш магазин!",
                    ch_id,
                    card_num,
                    card_hld,
                    phone,
                    initial_sub,
                ),
            ) as cursor:
                first_shop_id = cursor.lastrowid

            for adm_id in ADMIN_IDS:
                if adm_id != owner:
                    await db.execute(
                        "INSERT OR IGNORE INTO shop_admins (shop_id, user_id) VALUES (?, ?)",
                        (first_shop_id, adm_id),
                    )
            await db.execute(
                "UPDATE items SET shop_id = ? WHERE shop_id IS NULL OR shop_id = 0",
                (first_shop_id,),
            )
            await db.execute(
                "UPDATE orders SET shop_id = ? WHERE shop_id IS NULL OR shop_id = 0",
                (first_shop_id,),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO shop_customers (shop_id, user_id)
                SELECT ?, user_id FROM users
                """,
                (first_shop_id,),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO shop_customers (shop_id, user_id)
                SELECT DISTINCT ?, user_id FROM orders
                """,
                (first_shop_id,),
            )
            await db.execute(
                "UPDATE users SET active_shop_id = ? WHERE active_shop_id IS NULL OR active_shop_id = 0",
                (first_shop_id,),
            )

    # Для магазинов владельца платформы (ADMIN_IDS) выдаём бессрочный VIP PRO
    async with db.execute("SELECT id, owner_id FROM shops WHERE sub_until IS NULL AND trial_used = 0") as cur_legacy:
        legacy_shops = await cur_legacy.fetchall()
    for ls in legacy_shops:
        if ls["owner_id"] in ADMIN_IDS:
            sub_dt = _fmt_utc(_utc_now() + timedelta(days=3650))
            await db.execute(
                "UPDATE shops SET sub_until = ?, trial_used = 2 WHERE id = ?",
                (sub_dt, ls["id"]),
            )

    await db.commit()


async def _fetchone(query, params=()):
    db = await get_db()
    async with db.execute(query, params) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None


async def _fetchall(query, params=()):
    db = await get_db()
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def _execute(query, params=()) -> tuple[int, int]:
    """Выполняет запрос и возвращает (lastrowid, rowcount), гарантированно закрывая курсор."""
    db = await get_db()
    async with db.execute(query, params) as cursor:
        lastrowid = cursor.lastrowid
        rowcount = cursor.rowcount
    await db.commit()
    return lastrowid, rowcount


# ====================== ГЛОБАЛЬНЫЕ НАСТРОЙКИ ТАРИФОВ И ПЛАТФОРМЫ ======================

async def get_platform_setting(key: str, default: str = "") -> str:
    row = await _fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default


async def set_platform_setting(key: str, value: str):
    await _execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


async def get_platform_billing_config() -> dict:
    """Возвращает актуальные настройки подписки ($1/мес, курс сум, Stars, пробный период, реквизиты платформы)."""
    rows = await _fetchall("SELECT key, value FROM settings")
    cfg = {r["key"]: r["value"] for r in rows}

    def _int(k, d):
        try:
            return int(cfg.get(k, d))
        except (TypeError, ValueError):
            return d

    def _float(k, d):
        try:
            return float(cfg.get(k, d))
        except (TypeError, ValueError):
            return d

    price_usd = _float("sub_price_usd", SUB_PRICE_USD)
    price_uzs = _int("sub_price_uzs", SUB_PRICE_UZS)
    price_stars = _int("sub_price_stars", SUB_PRICE_STARS)
    trial_days = _int("sub_trial_days", SUB_TRIAL_DAYS)
    free_max_products = _int("free_max_products", FREE_MAX_PRODUCTS)
    strict_lock = bool(_int("sub_strict_lock", 0))

    return {
        "price_usd": price_usd,
        "price_uzs": price_uzs,
        "price_stars": price_stars,
        "trial_days": trial_days,
        "free_max_products": free_max_products,
        "strict_lock": strict_lock,
        "card_number": cfg.get("platform_card_number") or CARD_NUMBER,
        "card_holder": cfg.get("platform_card_holder") or CARD_HOLDER,
        "support_phone": cfg.get("platform_support_phone") or SHOP_PHONE,
        "click_token": (cfg.get("platform_click_token") or CLICK_TOKEN or "").strip(),
        "payme_token": (cfg.get("platform_payme_token") or PAYME_TOKEN or "").strip(),
    }


def get_sub_plans(billing: dict) -> dict:
    """Тарифные планы подписки PRO на основе базовой цены ($1/мес)."""
    u1 = billing["price_usd"]
    uz1 = billing["price_uzs"]
    st1 = billing["price_stars"]
    return {
        1: {
            "months": 1,
            "days": 30,
            "title": "1 месяц PRO",
            "badge": "🔥 Хит",
            "usd": round(u1, 2),
            "uzs": uz1,
            "stars": st1,
        },
        3: {
            "months": 3,
            "days": 90,
            "title": "3 месяца PRO (-17%)",
            "badge": "⚡️ Выгодно",
            "usd": round(u1 * 2.5, 2),
            "uzs": int(round(uz1 * 2.5, -2)),
            "stars": max(1, int(round(st1 * 2.5))),
        },
        12: {
            "months": 12,
            "days": 365,
            "title": "12 месяцев PRO (-25%)",
            "badge": "💎 3 мес. в подарок",
            "usd": round(u1 * 9, 2),
            "uzs": int(round(uz1 * 9, -2)),
            "stars": max(1, int(round(st1 * 9))),
        },
    }


# ====================== ПОДПИСКА МАГАЗИНА (PRO / TRIAL / FREE) ======================

def compute_shop_sub_state(shop: dict, free_limit: int = FREE_MAX_PRODUCTS) -> dict:
    """Вычисляет статус подписки магазина без лишних запросов к БД."""
    if not shop:
        return {
            "is_pro": False,
            "status": "expired",
            "label": "Базовый (Free)",
            "sub_until": None,
            "days_left": 0,
            "hours_left": 0,
            "free_limit": free_limit,
        }
    owner_id = shop.get("owner_id") or 0
    if owner_id in ADMIN_IDS:
        return {
            "is_pro": True,
            "status": "vip",
            "label": "👑 Владелец платформы (VIP PRO)",
            "sub_until": shop.get("sub_until"),
            "days_left": 9999,
            "hours_left": 99999,
            "free_limit": 999999,
        }

    now = _utc_now()
    until_dt = _parse_utc(shop.get("sub_until"))
    if until_dt and until_dt > now:
        delta = until_dt - now
        days_left = delta.days
        hours_left = int(delta.total_seconds() // 3600)
        is_trial = int(shop.get("trial_used") or 0) == 1
        status = "trial" if is_trial else "pro"
        return {
            "is_pro": True,
            "status": status,
            "label": "🎁 Пробный PRO" if is_trial else "💎 Подписка PRO",
            "sub_until": _fmt_utc(until_dt),
            "days_left": days_left,
            "hours_left": hours_left,
            "free_limit": 999999,
        }

    return {
        "is_pro": False,
        "status": "expired",
        "label": "⚠️ Базовый (подписка не активна)",
        "sub_until": shop.get("sub_until"),
        "days_left": 0,
        "hours_left": 0,
        "free_limit": free_limit,
    }


async def get_shop_subscription_info(shop_id: int) -> dict:
    shop = await get_shop(shop_id)
    billing = await get_platform_billing_config()
    info = compute_shop_sub_state(shop, free_limit=billing["free_max_products"])
    info["billing"] = billing
    return info


async def is_shop_pro(shop_id: int) -> bool:
    shop = await get_shop(shop_id)
    return compute_shop_sub_state(shop)["is_pro"]


async def extend_shop_subscription(shop_id: int, days: int, is_paid: bool = True) -> str:
    """Продлевает подписку PRO магазина на указанное количество дней."""
    shop = await get_shop(shop_id)
    now = _utc_now()
    cur_until = _parse_utc(shop.get("sub_until")) if shop else None
    base_dt = cur_until if (cur_until and cur_until > now) else now
    new_until = _fmt_utc(base_dt + timedelta(days=days))
    trial_val = 2 if is_paid else 1

    await _execute(
        """
        UPDATE shops
        SET sub_until = ?,
            trial_used = ?,
            sub_reminded_3d = 0,
            sub_reminded_1d = 0,
            sub_expired_notified = 0,
            is_active = 1
        WHERE id = ?
        """,
        (new_until, trial_val, shop_id),
    )
    return new_until


async def revoke_shop_subscription(shop_id: int):
    past = _fmt_utc(_utc_now() - timedelta(seconds=10))
    await _execute(
        """
        UPDATE shops
        SET sub_until = ?,
            trial_used = 2,
            sub_expired_notified = 1
        WHERE id = ?
        """,
        (past, shop_id),
    )


async def create_sub_payment(
    shop_id: int,
    user_id: int,
    months: int,
    days: int,
    amount: int,
    currency: str,
    method: str = "card",
    status: str = "review",
    receipt_photo_id: str = None,
    note: str = "",
) -> int:
    lastrowid, _ = await _execute(
        """
        INSERT INTO subscriptions (
            shop_id, user_id, months, days, amount, currency, method, status, receipt_photo_id, note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (shop_id, user_id, months, days, amount, currency, method, status, receipt_photo_id, note),
    )
    return lastrowid


async def get_sub_payment(sub_id: int):
    return await _fetchone(
        """
        SELECT sub.*, s.title AS shop_title, s.channel_username, u.full_name, u.username
        FROM subscriptions sub
        LEFT JOIN shops s ON s.id = sub.shop_id
        LEFT JOIN users u ON u.user_id = sub.user_id
        WHERE sub.id = ?
        """,
        (sub_id,),
    )


async def confirm_sub_payment(sub_id: int) -> tuple[bool, str | None]:
    sub = await get_sub_payment(sub_id)
    if not sub or sub["status"] == "confirmed":
        return False, None
    _, rc = await _execute(
        """
        UPDATE subscriptions
        SET status = 'confirmed', reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status != 'confirmed'
        """,
        (sub_id,),
    )
    if rc <= 0:
        return False, None
    days = sub.get("days") or (sub.get("months", 1) * 30)
    new_until = await extend_shop_subscription(sub["shop_id"], days=days, is_paid=True)
    return True, new_until


async def reject_sub_payment(sub_id: int, note: str = "") -> bool:
    _, rc = await _execute(
        """
        UPDATE subscriptions
        SET status = 'rejected', note = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'review'
        """,
        (note, sub_id),
    )
    return rc > 0


async def count_pending_sub_payments() -> int:
    row = await _fetchone("SELECT COUNT(*) AS n FROM subscriptions WHERE status = 'review'")
    return row["n"] if row else 0


async def list_sub_payments(status: str = None, limit: int = 10, offset: int = 0) -> list:
    if status:
        return await _fetchall(
            """
            SELECT sub.*, s.title AS shop_title, u.full_name, u.username
            FROM subscriptions sub
            LEFT JOIN shops s ON s.id = sub.shop_id
            LEFT JOIN users u ON u.user_id = sub.user_id
            WHERE sub.status = ?
            ORDER BY sub.id DESC LIMIT ? OFFSET ?
            """,
            (status, limit, offset),
        )
    return await _fetchall(
        """
        SELECT sub.*, s.title AS shop_title, u.full_name, u.username
        FROM subscriptions sub
        LEFT JOIN shops s ON s.id = sub.shop_id
        LEFT JOIN users u ON u.user_id = sub.user_id
        ORDER BY sub.id DESC LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )


async def create_sub_voucher(code: str, days: int = 30, max_uses: int = 1) -> bool:
    clean = code.strip().upper()
    if not clean:
        return False
    _, rc = await _execute(
        """
        INSERT OR REPLACE INTO sub_vouchers (code, days, max_uses, used_count)
        VALUES (?, ?, ?, COALESCE((SELECT used_count FROM sub_vouchers WHERE code = ?), 0))
        """,
        (clean, days, max_uses, clean),
    )
    return rc > 0


async def list_sub_vouchers() -> list:
    return await _fetchall("SELECT * FROM sub_vouchers ORDER BY created_at DESC LIMIT 20")


async def delete_sub_voucher(code: str):
    await _execute("DELETE FROM sub_vouchers WHERE code = ?", (code.strip().upper(),))


async def redeem_sub_voucher(shop_id: int, user_id: int, code: str) -> tuple[bool, str, int]:
    """Активирует подарочный код на PRO-подписку для магазина."""
    clean = code.strip().upper()
    v = await _fetchone("SELECT * FROM sub_vouchers WHERE code = ?", (clean,))
    if not v:
        return False, "❌ Промокод не найден.", 0
    if v["used_count"] >= v["max_uses"]:
        return False, "❌ Лимит активаций этого промокода уже исчерпан.", 0
    already = await _fetchone(
        "SELECT 1 FROM sub_voucher_uses WHERE code = ? AND shop_id = ?",
        (clean, shop_id),
    )
    if already:
        return False, "⚠️ Этот промокод уже был активирован для вашего магазина.", 0

    db = await get_db()
    await db.execute(
        "INSERT INTO sub_voucher_uses (code, shop_id, user_id) VALUES (?, ?, ?)",
        (clean, shop_id, user_id),
    )
    await db.execute(
        "UPDATE sub_vouchers SET used_count = used_count + 1 WHERE code = ?",
        (clean,),
    )
    await db.commit()

    new_until = await extend_shop_subscription(shop_id, days=v["days"], is_paid=True)
    await create_sub_payment(
        shop_id=shop_id,
        user_id=user_id,
        months=max(1, v["days"] // 30),
        days=v["days"],
        amount=0,
        currency="USD",
        method="voucher",
        status="confirmed",
        note=f"Voucher {clean}",
    )
    return True, new_until, v["days"]


async def get_shops_for_sub_notifications() -> dict:
    """Возвращает списки магазинов, которым пора отправить напоминание (3 дня, 1 день) или уведомление об окончании."""
    now = _utc_now()
    in_3d = _fmt_utc(now + timedelta(days=3))
    in_1d = _fmt_utc(now + timedelta(days=1))
    now_str = _fmt_utc(now)

    remind_3d = await _fetchall(
        """
        SELECT * FROM shops
        WHERE sub_until IS NOT NULL
          AND sub_until > ?
          AND sub_until <= ?
          AND sub_reminded_3d = 0
        """,
        (in_1d, in_3d),
    )
    remind_1d = await _fetchall(
        """
        SELECT * FROM shops
        WHERE sub_until IS NOT NULL
          AND sub_until > ?
          AND sub_until <= ?
          AND sub_reminded_1d = 0
        """,
        (now_str, in_1d),
    )
    expired = await _fetchall(
        """
        SELECT * FROM shops
        WHERE sub_until IS NOT NULL
          AND sub_until <= ?
          AND sub_expired_notified = 0
        """,
        (now_str,),
    )
    return {
        "remind_3d": [s for s in remind_3d if s["owner_id"] not in ADMIN_IDS],
        "remind_1d": [s for s in remind_1d if s["owner_id"] not in ADMIN_IDS],
        "expired": [s for s in expired if s["owner_id"] not in ADMIN_IDS],
    }


async def mark_sub_notification(shop_id: int, kind: str, pause_shop: bool = False):
    if kind == "3d":
        await _execute("UPDATE shops SET sub_reminded_3d = 1 WHERE id = ?", (shop_id,))
    elif kind == "1d":
        await _execute("UPDATE shops SET sub_reminded_1d = 1 WHERE id = ?", (shop_id,))
    elif kind == "expired":
        if pause_shop:
            await _execute(
                "UPDATE shops SET sub_expired_notified = 1, is_active = 0 WHERE id = ?",
                (shop_id,),
            )
        else:
            await _execute("UPDATE shops SET sub_expired_notified = 1 WHERE id = ?", (shop_id,))


# ====================== ПРОМОКОДЫ МАГАЗИНОВ ДЛЯ ПОКУПАТЕЛЕЙ ======================

async def create_promocode(shop_id: int, code: str, discount_pct: int, max_uses: int = 0) -> bool:
    clean = code.strip().upper()
    if not clean or not (1 <= discount_pct <= 90):
        return False
    await _execute(
        """
        INSERT INTO promocodes (shop_id, code, discount_pct, max_uses, used_count, is_active)
        VALUES (?, ?, ?, ?, 0, 1)
        ON CONFLICT(shop_id, code) DO UPDATE SET
            discount_pct = excluded.discount_pct,
            max_uses = excluded.max_uses,
            is_active = 1
        """,
        (shop_id, clean, discount_pct, max_uses),
    )
    return True


async def list_promocodes(shop_id: int) -> list:
    return await _fetchall(
        "SELECT * FROM promocodes WHERE shop_id = ? AND is_active = 1 ORDER BY id DESC",
        (shop_id,),
    )


async def delete_promocode(shop_id: int, promo_id: int):
    await _execute("DELETE FROM promocodes WHERE id = ? AND shop_id = ?", (promo_id, shop_id))


async def validate_promocode(shop_id: int, code: str) -> dict | None:
    if not code:
        return None
    clean = code.strip().upper()
    row = await _fetchone(
        "SELECT * FROM promocodes WHERE shop_id = ? AND code = ? AND is_active = 1",
        (shop_id, clean),
    )
    if not row:
        return None
    if row["max_uses"] > 0 and row["used_count"] >= row["max_uses"]:
        return None
    return row


# ====================== ПОЛЬЗОВАТЕЛИ ======================

async def get_lang(user_id: int):
    row = await _fetchone("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    return row["lang"] if row else None


async def ensure_user(
    user_id: int, full_name: str = "", username: str = None, default_lang: str = "ru", referrer_id: int = 0
):
    await _execute(
        """
        INSERT INTO users (user_id, lang, full_name, username, blocked, referrer_id)
        VALUES (?, ?, ?, ?, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            full_name = COALESCE(NULLIF(excluded.full_name, ''), users.full_name),
            username = COALESCE(excluded.username, users.username),
            referrer_id = CASE WHEN users.referrer_id IS NULL OR users.referrer_id = 0 THEN excluded.referrer_id ELSE users.referrer_id END,
            blocked = 0
        """,
        (user_id, default_lang, full_name or "", username, referrer_id if referrer_id != user_id else 0),
    )


async def set_lang(user_id: int, lang: str, full_name: str, username):
    await _execute(
        """
        INSERT INTO users (user_id, lang, full_name, username, blocked)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            lang = excluded.lang,
            full_name = excluded.full_name,
            username = excluded.username,
            blocked = 0
        """,
        (user_id, lang, full_name, username),
    )


async def mark_blocked(user_id: int, blocked: bool = True):
    await _execute("UPDATE users SET blocked = ? WHERE user_id = ?", (int(blocked), user_id))


async def get_user_saved_checkout(user_id: int) -> dict | None:
    u = await _fetchone(
        "SELECT saved_name, saved_phone, saved_address FROM users WHERE user_id = ?",
        (user_id,),
    )
    if u and u.get("saved_name") and u.get("saved_phone") and u.get("saved_address"):
        return {
            "name": u["saved_name"],
            "phone": u["saved_phone"],
            "address": u["saved_address"],
        }
    last_order = await _fetchone(
        "SELECT name, phone, address FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    if last_order and last_order.get("name") and last_order.get("phone") and last_order.get("address"):
        return {
            "name": last_order["name"],
            "phone": last_order["phone"],
            "address": last_order["address"],
        }
    return None


async def save_user_checkout(user_id: int, name: str, phone: str, address: str):
    await _execute(
        "UPDATE users SET saved_name = ?, saved_phone = ?, saved_address = ? WHERE user_id = ?",
        (name, phone, address, user_id),
    )


# ====================== МАГАЗИНЫ И КАНАЛЫ ======================

async def create_shop(
    owner_id: int,
    title: str,
    description: str = "",
    channel_id: int = 0,
    channel_title: str = "",
    channel_username: str = "",
    card_number: str = "",
    card_holder: str = "",
    shop_phone: str = "",
) -> int:
    billing = await get_platform_billing_config()
    trial_days = billing["trial_days"]

    # Проверяем, был ли у этого владельца уже магазин с триалом
    prev = await _fetchone("SELECT id FROM shops WHERE owner_id = ? LIMIT 1", (owner_id,))
    is_first_shop = prev is None

    if owner_id in ADMIN_IDS:
        sub_until = _fmt_utc(_utc_now() + timedelta(days=3650))
        trial_used = 2
    elif is_first_shop and trial_days > 0:
        sub_until = _fmt_utc(_utc_now() + timedelta(days=trial_days))
        trial_used = 1
    else:
        sub_until = None
        trial_used = 0

    u_row = await _fetchone("SELECT referrer_id FROM users WHERE user_id = ?", (owner_id,))
    referrer_id = (u_row["referrer_id"] if u_row else 0) or 0

    db = await get_db()
    if channel_id and channel_id not in (0, -100):
        await db.execute(
            "UPDATE shops SET channel_id = 0, channel_title = '', channel_username = '' WHERE channel_id = ?",
            (channel_id,),
        )
    async with db.execute(
        """
        INSERT INTO shops (
            owner_id, title, description, channel_id, channel_title,
            channel_username, card_number, card_holder, shop_phone,
            is_active, auto_parse_posts, sub_until, trial_used, referred_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
        """,
        (
            owner_id,
            title.strip() or "Мой магазин",
            description.strip(),
            channel_id,
            channel_title.strip(),
            (channel_username or "").lstrip("@").strip(),
            card_number.strip(),
            card_holder.strip(),
            shop_phone.strip(),
            sub_until,
            trial_used,
            referrer_id if is_first_shop else 0,
        ),
    ) as cursor:
        shop_id = cursor.lastrowid

    await db.execute(
        """
        UPDATE users
        SET managed_shop_id = ?,
            active_shop_id = CASE WHEN active_shop_id IS NULL OR active_shop_id = 0 THEN ? ELSE active_shop_id END
        WHERE user_id = ?
        """,
        (shop_id, shop_id, owner_id),
    )
    await db.execute(
        "INSERT OR IGNORE INTO shop_customers (shop_id, user_id) VALUES (?, ?)",
        (shop_id, owner_id),
    )
    await db.commit()

    # Если владельца пригласил другой владелец канала и это первый магазин — начисляем бонус пригласившему!
    if is_first_shop and referrer_id and referrer_id != owner_id:
        ref_shop = await _fetchone(
            "SELECT id FROM shops WHERE owner_id = ? ORDER BY id ASC LIMIT 1",
            (referrer_id,),
        )
        if ref_shop:
            await extend_shop_subscription(ref_shop["id"], days=REFERRAL_BONUS_DAYS, is_paid=False)

    return shop_id



async def delete_shop(shop_id: int) -> bool:
    """Полностью удаляет магазин и все связанные с ним товары, корзину, промокоды и привязки."""
    if not shop_id:
        return False
    db = await get_db()
    await db.execute(
        "DELETE FROM cart WHERE item_id IN (SELECT id FROM items WHERE shop_id = ?)",
        (shop_id,),
    )
    await db.execute("DELETE FROM items WHERE shop_id = ?", (shop_id,))
    await db.execute(
        "DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE shop_id = ?)",
        (shop_id,),
    )
    await db.execute("DELETE FROM orders WHERE shop_id = ?", (shop_id,))
    await db.execute("DELETE FROM promocodes WHERE shop_id = ?", (shop_id,))
    await db.execute("DELETE FROM shop_admins WHERE shop_id = ?", (shop_id,))
    await db.execute("DELETE FROM shop_customers WHERE shop_id = ?", (shop_id,))
    await db.execute("DELETE FROM subscriptions WHERE shop_id = ?", (shop_id,))
    await db.execute("DELETE FROM sub_voucher_uses WHERE shop_id = ?", (shop_id,))
    await db.execute("UPDATE users SET active_shop_id = 0 WHERE active_shop_id = ?", (shop_id,))
    await db.execute("UPDATE users SET managed_shop_id = 0 WHERE managed_shop_id = ?", (shop_id,))
    async with db.execute("DELETE FROM shops WHERE id = ?", (shop_id,)) as cur:
        rc = cur.rowcount
    await db.commit()
    return rc > 0


async def get_shop(shop_id: int):
    if not shop_id:
        return None
    return await _fetchone("SELECT * FROM shops WHERE id = ?", (shop_id,))


async def get_shop_by_channel(channel_id: int):
    if not channel_id or channel_id in (0, -100):
        return None
    return await _fetchone("SELECT * FROM shops WHERE channel_id = ?", (channel_id,))


async def get_user_shops(user_id: int) -> list:
    """Возвращает магазины, где пользователь является владельцем или менеджером."""
    if user_id in ADMIN_IDS:
        orphan = await _fetchone("SELECT id FROM shops WHERE owner_id = 0 LIMIT 1")
        if orphan:
            await _execute("UPDATE shops SET owner_id = ? WHERE id = ?", (user_id, orphan["id"]))

    rows = await _fetchall(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM items i WHERE i.shop_id = s.id AND i.hidden = 0) AS items_count,
               (SELECT COUNT(*) FROM orders o WHERE o.shop_id = s.id AND o.status = 'review') AS review_count
        FROM shops s
        WHERE s.owner_id = ?
           OR s.id IN (SELECT shop_id FROM shop_admins WHERE user_id = ?)
        ORDER BY s.id ASC
        """,
        (user_id, user_id),
    )
    for r in rows:
        sub = compute_shop_sub_state(r)
        r["is_pro"] = sub["is_pro"]
        r["sub_status"] = sub["status"]
    return rows


async def can_manage_shop(user_id: int, shop_id: int) -> bool:
    if not shop_id:
        return False
    if user_id in ADMIN_IDS:
        return True
    row = await _fetchone(
        """
        SELECT 1 FROM shops s
        WHERE s.id = ? AND (
            s.owner_id = ? OR EXISTS (
                SELECT 1 FROM shop_admins sa WHERE sa.shop_id = s.id AND sa.user_id = ?
            )
        )
        """,
        (shop_id, user_id, user_id),
    )
    return bool(row)


async def set_managed_shop(user_id: int, shop_id: int):
    await _execute("UPDATE users SET managed_shop_id = ? WHERE user_id = ?", (shop_id, user_id))


async def get_managed_shop(user_id: int):
    """Возвращает текущий управляемый магазин продавца (или выбирает первый доступный)."""
    u = await _fetchone("SELECT managed_shop_id FROM users WHERE user_id = ?", (user_id,))
    managed_id = u["managed_shop_id"] if u else 0
    if managed_id and await can_manage_shop(user_id, managed_id):
        shop = await get_shop(managed_id)
        if shop:
            return shop

    shops = await get_user_shops(user_id)
    if shops:
        first = shops[0]
        await set_managed_shop(user_id, first["id"])
        return first
    return None


async def set_active_shop(user_id: int, shop_id: int):
    """Переключает покупателя на выбранный магазин и регистрирует его в клиентах магазина."""
    db = await get_db()
    await db.execute("UPDATE users SET active_shop_id = ? WHERE user_id = ?", (shop_id, user_id))
    if shop_id:
        await db.execute(
            "INSERT OR IGNORE INTO shop_customers (shop_id, user_id) VALUES (?, ?)",
            (shop_id, user_id),
        )
    await db.commit()


async def get_active_shop(user_id: int):
    """Возвращает выбранный покупателем магазин. Если не выбран и активен всего один — выбирает его."""
    u = await _fetchone("SELECT active_shop_id FROM users WHERE user_id = ?", (user_id,))
    shop_id = u["active_shop_id"] if u else 0
    if shop_id:
        shop = await get_shop(shop_id)
        if shop and shop["is_active"]:
            return shop

    active_shops = await _fetchall("SELECT * FROM shops WHERE is_active = 1 ORDER BY id ASC LIMIT 2")
    if len(active_shops) == 1:
        await set_active_shop(user_id, active_shops[0]["id"])
        return active_shops[0]
    return None


async def list_public_shops(limit: int = 8, offset: int = 0) -> list:
    now_str = _fmt_utc(_utc_now())
    rows = await _fetchall(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM items i WHERE i.shop_id = s.id AND i.hidden = 0 AND i.in_stock = 1) AS stock_count,
               CASE WHEN s.sub_until IS NOT NULL AND s.sub_until > ? THEN 1 ELSE 0 END AS is_pro_sort
        FROM shops s
        WHERE s.is_active = 1
        ORDER BY is_pro_sort DESC, stock_count DESC, s.id DESC
        LIMIT ? OFFSET ?
        """,
        (now_str, limit, offset),
    )
    for r in rows:
        r["is_pro"] = compute_shop_sub_state(r)["is_pro"]
    return rows


async def count_public_shops() -> int:
    row = await _fetchone("SELECT COUNT(*) AS n FROM shops WHERE is_active = 1")
    return row["n"] if row else 0


async def search_public_shops(query: str, limit: int = 10) -> list:
    q = query.strip().casefold()
    rows = await _fetchall(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM items i WHERE i.shop_id = s.id AND i.hidden = 0 AND i.in_stock = 1) AS stock_count
        FROM shops s
        WHERE s.is_active = 1
        ORDER BY stock_count DESC, s.id DESC
        """
    )
    found = []
    for r in rows:
        if (
            q in (r["title"] or "").casefold()
            or q in (r["description"] or "").casefold()
            or q in (r["channel_title"] or "").casefold()
            or q in (r["channel_username"] or "").casefold()
        ):
            r["is_pro"] = compute_shop_sub_state(r)["is_pro"]
            found.append(r)
            if len(found) >= limit:
                break
    return found


async def update_shop_field(shop_id: int, field: str, value):
    if field not in SHOP_UPDATABLE_FIELDS:
        raise ValueError(f"Unsupported shop field: {field}")
    await _execute(f"UPDATE shops SET {field} = ? WHERE id = ?", (value, shop_id))


async def bind_shop_channel(
    shop_id: int,
    channel_id: int,
    channel_title: str = "",
    channel_username: str = "",
    sync_title: bool = False,
):
    db = await get_db()
    if channel_id and channel_id not in (0, -100):
        await db.execute(
            "UPDATE shops SET channel_id = 0, channel_title = '', channel_username = '' WHERE channel_id = ? AND id != ?",
            (channel_id, shop_id),
        )
    clean_title = (channel_title or "").strip()
    clean_user = (channel_username or "").lstrip("@").strip()
    if sync_title and clean_title:
        await db.execute(
            """
            UPDATE shops
            SET channel_id = ?, channel_title = ?, channel_username = ?, title = ?
            WHERE id = ?
            """,
            (channel_id, clean_title, clean_user, clean_title[:60], shop_id),
        )
    else:
        await db.execute(
            """
            UPDATE shops
            SET channel_id = ?, channel_title = ?, channel_username = ?
            WHERE id = ?
            """,
            (channel_id, clean_title, clean_user, shop_id),
        )
    await db.commit()


async def get_shop_settings(shop_id: int = None) -> dict:
    """Возвращает реквизиты и настройки конкретного магазина."""
    shop = await get_shop(shop_id) if shop_id else None
    if shop:
        sub = compute_shop_sub_state(shop)
        return {
            "card_number": shop["card_number"] or "",
            "card_holder": shop["card_holder"] or "",
            "shop_phone": shop["shop_phone"] or "",
            "click_token": (shop.get("click_token") or "").strip(),
            "payme_token": (shop.get("payme_token") or "").strip(),
            "title": shop["title"] or "Магазин",
            "description": shop["description"] or "",
            "channel_id": shop["channel_id"] or 0,
            "channel_title": shop["channel_title"] or "",
            "channel_username": shop["channel_username"] or "",
            "is_active": bool(shop["is_active"]),
            "auto_parse_posts": bool(shop.get("auto_parse_posts", 1)),
            "pinned_message_id": shop.get("pinned_message_id") or 0,
            "is_pro": sub["is_pro"],
            "sub_status": sub["status"],
            "sub_until": sub["sub_until"],
            "days_left": sub["days_left"],
        }
    return {
        "card_number": CARD_NUMBER,
        "card_holder": CARD_HOLDER,
        "shop_phone": SHOP_PHONE,
        "click_token": CLICK_TOKEN,
        "payme_token": PAYME_TOKEN,
        "title": "Магазин",
        "description": "",
        "channel_id": CHANNEL_ID,
        "channel_title": "",
        "channel_username": "",
        "is_active": True,
        "auto_parse_posts": True,
        "pinned_message_id": 0,
        "is_pro": True,
        "sub_status": "vip",
        "sub_until": None,
        "days_left": 999,
    }


# ---------- менеджеры магазина ----------

async def get_shop_staff(shop_id: int) -> list:
    return await _fetchall(
        """
        SELECT sa.user_id, sa.created_at, u.full_name, u.username
        FROM shop_admins sa
        LEFT JOIN users u ON u.user_id = sa.user_id
        WHERE sa.shop_id = ?
        ORDER BY sa.created_at ASC
        """,
        (shop_id,),
    )


async def add_shop_admin(shop_id: int, user_id: int) -> bool:
    shop = await get_shop(shop_id)
    if not shop or shop["owner_id"] == user_id:
        return False
    _, rowcount = await _execute(
        "INSERT OR IGNORE INTO shop_admins (shop_id, user_id) VALUES (?, ?)",
        (shop_id, user_id),
    )
    return rowcount > 0


async def remove_shop_admin(shop_id: int, user_id: int) -> bool:
    _, rowcount = await _execute(
        "DELETE FROM shop_admins WHERE shop_id = ? AND user_id = ?",
        (shop_id, user_id),
    )
    return rowcount > 0


async def get_shop_notify_ids(shop_id: int) -> set:
    shop = await get_shop(shop_id)
    if not shop:
        return set(ADMIN_IDS)
    ids = set()
    if shop["owner_id"]:
        ids.add(shop["owner_id"])
    staff = await get_shop_staff(shop_id)
    for r in staff:
        if r["user_id"]:
            ids.add(r["user_id"])
    return ids or set(ADMIN_IDS)


# ====================== ТОВАРЫ ======================

async def add_product(
    shop_id: int, title: str, description: str, price: int, currency: str, photo_ids: list
) -> int:
    first_photo = photo_ids[0] if photo_ids else None
    joined_photos = ",".join(photo_ids) if photo_ids else ""
    lastrowid, _ = await _execute(
        """
        INSERT INTO items (shop_id, title, description, price, currency, photo_id, photo_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (shop_id, title, description, price, currency, first_photo, joined_photos),
    )
    return lastrowid


async def duplicate_product(product_id: int) -> int | None:
    p = await get_product(product_id)
    if not p:
        return None
    photos = [x for x in (p.get("photo_ids") or p.get("photo_id") or "").split(",") if x]
    return await add_product(
        shop_id=p["shop_id"],
        title=f"{p['title']} (копия)"[:100],
        description=p.get("description") or "",
        price=p["price"],
        currency=p["currency"],
        photo_ids=photos,
    )


async def get_product(product_id: int):
    return await _fetchone("SELECT * FROM items WHERE id = ?", (product_id,))


async def get_catalog(shop_id: int, limit: int = 8, offset: int = 0, sort: str = "new"):
    order_clause = "id DESC"
    if sort == "cheap":
        order_clause = "price ASC, id DESC"
    elif sort == "exp":
        order_clause = "price DESC, id DESC"
    return await _fetchall(
        f"""
        SELECT * FROM items
        WHERE shop_id = ? AND hidden = 0 AND in_stock = 1
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        (shop_id, limit, offset),
    )


async def count_catalog(shop_id: int) -> int:
    row = await _fetchone(
        "SELECT COUNT(*) AS n FROM items WHERE shop_id = ? AND hidden = 0 AND in_stock = 1",
        (shop_id,),
    )
    return row["n"] if row else 0


async def get_all_products(shop_id: int, limit: int = 8, offset: int = 0):
    return await _fetchall(
        "SELECT * FROM items WHERE shop_id = ? AND hidden = 0 ORDER BY id DESC LIMIT ? OFFSET ?",
        (shop_id, limit, offset),
    )


async def count_products(shop_id: int) -> int:
    row = await _fetchone("SELECT COUNT(*) AS n FROM items WHERE shop_id = ? AND hidden = 0", (shop_id,))
    return row["n"] if row else 0


async def search_products(shop_id: int, query: str, limit: int = 10, only_in_stock: bool = False):
    q = query.strip().casefold()
    stock_clause = "AND in_stock = 1" if only_in_stock else ""
    rows = await _fetchall(
        f"SELECT * FROM items WHERE shop_id = ? AND hidden = 0 {stock_clause} ORDER BY id DESC",
        (shop_id,),
    )
    found = [
        r for r in rows
        if q in (r["title"] or "").casefold() or q in (r["description"] or "").casefold()
    ]
    return found[:limit]


async def set_channel_messages(product_id: int, message_ids: list):
    if not message_ids:
        await _execute(
            "UPDATE items SET channel_message_id = NULL, channel_message_ids = '' WHERE id = ?",
            (product_id,),
        )
        return
    await _execute(
        "UPDATE items SET channel_message_id = ?, channel_message_ids = ? WHERE id = ?",
        (message_ids[0], ",".join(str(m) for m in message_ids), product_id),
    )


async def set_price(product_id: int, price: int):
    await _execute("UPDATE items SET price = ? WHERE id = ?", (price, product_id))


async def set_currency(product_id: int, currency: str):
    if currency in ("UZS", "USD"):
        await _execute("UPDATE items SET currency = ? WHERE id = ?", (currency, product_id))


async def set_photos(product_id: int, photo_ids: list):
    first_photo = photo_ids[0] if photo_ids else None
    joined = ",".join(photo_ids) if photo_ids else ""
    await _execute(
        "UPDATE items SET photo_id = ?, photo_ids = ? WHERE id = ?",
        (first_photo, joined, product_id),
    )


async def set_title(product_id: int, title: str):
    await _execute("UPDATE items SET title = ? WHERE id = ?", (title, product_id))


async def set_description(product_id: int, description: str):
    await _execute("UPDATE items SET description = ? WHERE id = ?", (description, product_id))


async def set_in_stock(product_id: int, in_stock: bool):
    await _execute("UPDATE items SET in_stock = ? WHERE id = ?", (int(in_stock), product_id))


async def set_hidden(product_id: int, hidden: bool):
    await _execute("UPDATE items SET hidden = ? WHERE id = ?", (int(hidden), product_id))


async def delete_product(product_id: int):
    await _execute("DELETE FROM items WHERE id = ?", (product_id,))


# ====================== КОРЗИНА ======================

async def get_cart(user_id: int, shop_id: int = None):
    if shop_id:
        return await _fetchall(
            """
            SELECT c.item_id, c.qty, i.shop_id, i.title, i.price, i.currency, i.in_stock, i.hidden
            FROM cart c JOIN items i ON i.id = c.item_id
            WHERE c.user_id = ? AND i.shop_id = ?
            ORDER BY c.rowid
            """,
            (user_id, shop_id),
        )
    return await _fetchall(
        """
        SELECT c.item_id, c.qty, i.shop_id, i.title, i.price, i.currency, i.in_stock, i.hidden
        FROM cart c JOIN items i ON i.id = c.item_id
        WHERE c.user_id = ?
        ORDER BY c.rowid
        """,
        (user_id,),
    )


async def get_cart_item_qty(user_id: int, item_id: int) -> int:
    row = await _fetchone(
        "SELECT qty FROM cart WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    )
    return row["qty"] if row else 0


async def get_user_cart_shops(user_id: int) -> list:
    """Возвращает магазины, товары которых сейчас лежат в корзине пользователя."""
    return await _fetchall(
        """
        SELECT s.id, s.title, COUNT(c.item_id) AS items_count
        FROM cart c
        JOIN items i ON i.id = c.item_id
        JOIN shops s ON s.id = i.shop_id
        WHERE c.user_id = ? AND i.hidden = 0 AND s.is_active = 1
        GROUP BY s.id, s.title
        ORDER BY MAX(c.rowid) DESC
        """,
        (user_id,),
    )


async def add_to_cart(user_id: int, item_id: int) -> tuple[bool, int]:
    """Добавляет товар в корзину (или увеличивает количество до 10, если уже в корзине).
    Возвращает (is_new, new_qty).
    """
    existing = await get_cart_item_qty(user_id, item_id)
    if existing == 0:
        await _execute(
            "INSERT OR IGNORE INTO cart (user_id, item_id, qty) VALUES (?, ?, 1)",
            (user_id, item_id),
        )
        return True, 1
    new_qty = min(10, existing + 1)
    await _execute(
        "UPDATE cart SET qty = ? WHERE user_id = ? AND item_id = ?",
        (new_qty, user_id, item_id),
    )
    return False, new_qty


async def change_qty(user_id: int, item_id: int, delta: int):
    await _execute(
        "UPDATE cart SET qty = MIN(10, MAX(1, qty + ?)) WHERE user_id = ? AND item_id = ?",
        (delta, user_id, item_id),
    )


async def remove_from_cart(user_id: int, item_id: int):
    await _execute("DELETE FROM cart WHERE user_id = ? AND item_id = ?", (user_id, item_id))


async def clear_cart(user_id: int, shop_id: int = None):
    if shop_id:
        await _execute(
            """
            DELETE FROM cart
            WHERE user_id = ? AND item_id IN (SELECT id FROM items WHERE shop_id = ?)
            """,
            (user_id, shop_id),
        )
    else:
        await _execute("DELETE FROM cart WHERE user_id = ?", (user_id,))


# ====================== ЗАКАЗЫ ======================

async def create_order(
    user_id: int,
    shop_id: int,
    name: str,
    phone: str,
    address: str,
    rows: list,
    promo_code: str = None,
) -> int:
    subtotal = sum(r["price"] * r["qty"] for r in rows)
    currency = rows[0]["currency"]
    discount_amount = 0
    applied_code = None

    if promo_code:
        promo = await validate_promocode(shop_id, promo_code)
        if promo:
            applied_code = promo["code"]
            discount_amount = int(round(subtotal * (promo["discount_pct"] / 100.0)))
            if discount_amount >= subtotal:
                discount_amount = max(0, subtotal - 1)

    total = max(1, subtotal - discount_amount)

    db = await get_db()
    async with db.execute(
        """
        INSERT INTO orders (shop_id, user_id, name, phone, address, total, currency, promo_code, discount_amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (shop_id, user_id, name, phone, address, total, currency, applied_code, discount_amount),
    ) as cursor:
        order_id = cursor.lastrowid

    if applied_code:
        await db.execute(
            "UPDATE promocodes SET used_count = used_count + 1 WHERE shop_id = ? AND code = ?",
            (shop_id, applied_code),
        )

    await db.executemany(
        """
        INSERT INTO order_items (order_id, item_id, title, price, currency, qty)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(order_id, r["item_id"], r["title"], r["price"], r["currency"], r["qty"]) for r in rows],
    )
    await db.execute(
        """
        DELETE FROM cart
        WHERE user_id = ? AND item_id IN (SELECT id FROM items WHERE shop_id = ?)
        """,
        (user_id, shop_id),
    )
    await db.execute(
        "INSERT OR IGNORE INTO shop_customers (shop_id, user_id) VALUES (?, ?)",
        (shop_id, user_id),
    )
    await db.execute(
        "UPDATE users SET saved_name = ?, saved_phone = ?, saved_address = ? WHERE user_id = ?",
        (name, phone, address, user_id),
    )
    await db.commit()
    return order_id


async def get_order(order_id: int):
    return await _fetchone(
        """
        SELECT o.*, s.title AS shop_title
        FROM orders o
        LEFT JOIN shops s ON s.id = o.shop_id
        WHERE o.id = ?
        """,
        (order_id,),
    )


async def get_order_items(order_id: int):
    return await _fetchall("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,))


async def get_user_orders(user_id: int, limit: int = 15, shop_id: int = None):
    if shop_id:
        return await _fetchall(
            """
            SELECT o.*, s.title AS shop_title
            FROM orders o
            LEFT JOIN shops s ON s.id = o.shop_id
            WHERE o.user_id = ? AND o.shop_id = ?
            ORDER BY o.id DESC LIMIT ?
            """,
            (user_id, shop_id, limit),
        )
    return await _fetchall(
        """
        SELECT o.*, s.title AS shop_title
        FROM orders o
        LEFT JOIN shops s ON s.id = o.shop_id
        WHERE o.user_id = ?
        ORDER BY o.id DESC LIMIT ?
        """,
        (user_id, limit),
    )



async def mark_order_paid_auto(order_id: int, method: str, charge_id: str = "") -> bool:
    _, rc = await _execute(
        """
        UPDATE orders
        SET status = 'confirmed',
            payment_method = ?,
            tracking = COALESCE(tracking, ?),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status IN ('pending_payment', 'review', 'rejected')
        """,
        (method, f"Авто-оплата {method.upper()} ({charge_id})" if charge_id else f"Авто-оплата {method.upper()}", order_id),
    )
    return rc > 0


async def set_order_payment(order_id: int, photo_id: str):
    await _execute(
        """
        UPDATE orders SET status = 'review', payment_photo_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (photo_id, order_id),
    )


async def cancel_order(order_id: int, user_id: int) -> bool:
    _, rowcount = await _execute(
        """
        UPDATE orders SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ? AND status = 'pending_payment'
        """,
        (order_id, user_id),
    )
    return rowcount > 0


async def change_order_status(
    order_id: int, from_statuses, to_status: str, tracking=None, reason=None
) -> bool:
    marks = ",".join("?" for _ in from_statuses)
    _, rowcount = await _execute(
        f"""
        UPDATE orders
        SET status = ?,
            tracking = COALESCE(?, tracking),
            reason = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status IN ({marks})
        """,
        (to_status, tracking, reason, order_id, *from_statuses),
    )
    return rowcount > 0


async def count_orders_by_status(shop_id: int) -> dict:
    rows = await _fetchall(
        "SELECT status, COUNT(*) AS n FROM orders WHERE shop_id = ? GROUP BY status",
        (shop_id,),
    )
    return {r["status"]: r["n"] for r in rows}


async def count_orders(shop_id: int, status=None) -> int:
    if status:
        row = await _fetchone(
            "SELECT COUNT(*) AS n FROM orders WHERE shop_id = ? AND status = ?",
            (shop_id, status),
        )
    else:
        row = await _fetchone("SELECT COUNT(*) AS n FROM orders WHERE shop_id = ?", (shop_id,))
    return row["n"] if row else 0


async def list_orders(shop_id: int, status=None, limit: int = 8, offset: int = 0):
    if status:
        return await _fetchall(
            "SELECT * FROM orders WHERE shop_id = ? AND status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (shop_id, status, limit, offset),
        )
    return await _fetchall(
        "SELECT * FROM orders WHERE shop_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (shop_id, limit, offset),
    )


async def get_all_orders_for_export(shop_id: int) -> list:
    return await _fetchall(
        """
        SELECT o.id, o.created_at, o.status, o.name, o.phone, o.address,
               o.total, o.currency, o.promo_code, o.discount_amount, o.tracking,
               GROUP_CONCAT(oi.title || ' x' || oi.qty, '; ') AS items_summary
        FROM orders o
        LEFT JOIN order_items oi ON oi.order_id = o.id
        WHERE o.shop_id = ?
        GROUP BY o.id
        ORDER BY o.id DESC
        """,
        (shop_id,),
    )


async def mark_order_items_sold(order_id: int) -> list:
    rows = await _fetchall(
        "SELECT DISTINCT item_id FROM order_items WHERE order_id = ? AND item_id IS NOT NULL",
        (order_id,),
    )
    ids = [r["item_id"] for r in rows]
    if ids:
        marks = ",".join("?" for _ in ids)
        await _execute(f"UPDATE items SET in_stock = 0 WHERE id IN ({marks})", tuple(ids))
    return ids


# ====================== КЛИЕНТЫ И РАССЫЛКА МАГАЗИНА ======================

async def count_shop_users(shop_id: int, lang=None) -> int:
    if lang:
        row = await _fetchone(
            """
            SELECT COUNT(*) AS n FROM users u
            WHERE u.blocked = 0 AND u.lang = ?
              AND (
                  EXISTS (SELECT 1 FROM shop_customers sc WHERE sc.shop_id = ? AND sc.user_id = u.user_id)
                  OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id)
              )
            """,
            (lang, shop_id, shop_id),
        )
    else:
        row = await _fetchone(
            """
            SELECT COUNT(*) AS n FROM users u
            WHERE u.blocked = 0
              AND (
                  EXISTS (SELECT 1 FROM shop_customers sc WHERE sc.shop_id = ? AND sc.user_id = u.user_id)
                  OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id)
              )
            """,
            (shop_id, shop_id),
        )
    return row["n"] if row else 0


async def get_shop_user_ids(shop_id: int, lang=None) -> list:
    if lang:
        rows = await _fetchall(
            """
            SELECT u.user_id FROM users u
            WHERE u.blocked = 0 AND u.lang = ?
              AND (
                  EXISTS (SELECT 1 FROM shop_customers sc WHERE sc.shop_id = ? AND sc.user_id = u.user_id)
                  OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id)
              )
            ORDER BY u.user_id
            """,
            (lang, shop_id, shop_id),
        )
    else:
        rows = await _fetchall(
            """
            SELECT u.user_id FROM users u
            WHERE u.blocked = 0
              AND (
                  EXISTS (SELECT 1 FROM shop_customers sc WHERE sc.shop_id = ? AND sc.user_id = u.user_id)
                  OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id)
              )
            ORDER BY u.user_id
            """,
            (shop_id, shop_id),
        )
    return [r["user_id"] for r in rows]


async def count_customers(shop_id: int) -> int:
    row = await _fetchone(
        """
        SELECT COUNT(*) AS n FROM users u
        WHERE EXISTS (SELECT 1 FROM shop_customers sc WHERE sc.shop_id = ? AND sc.user_id = u.user_id)
           OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id)
        """,
        (shop_id, shop_id),
    )
    return row["n"] if row else 0


async def list_customers(shop_id: int, limit: int = 8, offset: int = 0):
    return await _fetchall(
        """
        SELECT u.user_id, u.full_name, u.username, u.lang, u.blocked, u.created_at,
               (SELECT COUNT(*) FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id) AS orders_count
        FROM users u
        WHERE EXISTS (SELECT 1 FROM shop_customers sc WHERE sc.shop_id = ? AND sc.user_id = u.user_id)
           OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id)
        ORDER BY orders_count DESC, u.created_at DESC, u.user_id DESC
        LIMIT ? OFFSET ?
        """,
        (shop_id, shop_id, shop_id, limit, offset),
    )


async def get_customer(user_id: int, shop_id: int = None):
    if shop_id:
        return await _fetchone(
            """
            SELECT u.user_id, u.full_name, u.username, u.lang, u.blocked, u.created_at,
                   (SELECT COUNT(*) FROM orders o WHERE o.shop_id = ? AND o.user_id = u.user_id) AS orders_count,
                   (SELECT COUNT(*) FROM orders o
                     WHERE o.shop_id = ? AND o.user_id = u.user_id AND o.status IN ('confirmed', 'shipped')) AS paid_count
            FROM users u WHERE u.user_id = ?
            """,
            (shop_id, shop_id, user_id),
        )
    return await _fetchone(
        """
        SELECT u.user_id, u.full_name, u.username, u.lang, u.blocked, u.created_at,
               (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.user_id) AS orders_count,
               (SELECT COUNT(*) FROM orders o
                 WHERE o.user_id = u.user_id AND o.status IN ('confirmed', 'shipped')) AS paid_count
        FROM users u WHERE u.user_id = ?
        """,
        (user_id,),
    )


async def customer_spent(user_id: int, shop_id: int = None) -> dict:
    if shop_id:
        rows = await _fetchall(
            """
            SELECT currency, SUM(total) AS s FROM orders
            WHERE shop_id = ? AND user_id = ? AND status IN ('confirmed', 'shipped')
            GROUP BY currency
            """,
            (shop_id, user_id),
        )
    else:
        rows = await _fetchall(
            """
            SELECT currency, SUM(total) AS s FROM orders
            WHERE user_id = ? AND status IN ('confirmed', 'shipped')
            GROUP BY currency
            """,
            (user_id,),
        )
    return {r["currency"]: r["s"] for r in rows}


# ====================== СТАТИСТИКА МАГАЗИНА И ПЛАТФОРМЫ ======================

def _tz() -> str:
    return f"{TZ_OFFSET_HOURS:+d} hours"


def _period_clause(days):
    if days is None:
        return "1 = 1", ()
    if days == 0:
        return "date(created_at, ?) = date('now', ?)", (_tz(), _tz())
    return f"date(created_at, ?) >= date('now', ?, '-{int(days) - 1} days')", (_tz(), _tz())


async def stats_period(shop_id: int, days) -> dict:
    clause, params = _period_clause(days)
    row = await _fetchone(
        f"SELECT COUNT(*) AS n FROM orders WHERE shop_id = ? AND status != 'cancelled' AND {clause}",
        (shop_id, *params),
    )
    rows = await _fetchall(
        f"""
        SELECT currency, SUM(total) AS s FROM orders
        WHERE shop_id = ? AND status IN ('confirmed', 'shipped') AND {clause}
        GROUP BY currency
        """,
        (shop_id, *params),
    )
    return {"orders": row["n"] if row else 0, "revenue": {r["currency"]: r["s"] for r in rows}}


async def stats_extra(shop_id: int) -> dict:
    customers = await count_customers(shop_id)
    row = await _fetchone(
        """
        SELECT
            (SELECT COUNT(*) FROM shop_customers WHERE shop_id = ? AND date(created_at, ?) >= date('now', ?, '-6 days')) AS customers_week,
            (SELECT COUNT(*) FROM items WHERE shop_id = ? AND hidden = 0) AS products,
            (SELECT COUNT(*) FROM items WHERE shop_id = ? AND hidden = 0 AND in_stock = 1) AS in_stock,
            (SELECT COUNT(*) FROM promocodes WHERE shop_id = ? AND is_active = 1) AS promocodes
        """,
        (shop_id, _tz(), _tz(), shop_id, shop_id, shop_id),
    )
    return {
        "customers": customers,
        "customers_week": row["customers_week"] if row else 0,
        "products": row["products"] if row else 0,
        "in_stock": row["in_stock"] if row else 0,
        "promocodes": row["promocodes"] if row else 0,
    }


async def top_products(shop_id: int, limit: int = 5):
    return await _fetchall(
        """
        SELECT oi.title AS title, SUM(oi.qty) AS qty
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE o.shop_id = ? AND o.status IN ('confirmed', 'shipped')
        GROUP BY COALESCE(oi.item_id, oi.title), oi.title
        ORDER BY qty DESC, oi.title
        LIMIT ?
        """,
        (shop_id, limit),
    )


# ---------- супер-админка платформы ----------

async def platform_stats() -> dict:
    now_str = _fmt_utc(_utc_now())
    row = await _fetchone(
        """
        SELECT
            (SELECT COUNT(*) FROM shops) AS total_shops,
            (SELECT COUNT(*) FROM shops WHERE is_active = 1) AS active_shops,
            (SELECT COUNT(*) FROM shops WHERE sub_until IS NOT NULL AND sub_until > ?) AS pro_shops,
            (SELECT COUNT(*) FROM shops WHERE trial_used = 2 AND sub_until IS NOT NULL AND sub_until > ?) AS paid_pro_shops,
            (SELECT COUNT(*) FROM shops WHERE channel_id != 0 AND channel_id != -100) AS bound_channels,
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM items WHERE hidden = 0) AS total_items,
            (SELECT COUNT(*) FROM orders WHERE status != 'cancelled') AS total_orders,
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'review') AS pending_subs
        """,
        (now_str, now_str),
    )
    rev_rows = await _fetchall(
        """
        SELECT currency, SUM(total) AS s FROM orders
        WHERE status IN ('confirmed', 'shipped')
        GROUP BY currency
        """
    )
    sub_rev_rows = await _fetchall(
        """
        SELECT currency, SUM(amount) AS s FROM subscriptions
        WHERE status = 'confirmed' AND amount > 0
        GROUP BY currency
        """
    )
    return {
        "total_shops": row["total_shops"] if row else 0,
        "active_shops": row["active_shops"] if row else 0,
        "pro_shops": row["pro_shops"] if row else 0,
        "paid_pro_shops": row["paid_pro_shops"] if row else 0,
        "bound_channels": row["bound_channels"] if row else 0,
        "total_users": row["total_users"] if row else 0,
        "total_items": row["total_items"] if row else 0,
        "total_orders": row["total_orders"] if row else 0,
        "pending_subs": row["pending_subs"] if row else 0,
        "revenue": {r["currency"]: r["s"] for r in rev_rows},
        "sub_revenue": {r["currency"]: r["s"] for r in sub_rev_rows},
    }


async def count_all_shops() -> int:
    row = await _fetchone("SELECT COUNT(*) AS n FROM shops")
    return row["n"] if row else 0


async def list_all_shops(limit: int = 8, offset: int = 0) -> list:
    rows = await _fetchall(
        """
        SELECT s.*,
               u.full_name AS owner_name,
               u.username AS owner_username,
               (SELECT COUNT(*) FROM items i WHERE i.shop_id = s.id AND i.hidden = 0) AS items_count,
               (SELECT COUNT(*) FROM orders o WHERE o.shop_id = s.id AND o.status != 'cancelled') AS orders_count
        FROM shops s
        LEFT JOIN users u ON u.user_id = s.owner_id
        ORDER BY s.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    for r in rows:
        sub = compute_shop_sub_state(r)
        r["is_pro"] = sub["is_pro"]
        r["sub_status"] = sub["status"]
        r["sub_label"] = sub["label"]
    return rows


async def get_all_user_ids(only_owners: bool = False) -> list:
    if only_owners:
        rows = await _fetchall(
            """
            SELECT DISTINCT s.owner_id AS user_id
            FROM shops s
            LEFT JOIN users u ON u.user_id = s.owner_id
            WHERE s.owner_id != 0 AND COALESCE(u.blocked, 0) = 0
            ORDER BY s.owner_id
            """
        )
    else:
        rows = await _fetchall("SELECT user_id FROM users WHERE blocked = 0 ORDER BY user_id")
    return [r["user_id"] for r in rows]
