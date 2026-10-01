import os
import re
import sqlite3
import html
import requests as std_requests

from bs4 import BeautifulSoup
from datetime import datetime


# ============================================================
# AYARLAR
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY")

CHAT_ID = "@firsatanlik"
DB_NAME = "firsat_anlik.db"

MAX_DAILY_POSTS = 12


# ============================================================
# VERİTABANI
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id TEXT PRIMARY KEY,
            title TEXT,
            price REAL,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_counter (
            post_date DATE PRIMARY KEY,
            count INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def can_post_today():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute(
        "SELECT count FROM daily_counter WHERE post_date = ?",
        (today,)
    )

    row = cursor.fetchone()
    conn.close()

    return not (row and row[0] >= MAX_DAILY_POSTS)


def increment_daily_count():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        INSERT INTO daily_counter (post_date, count)
        VALUES (?, 1)
        ON CONFLICT(post_date)
        DO UPDATE SET count = count + 1
    """, (today,))

    conn.commit()
    conn.close()


def is_already_posted(deal_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM posted_deals WHERE id = ?",
        (deal_id,)
    )

    row = cursor.fetchone()
    conn.close()

    return row is not None


def record_posted_deal(deal_id, title, price):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO posted_deals
        (id, title, price)
        VALUES (?, ?, ?)
    """, (deal_id, title, price))

    conn.commit()
    conn.close()


# ============================================================
# FİYAT YARDIMCILARI
# ============================================================

def parse_price(text):
    if not text:
        return None

    text = str(text).strip()
    text = text.replace("\xa0", " ")
    text = text.replace("₺", "")
    text = text.replace("TL", "")
    text = text.replace(" ", "")

    # Türkçe fiyat biçimi:
    # 12.999,90 -> 12999.90
    if "," in text:
        text = text.replace(".", "")
        text = text.replace(",", ".")
    else:
        # 1299.90 gibi gelirse dokunma
        parts = text.split(".")
        if len(parts) > 2:
            text = "".join(parts)

    text = re.sub(r"[^\d.]", "", text)

    if not text:
        return None

    try:
        value = float(text)

        if value <= 0:
            return None

        return value

    except ValueError:
        return None


def extract_prices_from_text(text):
    if not text:
        return []

    patterns = [
        r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*TL",
        r"₺\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)",
        r"(\d+(?:,\d{2})?)\s*₺"
    ]

    prices = []

    for pattern in patterns:
        matches = re.findall(pattern, text)

        for match in matches:
            price = parse_price(match)

            if price is not None:
                prices.append(price)

    return prices


def find_price(item):
    # 1. Amazon klasik fiyat alanları
    selectors = [
        "span.a-price span.a-offscreen",
        "span.a-price-whole",
        "span.a-color-price",
        "span.a-offscreen",
        "[aria-label*='TL']",
        "[aria-label*='₺']"
    ]

    for selector in selectors:
        elements = item.select(selector)

        for element in elements:
            possible_values = [
                element.get_text(" ", strip=True),
                element.get("aria-label"),
                element.get("data-a-color")
            ]

            for value in possible_values:
                price = parse_price(value)

                if price:
                    return price

    # 2. Kartın bütün metninden fiyat ara
    card_text = item.get_text(" ", strip=True)

    prices = extract_prices_from_text(card_text)

    if prices:
        # Genellikle kartta görünen ilk fiyat güncel fiyattır
        return prices[0]

    return None


def find_old_price(item, current_price):
    selectors = [
        "span.a-price[data-a-strike='true'] span.a-offscreen",
        "span.a-text-price span.a-offscreen",
        "span.a-price.a-text-price span.a-offscreen"
    ]

    for selector in selectors:
        elements = item.select(selector)

        for element in elements:
            old_price = parse_price(
                element.get_text(" ", strip=True)
            )

            if old_price and old_price > current_price:
                return old_price

    # Kart içindeki tüm fiyatlara bak
    card_text = item.get_text(" ", strip=True)

    prices = extract_prices_from_text(card_text)

    bigger_prices = [
        p for p in prices
        if p > current_price
    ]

    if bigger_prices:
        return max(bigger_prices)

    return None


def absolute_amazon_link(href):
    if not href:
        return None

    if href.startswith("http"):
        return href

    if href.startswith("/"):
        return "https://www.amazon.com.tr" + href

    return "https://www.amazon.com.tr/" + href


# ============================================================
# AMAZON
# ============================================================

