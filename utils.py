"""Firma Bulucu — Ortak yardımcı fonksiyonlar.

sitebul.py, mailbul.py ve panel.py tarafından paylaşılan kodlar:
  • Config yükleme
  • Logging altyapısı (tqdm-uyumlu)
  • Sütun adı normalizasyonu (SİCİL / UNVAN / WEB / EMAIL / SKOR / İLÇE)
  • Türkçe karakter normalizasyonu
  • Marka token / domain çekirdek / benzerlik skoru
  • Google sonuç ignore listeleri
  • Ara kayıt (disk yazma) yardımcıları
  • CAPTCHA durum sinyali + Chrome öne getirme
"""

import json
import logging
import os
import platform
import re
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import yaml
from tqdm import tqdm


# Proje kökü (utils.py burada)
ROOT_DIR = Path(__file__).resolve().parent
CAPTCHA_STATUS_PATH = ROOT_DIR / "panel_jobs" / "captcha_status.json"
PROGRESS_STATUS_PATH = ROOT_DIR / "panel_jobs" / "progress_status.json"

# Google SERP CSS — obfuscated sınıflar sık değişir; asıl kopya config.yaml'dadır.
DEFAULT_SERP_CARDS = (
    "div.tF2Cxc, div.g, div[data-sokoban-container], div.MjjYud > div"
)
DEFAULT_SERP_SNIPPET = (
    "div.VwiC3b, div[data-sncf], div.IsZvec, span.st, "
    'div[data-content-feature="1"], .MUxGbd'
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "skor": {"yuksek_esik": 65, "dusuk_esik": 40},
    "bekleme": {
        "min_arasi": 6,
        "max_arasi": 14,
        "min_mail": 0.5,
        "max_mail": 1.5,
    },
    "dosyalar": {
        "input": "firmalar.xlsx",
        "output_web": "firmalar_web.xlsx",
        "output_mail": "firmalar_mail.xlsx",
        "log": "firma_bulucu.log",
    },
    "google": {
        "max_aday": 10,
        "captcha_timeout": 600,
        # Google SERP sınıf adları sık değişir; config.yaml'dan okunur.
        "selectors": {
            "cards": DEFAULT_SERP_CARDS,
            "snippet": DEFAULT_SERP_SNIPPET,
        },
    },
    "chrome": {"debug_port": 9222},
    "mail": {
        "max_iletisim_sayfasi": 5,
        "max_sitemap_sayfa": 5,
        "timeout": 12,
        "retries": 2,
        "workers": 8,
    },
    "llm": {
        "enabled": True,
        "model": "openai/gpt-oss-120b",
        "temperature": 0.3,
        "max_tokens": 512,
    },
    "ignore_marka_domainleri": [],
    "ignore_domain_kaliplari": [
        "tso.org.tr",
        "tobb.org.tr",
        "ticaretodasi",
        "sanayiodasi",
        "esnafodasi",
        "denizticaretodasi",
        "rehber.",
        "firmarehberi",
        "sirketdizin",
        "ticaretsicil.gov.tr",
    ],
}


def load_config(path: str = "config.yaml") -> dict:
    """YAML config dosyasını yükler; bulunamazsa varsayılan değerleri döner."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Config dosyası yüklenemedi: {e}")
        print("Varsayılan değerler kullanılıyor...")
        cfg = {}

    # Eksik üst-düzey anahtarları varsayılanlardan doldur
    for key, default_val in _DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = default_val
        elif isinstance(default_val, dict):
            for sub_key, sub_val in default_val.items():
                cfg[key].setdefault(sub_key, sub_val)
                if isinstance(sub_val, dict) and isinstance(cfg[key].get(sub_key), dict):
                    for k3, v3 in sub_val.items():
                        cfg[key][sub_key].setdefault(k3, v3)

    return cfg


def google_serp_secicileri(cfg: dict | None = None) -> dict[str, str]:
    """SERP kart / snippet CSS seçicileri (config.yaml → varsayılan).

    Google sınıf adları (tF2Cxc, VwiC3b…) sık değişir. Önce
    ``google.selectors``, yoksa üst düzey ``google_selectors`` okunur.
    Eksik veya boş değerler DEFAULT_SERP_* ile doldurulur.
    """
    cfg = cfg or {}
    sel: dict = {}
    google = cfg.get("google")
    if isinstance(google, dict) and isinstance(google.get("selectors"), dict):
        sel = google["selectors"]
    elif isinstance(cfg.get("google_selectors"), dict):
        sel = cfg["google_selectors"]

    cards = str(sel.get("cards") or "").strip() or DEFAULT_SERP_CARDS
    snippet = str(sel.get("snippet") or "").strip() or DEFAULT_SERP_SNIPPET
    return {"cards": cards, "snippet": snippet}


# ---------------------------------------------------------------------------
# Sütun adları (kanonik)
# ---------------------------------------------------------------------------

COL_SICIL = "SİCİL"
COL_UNVAN = "UNVAN"
COL_WEB = "WEB"
COL_EMAIL = "EMAIL"
COL_SKOR = "SKOR"
COL_ILCE = "İLÇE"
COL_DURUM = "DURUM"
# Reddedilen en iyi aday ve gerekçesi. Karar akışını DEĞİŞTİRMEZ; yalnızca
# insan gözüyle gözden geçirilebilsin diye çıktıya yazılır (KABUL'de boştur).
COL_ADAY_WEB = "ADAY_WEB"
COL_RED_NEDEN = "RED_NEDEN"
# Sitede bulunan ama farklı domainde olduğu için seçilmeyen e-posta.
# EMAIL doluysa boştur; gözden geçirme içindir.
COL_ADAY_EMAIL = "ADAY_EMAIL"
# Girdide aynı SİCİL birden fazla kez bulunabilir. Bu alan, her satırın
# değişmeyen teknik kimliğidir; resume işlemi SİCİL yerine bunu kullanır.
COL_KAYNAK_SATIR = "_KAYNAK_SATIR"

DURUM_KABUL = "KABUL"
DURUM_SITE_YOK = "SITE_YOK"
DURUM_RED_SKOR = "RED_SKOR"
DURUM_RED_SEKTOR = "RED_SEKTOR"
DURUM_RED_DOGRULAMA = "RED_DOGRULAMA"
DURUM_TIMEOUT = "TIMEOUT"
# LLM kotası/erişimi yüzünden karar VERİLEMEDİ — red değil, kararsız. TIMEOUT
# gibi işlenmiş sayılır: satır bir kez yazılır, resume tekrar denemez. Yeniden
# denemek için bu satırlar DURUM'a göre süzülüp ayrı bir girdi ile çalıştırılır.
DURUM_LLM_YOK = "LLM_YOK"
# Kabul edildi ama aynı domain bu çalıştırmada başka bir ünvana da atandı.
# Jenerik marka (teknikyapi, akyapi…) belirtisi — gözden geçirilmeli.
DURUM_KABUL_SUPHELI = "KABUL_SUPHELI"


def kaynak_satir_anahtari(indeks) -> str:
    """Excel'den sayı olarak dönse de kaynak satır anahtarını sabit tutar."""
    try:
        return str(int(indeks))
    except (TypeError, ValueError):
        return str(indeks).strip()


def islenmis_kaynak_satirlari(
    df_uretilen: pd.DataFrame,
    df_girdi: pd.DataFrame,
    sicil_var: bool,
) -> set[str]:
    """Çıktıdaki işlenmiş girdi satırlarının benzersiz anahtarlarını döndürür.

    Yeni çıktılar ``_KAYNAK_SATIR`` taşır. Eski çıktı dosyaları için ise
    satırlar aynı SİCİL/UNVAN'ın girdideki görünme sırasına eşlenir; böylece
    ilk güncellemeden sonra da tekrarlı SİCİL'ler yeniden kaybolmaz.
    """
    if COL_KAYNAK_SATIR in df_uretilen.columns:
        return {
            kaynak_satir_anahtari(deger)
            for deger in df_uretilen[COL_KAYNAK_SATIR]
            if not pd.isna(deger)
        }

    anahtar = COL_SICIL if sicil_var and COL_SICIL in df_uretilen.columns else COL_UNVAN
    if anahtar not in df_uretilen.columns or anahtar not in df_girdi.columns:
        return set()

    girdi_konumlari: dict[str, deque[str]] = defaultdict(deque)
    for indeks, deger in df_girdi[anahtar].items():
        girdi_konumlari[str(deger)].append(kaynak_satir_anahtari(indeks))

    islenmis: set[str] = set()
    for deger in df_uretilen[anahtar]:
        konumlar = girdi_konumlari[str(deger)]
        if konumlar:
            islenmis.add(konumlar.popleft())
    return islenmis

# normalize_columns: Türkçe/ASCII alias → kanonik ad
_SUTUN_ALIAS = {
    "unvan": COL_UNVAN,
    "firma": COL_UNVAN,
    "sirket": COL_UNVAN,
    "company": COL_UNVAN,
    "name": COL_UNVAN,
    "sicil": COL_SICIL,
    "no": COL_SICIL,
    "id": COL_SICIL,
    "sira": COL_SICIL,
    "web": COL_WEB,
    "website": COL_WEB,
    "site": COL_WEB,
    "url": COL_WEB,
    "domain": COL_WEB,
    "email": COL_EMAIL,
    "mail": COL_EMAIL,
    "e-posta": COL_EMAIL,
    "eposta": COL_EMAIL,
    "skor": COL_SKOR,
    "durum": COL_DURUM,
    "status": COL_DURUM,
    "adayweb": COL_ADAY_WEB,
    "redneden": COL_RED_NEDEN,
    "adayemail": COL_ADAY_EMAIL,
    "adaymail": COL_ADAY_EMAIL,
    "ilce": COL_ILCE,
    "ilcesi": COL_ILCE,
    "district": COL_ILCE,
    "semt": COL_ILCE,
}


def _sutun_anahtar(col) -> str:
    """Sütun adını karşılaştırma için sadeleştirir (İ/ı → i, ş → s, …).

    Python `'İLÇE'.lower()` → `i̇lçe` (noktalı i); önce İ/I/ı map edilir.
    """
    key = str(col).strip().replace("\ufeff", "")
    key = (
        key.replace("İ", "i")
        .replace("I", "i")
        .replace("ı", "i")
        .replace("i\u0307", "i")
    )
    key = key.lower().replace("\u0307", "")
    key = (
        key.replace("ş", "s")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
        .replace("ğ", "g")
    )
    key = re.sub(r"[^a-z0-9]+", "", key)
    return key


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Yaygın sütun adlarını kanonik forma çevirir.

    Kanonik: SİCİL, UNVAN, WEB, EMAIL, SKOR, DURUM, İLÇE (opsiyonel).
    'Sicil' / 'SICIL' / 'sicil' → SİCİL; 'Firma' → UNVAN; 'Ilce' → İLÇE vb.
    """
    if df is None:
        return df

    out = df.copy()
    rename = {}
    for col in list(out.columns):
        hedef = _SUTUN_ALIAS.get(_sutun_anahtar(col))
        if hedef and col != hedef:
            rename[col] = hedef

    if rename:
        out = out.rename(columns=rename)

    # Aynı kanonik ada birden fazla sütun düşerse ilkini tut
    out = out.loc[:, ~out.columns.duplicated()]
    return out


_BOS_DEGERLER = frozenset({"", "nan", "none", "<na>", "nat", "null", "-"})


def dolu_hucre_sayisi(series: pd.Series) -> int:
    """Gerçekten dolu hücre sayısı (pandas NA / 'nan' / 'None' sayılmaz)."""
    if series is None or len(series) == 0:
        return 0
    dolu = series.notna()
    as_str = series.astype(str).str.strip().str.lower()
    dolu &= ~as_str.isin(_BOS_DEGERLER)
    dolu &= ~series.isna()
    return int(dolu.sum())


def temiz_ilce(deger) -> str:
    """İLÇE hücresini sorgu için temizler; boş/NaN ise ''.

    Sütun zorunlu değildir — yoksa veya hücre boşsa arama ilçesiz yürür.
    """
    if deger is None:
        return ""
    try:
        if pd.isna(deger):
            return ""
    except Exception:
        pass
    s = str(deger).strip()
    if not s or s.lower() in ("nan", "none", "nat", "-", "—", "."):
        return ""
    return s


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class TqdmLoggingHandler(logging.Handler):
    """tqdm ile uyumlu logging handler — çakışmayı önler."""

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg, end="\n")
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logging(log_file: str, name: str = __name__) -> logging.Logger:
    """Dosya + tqdm-uyumlu konsol handler'ları ile logger oluşturur."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Aynı handler'ları tekrar eklememek için kontrol
    if logger.handlers:
        return logger

    # Dosya handler — detaylı format, otomatik döndürme (5MB, 3 yedek)
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )

    # Konsol handler — sade format
    console_handler = TqdmLoggingHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------------------------
