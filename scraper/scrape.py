#!/usr/bin/env python3
"""
İndirim Avcısı - indirim tarayıcısı.

Güvenilir mağazaların ürün listelerine nazikçe bakar (istekler arası bekleme,
robots.txt kurallarına uyma, açık kimlikli User-Agent), indirimli ürünleri
bulur ve uygulamanın okuyacağı deals.json dosyasını yazar.

Kullanım:
    python scrape.py --out ../data          # gerçek tarama
    python scrape.py --out /tmp/x --fixture ornek.json   # çevrimdışı test

Sadece Python standart kütüphanesi kullanılır.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = (
    "IndirimAvcisiBot/0.1 (+https://github.com/Enskygr234/indirim-avcisi; "
    "30 dakikada bir, sadece urun listeleri)"
)
HISTORY_POINTS = 120          # ürün başına saklanan fiyat ölçümü
HISTORY_KEEP_DAYS = 45        # bu kadar gündür görülmeyen ürün geçmişten silinir

# Uygulamanın açmasına izin verilen güvenli mağaza alan adları
SAFE_HOSTS = ("trendyol.com", "hepsiburada.com", "amazon.com.tr", "n11.com", "mediamarkt.com.tr")


def log(msg: str) -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------- HTTP ----

class Blocked(Exception):
    """Site isteği reddetti (Cloudflare, 403, 429...)."""


_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
ROBOTS_NOTES: dict[str, str] = {}  # site -> robots.txt durumu (teşhis için)


def robots_allows(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            raw = http_get(base + "/robots.txt", check_robots=False, accept="text/plain")
            rp.parse(raw.splitlines())
            _robots_cache[base] = rp
            ROBOTS_NOTES[base] = f"robots.txt okundu ({len(raw)} bayt)"
        except Blocked as e:  # robots.txt bile reddedildiyse siteye hiç girme
            log(f"  robots.txt reddedildi ({base}): {e}")
            rp = urllib.robotparser.RobotFileParser()
            rp.disallow_all = True
            _robots_cache[base] = rp
            ROBOTS_NOTES[base] = f"robots.txt isteği reddedildi ({e}); site botlara kapalı sayıldı"
        except Exception as e:  # robots.txt yoksa standart: izinli
            log(f"  robots.txt okunamadı ({base}): {e}")
            _robots_cache[base] = None
            ROBOTS_NOTES[base] = f"robots.txt okunamadı ({e}); izinli sayıldı"
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def http_get(url: str, *, check_robots: bool = True, accept: str = "application/json, text/html") -> str:
    if check_robots and not robots_allows(url):
        raise Blocked(f"robots.txt bu adrese izin vermiyor: {url}")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "tr-TR,tr;q=0.9",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            text = data.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429, 503):
            raise Blocked(f"HTTP {e.code}") from e
        raise
    low = text[:4000].lower()
    if "just a moment" in low or "cf-chl" in low or "challenge-platform" in low:
        raise Blocked("Cloudflare doğrulama sayfası döndü")
    return text


# -------------------------------------------------------- JSON yardımcı ----

def to_number(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, dict):
        for k in ("value", "amount", "price"):
            if k in v:
                return to_number(v[k])
        return None
    if isinstance(v, str):
        s = re.sub(r"[^\d,\.]", "", v)
        if not s:
            return None
        # Türkçe biçim: 1.299,99
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", s):
            s = s.replace(".", "")  # 1.299 veya 1.299.999 -> binlik ayracı
        try:
            n = float(s)
            return n if n > 0 else None
        except ValueError:
            return None
    return None


CURRENT_KEYS = ("discountedPrice", "sellingPrice", "current", "salePrice", "finalPrice", "value")
OLD_KEYS = ("originalPrice", "oldPrice", "marketPrice", "listPrice", "strikethroughPrice")


def extract_prices(prod: dict) -> tuple[float | None, float | None]:
    """(güncel fiyat, eski fiyat) döndürür."""
    pools = []
    p = prod.get("price")
    if isinstance(p, dict):
        pools.append(p)
    pools.append(prod)
    current, old = [], []
    for pool in pools:
        for k in CURRENT_KEYS:
            n = to_number(pool.get(k)) if k in pool else None
            if n:
                current.append(n)
        for k in OLD_KEYS:
            n = to_number(pool.get(k)) if k in pool else None
            if n:
                old.append(n)
    if not current and isinstance(p, (int, float, str)):
        n = to_number(p)
        if n:
            current.append(n)
    cur = min(current) if current else None
    o = max(old) if old else None
    return cur, o


def walk(obj):
    """JSON ağacındaki tüm sözlükleri gezer."""
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            yield x
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


def json_blobs_from_html(html: str):
    """HTML içine gömülü JSON verilerini çıkarır."""
    dec = json.JSONDecoder()
    # <script type="application/json"> ve JSON-LD
    for m in re.finditer(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', html, re.S):
        try:
            yield json.loads(m.group(1))
        except Exception:
            pass
    # window.__SOMETHING__ = {...}
    for m in re.finditer(r'window(?:\[["\'][^"\']+["\']\]|\.\w+)\s*=\s*', html):
        start = m.end()
        if start < len(html) and html[start] in "{[":
            try:
                obj, _ = dec.raw_decode(html, start)
                yield obj
            except Exception:
                pass


# ------------------------------------------------------------ Trendyol ----

TY_WEB = "https://www.trendyol.com"
TY_CDN = "https://cdn.dsmcdn.com"


def ty_product_from(d: dict, category: str) -> dict | None:
    name = d.get("name")
    url = d.get("url")
    pid = d.get("id") or d.get("contentId") or d.get("productId")
    if not (isinstance(name, str) and isinstance(url, str) and pid):
        return None
    if "-p-" not in url:
        return None
    price, old = extract_prices(d)
    if not price:
        return None
    brand = d.get("brand")
    brand = brand.get("name") if isinstance(brand, dict) else (brand if isinstance(brand, str) else "")
    img = None
    imgs = d.get("images")
    if isinstance(imgs, list) and imgs:
        img = imgs[0] if isinstance(imgs[0], str) else (imgs[0] or {}).get("url")
    img = img or d.get("imageUrl") or d.get("image")
    if isinstance(img, str) and img.startswith("/"):
        img = TY_CDN + img
    full_url = url if url.startswith("http") else TY_WEB + url
    full_url = full_url.split("?")[0]
    return {
        "id": f"ty-{pid}",
        "store": "Trendyol",
        "title": name.strip(),
        "brand": (brand or "").strip(),
        "price": round(price, 2),
        "oldPrice": round(old, 2) if old else None,
        "image": img if isinstance(img, str) and img.startswith("http") else None,
        "url": full_url,
        "category": category,
    }


def ty_parse_any(obj, category: str) -> list[dict]:
    out, seen = [], set()
    for d in walk(obj):
        p = ty_product_from(d, category)
        if p and p["id"] not in seen:
            seen.add(p["id"])
            out.append(p)
    return out


# ------------------------------------------------- Diğer mağazalar ----

G_NAME_KEYS = ("name", "title", "productName", "displayName")
G_URL_KEYS = ("url", "productUrl", "link", "uri", "href", "detailUrl")
G_CURRENT_KEYS = ("discountedPrice", "sellingPrice", "current", "salePrice", "finalPrice",
                  "price", "amount", "lowPrice", "displayPrice", "currentPrice")
G_OLD_KEYS = ("originalPrice", "oldPrice", "old", "marketPrice", "listPrice", "strikethroughPrice",
              "strikeThroughPrice", "priceBeforeDiscount", "originalAmount", "insteadOfPrice")
G_IMG_KEYS = ("image", "imageUrl", "img", "thumbnail", "imageURL", "images", "picture")


def g_prices(d: dict) -> tuple[float | None, float | None]:
    pools = [d]
    for k in ("price", "prices", "offers", "priceInfo", "pricing"):
        v = d.get(k)
        if isinstance(v, dict):
            pools.append(v)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            pools.append(v[0])
    current, old = [], []
    for pool in pools:
        for k in G_CURRENT_KEYS:
            if k in pool and not isinstance(pool[k], (dict, list)):
                n = to_number(pool[k])
                if n:
                    current.append(n)
            elif k in pool and isinstance(pool[k], dict) and "value" in pool[k]:
                n = to_number(pool[k])
                if n:
                    current.append(n)
        for k in G_OLD_KEYS:
            if k in pool:
                n = to_number(pool[k])
                if n:
                    old.append(n)
    cur = min(current) if current else None
    o = max(old) if old else None
    return cur, o


def g_first_str(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        return g_first_str(v[0])
    if isinstance(v, dict):
        for k in ("url", "src", "link", "contentUrl"):
            if isinstance(v.get(k), str):
                return v[k]
    return None


def generic_product_from(d: dict, store: dict, category: str) -> dict | None:
    name = next((d[k] for k in G_NAME_KEYS if isinstance(d.get(k), str) and len(d[k]) > 3), None)
    if not name:
        return None
    url = next((d[k] for k in G_URL_KEYS if isinstance(d.get(k), str)), None)
    if not url and isinstance(d.get("offers"), dict):
        url = d["offers"].get("url")
    if not isinstance(url, str):
        return None
    m = re.search(store["product_url_regex"], url)
    if not m:
        return None
    price, old = g_prices(d)
    if not price:
        return None
    base = store["base"]
    full_url = url if url.startswith("http") else urllib.parse.urljoin(base + "/", url.lstrip("/"))
    full_url = full_url.split("#")[0]
    if not store.get("keep_query"):
        full_url = full_url.split("?")[0]
    img = None
    for k in G_IMG_KEYS:
        img = g_first_str(d.get(k))
        if img:
            break
    if img and img.startswith("//"):
        img = "https:" + img
    brand = d.get("brand") or d.get("brandName") or ""
    if isinstance(brand, dict):
        brand = brand.get("name", "")
    return {
        "id": f"{store['key']}-{m.group(1)}",
        "store": store["name"],
        "title": re.sub(r"\s+", " ", name).strip(),
        "brand": brand.strip() if isinstance(brand, str) else "",
        "price": round(price, 2),
        "oldPrice": round(old, 2) if old else None,
        "image": img if isinstance(img, str) and img.startswith("https://") else None,
        "url": full_url,
        "category": category,
    }


# ---------------------------------------------------------- MediaMarkt ----

def mm_prices(html: str) -> dict[str, tuple[float, float | None]]:
    """MediaMarkt: ürün no -> (güncel fiyat, üstü çizili fiyat).

    Liste sayfasındaki JSON-LD sadece güncel fiyatı verir; eski fiyat
    window.__PRELOADED_STATE__ içindeki CofrPriceFeature kayıtlarındadır.
    """
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*", html)
    if not m:
        return {}
    end = html.find("</script>", m.end())
    raw = html[m.end():end].rstrip().rstrip(";")
    raw = re.sub(r"(?<=[:\[,])undefined(?=[,\]}])", "null", raw)  # JS -> JSON
    try:
        state = json.loads(raw)
    except ValueError:
        return {}
    out = {}
    for d in walk(state):
        if d.get("__typename") != "CofrPriceFeature" or not isinstance(d.get("id"), str):
            continue
        pid = d["id"].rsplit(":", 1)[-1]
        price = to_number((d.get("price") or {}).get("amount"))
        strike = d.get("strikePrice") or {}
        old = to_number(strike.get("amount")) if strike.get("shouldBeStruck", True) else None
        if price:
            out[pid] = (price, old)
    return out


def parse_page(html: str, store: dict, category: str) -> list[dict]:
    out: dict[str, dict] = {}
    for blob in json_blobs_from_html(html):
        for d in walk(blob):
            if store["key"] == "trendyol":
                p = ty_product_from(d, category)
            else:
                p = generic_product_from(d, store, category)
            if not p:
                continue
            prev = out.get(p["id"])
            # Aynı ürün birden çok yerde geçebilir: eski fiyatı olan kaydı tercih et
            if prev is None or (not prev.get("oldPrice") and p.get("oldPrice")):
                out[p["id"]] = p
    if store["key"] == "mediamarkt":
        prices = mm_prices(html)
        for p in out.values():
            pid = p["id"].split("-", 1)[1]
            if pid in prices:
                p["price"], old = (round(x, 2) if x else None for x in prices[pid])
                p["oldPrice"] = old if old and old > p["price"] else None
    return list(out.values())


def write_debug(out_dir: str, label: str, html: str, store: dict) -> None:
    """Ürün bulunamayan sayfanın yapısını özetler (sayfanın tamamını kaydetmez)."""
    os.makedirs(out_dir, exist_ok=True)
    lines = [f"PATH {label} LEN {len(html)}", "HEAD " + html[:1500].replace("\n", " ")]
    lines += ["SCRIPT " + m.group(0)[:160] for m in re.finditer(r"<script[^>]*>", html)][:60]
    lines += ["WINVAR " + m.group(0) for m in re.finditer(r"window\[?[\"']?[\w\-\.]+[\"']?\]?\s*=", html)][:40]
    for i, m in enumerate(re.finditer(store["product_url_regex"], html)):
        if i >= 4:
            break
        s = max(0, m.start() - 900)
        lines.append(f"CTX{i} " + html[s:m.end() + 900].replace("\n", " "))
    name = re.sub(r"\W+", "_", store["key"] + label).strip("_")[:60]
    with open(os.path.join(out_dir, f"debug_{name}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def scrape_store(store: dict, delay: float) -> tuple[list[dict], dict]:
    """Bir mağazanın robots.txt'in izin verdiği liste sayfalarını tarar."""
    products: dict[str, dict] = {}
    log_lines = []
    per_cat = int(store.get("pages_per_category", 1))
    pparam = store.get("page_param", "page")
    for item in store.get("pages", []):
        path, cat = item["path"], item.get("category", "Diğer")
        for page in range(1, per_cat + 1):
            sep = "&" if "?" in path else "?"
            url = store["base"] + path + (f"{sep}{pparam}={page}" if page > 1 else "")
            label = path + (f" s{page}" if page > 1 else "")
            try:
                text = http_get(url, accept="text/html")
                found = parse_page(text, store, cat)
                msg = f"{label}: {len(found)} ürün" + ("" if found else f" (sayfa {len(text)} bayt, ürün verisi bulunamadı)")
                if not found and os.environ.get("SCRAPER_DEBUG_DIR"):
                    write_debug(os.environ["SCRAPER_DEBUG_DIR"], label, text, store)
            except Blocked as e:
                found, msg = [], f"{label}: engellendi - {e}"
            except Exception as e:
                found, msg = [], f"{label}: hata - {type(e).__name__}: {e}"
            log("  " + msg)
            log_lines.append(msg)
            new = 0
            for p in found:
                if p["id"] not in products:
                    products[p["id"]] = p
                    new += 1
            time.sleep(delay)
            if new == 0:  # sayfa boş ya da aynı ürünler: sonraki sayfalara gerek yok
                break
            if "robots.txt" in msg:
                break
    with_old = sum(1 for p in products.values() if p.get("oldPrice"))
    status = {
        "ok": bool(products),
        "count": len(products),
        "withOldPrice": with_old,
        "error": None if products else (log_lines[-1] if log_lines else "Sayfa tanımlı değil"),
        "robots": ROBOTS_NOTES.get(store["base"]),
        "log": log_lines,  # teşhis için: her sayfada ne oldu
    }
    return list(products.values()), status


