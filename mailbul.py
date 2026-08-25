import argparse
import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
from urllib.parse import urljoin, urlparse
import time
import random
import os
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from utils import load_config, setup_logging, sonuclari_diske_yaz, final_kaydet
from utils import girdi_sirasina_diz, normalize_columns, COL_SICIL, COL_UNVAN, COL_WEB, COL_EMAIL
from utils import COL_ADAY_EMAIL, mail_marka_uyumlu
from utils import COL_KAYNAK_SATIR, kaynak_satir_anahtari, islenmis_kaynak_satirlari
from utils import progress_durum_yaz, progress_durum_temizle
from utils import dolu_hucre_sayisi

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Uzunluk sınırlı — sınırsız + nicelikleri büyük HTML'de felaket backtracking yapar
# (serkanturanli.com ~1.1MB → eski regex dakikalarca %99 CPU).
EMAIL_REGEX = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]{1,64}"
    r"@"
    r"[A-Za-z0-9.-]{1,253}"
    r"\."
    r"[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9._%+-])"
)

OBFUSCATED_PATTERNS = [
    # [at] ve [dot]
    (re.compile(
        r"[\w._%+-]{1,64}\s*\[\s*at\s*\]\s*[\w.-]{1,253}\s*\[\s*dot\s*\]\s*[\w]{2,24}",
        re.IGNORECASE,
    ),
     lambda m: m.group(0)
     .replace('[ at ]', '@')
     .replace('[at]', '@')
     .replace(' [at] ', '@')
     .replace('[at] ', '@')
     .replace(' [at]', '@')
     .replace('[ dot ]', '.')
     .replace('[dot]', '.')
     .replace(' [dot] ', '.')
     .replace('[dot] ', '.')
     .replace(' [dot]', '.')
     .replace(' ', '')),
    # (at) ve (dot)
    (re.compile(
        r"[\w._%+-]{1,64}\s*\(\s*at\s*\)\s*[\w.-]{1,253}\s*\(\s*dot\s*\)\s*[\w]{2,24}",
        re.IGNORECASE,
    ),
     lambda m: m.group(0)
     .replace('( at )', '@')
     .replace('(at)', '@')
     .replace(' (at) ', '@')
     .replace('(at) ', '@')
     .replace(' (at)', '@')
     .replace('( dot )', '.')
     .replace('(dot)', '.')
     .replace(' (dot) ', '.')
     .replace('(dot) ', '.')
     .replace(' (dot)', '.')
     .replace(' ', '')),
    # " at " ve " dot " (boşlukla ayrılmış)
    (re.compile(
        r"[\w._%+-]{1,64}\s+at\s+[\w.-]{1,253}\s+dot\s+[\w]{2,24}",
        re.IGNORECASE,
    ),
     lambda m: m.group(0)
     .replace(' at ', '@')
     .replace(' dot ', '.')
     .replace(' ', '')),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

ONCELIK = [
    "info@",
    "iletisim@",
    "contact@",
    "mail@",
    "kurumsal@",
    "sales@",
    "destek@",
    "satis@",
    "musteri@",
    "service@",
    "office@",
    "hello@",
    "general@",
]

RED = [
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "webmaster",
    "career",
    "kariyer",
    "ik@",
    "hr@",
    "humanresources",
    "muhasebe",
    "accounting",
    "teklif",
    "example",
    "ornek@",
    "örnek@",
    "test",
    "demo@",
    "sample@",
    "admin@",
    "wordpress",
    "example.com",
    "@firma.com",
    "@sirket.com",
    "@email.com",
    "@domain.com",
    "@ornek.com",
    "name@domain",
    "user@domain",
    "email@domain",
    "yourmail",
    "youremail",
    "yourname",
    "isim@",
    "adiniz",
    "soyadiniz",
    "adsoyad",
    "sentry",
    "wix",
    "squarespace",
    "godaddy",
    "hostgator",
    "bluehost",
    "newsletter",
    "abuse@",
    "postmaster",
    "security@",
    "ssl@",
    "cert@",
    "domain@",
    "hosting",
    "billing@",
    "payment@",
    "invoice@",
    "spam@",
    "marketing@",
    "analytics@",
    "tag@",
    "pixel@",
    "kvkk@",
    "kvkk",
    "aydinlatma@",
    "riza@",
    "privacy@",
    "legal@",
    "hukuk@",
    "compliance@",
    "gdpr@",
    "tracking@",
    "notification@",
    "alert@",
    "monitor@",
    "log@",
    "error@",
    "dev@",
    "developer@",
    "git@",
    "github",
]

# Regex'in dosya yolu / görsel adını mail sanması: contact@2x.png, foo@bar.jpg
ASSET_EMAIL_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".mjs", ".map",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp4", ".webm", ".mp3",
    ".pdf", ".zip", ".rar",
)