def fetch_amazon_deals():

    url = (
        "https://www.amazon.com.tr/"
        "s?k=f%C3%BCrsat&rh=p_n_specials_match%3A21618252031"
    )

    deals = []

    if not ZENROWS_API_KEY:
        print("❌ ZENROWS_API_KEY bulunamadı.")
        return deals

    try:
        response = std_requests.get(
            "https://api.zenrows.com/v1/",
            params={
                "apikey": ZENROWS_API_KEY,
                "url": url,
                "js_render": "true",
                "premium_proxy": "true"
            },
            timeout=90
        )

        print("Amazon Yanıt Kodu:", response.status_code)
        print("HTML uzunluğu:", len(response.text))

        if response.status_code != 200:
            print("❌ Amazon sayfası alınamadı.")
            print(response.text[:500])
            return deals

        soup = BeautifulSoup(
            response.content,
            "html.parser"
        )

        if soup.title:
            print(
                "Sayfa başlığı:",
                soup.title.get_text(
                    " ",
                    strip=True
                )
            )

        # Bütün ASIN içeren kartları bul
        items = [
            item
            for item in soup.select("[data-asin]")
            if item.get("data-asin", "").strip()
        ]

        print("Ürün kartı sayısı:", len(items))

        for index, item in enumerate(items, start=1):

            try:
                asin = item.get(
                    "data-asin",
                    ""
                ).strip()

                if not asin:
                    continue

                # ----------------------------
                # BAŞLIK
                # ----------------------------

                title_elem = (
                    item.select_one("h2 span")
                    or item.select_one("h2")
                    or item.select_one(
                        "[data-cy='title-recipe'] span"
                    )
                )

                if not title_elem:
                    print(
                        f"⚠️ Kart {index}: "
                        "başlık bulunamadı"
                    )
                    continue

                title = title_elem.get_text(
                    " ",
                    strip=True
                )

                if not title:
                    continue

                # ----------------------------
                # LİNK
                # ----------------------------

                link_elem = (
                    item.select_one("h2 a[href]")
                    or item.select_one(
                        "a.a-link-normal[href]"
                    )
                    or item.select_one("a[href*='/dp/']")
                )

                if not link_elem:
                    print(
                        f"⚠️ Kart {index}: "
                        "link bulunamadı"
                    )
                    continue

                link = absolute_amazon_link(
                    link_elem.get("href")
                )

                # ----------------------------
                # FİYAT
                # ----------------------------

                price = find_price(item)

                if price is None:
                    print(
                        f"⚠️ Kart {index}: "
                        f"fiyat bulunamadı | {title[:70]}"
                    )

                    # Teşhis için kart metnini göster
                    print(
                        "Kart metni:",
                        item.get_text(
                            " ",
                            strip=True
                        )[:500]
                    )

                    continue

                old_price = find_old_price(
                    item,
                    price
                )

                deals.append({
                    "id": asin,
                    "title": title,
                    "price": price,
                    "old_price": old_price,
                    "link": link
                })

                print(
                    "✅ Ürün bulundu:",
                    asin,
                    "|",
                    title[:60],
                    "|",
                    price,
                    "| eski:",
                    old_price
                )

            except Exception as e:
                print(
                    f"⚠️ Kart {index} hatası:",
                    str(e)
                )

    except Exception as e:
        print(
            "❌ Tarama Hatası:",
            str(e)
        )

    return deals


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_deal(deal):

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN bulunamadı.")
        return False

    title = html.escape(
        deal["title"]
    )

    link = html.escape(
        deal["link"]
    )

    price = deal["price"]
    old_price = deal.get(
        "old_price"
    )

    if old_price and old_price > price:

        discount = round(
            (
                (old_price - price)
                / old_price
            ) * 100
        )

        message = (
            "⚡️ <b>FIRSAT ANLIK</b>\n\n"
            f"📦 <b>{title}</b>\n\n"
            f"❌ <s>{old_price:,.2f} TL</s>\n"
            f"✅ <b>{price:,.2f} TL</b>\n"
            f"🔥 <b>%{discount} indirim</b>\n\n"
            f'👉 <a href="{link}">'
            "Fırsata Git"
            "</a>"
        )

    else:

        message = (
            "⚡️ <b>FIRSAT ANLIK</b>\n\n"
            f"📦 <b>{title}</b>\n\n"
            f"💰 <b>{price:,.2f} TL</b>\n\n"
            f'👉 <a href="{link}">'
            "Fırsata Git"
            "</a>"
        )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:
        response = std_requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=30
        )

        result = response.json()

        print(
            "Telegram yanıtı:",
            result
        )

        return result.get(
            "ok",
            False
        )

    except Exception as e:
        print(
            "❌ Telegram hatası:",
            str(e)
        )

        return False


# ============================================================
# ÇALIŞTIR
# ============================================================

def run_bot():

    init_db()

    if not can_post_today():
        print(
            "Günlük maksimum paylaşım "
            "limitine ulaşıldı."
        )
        return

    deals = fetch_amazon_deals()

    print(
        "Bulunan fırsat sayısı:",
        len(deals)
    )

    if not deals:
        print(
            "Bu taramada paylaşılacak "
            "ürün bulunamadı."
        )
        return

    for deal in deals:

        if is_already_posted(
            deal["id"]
        ):
            print(
                "Atlandı:",
                deal["id"]
            )
            continue

        success = send_telegram_deal(
            deal
        )

        if success:

            record_posted_deal(
                deal["id"],
                deal["title"],
                deal["price"]
            )

            increment_daily_count()

            print(
                "✅ Paylaşıldı:",
                deal["title"]
            )

            # Şimdilik her çalışmada
            # yalnızca 1 paylaşım
            break


if __name__ == "__main__":
    run_bot()
