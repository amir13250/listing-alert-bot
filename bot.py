# bot.py
import os, re, json, hashlib
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

# ---------- تنظیمات ----------
RECENT_HOURS = int(os.getenv("RECENT_HOURS", "6"))   # فقط اعلان‌های <= این بازه
TIMEOUT = 20
HEADERS = {"User-Agent": "ListingBot/1.1 (+https://github.com/your/repo)"}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

SEEN_PATH = "seen.json"

# کلیدواژه‌ها برای تشخیص «لیست جدید»
KEYWORDS = (
    "will list",
    "will be listed",
    "new listing",
    "lists",              # Toobit غالباً "Toobit lists <TOKEN> ..." می‌نویسد
    "initial listing",    # KCEX
    "perpetual contract", # اگر لیست فیوچرز باشد
)

def now_utc():
    return datetime.now(timezone.utc)

def load_seen():
    if os.path.exists(SEEN_PATH):
        try:
            return set(json.load(open(SEEN_PATH, "r", encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    try:
        json.dump(sorted(list(seen)), open(SEEN_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        print("save_seen error:", e)

def http_get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def extract_iso_datetime_from_html(html, fallback_tz=timezone.utc):
    """
    تاریخ/زمان انتشار را از صفحه حدس می‌زنیم:
    1) <time datetime="...">
    2) متای article:published_time / pubdate / date
    3) regex روی متن (YYYY-MM-DD HH:MM یا YYYY-MM-DD)
    """
    soup = BeautifulSoup(html, "lxml")

    t = soup.select_one("time[datetime]")
    if t and t.has_attr("datetime"):
        try:
            return datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
        except Exception:
            pass

    for sel in ["meta[property='article:published_time']",
                "meta[name='pubdate']",
                "meta[name='date']",
                "meta[property='og:updated_time']",
                "meta[name='lastmod']"]:
        m = soup.select_one(sel)
        if m and m.get("content"):
            try:
                c = m["content"].strip()
                if len(c) == 10:  # YYYY-MM-DD
                    return datetime.fromisoformat(c + "T00:00:00+00:00")
                return datetime.fromisoformat(c.replace("Z", "+00:00"))
            except Exception:
                continue

    text = soup.get_text(" ", strip=True)
    m = re.search(r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=fallback_tz)
        except Exception:
            pass
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=fallback_tz)
        except Exception:
            pass
    return None

def matches_keywords(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in KEYWORDS)

# ---------- منابع: CoinEx ----------
def fetch_coinex_new_listings(max_articles=15):
    base = "https://coinex-announcement.zendesk.com"
    section_url = f"{base}/hc/en-us/sections/360003716631-New-Listing"
    html = http_get(section_url)
    soup = BeautifulSoup(html, "lxml")
    items = []
    for a in soup.select("a[href*='/hc/'][href*='/articles/']"):
        title = a.get_text(" ", strip=True)
        if not title or not matches_keywords(title):
            continue
        href = a.get("href");  link = href if href.startswith("http") else (base + href)
        items.append({"exchange": "CoinEx", "title": title, "url": link})
        if len(items) >= max_articles: break

    out = []
    for it in items:
        try:
            detail = http_get(it["url"])
            dt = extract_iso_datetime_from_html(detail)
            if dt: it["published_at"] = dt; out.append(it)
        except Exception as e:
            print("coinex detail err:", e)
    return out

# ---------- منابع: LBank ----------
def fetch_lbank_new_listings(max_articles=20):
    roots = [
        "https://www.lbank.com/support/announcement",
        "https://www.lbank.com/support/sections/CO00000044",  # New Listing section
    ]
    seen_urls = set()
    items = []
    for root in roots:
        try:
            html = http_get(root)
        except Exception as e:
            print("lbank root err:", e); continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/support/']"):
            title = a.get_text(" ", strip=True)
            if not title or not matches_keywords(title): continue
            href = a.get("href"); url = href if href.startswith("http") else ("https://www.lbank.com" + href)
            if url in seen_urls: continue
            seen_urls.add(url)
            items.append({"exchange": "LBank", "title": title, "url": url})
            if len(items) >= max_articles: break

    out = []
    for it in items:
        try:
            detail = http_get(it["url"])
            dt = extract_iso_datetime_from_html(detail)
            if dt: it["published_at"] = dt; out.append(it)
        except Exception as e:
            print("lbank detail err:", e)
    return out

# ---------- منابع: Toobit ----------
def fetch_toobit_new_listings(max_articles=30):
    roots = [
        "https://support.toobit.com/hc/en-us/sections/13177993830553-New-Listings",
        "https://support.toobit.com/hc/en-us/categories/13177471185817-Announcements",
    ]
    items = []
    seen_urls = set()
    for root in roots:
        try:
            html = http_get(root)
        except Exception as e:
            print("toobit root err:", e); continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            title = a.get_text(" ", strip=True)
            if not title or not matches_keywords(title): continue
            href = a.get("href").strip()
            if href.startswith("//"): href = "https:" + href
            if not href.startswith("http"):
                url = ("https://support.toobit.com" + href) if href.startswith("/") else ("https://support.toobit.com/" + href)
            else:
                url = href
            if url in seen_urls: continue
            seen_urls.add(url)
            items.append({"exchange": "Toobit", "title": title, "url": url})
            if len(items) >= max_articles: break

    out = []
    for it in items:
        try:
            detail = http_get(it["url"])
            dt = extract_iso_datetime_from_html(detail)
            if not dt:
                dt = now_utc()  # اگر پیدا نشد، موقتاً الان
            it["published_at"] = dt
            out.append(it)
        except Exception as e:
            print("toobit detail err:", e)
    return out

# ---------- منابع: KCEX ----------
def fetch_kcex_new_listings(max_articles=30):
    roots = [
        "https://www.kcex.com/support/categories/25313105314073",   # Listing Information
        "https://www.kcex.com/support/categories/25312191952921",   # Latest Announcements
    ]
    items = []
    seen_urls = set()
    for root in roots:
        try:
            html = http_get(root)
        except Exception as e:
            print("kcex root err:", e); continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/support/articles/']"):
            title = a.get_text(" ", strip=True)
            if not title or not matches_keywords(title): continue
            href = a.get("href")
            url = href if href.startswith("http") else ("https://www.kcex.com" + href)
            if url in seen_urls: continue
            seen_urls.add(url)
            items.append({"exchange": "KCEX", "title": title, "url": url})
            if len(items) >= max_articles: break

    out = []
    for it in items:
        try:
            detail = http_get(it["url"])
            dt = extract_iso_datetime_from_html(detail)
            it["published_at"] = dt if dt else now_utc()
            out.append(it)
        except Exception as e:
            print("kcex detail err:", e)
    return out

# ---------- ارسال تلگرام ----------
def send_telegram(messages):
    if not messages: return
    token = TELEGRAM_BOT_TOKEN; chat_id = TELEGRAM_CHAT_ID
    if not token or not chat_id:
        print("TELEGRAM secrets missing"); return
    text = "\n\n".join(messages)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=data, timeout=TIMEOUT)
        print("Telegram status:", r.status_code, r.text[:200])
    except Exception as e:
        print("telegram err:", e)

# ---------- منطق اصلی ----------
def main():
    seen = load_seen()
    cutoff = now_utc() - timedelta(hours=RECENT_HOURS)
    def is_recent(d): return (d and d >= cutoff)

    new_items = []
    for fetch in (
        fetch_coinex_new_listings,
        fetch_lbank_new_listings,
        fetch_toobit_new_listings,
        fetch_kcex_new_listings,
    ):
        try:
            res = fetch()
            for it in res:
                if not is_recent(it.get("published_at")):
                    continue
                uid = hashlib.sha256(it["url"].encode()).hexdigest()
                if uid in seen:
                    continue
                seen.add(uid)
                new_items.append(it)
        except Exception as e:
            print("source err:", e)

    new_items.sort(key=lambda x: x["published_at"], reverse=True)

    messages = []
    for it in new_items:
        ts = it["published_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        messages.append(
            f"🚨 <b>New Listing Alert</b>\n"
            f"🏦 Exchange: <b>{it['exchange']}</b>\n"
            f"🧾 {it['title']}\n"
            f"🕒 {ts}\n"
            f"🔗 {it['url']}"
        )

    if messages:
        send_telegram(messages)
    else:
        print("No new listings in the last", RECENT_HOURS, "hours.")

    save_seen(seen)

if __name__ == "__main__":
    main()