# Local-part tamamen bunlar ise şablon/placeholder (name@domain.com vb.)
PLACEHOLDER_LOCAL = {
    "name",
    "ornek",
    "örnek",
    "example",
    "sample",
    "demo",
    "test",
    "user",
    "username",
    "yourname",
    "youremail",
    "isim",
    "adsoyad",
    "adiniz",
    "soyadiniz",
}

ILETISIM_KEYWORDS = [
    "iletisim",
    "contact",
    "contact-us",
    "contact_us",
    "kurumsal",
    "about",
    "hakkimizda",
    "bize-ulasin",
    "bize-ulas",
    "bize_yazin",
    "bize-yazin",
    "reach-us",
    "reach",
    "get-in-touch",
    "support",
    "destek",
    "musteri-hizmetleri",
    "customer-service",
    "musteri",
    "satis",
    "sales",
    "bayi",
    "dealer",
    "where-to-buy",
    "nerede-satin-alinir",
]

# Module-level variables set by main()
config = None
logger = None

# requests.Session thread-safe değildir; her işçi thread kendi session'ını kullanır.
_thread_local = threading.local()

# Aynı site URL'si için email sonucu (tekrarlı satırlarda yeniden tarama yok)
_PROCESSING = object()
_site_email_cache = {}
_site_cache_lock = threading.Lock()

INPUT_WEB_FILE = None
WORKERS = None
OUTPUT_MAIL_FILE = None
MAX_ILETISIM_SAYFASI = None
MAX_SITEMAP_SAYFA = None
MAIL_TIMEOUT = None
MAIL_MAX_FIRMA_SANIYE = 20
MAIL_RETRIES = None
MIN_MAIL_BEKLEME = None
MAX_MAIL_BEKLEME = None

ARA_KAYIT_ADIMI = 10


def _deadline_kur():
    tavan = float(MAIL_MAX_FIRMA_SANIYE or 20)
    _thread_local.deadline = time.monotonic() + max(1.0, tavan)


def _deadline_temizle():
    _thread_local.deadline = None


def _sure_doldu_mu() -> bool:
    dl = getattr(_thread_local, "deadline", None)
    return dl is not None and time.monotonic() >= dl


def _http_timeout(varsayilan=None) -> float:
    """Kalan firma süresi HTTP timeout'unu kısaltır; süre bittiyle 0.2 sn."""
    t = float(varsayilan if varsayilan is not None else (MAIL_TIMEOUT or 8))
    dl = getattr(_thread_local, "deadline", None)
    if dl is None:
        return t
    kalan = dl - time.monotonic()
    if kalan <= 0:
        return 0.2
    return min(t, max(0.2, kalan))


def _kalan_uyku(saniye) -> float:
    """Sayfa arası beklemeyi firma tavanının dışına taşımaz."""
    saniye = float(saniye or 0)
    if saniye <= 0:
        return 0.0
    dl = getattr(_thread_local, "deadline", None)
    if dl is None:
        return saniye
    kalan = dl - time.monotonic()
    if kalan <= 0:
        return 0.0
    return min(saniye, kalan)


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def site_domain(site):
    """Web sitesinin ana domain'ini döndürür. www. atılır."""
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    net = urlparse(site).netloc.lower()
    if net.startswith("www."):
        net = net[4:]
    return net

def ayni_domain(mail, sdomain):
    """Mail'in domain'i site domain'i ile aynı (veya alt/üst domain) mı?"""
    if "@" not in mail:
        return False
    mdom = mail.split("@")[1].lower()
    if not sdomain:
        return False
    return (
        mdom == sdomain
        or mdom.endswith("." + sdomain)
        or sdomain.endswith("." + mdom)
    )