# Türkçe Normalizasyon
# ---------------------------------------------------------------------------

TR_MAP = str.maketrans(
    {
        "ı": "i", "İ": "i",
        "ş": "s", "Ş": "s",
        "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o",
    }
)


def normalize_tr(s: str) -> str:
    """Türkçe karakterleri sadeleştir, küçült, sadece harf/rakam bırak."""
    s = s.translate(TR_MAP).lower()
    return "".join(ch for ch in s if ch.isalnum())


# ---------------------------------------------------------------------------
# Marka / domain skorlama (sitebul + şüpheli analiz ortak)
# ---------------------------------------------------------------------------

# Firma adında marka OLMAYAN genel / sektör / hukuki / coğrafya kelimeleri.
GENEL_KELIMELER = {
    "limited", "ltd", "sirketi", "sti", "anonim", "as",
    "sanayi", "sanayii", "san", "ticaret", "tic", "dis", "ithalat", "ihracat",
    "malzeme", "malzemeler", "malzemeleri",
    "insaat", "dekorasyon", "yapi", "mimarlik", "proje", "danismanlik",
    "muhendislik", "otomotiv", "bilisim", "hizmetleri", "hizmet",
    "test", "laboratuvar", "belgelendirme", "gida", "tekstil",
    "elektrik", "elektronik", "makina", "makine", "enerji", "turizm",
    "saglik", "egitim", "grup", "holding", "ve", "ile",
    "emlak", "gayrimenkul", "yonetim", "yonetimi", "tasarim",
    "danismanligi",
    "konut", "konutlari", "residence", "residences",
    "girisim", "girisimcilik", "holding", "investment", "yatirim",
    "taahhut", "taahhutculuk", "muteahhit", "muteahhitlik",
    "vakif", "dernek", "kulubu", "kulup",
    "sube", "subesi", "branch", "merkez", "genel",
    "istanbul", "ankara", "izmir", "bursa", "antalya", "adana",
    "kocaeli", "gaziantep", "konya", "mersin", "diyarbakir",
    "turkey", "turkiye", "turk", "international", "global",
    # Sektör / faaliyet kelimeleri (marka değil)
    "nakliyat", "nakliye", "lojistik", "tasimacilik", "kargo",
    "medikal", "tibbi", "plastik", "ambalaj", "paketleme",
    "mobilya", "dekor", "ahsap", "metal", "celik",
    "reklamcilik", "reklam", "matbaa", "ajans",
    "temizlik", "guvenlik", "cevre", "peyzaj",
    "market", "magaza", "perakende", "toptan",
    "otel", "pansiyon", "restoran", "restaurant", "lokanta",
    "bilgisayar", "yazilim", "teknoloji", "iletisim", "telekomunikasyon",
    "kentsel", "donusum", "arazi", "gelistirme",
    "corporation", "construction", "company", "group", "enterprise",
    # Yön / coğrafya kelimeleri (marka değil)
    "bati", "dogu", "kuzey", "guney", "marmara", "anadolu", "trakya",
    # Ek sektör kelimeleri
    "mekanik", "madencilik", "maden", "hafriyat", "mermer",
}

# Domain'de TLD sayılan (marka olmayan) etiketler
GENEL_TLD = {
    "com", "net", "org", "tr", "co", "gov", "edu",
    "io", "biz", "info", "web", "gen",
}


def unvan_subeli_mi(unvan: str) -> bool:
    """Ünvanda şube ifadesi var mı? (kelime bazlı, yanlış pozitif azaltır)"""
    if not isinstance(unvan, str) or not unvan.strip():
        return False
    for kelime in unvan.replace(".", " ").replace("/", " ").split():
        n = normalize_tr(kelime)
        if n in {"sube", "subesi", "branch"} or n.startswith("subesi"):
            return True
    return False


def marka_tokenlari(unvan: str) -> list[str]:
    """Firma ünvanından marka (ayırt edici) kelimeleri çıkarır."""
    tokenlar = []
    for kelime in str(unvan).split():
        norm = normalize_tr(kelime)
        if not norm or len(norm) < 2:
            continue
        if norm in GENEL_KELIMELER:
            continue
        tokenlar.append(norm)

    # Tüm kelimeler genel ise ünvanın ilk kelimesini fallback olarak kullan
    if not tokenlar:
        for kelime in str(unvan).split():
            norm = normalize_tr(kelime)
            if norm and len(norm) >= 2:
                tokenlar.append(norm)
                break
    return tokenlar


# Tek başına kaldığında güvenilir marka sayılmayan / sık yanlış pozitif üreten kelimeler
ZAYIF_TEK_TOKENLAR = {
    "ileri",
    "yeni",
    "ilk",
    "ozel",
    "genel",  # GENEL_KELIMELER'de de olabilir; yine de
    "pro",
    "plus",
    "max",
    "ultra",
    "mega",
    "super",
    "best",
    "top",
    "star",
    "gold",
    "silver",
    "premium",
    "modern",
    "aktif",
    "garanti",
    "guven",
    "hizli",
    "kolay",
    "ucuz",
    "ucretsiz",
}


def zayif_tek_marka_tokeni(unvan: str) -> bool:
    """Ünvanda tek ayırt edici token kaldıysa ve bu token zayıf/kısa mı?

    True ise skor ≥ yüksek eşik olsa bile direkt kabul edilmemeli;
    title veya LLM onayı gerekir (AS İLERİ → yalnızca 'ileri' gibi).
    """
    tokenlar = marka_tokenlari(unvan)
    if len(tokenlar) != 1:
        return False
    t = tokenlar[0]
    if len(t) <= 5:
        return True
    if t in ZAYIF_TEK_TOKENLAR:
        return True
    return False


def kisa_marka_mi(unvan: str) -> bool:
    """Marka token'larından herhangi biri ≤3 harf mi? (VKV, ATA, ABC…)

    Kısa kısaltmalar büyük kurum domain'lerine yapışır; direkt kabul yok.
    """
    return any(len(t) <= 3 for t in marka_tokenlari(unvan))


# Ünvanda inşaat / yapı sektörü sinyali (ham kelimeler; GENEL listeden önce)
_SEKTOR_INSAAT_UNVAN = {
    "insaat",
    "yapi",
    "yapı",
    "muteahhit",
    "taahhut",
    "taahhüt",
    "konut",
    "mimarlik",
    "mimarlık",
    "dekorasyon",
    "gayrimenkul",
    "emlak",
    "restorasyon",
    "kentsel",
    "donusum",
    "dönüşüm",
}

# Müteahhit proje/müşteri kelimeleri. Yalnız domain etiketinde red
# (atayatirim, grandhotel). Google title/snippet'te yok sayılır.
_SEKTOR_UYUMSUZ_PROJE = {
    "yatirim",
    "yatırım",
    "investment",
    "holding",
    "hastane",
    "hospital",
    "muze",
    "museum",
    "okul",
    "kolej",
    "college",
    "school",
    "cami",
    "mosque",
    "kilise",
    "church",
    "otel",
    "hotel",
    "hostel",
    "pansiyon",
}

# Gerçekten başka iş: vakıf, banka, petshop, avukat… Domain ve özet ikisinde red.
_SEKTOR_UYUMSUZ_SERT = {
    "vakif",
    "vakfi",
    "foundation",
    "dernek",
    "association",
    "menkul",
    "broker",
    "borsa",
    "banka",
    "bank",
    "finans",
    "finance",
    "sigorta",
    "insurance",
    "university",
    "universite",
    "üniversite",
    "petshop",
    "pet",
    "veteriner",
    "restoran",
    "restaurant",
    "kafe",
    "cafe",
    "lokanta",
    "kebap",
    "pide",
    "doner",
    "baklava",
    "pastane",
    "pizza",
    "burger",
    "kurs",
    "dershane",
    "kres",
    "anaokulu",
    "eczane",
    "pharmacy",
    "kuafor",
    "berber",
    "guzellik",
    "spa",
    "avukat",
    "hukuk",
    "noter",
    "lawyer",
    "spor",
    "fitness",
    "gym",
    "sinema",
    "tiyatro",
    "cinema",
    "menu",
    "kirtasiye",
    "papeterie",
    "telekom",
    "telecom",
}

_SEKTOR_UYUMSUZ = _SEKTOR_UYUMSUZ_SERT | _SEKTOR_UYUMSUZ_PROJE


def unvan_insaat_sektorlu_mu(unvan: str) -> bool:
    """Ünvan inşaat/yapı/emlak sektörüne mi işaret ediyor?"""
    for kelime in str(unvan or "").replace(".", " ").split():
        n = normalize_tr(kelime)
        if n in _SEKTOR_INSAAT_UNVAN:
            return True
    return False


def _norm_kume(kaynak: set[str]) -> set[str]:
    return {normalize_tr(x) for x in kaynak}


def _etiket_kumede(etiket: str, kume: set[str], *, min_len: int = 4) -> bool:
    """Etiket kümede mi, veya en az min_len karakterlik küme üyesi içinde mi?"""
    n = normalize_tr(etiket)
    if not n:
        return False
    if n in kume:
        return True
    return any(len(k) >= min_len and k in n for k in kume)


def _domain_insaat_etiketi_var(domain: str) -> bool:
    """Host'ta yapi/insaat/construction ailesi var mı? (dossayapi → yapi)."""
    ailesi = _norm_kume(_SEKTOR_INSAAT_UNVAN) | _norm_kume(_SEKTOR_AILELER[0])
    return any(_etiket_kumede(et, ailesi) for et in domain_marka_etiketleri(domain))


def sektor_uyumsuz_mu(unvan: str, domain: str = "", title: str = "", snippet: str = "") -> bool:
    """İnşaat ünvanı ↔ vakıf/banka/petshop veya yatırım-domain sapması mı?

    otel/hastane/yatırım yalnız host'ta red (atayatirim). SERP özeti proje
    cümlesi sayılır, red değil. Vakıf/banka/petshop domain ve özet ikisinde red.
    Host'ta yapi/insaat varsa otelinsaat gibi proje+inşaat etiketi de red değil.
    Soyad + başka faaliyet eki (basogluoto, kadioglureklam) tüm ünvanlarda red.
    """
    if domain and domain_yabanci_sektor_eki_mi(unvan, domain):
        return True
    if not unvan_insaat_sektorlu_mu(unvan):
        return False
    if domain and ".av.tr" in domain.lower():
        return True

    sert = _norm_kume(_SEKTOR_UYUMSUZ_SERT)
    proje = _norm_kume(_SEKTOR_UYUMSUZ_PROJE)
    insaat_domain = _domain_insaat_etiketi_var(domain)

    for et in domain_marka_etiketleri(domain):
        if _etiket_kumede(et, sert):
            return True
        if not insaat_domain and _etiket_kumede(et, proje):
            return True

    domain_kelimeler = set(metin_kelimeleri_normalize((domain or "").replace(".", " ")))
    if domain_kelimeler & sert:
        return True
    if not insaat_domain and domain_kelimeler & proje:
        return True

    serp_kelimeler = set(
        metin_kelimeleri_normalize(" ".join([title or "", snippet or ""]))
    )
    return bool(serp_kelimeler & sert)


def dogrulama_zorunlu_mu(unvan: str, domain: str = "") -> bool:
    """Direkt kabul yerine title/LLM zorunlu mu?"""
    if zayif_tek_marka_tokeni(unvan) or kisa_marka_mi(unvan):
        return True
    if domain and sektor_uyumsuz_mu(unvan, domain):
        return True
    return False


# Jenerik / ticari TLD — ülke kodu sayılmaz (.co = ticari, .co.uk = İngiltere).
_JENERIK_TLD_SONLARI = (
    ".com",
    ".net",
    ".org",
    ".biz",
    ".info",
    ".online",
    ".site",
    ".xyz",
    ".app",
    ".dev",
    ".io",
    ".co",
    ".me",
    ".cc",
    ".tv",
    ".pro",
    ".name",
    ".cloud",
    ".shop",
    ".store",
    ".web",
)

_TR_ULKE_KELIMELER = {
    "turkiye",
    "turkey",
    "turkish",
    "turkce",
    "istanbul",
    "ankara",
    "izmir",
    "bursa",
    "antalya",
    "adana",
    "kocaeli",
    "gaziantep",
    "konya",
    "mersin",
    "diyarbakir",
    "marmara",
    "anadolu",
    "trakya",
}

