import os
import re
import sqlite3
import html
import requests as std_requests

from bs4 import BeautifulSoup
from datetime import datetime


# ============================================================
# GENEL KONFİGÜRASYON
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
# YARDIMCI FONKSİYONLAR
# ============================================================

def parse_price(price_text):
    """
    Türkçe Amazon fiyatlarını float'a çevirir.

    Örnek:
    1.299,90 TL -> 1299.90
    """

    if not price_text:
        return None

    cleaned = price_text.strip()

    cleaned = cleaned.replace("₺", "")
    cleaned = cleaned.replace("TL", "")
    cleaned = cleaned.replace("\xa0", "")
    cleaned = cleaned.replace(" ", "")

    # Türkiye formatı
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", ".")

    cleaned = re.sub(r"[^\d.]", "", cleaned)

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
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
# AMAZON FIRSATLARINI ÇEK
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
            timeout=60
        )

        print(f"Amazon Yanıt Kodu: {response.status_code}")
        print(f"HTML uzunluğu: {len(response.text)}")

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
                    strip=True
                )
            )
        else:
            print("Sayfa başlığı bulunamadı.")

        # Amazon'un yeni sonuç kartları
        items = soup.select(
            "div.s-result-item[data-asin]"
        )

        # Alternatif yöntem
        if not items:
            items = [
                item
                for item in soup.select(
                    "div[data-asin]"
                )
                if item.get("data-asin")
            ]

        print(
            f"Ürün kartı sayısı: {len(items)}"
        )

        print(
            "Fiyat alanı sayısı:",
            len(
                soup.select(
                    "span.a-price "
                    "span.a-offscreen"
                )
            )
        )

        print(
            "H2 başlık sayısı:",
            len(soup.select("h2"))
        )

        for item in items:

            try:

                asin = (
                    item.get(
                        "data-asin",
                        ""
                    )
                    .strip()
                )

                if not asin:
                    continue

                # ----------------------------
                # BAŞLIK
                # ----------------------------

                title_elem = (
                    item.select_one(
                        "h2 span"
                    )
                    or item.select_one(
                        "h2"
                    )
                )

                if not title_elem:
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
                    item.select_one(
                        "h2 a[href]"
                    )
                    or item.select_one(
                        "a.a-link-normal[href]"
                    )
                )

                if not link_elem:
                    continue

                link = absolute_amazon_link(
                    link_elem.get("href")
                )

                if not link:
                    continue

                # ----------------------------
                # GÜNCEL FİYAT
                # ----------------------------

                price_elem = (
                    item.select_one(
                        "span.a-price "
                        "span.a-offscreen"
                    )
                    or item.select_one(
                        "span.a-color-price"
                    )
                )

                price = None

                if price_elem:
                    price = parse_price(
                        price_elem.get_text(
                            strip=True
                        )
                    )

                # Bazı Amazon kartlarında
                # fiyat parçalara ayrılmış olabilir
                if price is None:

                    whole = item.select_one(
                        "span.a-price-whole"
                    )

                    fraction = item.select_one(
                        "span.a-price-fraction"
                    )

                    if whole:

                        whole_text = (
                            whole.get_text(
                                strip=True
                            )
                        )

                        fraction_text = (
                            fraction.get_text(
                                strip=True
                            )
                            if fraction
                            else "00"
                        )

                        combined_price = (
                            f"{whole_text},"
                            f"{fraction_text}"
                        )

                        price = parse_price(
                            combined_price
                        )

                if price is None:
                    continue

                # ----------------------------
                # ESKİ FİYAT
                # ----------------------------

                old_price = None

                old_price_elem = (
                    item.select_one(
                        "span.a-price"
                        "[data-a-strike='true'] "
                        "span.a-offscreen"
                    )
                    or item.select_one(
                        "span.a-text-price "
                        "span.a-offscreen"
                    )
                )

                if old_price_elem:

                    old_price = parse_price(
                        old_price_elem.get_text(
                            strip=True
                        )
                    )

                # Güncel fiyatla aynıysa
                # eski fiyat sayma
                if (
                    old_price is not None
                    and old_price <= price
                ):
                    old_price = None

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
                    "-",
                    title[:60],
                    "-",
                    price
                )

            except Exception as e:
                print(
                    "Ürün işleme hatası:",
                    str(e)
                )
                continue

    except Exception as e:

        print(
            f"❌ Tarama Hatası: {e}"
        )

    return deals


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def send_telegram_deal(deal_data):

    if not BOT_TOKEN:
        print(
            "❌ BOT_TOKEN bulunamadı."
        )
        return False

    title = deal_data.get(
        "title",
        "Amazon Fırsatı"
    )

    price = deal_data.get(
        "price"
    )

    old_price = deal_data.get(
        "old_price"
    )

    link = deal_data.get(
        "link"
    )

    safe_title = html.escape(title)

    # ----------------------------
    # İNDİRİM VARSA
    # ----------------------------

    if (
        old_price
        and old_price > price
    ):

        discount_percent = round(
            (
                (old_price - price)
                / old_price
            )
            * 100
        )

        message = (
            "⚡️ <b>FIRSAT ANLIK</b>\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            "📉 Fiyat düştü\n"
            f"❌ <s>{old_price:,.2f} TL</s>\n"
            f"✅ <b>{price:,.2f} TL</b>\n"
            f"🔥 <b>%{discount_percent} indirim</b>\n\n"
            f'👉 <a href="{html.escape(link)}">'
            "Fırsata Git"
            "</a>"
        )

    # ----------------------------
    # ESKİ FİYAT YOKSA
    # ----------------------------

    else:

        message = (
            "⚡️ <b>FIRSAT ANLIK</b>\n\n"
            f"📦 <b>{safe_title}</b>\n\n"
            f"💰 <b>{price:,.2f} TL</b>\n\n"
            f'👉 <a href="{html.escape(link)}">'
            "Fırsata Git"
            "</a>"
        )

    telegram_url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:

        response = std_requests.post(
            telegram_url,
            json=payload,
            timeout=30
        )

        result = response.json()

        if result.get("ok"):
            print(
                "✅ Telegram mesajı gönderildi."
            )
            return True

        print(
            "❌ Telegram hatası:",
            result
        )

        return False

    except Exception as e:

        print(
            "❌ Telegram bağlantı hatası:",
            str(e)
        )

        return False


# ============================================================
# BOTU ÇALIŞTIR
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
        f"Bulunan fırsat sayısı: "
        f"{len(deals)}"
    )

    if not deals:

        print(
            "Bu taramada paylaşılacak "
            "ürün bulunamadı."
        )

        return

    for deal in deals:

        deal_id = deal["id"]

        if is_already_posted(
            deal_id
        ):

            print(
                "Atlandı "
                "(Zaten Paylaşıldı):",
                deal_id
            )

            continue

        success = send_telegram_deal(
            deal
        )

        if success:

            record_posted_deal(
                deal_id,
                deal["title"],
                deal["price"]
            )

            increment_daily_count()

            print(
                "✅ Paylaşıldı:",
                deal["title"]
            )

            # Her taramada şimdilik
            # yalnızca 1 ürün paylaş
            break


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":
    run_bot()
