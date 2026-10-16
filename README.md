# İndirim Avcısı

Güvenilir mağazalardaki indirimleri toplayıp kendi tasarımıyla gösteren Android uygulaması.

**APK indir:** https://github.com/Enskygr234/indirim-avcisi/releases/download/son-surum/indirim-avcisi.apk

Kurarken telefon "bilinmeyen kaynak" izni isterse, sadece bu kurulum için izin ver.

## Nasıl çalışır

```
GitHub Actions (30 dk'da bir)          Telefon
  scraper/scrape.py  ──►  data dalı  ──►  uygulama listeyi çeker
  (Trendyol ürün listeleri)  deals.json      ve gösterir
```

- **Tarayıcı** (`scraper/`): Mağazaların ürün listelerine nazikçe bakar. İstekler arasında bekler, `robots.txt` kurallarına uyar, kim olduğunu açıkça söyler. İndirimli ürünleri ve fiyat geçmişini `data` dalına yazar.
- **Uygulama** (`android/`): Kotlin + Jetpack Compose. Listeyi indirir, indirim oranı ve kategoriye göre filtreler. Sadece onaylı mağaza linklerini açar.
- **APK**: `android/` değişince GitHub otomatik derler, yukarıdaki link hep son sürümü verir.

## Ayarlar

Hangi ürünlerin taranacağı `scraper/sources.json` içinde. Arama kelimesi eklemek için `queries` listesine satır eklemen yeter.

## Mağaza durumu

| Mağaza | Durum |
|---|---|
| Trendyol | ✅ Kategori sayfaları taranıyor (robots.txt izin veriyor) |
| Hepsiburada | ⏸ Botlara kapalı (403). Ortaklık programı onayı gerekiyor |
| n11 | ⏸ Botlara kapalı (403). Ortaklık programı onayı gerekiyor |
| Amazon | ⏸ Listelerde indirim bilgisi yok. Amazon Associates API gerekiyor |

Kapalı mağazalar `scraper/sources.json` içinde hazır duruyor; `"enabled": true` yapınca devreye girer.

## Yol haritası

1. ✅ Trendyol tarayıcısı ve indirim akışı
2. Hepsiburada, Amazon ve diğerleri
3. Filtreye uyan yeni indirimlerde bildirim
4. Fiyat geçmişi grafiği ve sahte indirim dedektörü