# Ünvanda faaliyet sektörü. ticaret/sanayi evrak kelimesidir, buraya konmaz —
# yoksa her LTD ŞTİ için sayfada "ticaret" aranır ve doğru siteler düşer.
_SEKTOR_POZITIF = _SEKTOR_INSAAT_UNVAN | {
    "muhendislik",
    "muhendis",
    "imalat",
    "uretim",
    "lojistik",
    "nakliye",
    "nakliyat",
    "otomotiv",
    "elektrik",
    "elektronik",
    "mekanik",
    "plastik",
    "aluminyum",
    "celik",
    "tekstil",
    "gida",
    "turizm",
    "madencilik",
    "enerji",
    "kimya",
    "mobilya",
    "yazilim",
    "bilisim",
    "teknoloji",
    "reklam",
    "reklamcilik",
    "matbaa",
    "ajans",
    "kargo",
    "tasimacilik",
    "hafriyat",
    "mermer",
    "ambalaj",
    "paketleme",
    "medikal",
    "tibbi",
    "temizlik",
    "guvenlik",
    "ahsap",
    "makine",
    "makina",
    "construction",
    "engineering",
    "metal",
}

# Aynı faaliyet ailesi: ünvanda inşaat varsa adayda yapı/construction da sayılır.
_SEKTOR_AILELER = (
    {
        "insaat",
        "yapi",
        "construction",
        "muteahhit",
        "taahhut",
        "konut",
        "mimarlik",
        "muhendislik",
        "muhendis",
        "engineering",
        "dekorasyon",
        "gayrimenkul",
        "emlak",
        "restorasyon",
        "hafriyat",
        "mermer",
    },
    {"lojistik", "nakliye", "nakliyat", "kargo", "tasimacilik"},
    {"gida", "food"},
    {"tekstil", "textile"},
    {"otomotiv", "automotive"},
    {"yazilim", "bilisim", "teknoloji", "software"},
    {"reklam", "reklamcilik", "matbaa", "ajans"},
    {"ambalaj", "paketleme"},
    {"medikal", "tibbi"},
    {"makine", "makina", "mekanik"},
    {"mobilya", "ahsap"},
)

# Domain eki: soyad + faaliyet (basogluoto, serefnakliyat, kadioglureklam).
# "oto"/"auto" yalnız ek tespiti içindir — title'da "foto" ile karışmasın.
_EKI_INSAAT = frozenset(_SEKTOR_AILELER[0])
_EKI_LOJISTIK = frozenset(_SEKTOR_AILELER[1])
_EKI_GIDA = frozenset(_SEKTOR_AILELER[2])
_EKI_TEKSTIL = frozenset(_SEKTOR_AILELER[3])
_EKI_OTOMOTIV = frozenset(_SEKTOR_AILELER[4] | {"oto", "auto"})
_EKI_REKLAM = frozenset(_SEKTOR_AILELER[6])

_SEKTOR_EKI_GRUPLAR = (
    _EKI_INSAAT,
    _EKI_LOJISTIK,
    _EKI_GIDA,
    _EKI_TEKSTIL,
    _EKI_OTOMOTIV,
    _EKI_REKLAM,
)

# Ünvanda sektör yokken inşaat eki serbest (ahmetinsaat); bunlar değil.
_SEKTOR_EKI_YABANCI = frozenset({_EKI_LOJISTIK, _EKI_OTOMOTIV, _EKI_REKLAM})

_SEKTOR_EKI_MIN_MARKA = 4


def _sektor_eki_harita() -> tuple[tuple[str, frozenset[str]], ...]:
    harita: list[tuple[str, frozenset[str]]] = []
    for aile in _SEKTOR_EKI_GRUPLAR:
        for kelime in aile:
            k = normalize_tr(kelime)
            if len(k) >= 3:
                harita.append((k, aile))
    harita.sort(key=lambda x: len(x[0]), reverse=True)
    return tuple(harita)


_SEKTOR_EKI_HARITA = _sektor_eki_harita()
_SEKTOR_EKI_KELIMELER = {k for k, _ in _SEKTOR_EKI_HARITA}


def _domain_sektor_eki_aileleri(domain: str) -> list[frozenset[str]]:
    """Host'ta yapışık veya tireli faaliyet eki aileleri (en uzun eşleşme)."""
    etiketler = [normalize_tr(e) for e in domain_marka_etiketleri(domain)]
    if not etiketler:
        return []
    bulunan: list[frozenset[str]] = []
    gorulen: set[int] = set()

    def _ekle(aile: frozenset[str]) -> None:
        kim = id(aile)
        if kim not in gorulen:
            gorulen.add(kim)
            bulunan.append(aile)

    for et in etiketler:
        for ek, aile in _SEKTOR_EKI_HARITA:
            if len(et) <= len(ek):
                continue
            if et.endswith(ek) and len(et) - len(ek) >= _SEKTOR_EKI_MIN_MARKA:
                _ekle(aile)
                break

    marka_etiket = any(
        len(et) >= _SEKTOR_EKI_MIN_MARKA and et not in _SEKTOR_EKI_KELIMELER
        for et in etiketler
    )
    if marka_etiket:
        for et in etiketler:
            for ek, aile in _SEKTOR_EKI_HARITA:
                if et == ek:
                    _ekle(aile)
                    break

    return bulunan


def _unvan_eki_aile_uyumlu(unvan: str, aile: frozenset[str]) -> bool:
    """Ünvan kelimeleri veya genişletilmiş sektör kümesi bu ek ailesinde mi?"""
    kelimeler = set(metin_kelimeleri_normalize(unvan))
    if kelimeler & aile:
        return True
    return bool(_sektor_arama_kumesi(unvan) & aile)


def domain_yabanci_sektor_eki_mi(unvan: str, domain: str) -> bool:
    """Domain, ünvan markasının yanında başka faaliyet eki mi taşıyor?

    BAŞOĞLU İNŞAAT → basogluoto.com evet; GÜNAY İNŞAAT → gunayinsaat.com hayır.
    """
    aileler = _domain_sektor_eki_aileleri(domain)
    if not aileler:
        return False

    unvan_sektorlu = bool(unvan_sektor_tokenlari(unvan))
    if not unvan_sektorlu:
        kelimeler = set(metin_kelimeleri_normalize(unvan))
        unvan_sektorlu = any(kelimeler & aile for aile in _SEKTOR_EKI_GRUPLAR)

    for aile in aileler:
        if _unvan_eki_aile_uyumlu(unvan, aile):
            continue
        if unvan_sektorlu or aile in _SEKTOR_EKI_YABANCI:
            return True
    return False


def _host_normalize(domain: str) -> str:
    d = (domain or "").lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def unvan_sektor_tokenlari(unvan: str) -> set[str]:
    """Ünvandaki faaliyet/sektör kelimeleri (inşaat, mühendislik, …)."""
    tokenlar: set[str] = set()
    for kelime in str(unvan or "").replace(".", " ").split():
        n = normalize_tr(kelime)
        if n in _SEKTOR_POZITIF:
            tokenlar.add(n)
    return tokenlar


def unvan_faaliyet_kelimeleri(unvan: str, limit: int = 2) -> list[str]:
    """Google sorgusu için faaliyet kelimeleri (ünvan yazımı, en fazla `limit`).

    sanayi/ticaret/ltd buraya girmez — _SEKTOR_POZITIF'te yoklar.
    Domain skorundaki marka tokenları değişmez.
    """
    bulunan: list[str] = []
    gorulen: set[str] = set()
    for kelime in str(unvan or "").replace(".", " ").split():
        n = normalize_tr(kelime)
        if not n or n not in _SEKTOR_POZITIF or n in gorulen:
            continue
        gorulen.add(n)
        bulunan.append(kelime)
        if len(bulunan) >= limit:
            break
    return bulunan


# Kısa / jenerik ilçe adları sektör ikamesi veya "başka ilçe" reddi olmaz.
_JENERIK_ILCE = {
    "merkez",
    "of",
    "il",
    "ilce",
    "ilcesi",
    "mahalle",
    "belde",
    "koy",
    "kent",
    "yeni",
    "kale",
    "pazar",
    "saray",
    "ova",
    "dere",
}

# Ters red için ayırt edici ilçe adları (kelime sınırı). Jenerik/çok kısa yok.
_TR_ILCE_ADLARI = {
    normalize_tr(x)
    for x in """
    kadikoy besiktas sisli uskudar fatih bakirkoy bahcelievler bagcilar
    kucukcekmece buyukcekmece sariyer umraniye maltepe pendik kartal tuzla
    atasehir cekmekoy sultangazi esenyurt avcilar gaziosmanpasa eyupsultan
    bayrampasa zeytinburnu beylikduzu silivri catalca sile arnavutkoy
    basaksehir sultanbeyli cekmekoy
    cankaya yenimahalle kecioren mamak sincan etimesgut golbasi polatli
    pursaklar cubuk elmadag akyurt nallihan
    bornova karsiyaka konak buca cigli bayrakli gaziemir karabaglar
    bergama tire torbali urla cesme seferihisar menderes
    osmangazi yildirim nilufer gemlik inegol mudanya iznik orhangazi
    mustafakemalpasa yenisehir
    muratpasa kepez dosemealti konyaalti alanya manavgat serik kemer
    seyhan yuregir cukurova ceyhan kozan
    izmit gebze darica korfez derince golcuk basiskele kandira
    sehitkamil sahinbey nizip
    selcuklu meram karatay eregli
    melikgazi kocasihan talas
    toroslar mezitli tarsus
    baglar kayapinar
    atakum ilkadim canik
    odunpazari tepebasi
    corlu cerkezkoy ergene kapakli
    bodrum fethiye marmaris mentese milas
    efeler nazilli soke kusadasi didim
    pamukkale merkezefendi
    antakya iskenderun defne samandag
    yunusemre sehzadeler akhisar turgutlu soma
    """.split()
    if len(normalize_tr(x)) >= 4
} - _JENERIK_ILCE


def ilce_sinyali_uygun_mu(ilce: str) -> bool:
    """İlçe sektör ikamesi / ters red için yeterince ayırt edici mi?"""
    t = normalize_tr(temiz_ilce(ilce)).replace("ilcesi", "").strip()
    if len(t) < 4:
        return False
    if t in _JENERIK_ILCE:
        return False
    return True


def ilce_metinde_mi(ilce: str, metin: str) -> bool:
    """Sicil ilçesi metinde ayrı kelime olarak geçiyor mu?"""
    if not metin or not ilce_sinyali_uygun_mu(ilce):
        return False
    t = normalize_tr(temiz_ilce(ilce)).replace("ilcesi", "").strip()
    parcalar = [p for p in t.split() if len(p) >= 3]
    if len(parcalar) >= 2:
        return all(token_metinde_kelime(p, metin) for p in parcalar)
    return token_metinde_kelime(t, metin)


def _net_baska_ilce_var(metin: str, sicil_ilce: str) -> bool:
    """Sayfada sicil dışındaki ayırt edici bir ilçe adı var mı?"""
    if not metin:
        return False
    kelimeler = set(metin_kelimeleri_normalize(metin))
    bizim = normalize_tr(temiz_ilce(sicil_ilce)).replace("ilcesi", "").strip()
    bizim_parca = set(bizim.split()) | {bizim}
    for ad in _TR_ILCE_ADLARI:
        if ad in bizim_parca:
            continue
        if ad in kelimeler:
            return True
    return False


def _sayfa_yabanci_sektor_mu(unvan: str, metin: str) -> bool:
    """Sayfada ünvandakinden farklı faaliyet ailesi (ve kendi sektör yok) mı?"""
    bizim = _sektor_arama_kumesi(unvan)
    if not bizim or not metin:
        return False
    kelimeler = set(metin_kelimeleri_normalize(metin))
    if kelimeler & bizim:
        return False
    for aile in _SEKTOR_AILELER:
        if aile & bizim:
            continue
        if kelimeler & aile:
            return True
    uyumsuz = {normalize_tr(x) for x in _SEKTOR_UYUMSUZ_SERT}
    return bool(kelimeler & uyumsuz)


def _ilce_yabanci_sektor_red(unvan: str, ilce: str, govde: str) -> bool:
    """Sicil ilçesi yok + net başka ilçe + başka sektör → red."""
    if not govde or not ilce_sinyali_uygun_mu(ilce):
        return False
    if ilce_metinde_mi(ilce, govde):
        return False
    if not _net_baska_ilce_var(govde, ilce):
        return False
    return _sayfa_yabanci_sektor_mu(unvan, govde)


def _tr_ulke_sinyali(
    domain: str, title: str = "", snippet: str = "", ilce: str = "", govde: str = ""
) -> bool:
    """Aday Türkiye'de mi? .tr TLD, TR coğrafya kelimesi veya verilen ilçe."""
    d = _host_normalize(domain)
    if d == "tr" or d.endswith(".tr"):
        return True
    blob = " ".join([title or "", snippet or "", govde or ""])
    kelimeler = set(metin_kelimeleri_normalize(blob))
    if kelimeler & _TR_ULKE_KELIMELER:
        return True
    ilce_t = temiz_ilce(ilce)
    if ilce_t and token_metinde_kelime(normalize_tr(ilce_t), blob):
        return True
    return False