# RED girdisi 'ik@' gibi tek '@' ile bitiyorsa TAM yerel kısım demektir.
# Alt dizge olarak arandığında meşru adresleri yiyordu:
#   ik@    → teknik@, mekanik@, mühendislik@, lojistik@, grafik@
#   isim@  → iletisim@   (ONCELIK listesinin ikinci sırası!)
RED_LOCAL_TAM = frozenset(
    x[:-1] for x in RED if x.endswith("@") and x.count("@") == 1
)
RED_ALT_DIZGE = tuple(x for x in RED if x not in {f"{y}@" for y in RED_LOCAL_TAM})


def temizle(mailler):
    """Mailleri temizle: küçült, reddet, tekrarları ele."""
    sonuc = []
    for mail in mailler:
        mail = mail.lower().strip().rstrip(".")
        if not EMAIL_REGEX.match(mail):
            continue
        if any(mail.endswith(ext) for ext in ASSET_EMAIL_EXT):
            continue
        if any(x in mail for x in RED_ALT_DIZGE):
            continue
        if mail.split("@", 1)[0] in RED_LOCAL_TAM:
            continue
        local = mail.split("@", 1)[0]
        if local in PLACEHOLDER_LOCAL:
            continue
        if len(mail) < 6 or len(mail) > 100:
            continue
        
        # Domain tire ile başlayamaz
        domain_part = mail.split("@", 1)[1]
        if domain_part.startswith("-") or domain_part.endswith("-"):
            continue
        # Local part'ta ardışık nokta olamaz
        if ".." in local:
            continue
        # Domain'de ardışık nokta olamaz
        if ".." in domain_part:
            continue
        # Tamamen sayısal local part placeholder'dır
        if local.isdigit():
            continue

        if mail not in sonuc:
            sonuc.append(mail)
    return sonuc

def obfuscation_coz(html_text):
    """Gizlenmiş email formatlarını çözer."""
    mailler = set()
    for pattern, replacer in OBFUSCATED_PATTERNS:
        matches = pattern.finditer(html_text)
        for m in matches:
            try:
                cozulmus = replacer(m).lower().strip()
                if EMAIL_REGEX.match(cozulmus):
                    mailler.add(cozulmus)
            except Exception:
                pass
    return mailler

def _oncelikli_sec(mailler):
    """ONCELIK sırasına göre en iyi adresi seç; yoksa ilkini."""
    for tercih in ONCELIK:
        for mail in mailler:
            if mail.startswith(tercih):
                return mail
    return mailler[0] if mailler else ""


def mail_sec(mailler, sdomain="", firma_adi=""):
    """(seçilen, aday) döndürür.

    Sıra:
      1. Site domain'i (veya alt/üst domain'i) ile aynı adresler
      2. Farklı domainde ama ünvanın markasını taşıyanlar — site zaten
         firmanın kendisi olarak doğrulandığı için bunlar firmanın adresi
         sayılır (medemainsaat@gmail.com, info@medema.com.tr)
      3. Kalan farklı-domain adresleri seçilmez ama **kaybolmaz**:
         en iyisi `aday` olarak döner, ADAY_EMAIL sütununa yazılır

    Ölçüm: mail bulunamayan 682 firmanın 286'sında sayfada aday vardı ve
    tamamı 1. adımda eleniyordu.
    """
    mailler = temizle(mailler)
    if not mailler:
        return "", ""
    if not sdomain:
        return _oncelikli_sec(mailler), ""

    ayni = [m for m in mailler if ayni_domain(m, sdomain)]
    if ayni:
        return _oncelikli_sec(ayni), ""

    disari = [m for m in mailler if not ayni_domain(m, sdomain)]
    if not disari:
        return "", ""

    uyumlu = [m for m in disari if mail_marka_uyumlu(m, firma_adi)]
    if uyumlu:
        return _oncelikli_sec(uyumlu), ""

    return "", _oncelikli_sec(disari)


def en_iyi_mail_sec(mailler, sdomain=""):
    """Geriye dönük sarmalayıcı — yalnızca seçilen adresi döndürür.

    Ünvan verilmediği için marka eşleşmesi devreye girmez; davranış
    eskisiyle birebir aynıdır.
    """
    return mail_sec(mailler, sdomain)[0]

# ---------------------------------------------------------------------------
# HTTP istek yardımcıları
# ---------------------------------------------------------------------------

# Domain DNS'te hiç çözülmüyorsa tekrar denemek anlamsız — hata metninde bunlar geçer.
_DNS_ISARETLERI = (
    "nodename nor servname",
    "name or service not known",
    "getaddrinfo failed",
    "nameresolutionerror",
    "temporary failure in name resolution",
    "no address associated with hostname",
)


