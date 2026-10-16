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
SAFE_HOSTS = ("trendyol.com", "hepsiburada.com", "amazon.com.tr", "n11.com")


def log(msg: str) -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------- HTTP ----

class Blocked(Exception):
    """Site isteği reddetti (Cloudflare, 403, 429...)."""


_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            raw = http_get(base + "/robots.txt", check_robots=False, accept="text/plain")
            rp.parse(raw.splitlines())
            _robots_cache[base] = rp
        except Exception as e:  # robots.txt yoksa/okunamazsa standart: izinli
            log(f"  robots.txt okunamadı ({base}): {e}")
            _robots_cache[base] = None
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
    for m in re.finditer(r'(?:__[A-Z0-9_\-]+__(?:PROPS)?["\']?\]?)\s*=\s*', html):
        start = m.end()
        if start < len(html) and html[start] in "{[":
            try:
                obj, _ = dec.raw_decode(html, start)
                yield obj
            except Exception:
                pass


# ------------------------------------------------------------ Trendyol ----

TY_SEARCH_APIS = [
    "https://apigw.trendyol.com/discovery-web-searchgw-service/v2/api/infinite-scroll/sr",
    "https://public.trendyol.com/discovery-web-searchgw-service/v2/api/infinite-scroll/sr",
]
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


def scrape_trendyol(cfg: dict, delay: float) -> tuple[list[dict], dict]:
    products: dict[str, dict] = {}
    working = None  # ilk çalışan yöntemi hatırla, gereksiz istek atma
    errors = []
    for item in cfg.get("queries", []):
        q, cat = item["q"], item.get("category", "Diğer")
        params = {"q": q, "qt": q, "st": q, "os": "1", "pi": "1", "culture": "tr-TR", "channelId": "1"}
        attempts = []
        for api in TY_SEARCH_APIS:
            attempts.append(("api", api + "?" + urllib.parse.urlencode(params)))
        attempts.append(("html", TY_WEB + "/sr?" + urllib.parse.urlencode({"q": q, "os": "1"})))
        if working:
            attempts.sort(key=lambda a: a[1].split("?")[0] != working)
        found = []
        for kind, url in attempts:
            try:
                text = http_get(url)
                time.sleep(delay)
                if kind == "api":
                    found = ty_parse_any(json.loads(text), cat)
                else:
                    found = []
                    for blob in json_blobs_from_html(text):
                        found.extend(ty_parse_any(blob, cat))
                if found:
                    working = url.split("?")[0]
                    log(f"  '{q}': {len(found)} ürün ({kind}: {working})")
                    break
                log(f"  '{q}': {kind} yanıt verdi ama ürün çıkmadı ({url.split('?')[0]})")
            except Blocked as e:
                log(f"  '{q}': engellendi - {e} ({url.split('?')[0]})")
                errors.append(str(e))
                time.sleep(delay)
            except Exception as e:
                log(f"  '{q}': hata - {type(e).__name__}: {e}")
                errors.append(f"{type(e).__name__}: {e}")
                time.sleep(delay)
        for p in found:
            products.setdefault(p["id"], p)
    status = {
        "ok": bool(products),
        "count": len(products),
        "method": working,
        "error": None if products else (errors[-1] if errors else "Ürün bulunamadı"),
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
    stores_failed = {name for name, s in statuses.items() if not s["ok"]}
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
    args = ap.parse_args()

    cfg = load_json(os.path.join(HERE, "sources.json"), {})
    delay = float(cfg.get("request_delay_seconds", 3))
    statuses, products = {}, []

    if args.fixture:
        with open(args.fixture, encoding="utf-8") as f:
            text = f.read()
        try:
            found = ty_parse_any(json.loads(text), "Test")
        except json.JSONDecodeError:
            found = [p for b in json_blobs_from_html(text) for p in ty_parse_any(b, "Test")]
        products += found
        statuses["trendyol"] = {"ok": bool(found), "count": len(found), "method": "fixture", "error": None}
    elif cfg.get("trendyol", {}).get("enabled"):
        log("Trendyol taranıyor...")
        found, st = scrape_trendyol(cfg["trendyol"], delay)
        products += found
        statuses["trendyol"] = st

    out = build_output(products, statuses, args.out, cfg)
    log(f"\nToplam ürün: {len(products)}, indirim: {len(out['deals'])}")
    for name, s in statuses.items():
        log(f"  {name}: {'OK' if s['ok'] else 'HATA'} - {s['count']} ürün - {s.get('error') or ''}")
    # Hiçbir kaynak çalışmadıysa iş akışı kırmızı görünsün
    return 0 if any(s["ok"] for s in statuses.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