def _yabanci_cctld_mi(domain: str) -> bool:
    """TR ve jenerik TLD değilse ülke kodlu yabancı alan adı."""
    d = _host_normalize(domain)
    if not d or d == "tr" or d.endswith(".tr"):
        return False
    for s in _JENERIK_TLD_SONLARI:
        if d == s.lstrip(".") or d.endswith(s):
            return False
    son = d.split(".")[-1]
    return len(son) == 2 and son.isalpha()


def _sektor_arama_kumesi(unvan: str) -> set[str]:
    """Ünvan sektör token'larını aynı faaliyet ailesiyle genişletir."""
    tokenlar = unvan_sektor_tokenlari(unvan)
    if not tokenlar:
        return set()
    genis = set(tokenlar)
    for aile in _SEKTOR_AILELER:
        if genis & aile:
            genis |= aile
    return genis


def _sektor_pozitif_sinyal(
    unvan: str, domain: str = "", title: str = "", snippet: str = "", govde: str = ""
) -> bool:
    """Ünvandaki sektör (veya aynı aile) domain/title/snippet/gövde'de geçiyor mu?"""
    tokenlar = _sektor_arama_kumesi(unvan)
    if not tokenlar:
        return False
    kelimeler = set(
        metin_kelimeleri_normalize(" ".join([title or "", snippet or "", govde or ""]))
    )
    if kelimeler & tokenlar:
        return True
    for et in domain_marka_etiketleri(domain):
        if et in tokenlar:
            return True
        for t in tokenlar:
            if len(t) >= 4 and t in et:
                return True
    return False


# Muafiyetin geçerli sayılması için domain'in karşıladığı ünvan önekinin
# en az bu kadar harf olması gerekir (YCD → 3 harf, muafiyet yok).
_TAM_MARKA_MIN_UZUNLUK = 5

# Ünvan kelimesi → domain'de görülen İngilizce karşılık (ata + silah → ataarms).
_TOKEN_CEVIRI = {
    "silah": ("arms", "gun"),
    "asindirici": ("abrasive", "abrasives"),
    "asindiricilari": ("abrasive", "abrasives"),
}

# Title'da marka dışı sayılmayan dolgu (kompakt title kontrolü).
_TITLE_DOLGU = {
    "home",
    "homepage",
    "official",
    "resmi",
    "site",
    "website",
    "anasayfa",
    "www",
    "welcome",
    "hosgeldiniz",
    "index",
}


def _token_cevirileri(token: str) -> tuple[str, ...]:
    t = (token or "").strip().lower()
    if not t:
        return ()
    return (t,) + _TOKEN_CEVIRI.get(t, ())


def _kisa_onek_atlanabilir(tokenlar: list[str], atlanan: int) -> bool:
    """Baştan `atlanan` token'ın hepsi ≤3 harf kısaltma mı? (ENB, TM, GES)."""
    if atlanan <= 0:
        return True
    if atlanan >= len(tokenlar):
        return False
    return all(len(tokenlar[j]) <= 3 for j in range(atlanan))


def unvan_cekirdek_adaylari(unvan: str) -> set[str]:
    """Domain çekirdeği olabilecek bitişik token birleşimleri.

    Baştan birleşim + başına kısa kısaltma (ENB) atlanmış birleşimler +
    sektör çevirisi (silah→arms). Soyad atılmaz: TUĞBA EFEOĞLU → efeoglu yok.
    """
    tokenlar = marka_tokenlari(unvan)
    aday: set[str] = set()
    n = len(tokenlar)
    for i in range(n):
        if not _kisa_onek_atlanabilir(tokenlar, i):
            continue
        varyantlar = [""]
        for t in tokenlar[i:]:
            alts = _token_cevirileri(t)
            if not alts:
                break
            varyantlar = [p + a for p in varyantlar for a in alts]
            for v in varyantlar:
                if len(v) >= _TAM_MARKA_MIN_UZUNLUK:
                    aday.add(v)
            if varyantlar and min(len(v) for v in varyantlar) > 48:
                break
    return aday


def ayirt_edici_token_domain_mi(unvan: str, domain: str) -> bool:
    """Domain çekirdeği, ünvandaki tek bir ayırt edici token (≥5 harf) mı?

    EKŞİOĞLU KANEK → kanek.com.tr. Soyad domain'i (efeoglu.com) .com'da
    tam_marka sayılmaz; bu yardımcı yalnız .tr + sayfa onayında kullanılır.
    """
    cekirdek = "".join(domain_marka_etiketleri(domain))
    if len(cekirdek) < _TAM_MARKA_MIN_UZUNLUK:
        return False
    return any(
        t == cekirdek
        for t in marka_tokenlari(unvan)
        if len(t) >= _TAM_MARKA_MIN_UZUNLUK
    )


def title_marka_kompakt_mi(unvan: str, title: str) -> bool:
    """Title büyük ölçüde marka adı mı? (pazar yeri cümlesi değil).

    'ORTASAN' / 'ORTASAN Sanayi' → True. 'Gerber Baby Food' → False.
    'Homes.com: Houses for Sale' → False. HOMES/GERBER çakışmasını keser.
    """
    tokenlar = marka_tokenlari(unvan)
    if not tokenlar or not (title or "").strip():
        return False
    kelimeler = metin_kelimeleri_normalize(title)
    if not kelimeler:
        return False
    marka_kume = set(tokenlar)
    birlesik = "".join(tokenlar)
    ceviri = {a for t in tokenlar for a in _token_cevirileri(t)}
    kalan = []
    for k in kelimeler:
        if len(k) <= 2:
            continue
        if k in marka_kume or k in ceviri or k in GENEL_KELIMELER or k in _TITLE_DOLGU:
            continue
        if k == birlesik:
            continue
        if any(len(t) >= 4 and (t == k or t in k or k in t) for t in tokenlar):
            continue
        kalan.append(k)
    return len(kalan) <= 1


def tam_marka_eslesmesi_mi(unvan: str, domain: str) -> bool:
    """Domain çekirdeği, ünvanın marka token birleşimine eşit mi?

    Domain, ünvanda olmayan hiçbir şey söylemiyorsa güçlü kimlik sinyalidir:

        NİLPA İNŞAAT              + nilpa.com.tr        → True
        DUMAN HİDROLİK YAPI …     + dumanhidrolik.com   → True
        ASİLLER OTO YEDEK PARÇA … + asilleroto.com      → True
        ENB ENGİN BANT            + enginbant.com       → True (kısa önek ENB)
        ATA SİLAH                 + ataarms.com         → True (silah→arms)

    Domain fazladan kelime/alt alan taşıyorsa False:

        SDK GAYRİMENKUL  + ai-sdk.dev          → çekirdek 'aisdk'
        BOSA MÜHENDİSLİK + bosa.belgium.be     → çekirdek 'bosabelgium'
        HEZA GAYRİMENKUL + fishing.heza.co.za  → çekirdek 'fishingheza'

    Kısa kısaltmalar (YCD → 'ycd') _TAM_MARKA_MIN_UZUNLUK ile elenir; onlar
    büyük kurum domain'lerine kolayca yapıştığı için ek doğrulama gerektirir.
    Soyad atılmaz: TUĞBA EFEOĞLU → efeoglu.com False.
    """
    cekirdek = "".join(domain_marka_etiketleri(domain))
    if len(cekirdek) < _TAM_MARKA_MIN_UZUNLUK:
        return False
    return cekirdek in unvan_cekirdek_adaylari(unvan)


def mail_marka_uyumlu(mail: str, unvan: str) -> bool:
    """E-postanın yerel kısmı veya domain'i ünvanın markasını taşıyor mu?

    Site zaten firmanın kendisi olarak doğrulanmıştır; sitede duran farklı
    domainli bir adres markayı taşıyorsa firmanın kendi adresi sayılır:

        MEDEMA İNŞAAT + medemainsaat@gmail.com  → True  (yerel kısım)
        MEDEMA İNŞAAT + info@medema.com.tr      → True  (kardeş domain)
        MEDEMA İNŞAAT + info@webtasarim.com     → False
        AS YAPI       + asistan@gmail.com       → False (token < 4 harf)

    Kısa marka token'ları (≤3 harf) rastgele kelimelere yapıştığı için
    hiçbir zaman tek başına eşleşme sayılmaz.
    """
    if "@" not in (mail or "") or not unvan:
        return False

    tokenlar = marka_tokenlari(unvan)
    if not tokenlar:
        return False
    marka = "".join(tokenlar)
    ilk = tokenlar[0]

    yerel, _, mail_domain = mail.partition("@")
    hedefler = [
        normalize_tr(yerel),
        "".join(domain_marka_etiketleri(mail_domain)),
    ]

    for hedef in hedefler:
        if not hedef:
            continue
        if len(marka) >= 4 and marka in hedef:
            return True
        if len(ilk) >= 4 and hedef.startswith(ilk):
            return True
    return False


def ulke_sektor_uyumlu_mu(
    unvan: str,
    domain: str,
    title: str = "",
    snippet: str = "",
    ilce: str = "",
    govde: str = "",
    marka_sayfada: bool = False,
) -> bool:
    """Aday ülke ve sektör olarak ünvana uyuyor mu? Tüm aramalarda kullanılır.

    - Yabancı ccTLD (.ug, .de, .uk…) ve TR sinyali yoksa hayır.
    - Ünvanda faaliyet sektörü varsa (inşaat, lojistik…) adayda da görünmeli.
      .tr tek başına yetmez; title/snippet/domain'de sektör ailesi aranır.
    - İLÇE sütunu opsiyoneldir. Sicil ilçesi (ayırt ediciyse) ana sayfa/iletişim
      gövdesinde geçiyorsa eksik sektör title'ını doldurur; tek başına KABUL değil.
    - Sicil ilçesi yok + net başka ilçe + başka sektör → hayır (şube: iki ilçe varsa red yok).
    - Sektör yok ve marka kısaysa: TR coğrafyası gerekir (yalnız title yetmez).
    - marka_sayfada: title/LLM markayı sayfada görmüş. Tam marka .com o zaman
      kompakt title ile kabul edilir (ORTASAN → ortasan.com). Direkt kabul
      (SERP-only) hâlâ TR ister — HOMES → homes.com otomatik girmez.
    """
    tr = _tr_ulke_sinyali(domain, title, snippet, ilce, govde=govde)
    if _yabanci_cctld_mi(domain) and not tr:
        return False
    if _ilce_yabanci_sektor_red(unvan, ilce, govde):
        return False
    tam = tam_marka_eslesmesi_mi(unvan, domain)
    cekirdek_len = len("".join(domain_marka_etiketleri(domain)))
    kisa_cekirdek = cekirdek_len <= _TAM_MARKA_MIN_UZUNLUK
    sayfa_tam_marka = (
        marka_sayfada
        and tam
        and not zayif_tek_marka_tokeni(unvan)
        and not (kisa_marka_mi(unvan) and kisa_cekirdek)
        and title_marka_kompakt_mi(unvan, title)
    )
    if unvan_sektor_tokenlari(unvan):
        if _sektor_pozitif_sinyal(unvan, domain, title, snippet, govde=govde):
            return True
        # Domain, ünvanın baştan gelen kelimelerinin birebir karşılığıysa kimlik
        # zaten kanıtlanmıştır; ayrıca sektör kelimesi şart koşmak marka-adı
        # domain'lerini (nilpa.com.tr, duzey.com.tr) haksız yere eliyordu.
        #
        # TR sinyali şart (direkt kabul): jenerik TLD'de bu muafiyet tek başına
        # HOMES İNŞAAT → homes.com, GERBER YAPI → gerber.com içeri alır.
        # Title/LLM markayı gördüyse kompakt title yeter (ortasan.com).
        # Ters yöndeki çelişki (petshop, vakıf…) sektor_uyumsuz_mu ile ayrıca
        # kontrol edildiği için bu muafiyet o korumayı zayıflatmaz.
        if tr and tam:
            return True
        if sayfa_tam_marka:
            return True
        # İkinci kelime marka + .tr (EKŞİOĞLU KANEK → kanek.com.tr)
        if (
            marka_sayfada
            and tr
            and ayirt_edici_token_domain_mi(unvan, domain)
        ):
            return True
        return ilce_metinde_mi(ilce, govde)
    if sayfa_tam_marka:
        return True
    if kisa_marka_mi(unvan):
        return tr
    return True


def kisa_marka_ek_sinyal_var(
    unvan: str,
    domain: str,
    title: str = "",
    snippet: str = "",
    ilce: str = "",
    govde: str = "",
    marka_sayfada: bool = False,
) -> bool:
    """ulke_sektor_uyumlu_mu takma adı (eski çağrılar)."""
    return ulke_sektor_uyumlu_mu(
        unvan, domain, title, snippet, ilce, govde, marka_sayfada=marka_sayfada
    )