def _dns_hatasi(exc):
    """İstisna, domain'in hiç çözülemediğini mi gösteriyor?"""
    return any(x in str(exc).lower() for x in _DNS_ISARETLERI)


def get_session():
    """Bu thread'e ait requests.Session'ı döndürür (yoksa oluşturur).
    
    urllib3.Retry ile 429/500/502/503/504 otomatik yeniden denenir.
    """
    s = getattr(_thread_local, "session", None)
    if s is None:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=MAIL_RETRIES or 2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        
        s = requests.Session()
        s.headers.update(HEADERS)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _thread_local.session = s
    return s


def safe_get(url, timeout=None, retries=None):
    """Güvenli GET isteği. HTTPAdapter retry + DNS hatası korumalı.

    Bellek koruması: Yanıt, Content-Length başlığı olmasa veya yanlış olsa
    bile en fazla 2 MB parça parça okunur. ``response.content`` kullanılmaz;
    o özellik yanıtın tamamını belleğe indirir.
    """
    if _sure_doldu_mu():
        return None
    if timeout is None:
        timeout = _http_timeout()
    else:
        timeout = _http_timeout(timeout)
    MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB
    try:
        response = get_session().get(
            url,
            timeout=timeout,
            allow_redirects=True,
            verify=False,
            stream=True
        )
        # Content-Length yalnızca erken elemedir; gerçek sınır aşağıdaki
        # iter_content döngüsünde uygulanır.
        content_length = response.headers.get("Content-Length")
        try:
            if content_length and int(content_length) > MAX_CONTENT_BYTES:
                response.close()
                return None
        except (TypeError, ValueError):
            # Bozuk başlık, akıştaki kesin sınırı etkilemez.
            pass

        indirilen = 0
        parcalar = []
        for parca in response.iter_content(chunk_size=64 * 1024):
            if _sure_doldu_mu():
                response.close()
                return None
            if not parca:
                continue
            indirilen += len(parca)
            if indirilen > MAX_CONTENT_BYTES:
                response.close()
                return None
            parcalar.append(parca)

        # Çağıran kod response.text kullanıyor; yalnızca sınır içindeki gövdeyi
        # requests.Response üzerinde saklayıp ağ bağlantısını kapatıyoruz.
        response._content = b"".join(parcalar)
        response.close()
        return response
    except Exception as e:
        if _dns_hatasi(e):
            return None
        return None

def url_denemeleri(site):
    """Bir site için farklı URL varyasyonları döndürür."""
    site = site.strip()
    if site.startswith(("http://", "https://")):
        return [site]
    return [
        f"https://www.{site}",
        f"https://{site}",
        f"http://www.{site}",
        f"http://{site}",
    ]


_MAIL_SKIP_HOST = (
    "mail.google.com",
    "gmail.com",
    "googlemail.com",
    "outlook.live.com",
    "outlook.office.com",
    "outlook.office365.com",
    "login.microsoftonline.com",
)


def tarama_url_gecerli_mi(url: str) -> bool:
    """Sitemap / iletişim href'i taranmaya değer mi?

    `###m/iletisim` gibi çöp loc ve Gmail compose linklerini ele.
    Göreli yol (`/iletisim`) kabul.
    """
    ham = (url or "").strip()
    if not ham:
        return False
    low = ham.lower()
    if low.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return False
    if ham.startswith("#") or "###" in ham:
        return False
    parsed = urlparse(ham)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parsed.scheme in ("http", "https"):
        if not host or "." not in host.split(":")[0]:
            return False
        if any(host == h or host.endswith("." + h) for h in _MAIL_SKIP_HOST):
            return False
        return True
    if ham.startswith("/") and not ham.startswith("//"):
        return True
    return False

# ---------------------------------------------------------------------------
# Sayfa tarama
# ---------------------------------------------------------------------------