# --------------------------------------------------------------- Çıktı ----

def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_output(products: list[dict], statuses: dict, out_dir: str, cfg: dict) -> dict:
    now = datetime.now(timezone.utc)
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    hist_path = os.path.join(out_dir, "history.json")
    deals_path = os.path.join(out_dir, "deals.json")
    history = load_json(hist_path, {})
    previous = load_json(deals_path, {})
    prev_by_id = {d["id"]: d for d in previous.get("deals", [])}

    # Fiyat geçmişini güncelle
    for p in products:
        h = history.setdefault(p["id"], {"firstSeen": now_s, "points": []})
        pts = h["points"]
        if not pts or pts[-1][1] != p["price"]:
            pts.append([now_s, p["price"]])
        h["lastSeen"] = now_s
        del pts[:-HISTORY_POINTS]
    cutoff = (now - timedelta(days=HISTORY_KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = {k: v for k, v in history.items() if v.get("lastSeen", "") >= cutoff}

    min_pct = cfg.get("min_discount_percent", 10)
    deals = []
    for p in products:
        if not p.get("oldPrice") or p["oldPrice"] <= p["price"]:
            continue
        pct = round((1 - p["price"] / p["oldPrice"]) * 100)
        if pct < min_pct or pct > 95:  # %95 üstü genelde hatalı veri
            continue
        h = history.get(p["id"], {})
        prices = [pt[1] for pt in h.get("points", [])]
        d = dict(p)
        d["discount"] = pct
        d["lowestSeen"] = min(prices) if prices else p["price"]
        d["firstSeen"] = prev_by_id.get(p["id"], {}).get("firstSeen", now_s)
        deals.append(d)

    # Bir mağaza bu turda başarısız olduysa eski indirimlerini koru
    stores_failed = {name.lower() for name, s in statuses.items() if not s["ok"]}
    for old in previous.get("deals", []):
        if old.get("store", "").lower() in stores_failed:
            deals.append(old)

    deals.sort(key=lambda d: (-d["discount"], d["price"]))
    deals = deals[: cfg.get("max_deals", 600)]

    for d in deals:
        host = urllib.parse.urlsplit(d["url"]).hostname or ""
        assert host.endswith(SAFE_HOSTS), f"güvensiz adres: {d['url']}"

    out = {
        "version": 1,
        "updatedAt": now_s,
        "sources": statuses,
        "deals": deals,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(deals_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="deals.json'un yazılacağı klasör")
    ap.add_argument("--fixture", help="ağ yerine bu JSON/HTML dosyasını kullan (test)")
    ap.add_argument("--store", help="--fixture ile: hangi mağazanın ayrıştırıcısı (varsayılan trendyol)")
    args = ap.parse_args()

    cfg = load_json(os.path.join(HERE, "sources.json"), {})
    delay = float(cfg.get("request_delay_seconds", 3))
    statuses, products = {}, []

    stores = cfg.get("stores", [])
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as f:
            text = f.read()
        store = next((st for st in stores if st["key"] == (args.store or "trendyol")), None)
        if text.lstrip().startswith(("{", "[")) and store["key"] == "trendyol":
            found = ty_parse_any(json.loads(text), "Test")
        else:
            found = parse_page(text if not text.lstrip().startswith(("{", "["))
                               else f'<script type="application/json">{text}</script>', store, "Test")
        products += found
        statuses[store["name"]] = {"ok": bool(found), "count": len(found), "error": None}
    else:
        for store in stores:
            if not store.get("enabled", True):
                continue
            log(f"{store['name']} taranıyor...")
            found, st = scrape_store(store, delay)
            products += found
            statuses[store["name"]] = st

    out = build_output(products, statuses, args.out, cfg)
    log(f"\nToplam ürün: {len(products)}, indirim: {len(out['deals'])}")
    for name, s in statuses.items():
        log(f"  {name}: {'OK' if s['ok'] else 'HATA'} - {s['count']} ürün - {s.get('error') or ''}")
    # Hiçbir kaynak çalışmadıysa iş akışı kırmızı görünsün
    return 0 if any(s["ok"] for s in statuses.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