def dogrulama_log_nedenleri(
    unvan: str,
    domain: str,
    *,
    kisa: bool,
    zayif: bool,
    sektor: bool,
    uyumlu: bool,
    title: str = "",
    snippet: str = "",
    ilce: str = "",
) -> list[str]:
    """Log için neden; Excel'de ÜLKE sütunu yoktur."""
    neden: list[str] = []
    if kisa:
        neden.append("kısa marka")
    if zayif:
        neden.append("zayıf token")
    if sektor:
        neden.append("sektör uyumsuz")
    if not uyumlu and not sektor:
        tr = _tr_ulke_sinyali(domain, title, snippet, ilce)
        if _yabanci_cctld_mi(domain) and not tr:
            neden.append("yabancı site")
        elif unvan_sektor_tokenlari(unvan):
            neden.append("sektör yok")
        else:
            neden.append("sicil ilçesi/Türkiye izi yok")
    return neden


def red_dogrulama_durumu(
    unvan: str, *, sektor_uyumsuz: bool, uyumlu: bool
) -> str:
    """Title/LLM onaylamayınca RED_SEKTOR mı RED_DOGRULAMA mı."""
    if sektor_uyumsuz:
        return DURUM_RED_SEKTOR
    if unvan_sektor_tokenlari(unvan) and not uyumlu:
        return DURUM_RED_SEKTOR
    return DURUM_RED_DOGRULAMA


def domain_cekirdek(netloc: str) -> str:
    """Domain'in marka kısmını döndürür. Örn: www.medema.com.tr -> medema"""
    return "".join(domain_marka_etiketleri(netloc)) or (
        (netloc or "").lower().replace("www.", "").replace(".", "")
    )


# Domain'e yapışık hukuki ekler (uzun → kısa). uscoltd → usco + ltd.
# "as"/"san"/"tic" bilinçli yok: atlas, nissan, plastic yanlış bölünmesin.
_DOMAIN_HUKUKI_EKLER = (
    "limited",
    "sirketi",
    "ltd",
    "sti",
)


def _hukuki_ek_ayir(parca: str) -> list[str]:
    """Yapışık hukuki eki ayırır. uscoltd → ['usco', 'ltd']; medema → ['medema']."""
    if not parca:
        return []
    for ek in _DOMAIN_HUKUKI_EKLER:
        if len(parca) <= len(ek):
            continue
        if not parca.endswith(ek):
            continue
        kok = parca[: -len(ek)]
        # Çok kısa kök yanlış pozitif (ör. xltd); en az 3 harf marka
        if len(kok) >= 3 and kok.isalnum():
            return [kok, ek]
    return [parca]


def domain_marka_etiketleri(netloc: str) -> list[str]:
    """www / TLD hariç domain etiketleri; tire vb. ile de bölünür.

    www.afy-insaat.com.tr → ['afy', 'insaat']
    uscoltd.com.tr → ['usco', 'ltd']  (yapışık hukuki ek)
    tevfikileriihl.meb.k12.tr → ['tevfikileriihl', 'meb', 'k12']
    """
    d = (netloc or "").lower().strip()
    if d.startswith("www."):
        d = d[4:]
    etiketler: list[str] = []
    for p in d.split("."):
        if not p or p in GENEL_TLD:
            continue
        for parca in re.split(r"[^a-z0-9]+", p):
            if parca:
                etiketler.extend(_hukuki_ek_ayir(parca))
    return etiketler


def token_domain_etiketinde(token: str, netloc: str) -> bool:
    """Token, domain'de ayrı etiket olarak geçiyor mu? (ortada yapışık alt dizge sayılmaz)."""
    t = (token or "").strip().lower()
    if len(t) < 2:
        return False
    return t in domain_marka_etiketleri(netloc)