def sayfa_tara(url, mailler, ziyaret_edilen):
    """Bir sayfayı tarar, email'leri ve iletişim linklerini toplar.

    Döner: (iletisim_linkleri, ok) — ok=True yalnızca HTTP 200 alındığında.
    """
    if _sure_doldu_mu():
        return [], False
    if url in ziyaret_edilen:
        return [], False
    ziyaret_edilen.add(url)

    iletisim_linkleri = []
    response = safe_get(url)
    if not response or response.status_code != 200:
        return iletisim_linkleri, False

    html = response.text
    # Aşırı büyük sayfalarda regex maliyetini sınırla (JS/CSS gömülü şişme)
    html_tara = html if len(html) <= 1_500_000 else html[:1_500_000]
    mailler.update(EMAIL_REGEX.findall(html_tara))

    # lxml, html.parser'dan belirgin şekilde hızlı (C ile yazılmış).
    soup = BeautifulSoup(html, "lxml")

    # Gizlenmiş mail: ham HTML yerine görünür metin (daha küçük, backtracking riski düşük)
    try:
        mailler.update(obfuscation_coz(soup.get_text(" ", strip=True)[:500_000]))
    except Exception:
        mailler.update(obfuscation_coz(html_tara[:500_000]))

    # Linkler üzerinde tek geçiş: hem mailto: hem iletişim linkleri.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            mail = href.replace("mailto:", "").split("?")[0].strip()
            if mail:
                mailler.add(mail)

        if any(k in href.lower() for k in ILETISIM_KEYWORDS):
            full_link = urljoin(url, href)
            if not tarama_url_gecerli_mi(full_link):
                continue
            if full_link not in ziyaret_edilen:
                iletisim_linkleri.append(full_link)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            text = json.dumps(data)
            mailler.update(EMAIL_REGEX.findall(text))
        except Exception:
            pass

    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        if content:
            mailler.update(EMAIL_REGEX.findall(content))

    return iletisim_linkleri, True

def sitemap_tara(base_url, mailler):
    """sitemap.xml'i tarar, iletişim sayfalarını bulur."""
    if _sure_doldu_mu():
        return []
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    response = safe_get(sitemap_url, timeout=8)
    if not response or response.status_code != 200:
        return []

    soup = BeautifulSoup(response.text, "xml")
    urls = [loc.text for loc in soup.find_all("loc")]

    iletisim_urls = [
        u for u in urls
        if u and any(k in u.lower() for k in ILETISIM_KEYWORDS)
        and tarama_url_gecerli_mi(u)
    ]
    return iletisim_urls[:MAX_SITEMAP_SAYFA]

# ---------------------------------------------------------------------------
# Ana fonksiyon
# ---------------------------------------------------------------------------

def mail_bul(site, firma_adi=""):
    """Bir siteden email adresleri toplar.

    İlk açılan ana sayfanın iletişim linklerini ve sitemap iletişim
    sayfalarını her zaman tarar; mail sayısına göre erken çıkmaz.
    """
    mailler = set()
    ziyaret_edilen = set()
    calisan_base_url = None  # sitemap için güvenli base URL

    atlandi = False
    for url in url_denemeleri(site):
        if _sure_doldu_mu():
            atlandi = True
            break
        logger.info(f"  🔍 {url}")
        iletisim_linkleri, ok = sayfa_tara(url, mailler, ziyaret_edilen)
        if not ok:
            # Bu şema/www varyasyonu açılmadı; sonrakini dene
            continue

        parsed = urlparse(url)
        calisan_base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Ana sayfa açıldı — iletişim sayfalarını mail olsun olmasın tara
        for link in iletisim_linkleri[:MAX_ILETISIM_SAYFASI]:
            if _sure_doldu_mu():
                atlandi = True
                break
            logger.info(f"    📄 {link}")
            sayfa_tara(link, mailler, ziyaret_edilen)
            uyku = _kalan_uyku(random.uniform(MIN_MAIL_BEKLEME, MAX_MAIL_BEKLEME))
            if uyku:
                time.sleep(uyku)

        # Çalışan ana sayfa bulundu; diğer http/https/www varyasyonlarına gerek yok
        break

    # Sitemap iletişim sayfaları: ana sayfada mail olsa bile dene
    # (footer'daki 3+ gürültü regex eşleşmesi gerçek info@'yi engellemesin)
    if calisan_base_url and not _sure_doldu_mu():
        try:
            sitemap_urls = sitemap_tara(calisan_base_url, mailler)
            for sm_url in sitemap_urls:
                if _sure_doldu_mu():
                    atlandi = True
                    break
                if not tarama_url_gecerli_mi(sm_url):
                    continue
                if sm_url in ziyaret_edilen:
                    continue
                logger.info(f"    🗺 {sm_url}")
                sayfa_tara(sm_url, mailler, ziyaret_edilen)
                uyku = _kalan_uyku(random.uniform(MIN_MAIL_BEKLEME, MAX_MAIL_BEKLEME))
                if uyku:
                    time.sleep(uyku)
        except Exception:
            pass
    elif _sure_doldu_mu():
        atlandi = True

    if atlandi or _sure_doldu_mu():
        logger.warning(f"  ⏱ yavaş site atlandı (süre doldu): {site}")

    return list(mailler)

