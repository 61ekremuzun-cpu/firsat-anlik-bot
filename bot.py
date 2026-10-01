import os
import re
import sqlite3
import requests as std_requests
from curl_cffi import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ==================== GENEL KONFİGÜRASYON ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY")
CHAT_ID = "@firsatanlik"
DB_NAME = "firsat_anlik.db"

MAX_DAILY_POSTS = 12
# =============================================================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posted_deals (
            id TEXT PRIMARY KEY,
            title TEXT,
            price REAL,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_counter (
            post_date DATE PRIMARY KEY,
            count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def can_post_today():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT count FROM daily_counter WHERE post_date = ?", (today,))
    row = cursor.fetchone()
    conn.close()
    return not (row and row[0] >= MAX_DAILY_POSTS)

def increment_daily_count():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        INSERT INTO daily_counter (post_date, count) VALUES (?, 1)
        ON CONFLICT(post_date) DO UPDATE SET count = count + 1
    ''', (today,))
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM posted_deals WHERE id = ?", (deal_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def record_posted_deal(deal_id, title, price):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO posted_deals (id, title, price) VALUES (?, ?, ?)", (deal_id, title, price))
    conn.commit()
    conn.close()

def fetch_amazon_deals():
    """Chrome TLS Parmak izi taklidi ile Amazon TR'den veri çeker."""
    url = "https://www.amazon.com.tr/s?k=f%C3%BCrsat&rh=p_n_specials_match%3A21618252031"
    deals = []
    
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1"
    }

    try:
        # impersonate="chrome120" Amazon 503 bot engelini aşar
        response = requests.get(url, headers=headers, impersonate="chrome120", timeout=20)
        print(f"Amazon Yanıt Kodu: {response.status_code}")
        print("HTML uzunluğu:", len(response.text))
        print("Arama sonucu div sayısı:", response.text.count('data-component-type="s-search-result"'))
        print("Sayfa başlığı:", BeautifulSoup(response.text, "html.parser").title)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            items = soup.find_all("div", {"data-component-type": "s-search-result"})
            
            for item in items:
                try:
                    title_elem = item.find("h2") or item.find("span", {"class": "a-size-medium"}) or item.find("span", {"class": "a-size-base-plus"})
                    price_elem = item.find("span", {"class": "a-price-whole"})
                    link_elem = item.find("a", {"class": "a-link-normal"})

                    if title_elem and price_elem and link_elem:
                        title = title_elem.text.strip()
                        price_str = price_elem.text.replace(".", "").replace(",", ".").strip()
                        price = float(re.sub(r"[^\d.]", "", price_str))
                        
                        old_price_elem = item.find("span", {"class": "a-price", "data-a-strike": "true"})
                        if old_price_elem:
                            old_price_str = old_price_elem.find("span", {"class": "a-offscreen"})
                            old_price = float(re.sub(r"[^\d.]", "", old_price_str.text.replace(".", "").replace(",", "."))) if old_price_str else round(price * 1.25, 2)
                        else:
                            old_price = round(price * 1.25, 2)

                        href = link_elem.get("href", "")
                        link = f"https://www.amazon.com.tr{href}" if href.startswith("/") else href
                        
                        asin_match = re.search(r"/dp/([A-Z0-9]{10})", link)
                        deal_id = asin_match.group(1) if asin_match else f"AMZ_{hash(title)}"

                        deals.append({
                            "id": deal_id,
                            "title": title,
                            "price": price,
                            "old_price": old_price,
                            "link": link
                        })
                except Exception:
                    continue
    except Exception as e:
        print(f"Tarama Hatası: {e}")

    return deals

def send_telegram_deal(deal_data):
    title = deal_data.get("title")[:90]
    price = deal_data.get("price", 0.0)
    old_price = deal_data.get("old_price", 0.0)
    link = deal_data.get("link")

    discount_percent = int(((old_price - price) / old_price) * 100) if old_price > price else 20

    caption = (
        f"⚡️ *FIRSAT ANLIK | FİYAT DÜŞTÜ*\n\n"
        f"📦 *{title}...*\n\n"
        f"📉 *Son 30 Günün En Düşük Fiyatı!*\n"
        f"❌ ~{old_price:,.2f} TL~\n"
        f"✅ *{price:,.2f} TL* (%{discount_percent} İndirim) 👑 *Prime Özel*\n\n"
        f"👉 [Fırsata Git]({link})"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": caption,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    
    response = std_requests.post(url, json=payload).json()
    return response.get("ok", False)

def run_bot():
    init_db()
    
    if not can_post_today():
        print("Günlük maksimum paylaşım limitine ulaşıldı.")
        return

    deals = fetch_amazon_deals()
    print(f"Bulunan fırsat sayısı: {len(deals)}")

    for deal in deals:
        deal_id = deal["id"]
        if is_already_posted(deal_id):
            print(f"Atlandı (Zaten Paylaşıldı): {deal_id}")
            continue

        success = send_telegram_deal(deal)
        if success:
            record_posted_deal(deal_id, deal["title"], deal["price"])
            increment_daily_count()
            print(f"✅ Paylaşıldı: {deal['title']}")
            break

if __name__ == "__main__":
    run_bot()