def metin_kelimeleri_normalize(ham: str) -> list[str]:
    """Ham metni kelimelere bölüp Türkçe normalize eder (title kelime sınırı için)."""
    if not ham:
        return []
    kelimeler = []
    for raw in re.split(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+", str(ham)):
        n = normalize_tr(raw)
        if n:
            kelimeler.append(n)
    return kelimeler


def token_metinde_kelime(token: str, ham_metin: str) -> bool:
    """Token, ham metinde ayrı kelime olarak geçiyor mu?"""
    t = (token or "").strip().lower()
    if len(t) < 2:
        return False
    return t in metin_kelimeleri_normalize(ham_metin)


def marka_metinde_kelime_dizisi(marka: str, ham_metin: str) -> bool:
    """Birleşik marka, ardışık title kelimelerinin birleşimine eşit mi?"""
    m = (marka or "").strip().lower()
    if len(m) < 4:
        return False
    kelimeler = metin_kelimeleri_normalize(ham_metin)
    for i in range(len(kelimeler)):
        acc = ""
        for j in range(i, len(kelimeler)):
            acc += kelimeler[j]
            if acc == m:
                return True
            if len(acc) > len(m):
                break
    return False


# E-ticaret / şüpheli TLD — tek marka token ile kolay yanlış pozitif (.store vb.)
SUPHELI_TLD = (
    ".store",
    ".shop",
    ".shopping",
    ".online",
    ".site",
    ".xyz",
    ".top",
    ".click",
    ".link",
    ".fun",
    ".lol",
)


def domain_tld_cezasi(netloc: str) -> int:
    """Şüpheli TLD için skor cezası (0 veya negatif etki için çıkarılacak puan)."""
    d = (netloc or "").lower().strip()
    if d.startswith("www."):
        d = d[4:]
    for tld in SUPHELI_TLD:
        if d.endswith(tld):
            return 30
    return 0


_KISA_TEK_MARKA_BULANIK_TAVAN = 35


def kisa_tek_marka_bulanik_yasak(unvan: str, netloc: str) -> bool:
    """≤5 harflik tek marka domain'e eşit değilse bulanık skor kullanılmasın.

    iska ≠ izka (s/z). iska.com ve iskainsaat.com (tam önek) serbest.
    """
    tokenlar = marka_tokenlari(unvan)
    if len(tokenlar) != 1:
        return False
    marka = tokenlar[0]
    if len(marka) > 5:
        return False
    etiketler = domain_marka_etiketleri(netloc)
    if not etiketler:
        return False
    if marka in etiketler:
        return False
    cekirdek = "".join(etiketler)
    if marka == cekirdek:
        return False
    if len(cekirdek) > len(marka) and cekirdek.startswith(marka):
        return False
    return True


def benzerlik_skoru(unvan: str, netloc: str) -> int:
    """Firma adı ile domain arasında 0-100 benzerlik skoru.

    Token eşleşmesi DNS etiketi sınırında yapılır
    (ileri ⊂ tevfikileriihl → eşleşme sayılmaz).
    Şüpheli TLD (.store, .shop…) cezası uygulanır.
    """
    tokenlar = marka_tokenlari(unvan)
    marka = "".join(tokenlar)
    etiketler = domain_marka_etiketleri(netloc)
    cekirdek = "".join(etiketler)

    if not marka or not cekirdek:
        return 0

    # Tam marka = çekirdek veya tek etiket (kısa önek / silah→arms dahil)
    if (
        marka == cekirdek
        or marka in etiketler
        or cekirdek in unvan_cekirdek_adaylari(unvan)
    ):
        skor = 100
    # Domain, markanın öneki/eki ise: kısa kalan harfler farklı firma demektir
    # (polat ⊂ polatim, celik ⊂ celikel) → otomatik kabul skoruna ÇIKARMA
    elif len(cekirdek) >= 4 and marka.startswith(cekirdek):
        kalan = marka[len(cekirdek) :]
        if not kalan:
            skor = 100
        elif len(kalan) <= 3:
            # polatim ≠ polat — düşük skor (yüksek eşik altı / reddet bandı)
            skor = min(35, int(SequenceMatcher(None, marka, cekirdek).ratio() * 100))
        elif len(cekirdek) >= 5 and len(kalan) >= 4:
            skor = 100
        else:
            skor = min(55, int(SequenceMatcher(None, marka, cekirdek).ratio() * 100))
    elif len(cekirdek) >= 4 and marka.endswith(cekirdek):
        kalan = marka[: -len(cekirdek)]
        if not kalan:
            skor = 100
        elif len(kalan) <= 3:
            skor = min(35, int(SequenceMatcher(None, marka, cekirdek).ratio() * 100))
        elif len(cekirdek) >= 5 and len(kalan) >= 4:
            skor = 100
        else:
            skor = min(55, int(SequenceMatcher(None, marka, cekirdek).ratio() * 100))
    else:
        # Token(lar) ayrı etiket olarak (min 3; kelime/etiket sınırı sayesinde güvenli)
        eslesen = [t for t in tokenlar if len(t) >= 3 and t in etiketler]
        if eslesen:
            if tokenlar and tokenlar[0] in eslesen:
                skor = 90
            elif len(eslesen) >= 2:
                skor = 90
            elif len(tokenlar) == 1:
                skor = 90
            else:
                skor = int(SequenceMatcher(None, marka, cekirdek).ratio() * 100)
        else:
            skor = int(SequenceMatcher(None, marka, cekirdek).ratio() * 100)

    if kisa_tek_marka_bulanik_yasak(unvan, netloc):
        skor = min(skor, _KISA_TEK_MARKA_BULANIK_TAVAN)

    ceza = domain_tld_cezasi(netloc)
    if ceza:
        skor = max(0, skor - ceza)
    return skor


def unvan_domain_benzerlik(unvan: str, domain: str) -> int:
    """Ünvan markası ↔ domain, 0–100 (benzerlik_skoru ile aynı mantık)."""
    return benzerlik_skoru(unvan, domain)


def domain_from_web_or_email(web: str = "", email: str = "") -> str:
    """WEB veya EMAIL'den domain çıkarır."""
    web_s = "" if web is None else str(web).strip()
    if web_s and web_s.lower() not in ("nan", "none", "<na>"):
        u = web_s if "://" in web_s else "http://" + web_s
        try:
            host = urlparse(u).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        except Exception:
            pass

    mail_s = "" if email is None else str(email).strip()
    if mail_s and "@" in mail_s and mail_s.lower() not in ("nan", "none", "<na>"):
        host = mail_s.split("@", 1)[-1].lower().strip()
        if host.startswith("www."):
            host = host[4:]
        return host
    return ""


# ---------------------------------------------------------------------------
# Google sonuç ignore listeleri (sitebul; config kalıpları ek filtre)
# ---------------------------------------------------------------------------

IGNORE = {
    "google.com",
    "gstatic.com",
    "accounts.google",
    "support.google",
    "youtube.com",
    "maps.google",
    "translate.google",
    "maps.app.goo.gl",
    "goo.gl",
    "googleusercontent.com",

    # Firma rehberleri / dizinler / VKN
    "find.com.tr",
    "118.com.tr",
    "yenifirma.com",
    "sayfa.istanbul",
    "firmasec.com",
    "firmarehberi",
    "firma-rehberi",
    "firmalarrehberi",
    "firmabulucu",
    "firmaenvanteri",
    "sirketrehberi",
    "isrehberi",
    "telefonrehberi",
    "bulurum.com",
    "vknsorgula",
    "mukellef.info",
    "infobel.com",
    "dnb.com",
    "kompass",
    "cylex",
    "yellowpages",
    "iyifirma.com",
    "sirketdizin",
    "sirketler.com",
    "neredenalinir",
    "firmaara.com",
    "firmalar.com",
    "firmalistesi",
    "firma-listesi",
    "turkiyefirmalari",
    "turkfirmalari",
    "sirketara.com",
    "sirket.info",
    "sirket.gen.tr",
    "isyerlerim.com",
    "isyeri.com",
    "isyerleri",
    "isrehberi.com",
    "telefonrehberi.com",
    "rehberim.com",
    "rehberiniz",
    "bizimhesap.com",
    "ticaretgazetesi",
    "ticariyet.com",
    "ticariisletme",
    "ticari.web.tr",
    "nacekodu",
    "vergino",
    "vergikimlik",
    "vkn.io",
    "vkn.net",
    "vknsorgulama",
    "mersis",
    "kap.org.tr",
    "kamupersoneli",
    "bloomberght.com",
    "kap.org",
    "ratingagency",
    "dunandbradstreet",
    "zoominfo.com",
    "apollo.io",
    "lusha.com",
    "hunter.io",
    "clearbit.com",
    "signalhire.com",
    "opengovus",
    "opencorporates.com",
    "northdata.com",
    "companieshouse",

    # Pazaryeri / ilan / ikinci el
    "n11.com",
    "trendyol.com",
    "hepsiburada.com",
    "amazon.com",
    "amazon.com.tr",
    "pazarama.com",
    "ciceksepeti.com",
    "gittigidiyor",
    "akakce.com",
    "cimri.com",
    "epey.com",
    "pttavm.com",
    "morhipo.com",
    "lcwaikiki.com",
    "letgo.com",
    "dolap.com",
    "arabam.com",
    "sahibinden.com",
    "hepsiemlak.com",
    "emlakjet.com",
    "hurriyetemlak.com",
    "zingat.com",
    "emlakkulisi.com",
    "remax.com.tr",
    "coldwellbanker",
    "teknosa.com",
    "vatanbilgisayar",
    "mediamarkt",
    "idefix.com",
    "yemeksepeti.com",
    "getir.com",
    "migros.com.tr",
    "gratis.com",

    # Oda / birlik / sicil / dernek
    "tso.org.tr",
    "tobb.org.tr",
    "ticaretodasi",
    "sanayiodasi",
    "esnafodasi",
    "denizticaretodasi",
    "tzob.org.tr",
    "tesk.org.tr",
    "ticaretsicil.gov.tr",
    "mersis.gtb.gov.tr",
    "gtb.gov.tr",
    "ticaret.gov.tr",
    "gib.gov.tr",
    "ito.org.tr",
    "atso.org.tr",
    "bso.org.tr",
    "aso.org.tr",
    "iso.org.tr",
    "tbb.org.tr",
    "tmsk.org.tr",
    "tmmob.org.tr",
    "mmo.org.tr",
    "emo.org.tr",
    "imo.org.tr",
    "beysiad.org.tr",
    "tobbemekder.org.tr",
    "etoist.org.tr",
    "iskid.org.tr",
    "isos.org.tr",
    "kosgeb.gov.tr",
    "osb.org.tr",
    "-osb.org",
    "sanayisitesi",

    # Sosyal medya / mesaj / video
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "pinterest.com",
    "reddit.com",
    "threads.net",
    "snapchat.com",
    "telegram.me",
    "telegram.org",
    "t.me/",
    "wa.me",
    "whatsapp.com",
    "vimeo.com",
    "dailymotion.com",
    "twitch.tv",

    # Haber / wiki / şikayet / içerik
    "haberler.com",
    "hurriyet.com.tr",
    "milliyet.com.tr",
    "sabah.com.tr",
    "sozcu.com.tr",
    "haber7.com",
    "ntv.com.tr",
    "cnnturk.com",
    "aa.com.tr",
    "dha.com.tr",
    "wikipedia.org",
    "wikizero",
    "eksisozluk.com",
    "sikayetvar.com",
    "sikayet.com",
    "sikayeti.com",
    "medium.com",
    "crunchbase.com",
    "quora.com",
    "stackoverflow.com",
    "github.com",
    "gitlab.com",
    "blogspot.com",
    "blogger.com",
    "wordpress.com",
    "wix.com",
    "wixsite.com",
    "sites.google.com",
    "notion.site",
    "carrd.co",

    # İş ilanı / freelance / hizmet pazarı
    "kariyer.net",
    "yenibiris.com",
    "secretcv.com",
    "eleman.net",
    "elemanonline",
    "isinolsun.com",
    "indeed.com",
    "glassdoor.com",
    "armut.com",
    "banaode.com",
    "bitaksi.com",
    "uber.com",
    "yandex.com",

    # Harita / app store / kısa link
    "maps.apple.com",
    "apple.com/maps",
    "play.google.com",
    "apps.apple.com",
    "appgallery.huawei",
    "bit.ly",
    "tinyurl.com",
    "t.co/",
    "linktr.ee",
    "bio.link",

    # Spor skor siteleri (kısa/ rastgele marka ile bulanık eşleşir)
    "sofascore.com",
    "flashscore.com",
    "livescore.com",

    # Diğer gürültü
    "itfaiye.ibb.gov.tr",
    "rekabet.gov.tr",
    "resmigazete.gov.tr",
    "mevzuat.gov.tr",
    "uyap.gov.tr",
    "e-devlet",
    "turkiye.gov.tr",
    "alfalaval.com.tr",
    "kombi-klimaservisi.com",
    "aratsana.com",
    "isletmeadresleri",
    "yapiprojeleri.com",
    "freshsignal.net",
    "lidergroup.org",
    "turkishexporter",
    "rocketreach",
    "cekici.biz",
    "tesisatturkiye.com",
    "emis.com",
    "tripadvisor.com",
    "booking.com",
    "airbnb.com",
    "hotels.com",
    "etsy.com",
    "ebay.com",
    "aliexpress.com",
    "alibaba.com",
    "made-in-china.com",
    "globalsources.com",
    "thomasnet.com",
    "europages",
    "wlw.de",
    "hotfrog",
    "brownbook",
    "foursquare.com",
    "yelp.com",
    "trustpilot.com",
    "bbb.org",

    # Yetkili servis / bayi / klima gürültüsü
    "enyakinyetkiliservis.com",
    "klimatoptansatis.com",
    "yetkiliservis",
    "yetkili-servis",
    "servisnoktasi",
    "servis-noktasi",
    "bayinumarasi",
    "bayi-numarasi",
    "masterdestek.com.tr",
    "dogatekyapi.net",
    "svsiklimlendirme.com",
    "pront.com.tr",
    "atilimtek.com.tr",
    "suvecevre.com",
    "barismakina.com",
    "ontesmekanik.com",
    "cozumbaca.com",
}

# Domain'in ilk etiketi bunlardan biriysa (rehber.corlutso.org.tr gibi) elenir.
REHBER_SUBDOMAIN = {
    "rehber",
    "katalog",
    "dizin",
    "uyeler",
    "uyelerimiz",
    "members",
    "member",
    "directory",
    "firmalar",
    "sirketler",
    "uye-firma",
    "uyefirma",
    "uye",
    "bayi",
    "bayiler",
    "servis",
    "yetkili",
    "dealer",
    "dealers",
    "partners",
    "partner",
    "locator",
    "store-locator",
    "magaza",
    "magazalar",
}

# Kamu / resmi / eğitim TLD ve etiketleri — firma web sitesi olarak kabul edilmez.
KAMU_DOMAIN_SONLARI = (
    ".gov.tr",
    ".bel.tr",
    ".pol.tr",
    ".mil.tr",
    ".k12.tr",
    ".edu.tr",
    ".gc.ca",
    ".govt.nz",
)

# Tam DNS etiketi olarak kamu/askeri (nasa.gov, economie.gouv.fr, gob.mx).
_KAMU_ETIKET = frozenset({"gov", "gouv", "gob", "govt", "gobierno", "mil"})


def _kamu_domain_mi(domain: str) -> bool:
    """Domain kamu/eğitim alan adı mı (TR + yabancı gov/gouv/gob/go.ccTLD)?"""
    d = (domain or "").lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if not d:
        return False
    if any(d == s.lstrip(".") or d.endswith(s) for s in KAMU_DOMAIN_SONLARI):
        return True
    parcalar = [p for p in d.split(".") if p]
    # Milli Eğitim: tevfikileriihl.meb.k12.tr, meb.gov.tr vb.
    if "meb" in parcalar:
        return True
    if any(p in _KAMU_ETIKET for p in parcalar):
        return True
    # Japonya/Kore/Uganda vb.: nam.go.ug, mofa.go.jp — go.com şirket domain'i değil
    if re.search(r"(?:^|\.)go\.[a-z]{2}$", d):
        return True
    return False


def _kalip_eslesti(kalip: str, host: str, url: str) -> bool:
    """Eleme kalıbı bu adayla eşleşiyor mu?

    Kalıp yol (path) içeriyorsa ('.org.tr/vakif') tüm URL'de aranır; aksi
    hâlde YALNIZCA host'ta. Aksi takdirde meşru bir kurumsal sitenin yolu
    (ornek.com/isrehberi) firmayı rehber sanıp eliyordu.

    Host içinde alt dizge araması korunur: config kalıpları buna dayanıyor
    (tso.org.tr → istanbultso.org.tr, corlutso.org.tr kasten elenir).
    """
    if not kalip:
        return False
    if "/" in kalip:
        return kalip in url
    return kalip in host


def ignore_edilmeli(url: str, domain: str, kaliplar: list[str] | None = None) -> bool:
    """Oda / rehber / dizin / kamu / IGNORE listesindeki siteleri eker.

    Kontroller:
      1. IGNORE kalıbı host içinde (yol kalıpları için tüm URL)
      2. Domain'in ilk etiketi rehber/katalog/dizin vb.
      3. Kamu TLD (TR + gov/gouv/gob/go.ccTLD, .edu.tr, meb)
      4. config.yaml ignore_domain_kaliplari — aynı kural
    """
    url_lower = (url or "").lower()
    domain_lower = (domain or "").lower()
    if domain_lower.startswith("www."):
        domain_lower = domain_lower[4:]

    if any(_kalip_eslesti(x, domain_lower, url_lower) for x in IGNORE):
        return True

    ilk_etiket = domain_lower.split(".")[0] if domain_lower else ""
    if ilk_etiket in REHBER_SUBDOMAIN:
        return True

    if _kamu_domain_mi(domain_lower):
        return True

    for kalip in kaliplar or []:
        if _kalip_eslesti((kalip or "").lower().strip(), domain_lower, url_lower):
            return True

    return False


def web_mail_aday_satirlari(df: pd.DataFrame) -> list[dict]:
    """WEB veya EMAIL dolu satırları LLM adayı olarak toplar."""
    if df is None or getattr(df, "empty", True):
        return []

    df = normalize_columns(df)
    if COL_UNVAN not in df.columns:
        return []

    has_web = COL_WEB in df.columns
    has_mail = COL_EMAIL in df.columns
    if not has_web and not has_mail:
        return []

    adaylar = []
    for idx, row in df.iterrows():
        unvan = str(row.get(COL_UNVAN) or "").strip()
        if not unvan or unvan.lower() in ("nan", "none"):
            continue

        web = row[COL_WEB] if has_web else ""
        email = row[COL_EMAIL] if has_mail else ""
        domain = domain_from_web_or_email(web, email)
        if not domain:
            continue

        skor = unvan_domain_benzerlik(unvan, domain)
        web_s = "" if web is None else str(web).strip()
        mail_s = "" if email is None else str(email).strip()
        if web_s.lower() in ("nan", "none", "<na>"):
            web_s = ""
        if mail_s.lower() in ("nan", "none", "<na>"):
            mail_s = ""

        item = {
            "id": len(adaylar) + 1,
            "UNVAN": unvan,
            "DOMAIN": domain,
            "WEB": web_s,
            "EMAIL": mail_s,
            "ESLESME_SKORU": skor,
            "_row": row,
        }
        if COL_SICIL in df.columns:
            item[COL_SICIL] = row[COL_SICIL]
        adaylar.append(item)
    return adaylar


def supheli_eslesmeleri_bul(df: pd.DataFrame, esik: int = 60) -> pd.DataFrame:
    """Kural tabanlı yedek: web/mail dolu + düşük benzerlik skoru."""
    adaylar = web_mail_aday_satirlari(df)
    kayitlar = []
    for a in adaylar:
        if a["ESLESME_SKORU"] >= esik:
            continue
        kayit = {
            "UNVAN": a["UNVAN"],
            "DOMAIN": a["DOMAIN"],
            "ESLESME_SKORU": a["ESLESME_SKORU"],
            "NEDEN": (
                f"Ünvan ile domain zayıf uyuyor "
                f"(skor {a['ESLESME_SKORU']} < {esik})"
            ),
        }
        if a.get("WEB"):
            kayit["WEB"] = a["WEB"]
        if a.get("EMAIL"):
            kayit["EMAIL"] = a["EMAIL"]
        if COL_SICIL in a:
            kayit[COL_SICIL] = a[COL_SICIL]
        kayitlar.append(kayit)
    return pd.DataFrame(kayitlar)


# llama-3.3-70b-versatile 16.08.2026'da kapatıldı; sırayla denenir.
GROQ_MODEL_YEDEK = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
)


class LLMErisilemedi(Exception):
    """LLM cevap veremedi (kota/ağ). 'Eşleşme yok' ile karıştırılmamalı."""


# Groq 429 mesajı: "Please try again in 4m18.767999999s"
_429_SURE_RE = re.compile(
    r"try again in\s*(?:(\d+)m)?\s*([\d.]+)s", re.IGNORECASE
)

# TPM (saniyelik) burst: kısa bekle. TPD (dakikalık) reset beklenmez —
# skor 100 olsa bile kuyruk 5–10 dk kilitlenmesin; yedek model / LLM_YOK.
_429_MAX_BEKLEME = 8.0
_429_MAX_DENEME = 1        # aynı model için en fazla bir kısa tekrar

# Tüm modeller TPD/dakikalık 429 verince bu koşunun kalan firmalarında
# LLM tekrar denenmez (aynı Groq org kotası).
_llm_kota_kesildi = False