def firma_isle(gorev):
    """Tek bir firmayı işler. Thread-safe: sadece yerel değişken ve
    salt-okunur global ayarlar kullanır, sonucu dict olarak döndürür.

    ``ekstra``: girdideki, bu aşamanın üretmediği sütunlar (SKOR, DURUM,
    ADAY_WEB, RED_NEDEN, İLÇE…). Aynen taşınır — aksi hâlde panelde
    "Site bul + Mail bul" birlikte çalıştırıldığında son çıktıda kaybolur.
    """
    firma, site, sicil_deger, kaynak_satir, ekstra = gorev

    if site == "" or site.lower() == "nan":
        logger.warning(f"  ❌ Website yok: {firma}")
        kayit = {"UNVAN": firma, "WEB": "", "EMAIL": "", COL_ADAY_EMAIL: ""}
    else:
        logger.info(f"  📧 {firma}")
        aday_mail = ""
        _deadline_kur()
        try:
            # Aynı site tekrarında yeniden tarama. Cache HAM mail listesini
            # tutar; seçim ünvana bağlı olduğu için (marka eşleşmesi) her
            # firma için ayrı yapılır.
            with _site_cache_lock:
                cached = _site_email_cache.get(site)
                if cached is None:
                    # İlk talep eden thread: sentinel koy ve devam et
                    _site_email_cache[site] = _PROCESSING

            if cached is not None and cached is not _PROCESSING:
                # Cache hit — ham liste hazır
                bulunan_mailler = list(cached)
                logger.info(f"  ↪ cache: {len(bulunan_mailler)} aday")
            elif cached is _PROCESSING:
                # Başka thread taratıyor — firma tavanı dolana kadar bekle
                while not _sure_doldu_mu():
                    time.sleep(0.2)
                    with _site_cache_lock:
                        cached = _site_email_cache.get(site)
                    if cached is not _PROCESSING:
                        break
                bulunan_mailler = (
                    list(cached)
                    if (cached is not None and cached is not _PROCESSING)
                    else []
                )
                logger.info(f"  ↪ cache (beklendi): {len(bulunan_mailler)} aday")
            else:
                # Bu thread tarayacak (sentinel zaten kondu)
                bulunan_mailler = []
                try:
                    bulunan_mailler = mail_bul(site, firma)
                finally:
                    # Sentinel'i her durumda temizle — deadlock önle
                    with _site_cache_lock:
                        _site_email_cache[site] = list(bulunan_mailler)

            secilen_mail, aday_mail = mail_sec(
                bulunan_mailler, site_domain(site), firma
            )
        except Exception as e:
            logger.error(f"  ❌ Beklenmedik hata ({firma}): {e}")
            bulunan_mailler = []
            secilen_mail = ""
            aday_mail = ""
        finally:
            _deadline_temizle()

        if secilen_mail:
            logger.info(f"  ✔ {secilen_mail}  ({len(bulunan_mailler)} aday)")
        elif aday_mail:
            logger.warning(
                f"  ⚠ Farklı domain, seçilmedi: {aday_mail}  "
                f"({len(bulunan_mailler)} aday)"
            )
        else:
            logger.warning(f"  ❌ Email bulunamadı  ({len(bulunan_mailler)} aday)")

        kayit = {
            "UNVAN": firma,
            "WEB": site,
            "EMAIL": secilen_mail,
            COL_ADAY_EMAIL: aday_mail,
        }

    # Girdiden gelen ek sütunlar (bu aşamanın ürettiklerinin üzerine yazmaz)
    if ekstra:
        kayit.update(ekstra)

    # SİCİL her zaman yazılır — girdi satır hizası için
    kayit[COL_SICIL] = sicil_deger
    kayit[COL_KAYNAK_SATIR] = kaynak_satir

    return kayit


# ---------------------------------------------------------------------------
# Resume Özelliği
# ---------------------------------------------------------------------------