def llm_kota_sifirla() -> None:
    """Yeni site-bul koşusunda kota kilidini aç."""
    global _llm_kota_kesildi
    _llm_kota_kesildi = False


def _429_bekleme_saniyesi(mesaj: str) -> float:
    """429 mesajındaki 'try again in ...' süresini saniyeye çevirir.

    Süre okunamazsa 0 döner (çağıran kendi varsayılanını seçer).
    """
    m = _429_SURE_RE.search(str(mesaj or ""))
    if not m:
        return 0.0
    dakika = float(m.group(1) or 0)
    saniye = float(m.group(2) or 0)
    return dakika * 60.0 + saniye


def _kota_hatasi_mi(err: str) -> bool:
    return ("rate_limit" in err) or ("429" in err) or ("too many requests" in err)


def groq_chat_metin(
    client,
    model: str,
    messages: list,
    *,
    temperature: float = 0.3,
    max_tokens: int = 512,
    logger=None,
) -> str:
    """Groq chat; model 404 olursa yedek modele, 429 olursa kısa bekler.

    Dakikalık TPD 429'de uyumaz, sonraki modele geçer. Hiçbir model
    açılamazsa ``LLMErisilemedi`` fırlatır ve bu süreçte LLM kapanır
    (aynı kota, her kısa markada 3×429 olmasın).
    """
    global _llm_kota_kesildi
    if _llm_kota_kesildi:
        raise LLMErisilemedi("LLM kotası tükendi; bu koşuda tekrar denenmeyecek")
    n = int(max_tokens or 512)
    if n < 256:
        n = 256

    modeller: list[str] = []
    if model:
        modeller.append(str(model).strip())
    for yedek in GROQ_MODEL_YEDEK:
        if yedek not in modeller:
            modeller.append(yedek)

    son_hata: Exception | None = None
    kota_engeli = False

    for m in modeller:
        kwargs = {
            "model": m,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": n,
        }
        if m.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = "low"

        resp = None
        for deneme in range(_429_MAX_DENEME + 1):
            try:
                resp = client.chat.completions.create(**kwargs)
                break
            except TypeError:
                # Eski SDK: reasoning_effort parametresini tanımıyor
                kwargs.pop("reasoning_effort", None)
                try:
                    resp = client.chat.completions.create(**kwargs)
                except Exception as e:
                    son_hata = e
                break
            except Exception as e:
                son_hata = e
                err = str(e).lower()

                if _kota_hatasi_mi(err):
                    kota_engeli = True
                    bekle = _429_bekleme_saniyesi(err)
                    if deneme >= _429_MAX_DENEME or bekle > _429_MAX_BEKLEME:
                        if logger:
                            logger.warning(
                                f"  ⏳ LLM kotası doldu ({m}); yedek modele "
                                f"geçiliyor (gereken bekleme: {bekle:.0f} sn)"
                            )
                        break
                    bekle = min(max(bekle + 1.0, 1.0), _429_MAX_BEKLEME)
                    if logger:
                        logger.warning(
                            f"  ⏳ LLM kota sınırı ({m}); {bekle:.0f} sn bekleniyor "
                            f"({deneme + 1}/{_429_MAX_DENEME})"
                        )
                    time.sleep(bekle)
                    continue

                if "reasoning" in err and "reasoning_effort" in kwargs:
                    kwargs.pop("reasoning_effort", None)
                    continue

                model_yok = any(
                    x in err
                    for x in (
                        "model_not_found",
                        "does not exist",
                        "not have access",
                        "deprecat",
                    )
                )
                if model_yok:
                    if logger:
                        logger.warning(f"LLM model atlandı ({m}): {e}")
                break

        if resp is None:
            continue

        msg = resp.choices[0].message
        text = (getattr(msg, "content", None) or "").strip()
        if not text:
            son_hata = RuntimeError(f"LLM boş cevap ({m})")
            continue

        if logger and m != (model or ""):
            logger.info(f"  LLM model: {m}")
        return text

    if kota_engeli:
        _llm_kota_kesildi = True
        raise LLMErisilemedi(f"LLM kotası tükendi: {son_hata}")
    raise son_hata or RuntimeError("LLM cevap üretmedi")


def _llm_json_metin_temizle(text: str) -> str:
    """Markdown fence ve uç boşlukları atar."""
    raw = (text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _llm_json_parse(text: str):
    """LLM cevabından JSON dizi çıkarır."""
    raw = _llm_json_metin_temizle(text)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "sonuclar" in data:
            sonuclar = data["sonuclar"]
            return sonuclar if isinstance(sonuclar, list) else []
    except Exception:
        pass
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _llm_json_nesne(text: str) -> dict | None:
    """LLM cevabından JSON nesne çıkarır ({"domain": ...}).

    Fence, önek metin ve tek elemanlı dizi sarmalayıcısını dener.
    """
    raw = _llm_json_metin_temizle(text)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def supheli_eslesmeleri_llm_bul(df, batch_size: int = 12) -> tuple[pd.DataFrame, str]:
    """Web/mail dolu satırları Groq LLM ile değerlendirir.

    Döner: (DataFrame, durum_mesajı)
    """
    adaylar = web_mail_aday_satirlari(df)
    if not adaylar:
        return pd.DataFrame(), "Web/mail dolu satır yok."

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return pd.DataFrame(), "GROQ_API_KEY yok — .env dosyasını kontrol edin."

    try:
        from groq import Groq
    except ImportError:
        return pd.DataFrame(), "groq paketi yüklü değil."

    cfg = load_config()
    llm_cfg = cfg.get("llm", {})
    if llm_cfg.get("enabled") is False:
        return pd.DataFrame(), "LLM config.yaml içinde kapalı (llm.enabled: false)."

    client = Groq(api_key=api_key)
    model = llm_cfg.get("model", "openai/gpt-oss-120b")

    kararlar: dict[int, dict] = {}

    for i in range(0, len(adaylar), batch_size):
        batch = adaylar[i : i + batch_size]
        liste = "\n".join(
            [
                f"{a['id']}. UNVAN: {a['UNVAN']}\n"
                f"   DOMAIN: {a['DOMAIN']}\n"
                f"   WEB: {a['WEB'] or '-'}\n"
                f"   EMAIL: {a['EMAIL'] or '-'}\n"
                f"   KURAL_SKOR: {a['ESLESME_SKORU']}"
                for a in batch
            ]
        )
        prompt = f"""Sen Türk ticari sicil verisi analistisin.
Aşağıda firma ünvanları ve otomatik bulunan web / e-posta domain'leri var.

Her satır için karar ver: bulunan WEB/EMAIL bu firma için ŞÜPHELİ mi (yanlış eşleşme)?

ŞÜPHELİ (true) say:
- Domain / e-posta, ünvanındaki markayla uyuşmuyorsa
- Başka bir markanın, holdingin, rehberin veya alakasız sitenin adresi gibi duruyorsa
- E-posta domain'i ünvanla alakasızsa

ŞÜPHELİ sayma (false):
- Marka adı domain'de geçiyorsa veya makul eşleşme varsa
- Firmanın kendi resmi sitesi / info@ maili normaldir
- Türkçe karakter farkları (ş→s, ı→i) normaldir

Sadece JSON dizi döndür, başka metin yazma:
[
  {{"id": 1, "supheli": true, "neden": "kısa Türkçe gerekçe"}},
  {{"id": 2, "supheli": false, "neden": ""}}
]

Satırlar:
{liste}
"""
        try:
            cevap = groq_chat_metin(
                client,
                model,
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1200,
            )
            for item in _llm_json_parse(cevap):
                try:
                    sid = int(item.get("id"))
                except Exception:
                    continue
                kararlar[sid] = {
                    "supheli": bool(item.get("supheli")),
                    "neden": str(item.get("neden") or "").strip(),
                }
        except Exception as e:
            return pd.DataFrame(), f"LLM hatası: {e}"

    kayitlar = []
    for a in adaylar:
        k = kararlar.get(a["id"])
        if not k or not k.get("supheli"):
            continue
        kayit = {
            "UNVAN": a["UNVAN"],
            "DOMAIN": a["DOMAIN"],
            "ESLESME_SKORU": a["ESLESME_SKORU"],
            "NEDEN": k.get("neden") or "LLM şüpheli buldu",
        }
        if a.get("WEB"):
            kayit["WEB"] = a["WEB"]
        if a.get("EMAIL"):
            kayit["EMAIL"] = a["EMAIL"]
        if COL_SICIL in a:
            kayit[COL_SICIL] = a[COL_SICIL]
        kayitlar.append(kayit)

    msg = f"{len(adaylar)} web/mail satırı LLM’e soruldu, {len(kayitlar)} şüpheli."
    return pd.DataFrame(kayitlar), msg


# ---------------------------------------------------------------------------
# Disk Yazma Yardımcıları
# ---------------------------------------------------------------------------

def _excel_atomik_yaz(df: pd.DataFrame, output_file: str) -> None:
    """DataFrame'i aynı dizindeki geçici dosyaya yazıp atomik olarak hedefe taşır."""
    output_file = os.path.abspath(output_file)
    dizin = os.path.dirname(output_file) or "."
    os.makedirs(dizin, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", prefix=".tmp_", dir=dizin)
    os.close(fd)
    try:
        df.to_excel(tmp_path, index=False)
        os.replace(tmp_path, output_file)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def sonuclari_diske_yaz(
    kayitlar: list[dict],
    output_file: str,
    logger: logging.Logger,
) -> bool:
    """Biriken kayıtları çıktı dosyasına ekler.

    Mevcut dosya varsa okuyup birleştirir; yoksa yeni oluşturur.
    Yazabildiyse True döner.
    """
    if not kayitlar:
        return True

    try:
        if os.path.exists(output_file):
            df_mevcut = pd.read_excel(output_file)
            birlesik = pd.concat(
                [df_mevcut, pd.DataFrame(kayitlar)], ignore_index=True
            )
        else:
            birlesik = pd.DataFrame(kayitlar)

        _excel_atomik_yaz(birlesik, output_file)
        return True

    except PermissionError:
        logger.error(
            f"  ❌ {output_file} başka programda açık, ara kayıt yapılamadı. "
            "Sonra tekrar denenecek."
        )
        return False
    except Exception as e:
        logger.error(f"  ❌ Ara kayıt hatası: {e}")
        return False


def final_kaydet(
    sonuclar: list[dict],
    output_file: str,
    logger: logging.Logger,
) -> pd.DataFrame | None:
    """Son kayıt: mevcut dosyayla birleştirip yazar, DataFrame döner."""
    try:
        if sonuclar:
            if os.path.exists(output_file):
                df_mevcut = pd.read_excel(output_file)
                sonuc_df = pd.concat(
                    [df_mevcut, pd.DataFrame(sonuclar)], ignore_index=True
                )
            else:
                sonuc_df = pd.DataFrame(sonuclar)

            _excel_atomik_yaz(sonuc_df, output_file)
        else:
            if os.path.exists(output_file):
                sonuc_df = pd.read_excel(output_file)
            else:
                sonuc_df = pd.DataFrame()

        return sonuc_df

    except PermissionError:
        logger.error(
            f"  ❌ HATA: {output_file} dosyası başka bir program tarafından "
            "kullanılıyor (Excel açık olabilir)."
        )
        logger.error("  Dosyayı kapatıp tekrar deneyin.")
        return None


def girdi_sirasina_diz(
    sonuc_df: pd.DataFrame,
    df_girdi: pd.DataFrame,
    sicil_var: bool,
    output_file: str,
    logger: logging.Logger,
) -> pd.DataFrame:
    """Çıktıyı girdi dosyasındaki satır sırasına göre yeniden dizer.

    Resume sonrası yeniden işlenen firmalar dosyanın sonuna eklendiği için
    sıra girdiden farklı kalabiliyor; bu adım hizayı geri kuruyor.

    Yeni çıktıların sıra anahtarı benzersiz kaynak satır numarasıdır; eski
    çıktılarda uyumluluk için SİCİL (veya girdi satır indeksi) kullanılır.
    Girdi/çıktı sütunları normalize_columns ile SİCİL kabul edilir.
    """
    if sonuc_df is None or sonuc_df.empty:
        return sonuc_df

    sonuc_df = normalize_columns(sonuc_df)
    df_girdi = normalize_columns(df_girdi)

    # Yeni çıktılarda kaynak satır anahtarı benzersizdir; aynı SİCİL'e sahip
    # satırların da girdi sırasını kesin olarak korur.
    if COL_KAYNAK_SATIR in sonuc_df.columns:
        konum = sonuc_df[COL_KAYNAK_SATIR].map(kaynak_satir_anahtari)
        konum = pd.to_numeric(konum, errors="coerce")
    elif COL_SICIL not in sonuc_df.columns:
        return sonuc_df
    elif sicil_var:
        if COL_SICIL not in df_girdi.columns:
            logger.warning(f"  ⚠ Girdide {COL_SICIL} yok, sıralama atlandı.")
            return sonuc_df

        sira = {}
        for i, deger in enumerate(df_girdi[COL_SICIL].astype(str)):
            # İlk kez görülen SİCİL bu indekse oturur; tekrarlı değer
            # nadir — yine de setdefault ile ilk konumu koru.
            sira.setdefault(str(deger), i)
    else:
        # SİCİL yokken çıktıdaki değer = girdi satır indeksi (0..n-1)
        sira = {str(i): i for i in range(len(df_girdi))}

    if COL_KAYNAK_SATIR not in sonuc_df.columns:
        konum = sonuc_df[COL_SICIL].astype(str).map(sira)

    eksik = int(konum.isna().sum())
    if eksik:
        logger.warning(f"  ⚠ {eksik} satır girdi dosyasında yok, sona alındı.")
    konum = konum.fillna(len(df_girdi))

    sirali = (
        sonuc_df.assign(_sira=konum)
        .sort_values("_sira", kind="stable")
        .drop(columns="_sira")
        .reset_index(drop=True)
    )

    if sirali.equals(sonuc_df):
        return sonuc_df

    try:
        _excel_atomik_yaz(sirali, output_file)
        logger.info("  ↕ Çıktı, girdi dosyasındaki sıraya göre dizildi.")
        return sirali
    except PermissionError:
        logger.error(f"  ❌ {output_file} açık, sıralama dosyaya yazılamadı.")
        return sonuc_df
    except Exception as e:
        logger.error(f"  ❌ Sıralama başarısız: {e}")
        return sonuc_df


# ---------------------------------------------------------------------------
# CAPTCHA durumu + Chrome odak
# ---------------------------------------------------------------------------

def _json_atomik_yaz(path: Path, payload: dict) -> None:
    """JSON dosyasını tempfile + os.replace ile atomik olarak yazar."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="~tmp_", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def captcha_durum_yaz(
    active: bool,
    waited: int = 0,
    message: str = "",
) -> None:
    """Panelin okuyacağı CAPTCHA durum dosyasını yazar."""
    payload = {
        "active": bool(active),
        "waited": int(waited),
        "message": message or (
            "Google CAPTCHA çıktı — Chrome penceresinde çözün."
            if active
            else ""
        ),
        "updated_at": time.time(),
    }
    _json_atomik_yaz(CAPTCHA_STATUS_PATH, payload)


def captcha_durum_oku() -> dict:
    """CAPTCHA durum dosyasını okur."""
    try:
        if not CAPTCHA_STATUS_PATH.exists():
            return {"active": False}
        data = json.loads(CAPTCHA_STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"active": False}
        return data
    except Exception:
        return {"active": False}


def captcha_durum_temizle() -> None:
    captcha_durum_yaz(False, 0, "")


def progress_durum_yaz(
    current: int,
    total: int,
    label: str = "",
    current_name: str = "",
) -> None:
    """Panelin okuyacağı ilerleme durumunu yazar (örn. 47/200)."""
    payload = {
        "current": max(0, int(current)),
        "total": max(0, int(total)),
        "label": label or "",
        "current_name": current_name or "",
        "updated_at": time.time(),
    }
    _json_atomik_yaz(PROGRESS_STATUS_PATH, payload)


def progress_durum_oku() -> dict:
    try:
        if not PROGRESS_STATUS_PATH.exists():
            return {}
        data = json.loads(PROGRESS_STATUS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def progress_durum_temizle() -> None:
    try:
        if PROGRESS_STATUS_PATH.exists():
            PROGRESS_STATUS_PATH.unlink()
    except Exception:
        pass


def _port_dinleyen_pid(port: int) -> int | None:
    """TCP portunu dinleyen sürecin PID'ini döner (debug Chrome)."""
    port = int(port)
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                    "-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess",
                ],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            ).strip()
            if out.isdigit():
                return int(out)
        except Exception:
            pass
        try:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "tcp"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            )
            needle = f":{port} "
            for line in out.splitlines():
                if "LISTENING" not in line.upper() or needle not in line:
                    continue
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
        except Exception:
            pass
        return None

    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
    except Exception:
        pass
    return None


def _process_cmdline(pid: int) -> str:
    """Sürecin komut satırını döner."""
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\")"
                    ".CommandLine",
                ],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            ).strip()
            return out
        except Exception:
            return ""
    try:
        return subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "args="],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _chrome_user_data_dir(pid: int | None = None) -> str:
    """Debug Chrome --user-data-dir; bulunamazsa ~/chrome_selenium."""
    default = str(Path.home() / "chrome_selenium")
    if pid is None:
        return default
    args = _process_cmdline(pid)
    marker = "--user-data-dir="
    if marker in args:
        rest = args.split(marker, 1)[1]
        # Argüman boşluksuz veya tırnaklı olabilir
        if rest.startswith('"'):
            return rest.split('"', 2)[1] or default
        if rest.startswith("'"):
            return rest.split("'", 2)[1] or default
        return rest.split()[0] or default
    return default


def _chrome_binary() -> str | None:
    if platform.system() == "Windows":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
        ]
        for path in candidates:
            if path.is_file():
                return str(path)
        return None

    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.isfile(mac) and os.access(mac, os.X_OK):
        return mac
    for name in ("google-chrome", "chromium-browser", "chromium"):
        try:
            path = subprocess.check_output(
                ["which", name], text=True, timeout=2, stderr=subprocess.DEVNULL
            ).strip()
            if path:
                return path
        except Exception:
            pass
    return None


def _macos_swift_activate_pid(pid: int) -> bool:
    """Erişilebilirlik izni olmadan PID ile mevcut uygulamayı öne al (yeni pencere açmaz)."""
    script = f"""
import AppKit
let pid: pid_t = {int(pid)}
guard let app = NSRunningApplication(processIdentifier: pid) else {{
  fputs("no-app\\n", stderr); exit(1)
}}
app.unhide()
let ok = app.activate(options: [.activateAllWindows])
exit(ok ? 0 : 2)
"""
    try:
        r = subprocess.run(
            ["swift", "-e", script],
            check=False,
            timeout=25,
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _chrome_cdp_sekmeleri(port: int) -> list[dict]:
    """Debug port'taki sayfa sekmelerini listeler."""
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json", timeout=2
        ) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(data, list):
            return []
        return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]
    except Exception:
        return []


def _chrome_cdp_sekme_aktif(port: int, tab_id: str) -> bool:
    """Mevcut sekmeyi öne alır (yeni pencere/sekme açmaz)."""
    try:
        import urllib.request

        url = f"http://127.0.0.1:{int(port)}/json/activate/{tab_id}"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def _chrome_cdp_sekme_kapat(port: int, tab_id: str) -> bool:
    try:
        import urllib.request

        url = f"http://127.0.0.1:{int(port)}/json/close/{tab_id}"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def _chrome_cdp_bos_sekmeleri_temizle(port: int) -> None:
    """Önceki hatalı 'öne getir' denemelerinden kalan chrome://newtab sekmelerini kapatır."""
    tabs = _chrome_cdp_sekmeleri(port)
    digger = [
        t
        for t in tabs
        if not (t.get("url") or "").lower().startswith(("chrome://newtab", "chrome://new-tab"))
    ]
    if not digger:
        return  # tek kalan boş sekme ise dokunma
    for t in tabs:
        u = (t.get("url") or "").lower()
        if u.startswith("chrome://newtab") or u.startswith("chrome://new-tab"):
            tid = t.get("id")
            if tid:
                _chrome_cdp_sekme_kapat(port, str(tid))


def _chrome_cdp_tarama_sekmesini_sec(port: int) -> bool:
    """Google arama / CAPTCHA sekmesini mevcut pencerede öne alır."""
    _chrome_cdp_bos_sekmeleri_temizle(port)
    tabs = _chrome_cdp_sekmeleri(port)
    if not tabs:
        return False

    def skor(t: dict) -> tuple:
        u = (t.get("url") or "").lower()
        title = (t.get("title") or "").lower()
        # CAPTCHA / Google arama öncelikli; yeni sekme en sonda
        if "/sorry/" in u or "unusual traffic" in title or "robot" in title:
            return (0,)
        if "google." in u and ("/search" in u or "captcha" in u):
            return (1,)
        if "google." in u:
            return (2,)
        if u.startswith("chrome://newtab") or u.startswith("chrome://new-tab"):
            return (9,)
        return (5,)

    tabs_sorted = sorted(tabs, key=skor)
    best = tabs_sorted[0]
    if skor(best)[0] >= 9:
        return False
    return _chrome_cdp_sekme_aktif(port, str(best.get("id") or ""))


def chrome_one_getir(port: int | None = None) -> bool:
    """Debug Chrome penceresini öne getirir (CAPTCHA için).

    Yeni pencere/sekme AÇMAZ. 9222'deki mevcut Google sekmesini seçer,
    ardından debug Chrome sürecini (PID) öne almaya çalışır.
    """
    if port is None:
        try:
            port = int(load_config().get("chrome", {}).get("debug_port", 9222))
        except Exception:
            port = 9222

    system = platform.system()
    pid = _port_dinleyen_pid(port)

    try:
        # Önce doğru sekmeyi seç (CDP) — tüm platformlarda
        sekme_ok = _chrome_cdp_tarama_sekmesini_sec(port)

        if system == "Darwin":
            ok = sekme_ok

            # Doğru PID (günlük Chrome değil) — yeni pencere açmaz
            if pid is not None and _macos_swift_activate_pid(pid):
                ok = True

            # System Events (Erişilebilirlik izni gerekir)
            if not ok and pid is not None:
                script = (
                    'tell application "System Events"\n'
                    f"  set proc to first process whose unix id is {pid}\n"
                    "  set frontmost of proc to true\n"
                    "  set visible of proc to true\n"
                    "  try\n"
                    "    repeat with w in windows of proc\n"
                    '      try\n'
                    '        set value of attribute "AXMinimized" of w to false\n'
                    "      end try\n"
                    "    end repeat\n"
                    "  end try\n"
                    "end tell\n"
                )
                r = subprocess.run(
                    ["osascript", "-e", script],
                    check=False,
                    timeout=6,
                    capture_output=True,
                    text=True,
                )
                ok = r.returncode == 0

            if ok:
                subprocess.run(
                    ["osascript", "-e", "beep 1"],
                    check=False,
                    timeout=3,
                    capture_output=True,
                )
            return ok

        if system == "Windows":
            if pid is not None:
                ps = (
                    "Add-Type @'\n"
                    "using System;\n"
                    "using System.Runtime.InteropServices;\n"
                    "public class Win {\n"
                    "  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);\n"
                    "  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int n);\n"
                    "  [DllImport(\"user32.dll\")] public static extern bool IsIconic(IntPtr h);\n"
                    "}\n"
                    "'@\n"
                    f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue\n"
                    "if (-not $p) { $p = Get-Process chrome -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1 }\n"
                    "if ($p -and $p.MainWindowHandle -ne 0) {\n"
                    "  if ([Win]::IsIconic($p.MainWindowHandle)) { [void][Win]::ShowWindow($p.MainWindowHandle, 9) }\n"
                    "  else { [void][Win]::ShowWindow($p.MainWindowHandle, 5) }\n"
                    "  [void][Win]::SetForegroundWindow($p.MainWindowHandle)\n"
                    "  exit 0\n"
                    "}\n"
                    "exit 1\n"
                )
            else:
                ps = (
                    "Add-Type @'\n"
                    "using System;\n"
                    "using System.Runtime.InteropServices;\n"
                    "public class Win {\n"
                    "  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);\n"
                    "  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int n);\n"
                    "}\n"
                    "'@\n"
                    "$p = Get-Process chrome -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1\n"
                    "if ($p) { [void][Win]::ShowWindow($p.MainWindowHandle, 9); "
                    "[void][Win]::SetForegroundWindow($p.MainWindowHandle); exit 0 }\n"
                    "exit 1\n"
                )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=False,
                timeout=8,
                capture_output=True,
            )
            return r.returncode == 0 or sekme_ok

        # Linux: port PID varsa o pencere
        if pid is not None:
            try:
                r = subprocess.run(
                    ["xdotool", "search", "--pid", str(pid), "windowactivate"],
                    check=False,
                    timeout=5,
                    capture_output=True,
                )
                if r.returncode == 0:
                    return True
            except FileNotFoundError:
                pass

        for cmd in (
            ["wmctrl", "-a", "Chrome"],
            ["wmctrl", "-a", "Chromium"],
            ["xdotool", "search", "--name", "Chrome", "windowactivate"],
        ):
            try:
                r = subprocess.run(cmd, check=False, timeout=5, capture_output=True)
                if r.returncode == 0:
                    return True
            except FileNotFoundError:
                continue
        return sekme_ok

    except Exception:
        return False