def islenmis_firmalari_yukle(df_girdi, sicil_var):
    """Çıktı dosyasındaki işlenmiş satır anahtarlarını yükler (SİCİL tercih)."""
    if not os.path.exists(OUTPUT_MAIL_FILE):
        return set()

    try:
        df_uretilen = normalize_columns(pd.read_excel(OUTPUT_MAIL_FILE))
        return islenmis_kaynak_satirlari(df_uretilen, df_girdi, sicil_var)
    except Exception:
        return set()


def main():
    global config, logger, WORKERS
    global INPUT_WEB_FILE, OUTPUT_MAIL_FILE, MAX_ILETISIM_SAYFASI, MAX_SITEMAP_SAYFA
    global MAIL_TIMEOUT, MAIL_MAX_FIRMA_SANIYE, MAIL_RETRIES, MIN_MAIL_BEKLEME, MAX_MAIL_BEKLEME
    global _site_email_cache, _site_cache_lock
    global ARA_KAYIT_ADIMI

    # CLI argümanları
    parser = argparse.ArgumentParser(description="Firma e-posta bulucu")
    parser.add_argument("--input", "-i", help="Girdi Excel dosyası (varsayılan: config.yaml'dan)")
    parser.add_argument("--output", "-o", help="Çıktı Excel dosyası (varsayılan: girdi adından türetilir)")
    args = parser.parse_args()

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    config = load_config()
    logger = setup_logging(config["dosyalar"]["log"], name="mailbul")
    
    # Dosya yolları: CLI > config
    INPUT_WEB_FILE = args.input or config["dosyalar"]["output_web"]
    if args.output:
        OUTPUT_MAIL_FILE = args.output
    elif args.input:
        # Girdi adından otomatik türet: firmalar_1_web.xlsx → firmalar_1_mail.xlsx
        base = os.path.splitext(INPUT_WEB_FILE)[0]
        base = base.replace("_web", "")  # "_web" varsa kaldır
        OUTPUT_MAIL_FILE = f"{base}_mail.xlsx"
    else:
        OUTPUT_MAIL_FILE = config["dosyalar"]["output_mail"]

    logger.info(f"Girdi: {INPUT_WEB_FILE} → Çıktı: {OUTPUT_MAIL_FILE}")
    MAX_ILETISIM_SAYFASI = config["mail"]["max_iletisim_sayfasi"]
    MAX_SITEMAP_SAYFA = config["mail"]["max_sitemap_sayfa"]
    MAIL_TIMEOUT = config["mail"]["timeout"]
    MAIL_MAX_FIRMA_SANIYE = float(config["mail"].get("max_firma_saniye", 20))
    MAIL_RETRIES = config["mail"]["retries"]
    MIN_MAIL_BEKLEME = config["bekleme"]["min_mail"]
    MAX_MAIL_BEKLEME = config["bekleme"]["max_mail"]
    WORKERS = max(1, int(config["mail"]["workers"]))
    ARA_KAYIT_ADIMI = max(1, int(config.get("ara_kayit_araligi", 10)))

    _site_email_cache = {}
    _site_cache_lock = threading.Lock()

    logger.info(f"Input dosyası okunuyor: {INPUT_WEB_FILE}")
    df = normalize_columns(pd.read_excel(INPUT_WEB_FILE))
    logger.info(f"Toplam {len(df)} satır bulundu.")

    SICIL_VAR = COL_SICIL in df.columns
    UNVAN_VAR = COL_UNVAN in df.columns

    if not UNVAN_VAR:
        logger.error(
            f"HATA: Excel dosyasında '{COL_UNVAN}' sütunu bulunamadı "
            "(Firma alias'ı da kabul edilir)!"
        )
        exit(1)

    if SICIL_VAR:
        logger.info(f"{COL_SICIL} sütunu bulundu, resume satır hizalı.")
    else:
        logger.info(
            f"{COL_SICIL} yok — satır indeksi kullanılacak "
            "(tekrarlı UNVAN'lar dahil her satır korunur)."
        )

    islenmis_satirlar = islenmis_firmalari_yukle(df, SICIL_VAR)
    baslangic_sayisi = len(islenmis_satirlar)

    if baslangic_sayisi > 0:
        logger.info(f"  📂 {baslangic_sayisi} satır zaten işlenmiş, kaldığı yerden devam ediliyor...")
    else:
        logger.info("  🆕 Yeni işlem başlatılıyor...")

    # Bu aşamanın kendi ürettiği sütunlar dışındaki her şey çıktıya taşınır:
    # SKOR, DURUM, ADAY_WEB, RED_NEDEN, İLÇE ve kullanıcının kendi sütunları.
    URETILEN = {
        COL_UNVAN, COL_WEB, COL_EMAIL, COL_ADAY_EMAIL, COL_SICIL, COL_KAYNAK_SATIR
    }
    TASINACAK = [c for c in df.columns if c not in URETILEN]
    if TASINACAK:
        logger.info(f"Taşınan sütunlar: {', '.join(map(str, TASINACAK))}")

    # Her girdi satırı ayrı görev — tekilleştirme yok (hizayı koru)
    gorevler = []
    for idx, satir in df.iterrows():
        sicil = str(satir[COL_SICIL]) if SICIL_VAR else str(idx)
        kaynak_satir = kaynak_satir_anahtari(idx)
        site = str(satir[COL_WEB]).strip() if COL_WEB in df.columns else ""
        if site.lower() in ("nan", "none"):
            site = ""
        firma = str(satir[COL_UNVAN]).strip()

        if kaynak_satir in islenmis_satirlar:
            continue

        ekstra = {c: satir[c] for c in TASINACAK}
        gorevler.append((firma, site, sicil, kaynak_satir, ekstra))

    logger.info(
        f"  ⚡ {len(gorevler)} satır, {WORKERS} paralel işçi "
        f"(firma tavanı {MAIL_MAX_FIRMA_SANIYE:.0f} sn)."
    )

    sonuclar = []
    executor = ThreadPoolExecutor(max_workers=WORKERS)
    toplam = len(df)
    progress_durum_yaz(baslangic_sayisi, toplam, "Mail bul")
    biten = 0

    try:
        # as_completed: yavaş bir site diğer biten firmaların ilerlemeyi
        # güncellemesini engellemez. Resume kaynak_satır anahtarıyla çalışır;
        # ara kayıt sırası önemli değil, final_kaydet girdiyi yeniden dizer.
        gelecekler = [executor.submit(firma_isle, g) for g in gorevler]
        for gelecek in tqdm(
            as_completed(gelecekler),
            total=len(gorevler),
            desc="Email'ler taranıyor",
            unit="firma",
        ):
            kayit = gelecek.result()
            sonuclar.append(kayit)
            biten += 1
            unvan = ""
            if isinstance(kayit, dict):
                unvan = str(kayit.get("UNVAN") or "")
            progress_durum_yaz(baslangic_sayisi + biten, toplam, "Mail bul", unvan)

            if len(sonuclar) >= ARA_KAYIT_ADIMI:
                if sonuclari_diske_yaz(sonuclar, OUTPUT_MAIL_FILE, logger):
                    logger.info(f"  💾 {len(sonuclar)} kayıt diske yazıldı.")
                    sonuclar = []

    except KeyboardInterrupt:
        logger.warning("Kullanıcı tarafından durduruldu (Ctrl+C).")
        logger.info("Şu ana kadar bulunan sonuçlar kaydediliyor...")

    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}")
        logger.info("Sonuçlar kaydediliyor...")

    finally:
        progress_durum_yaz(baslangic_sayisi + biten, toplam, "Mail bul", "Bitti")
        # Bekleyen görevleri iptal et, çalışanların bitmesini bekleme
        executor.shutdown(wait=False, cancel_futures=True)

        sonuc_df = final_kaydet(sonuclar, OUTPUT_MAIL_FILE, logger)
        if sonuc_df is not None:
            sonuc_df = girdi_sirasina_diz(sonuc_df, df, SICIL_VAR, OUTPUT_MAIL_FILE, logger)
            logger.info("Bitti.")
            logger.info(f"Dosya oluşturuldu: {OUTPUT_MAIL_FILE}")
            logger.info(f"Toplam satır: {len(sonuc_df)} (girdi: {len(df)})")

            if COL_EMAIL in sonuc_df.columns:
                n_dolu = dolu_hucre_sayisi(sonuc_df[COL_EMAIL])
                logger.info(f"Email bulunan: {n_dolu}")
                logger.info(f"Email bulunamayan: {len(sonuc_df) - n_dolu}")
        else:
            logger.info(f"  Sonuçlar bellekte tutuluyor, dosyayı kapatın ve script'i tekrar çalıştırın.")

if __name__ == "__main__":
    main()
