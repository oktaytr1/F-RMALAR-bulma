import argparse
import pandas as pd
import time
import random
import os
import yaml
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from tqdm.utils import _screen_shape_wrapper

from difflib import SequenceMatcher
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from groq import Groq
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException

from utils import load_config, setup_logging, normalize_tr, sonuclari_diske_yaz, final_kaydet
from utils import dolu_hucre_sayisi
from utils import girdi_sirasina_diz, normalize_columns, COL_SICIL, COL_UNVAN, COL_WEB, COL_SKOR, COL_ILCE
from utils import COL_KAYNAK_SATIR, kaynak_satir_anahtari, islenmis_kaynak_satirlari
from utils import temiz_ilce
from utils import (
    COL_ADAY_WEB,
    COL_RED_NEDEN,
    COL_DURUM,
    DURUM_KABUL,
    DURUM_SITE_YOK,
    DURUM_RED_SKOR,
    DURUM_TIMEOUT,
    DURUM_LLM_YOK,
    DURUM_KABUL_SUPHELI,
    red_dogrulama_durumu,
)
from utils import LLMErisilemedi
from utils import (
    captcha_durum_yaz,
    captcha_durum_temizle,
    chrome_one_getir,
    progress_durum_yaz,
    progress_durum_temizle,
)
from utils import (
    GENEL_KELIMELER,
    marka_tokenlari,
    benzerlik_skoru,
    ignore_edilmeli,
    token_metinde_kelime,
    marka_metinde_kelime_dizisi,
    domain_marka_etiketleri,
    zayif_tek_marka_tokeni,
    kisa_marka_mi,
    kisa_tek_marka_bulanik_yasak,
    ulke_sektor_uyumlu_mu,
    sektor_uyumsuz_mu,
    dogrulama_zorunlu_mu,
    dogrulama_log_nedenleri,
    metin_kelimeleri_normalize,
    ilce_sinyali_uygun_mu,
    ilce_metinde_mi,
    _sektor_pozitif_sinyal,
    groq_chat_metin,
    unvan_faaliyet_kelimeleri,
)

# .env dosyasını yükle (GROQ_API_KEY buradan okunur)
load_dotenv()

# ---------------------------------------------------------------------------
# Module-level variables
# ---------------------------------------------------------------------------

config = None
logger = None
groq_client = None

# Sonuç sayfasındaki tüm href'leri TEK CDP çağrısında toplar.
# Eşdeğeri: find_elements(...) + her element için get_attribute("href").
# Sonuç birebir aynı (aynı seçici, aynı doküman sırası, aynı mutlak URL),
# ama element başına round-trip yerine tek çağrı olduğu için ~80x hızlı.
HREF_TOPLA_JS = (
    "return Array.from(document.querySelectorAll('a[href]')).map(a => a.href);"
)

# Google SERP organik sonuçlarından title + snippet (LLM bağlamı).
# Sınıf adları değişebilir; birden fazla seçici + h3/a yapısı kullanılır.
SERP_META_JS = r"""
return (() => {
  const seen = new Set();
  const out = [];
  const cards = document.querySelectorAll(
    'div.tF2Cxc, div.g, div[data-sokoban-container], div.MjjYud > div'
  );
  for (const card of cards) {
    const h3 = card.querySelector('h3');
    if (!h3) continue;
    const a =
      card.querySelector('a[href^="http"]') ||
      (h3.closest('a') && h3.closest('a').href ? h3.closest('a') : null) ||
      h3.parentElement;
    let href = (a && a.href) ? a.href : '';
    if (!href || !href.startsWith('http')) continue;
    if (/google\.[a-z.]+/i.test(href) && !/\/url\?/.test(href)) continue;
    if (/\/url\?/.test(href)) {
      try {
        const u = new URL(href);
        href = u.searchParams.get('q') || u.searchParams.get('url') || href;
      } catch (e) {}
    }
    if (!href.startsWith('http') || /google\.[a-z.]+/i.test(href)) continue;
    let host = '';
    try { host = new URL(href).hostname.toLowerCase(); } catch (e) { continue; }
    if (host.startsWith('www.')) host = host.slice(4);
    if (seen.has(host)) continue;
    seen.add(host);
    const snEl = card.querySelector(
      'div.VwiC3b, div[data-sncf], div.IsZvec, span.st, div[data-content-feature="1"], .MUxGbd'
    );
    const title = (h3.innerText || '').trim().slice(0, 200);
    const snippet = (snEl ? snEl.innerText : '').trim().slice(0, 280);
    if (!title && !snippet) continue;
    out.push({ domain: host, url: href, title, snippet });
  }
  return out;
})();
"""

CAPTCHA_ISARETLERI = [
    "sıra dışı trafik",
    "sira disi trafik",
    "unusual traffic",
    "our systems have detected",
    "bir robot değil",
    "robot olmadığınızı",
    "recaptcha",
]

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def arama_sorgusu(firma, ilce=""):
    """Google: marka + 1-2 faaliyet + 'resmi site' (+ opsiyonel ilçe).

    Örn. ENSAR MİMARLIK İNŞAAT SANAYİ LTD → 'ENSAR MİMARLIK İNŞAAT resmi site'.
    sanayi/ticaret sorguya girmez. Domain skoru hâlâ yalnız marka tokenları kullanır.
    """
    marka_kelimeler = []
    for kelime in firma.split():
        norm = normalize_tr(kelime)
        if not norm or norm in GENEL_KELIMELER:
            continue
        marka_kelimeler.append(kelime)
        if len(marka_kelimeler) >= 3:
            break

    faaliyet = unvan_faaliyet_kelimeleri(firma, limit=2)
    marka_norm = {normalize_tr(x) for x in marka_kelimeler}
    faaliyet = [k for k in faaliyet if normalize_tr(k) not in marka_norm]

    parca = list(marka_kelimeler) + faaliyet
    if parca:
        sorgu = f"{' '.join(parca)} resmi site"
    else:
        sorgu = f"{firma} resmi site"

    ilce_t = temiz_ilce(ilce)
    if ilce_t:
        sorgu = f"{sorgu} {ilce_t}"
    return sorgu


_HTTP_BASLIK = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

_ILETISIM_ANAHTAR = (
    "iletisim",
    "contact",
    "bize-ulas",
    "bizeulas",
    "ulasin",
    "adresimiz",
    "get-in-touch",
)

_ADRES_SECICI = (
    "footer",
    "address",
    "[class*='footer']",
    "[id*='footer']",
    "[class*='adres']",
    "[id*='adres']",
    "[class*='address']",
    "[id*='address']",
    "[class*='iletisim']",
    "[id*='iletisim']",
    "[class*='contact']",
    "[id*='contact']",
)


def _html_indir(url, timeout=8) -> str:
    if not url:
        return ""
    try:
        resp = requests.get(
            url, headers=_HTTP_BASLIK, timeout=timeout, allow_redirects=True
        )
        if resp.status_code >= 400 or not resp.text:
            return ""
        return resp.text
    except (requests.RequestException, OSError):
        return ""


def _soup_yap(html: str):
    if not html:
        return None
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _title_from_soup(soup) -> str:
    if soup is None:
        return ""
    parcalar = []
    if soup.title and soup.title.string:
        parcalar.append(soup.title.string)
    og = soup.find("meta", property="og:site_name") or soup.find(
        "meta", attrs={"name": "og:site_name"}
    )
    if og and og.get("content"):
        parcalar.append(og["content"])
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        parcalar.append(og_title["content"])
    return " ".join(parcalar)


def _adres_from_soup(soup) -> str:
    """Footer / adres / iletişim bloğu; yoksa metnin sonu."""
    if soup is None:
        return ""
    parcalar = []
    for sel in _ADRES_SECICI:
        try:
            bulunan = soup.select(sel)[:4]
        except Exception:
            continue
        for el in bulunan:
            t = el.get_text(" ", strip=True)
            if t and len(t) >= 8:
                parcalar.append(t[:1500])
    metin = " ".join(parcalar).strip()
    if len(metin) >= 24:
        return metin[:4000]
    kopya = _soup_yap(str(soup))
    if kopya is not None:
        for tag in kopya(["script", "style", "noscript"]):
            tag.decompose()
        govde = kopya.get_text(" ", strip=True)
        if len(govde) > 2500:
            return govde[-2500:]
        return govde[:4000]
    return metin[:4000]


def _anasayfa_title_adres(url, timeout=8):
    """Tek GET: (title, soup, adres_metni)."""
    html = _html_indir(url, timeout)
    soup = _soup_yap(html)
    if soup is None:
        return "", None, ""
    return _title_from_soup(soup), soup, _adres_from_soup(soup)


def _title_og_metni(url, timeout=8) -> str:
    """Ana sayfa <title> / og:site_name / og:title metnini döndürür."""
    title, _, _ = _anasayfa_title_adres(url, timeout)
    return title


def _ilk_iletisim_url(base_url, soup) -> str:
    if soup is None or not base_url:
        return ""
    host = urlparse(base_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        tam = urljoin(base_url, href)
        p = urlparse(tam)
        d = (p.netloc or "").lower()
        if d.startswith("www."):
            d = d[4:]
        if d != host and not d.endswith("." + host):
            continue
        yol = (p.path or "").lower()
        etiket = (a.get_text(" ", strip=True) or "").lower()
        blob = yol + " " + etiket
        if any(k in blob for k in _ILETISIM_ANAHTAR):
            return tam
    return ""


def _iletisim_adres_ekle(base_url, soup, mevcut: str, ilce: str) -> str:
    """Sektör yoksa ve sicil ilçesi gövdede yoksa 1 iletişim sayfası daha oku."""
    if not ilce_sinyali_uygun_mu(ilce):
        return mevcut
    if ilce_metinde_mi(ilce, mevcut):
        return mevcut
    href = _ilk_iletisim_url(base_url, soup)
    if not href:
        return mevcut
    _, _, ekstra = _anasayfa_title_adres(href, timeout=8)
    if ekstra and logger:
        logger.info("  📄 İletişim sayfası okundu (ilçe sinyali)")
    return (mevcut + " " + ekstra).strip()


def title_marka_uyumlu(url, firma_adi, timeout=8, siki=False, ham=None):
    """Ana sayfa <title> / og:site_name içinde marka geçiyor mu?

    Token eşleşmesi kelime sınırında yapılır (afy ⊂ afyon sayılmaz).
    siki=True: title yalnızca marka adından ibaretse (örn. 'lenar') kabul etme.
    ham verilirse HTTP isteği atılmaz (kısa marka ülke/sektör kontrolü için).
    """
    tokenlar = marka_tokenlari(firma_adi)
    if not tokenlar or not url:
        return False

    if ham is None:
        ham = _title_og_metni(url, timeout)
    if not (ham or "").strip():
        return False

    eslesme = False
    # En az bir marka token'ı ayrı kelime olarak title'da
    for t in tokenlar:
        if len(t) >= 3 and token_metinde_kelime(t, ham):
            eslesme = True
            break

    # Birleşik marka = ardışık kelimelerin birleşimi (örn. "Medema İnşaat" → medemainsaat)
    marka = "".join(tokenlar)
    if not eslesme and marka_metinde_kelime_dizisi(marka, ham):
        eslesme = True

    if not eslesme:
        return False

    if siki:
        # Title/og yalnızca marka adından ibaretse yetersiz (lenar / lenar lenar)
        kelimeler = metin_kelimeleri_normalize(ham)
        uniq = set(kelimeler)
        if len(tokenlar) == 1 and uniq and uniq <= {tokenlar[0]}:
            return False
        if normalize_tr(ham) == marka:
            return False

    return True


def hizli_domain_kontrol(firma_adi, domain):
    """Hızlı domain kontrolü - düşük/yüksek eşik arası (config: dusuk_esik–yuksek_esik) için.
    
    Sıkılaştırılmış kontrol: sector uyumsuzluk filtresi + yüksek benzerlik eşiği.
    """
    tokenlar = marka_tokenlari(firma_adi)
    if not tokenlar:
        return False

    marka = "".join(tokenlar)
    etiketler = domain_marka_etiketleri(domain)
    cekirdek = "".join(etiketler)

    if not cekirdek:
        return False

    # Sektör uyumsuzluk kontrolü — petshop, restoran vb. inşaat firması değil
    if sektor_uyumsuz_mu(firma_adi, domain):
        return False

    # Etiket / tam marka eşitliği
    if marka == cekirdek or marka in etiketler:
        return True
    if any(len(t) >= 3 and t in etiketler for t in tokenlar):
        return True

    # startswith/endswith — yalnızca marka çekirdekle başlıyorsa
    # (çekirdek markadan uzun olamaz → "efeoglu" ⊂ "tugbaefeoglu" gibi durumları engelle)
    if len(cekirdek) >= 4 and len(marka) >= len(cekirdek):
        if marka.startswith(cekirdek) or marka.endswith(cekirdek):
            return True

    if len(cekirdek) < 3 or len(cekirdek) > 30:
        return False

    siddisli_tld = [".xyz", ".top", ".win", ".loan", ".club", ".online"]
    if any(tld in domain.lower() for tld in siddisli_tld):
        return False

    # iska ≠ izka: kısa tek markada bulanık %75 yetmez
    if kisa_tek_marka_bulanik_yasak(firma_adi, domain):
        return False

    # Bulanık benzerlik kontrolü — sıkılaştırılmış eşik
    ratio = SequenceMatcher(None, marka, cekirdek).ratio()
    if ratio >= 0.75:
        return True

    return False


def _domain_kok(netloc_or_url: str) -> str:
    """www. düşürülmüş host."""
    s = (netloc_or_url or "").strip().lower()
    if "://" in s:
        try:
            s = urlparse(s).netloc.lower()
        except Exception:
            return ""
    if s.startswith("www."):
        s = s[4:]
    return s


def google_serp_meta_topla(driver) -> dict[str, dict]:
    """Açık Google sonuç sayfasından domain → {title, snippet, url} sözlüğü.

    Ekstra sayfa gezmez; yalnızca mevcut SERP DOM'unu okur.
    """
    meta: dict[str, dict] = {}
    try:
        rows = driver.execute_script(SERP_META_JS) or []
    except Exception as e:
        if logger:
            logger.warning(f"  SERP title/snippet okunamadı: {e}")
        return meta

    if not isinstance(rows, list):
        return meta

    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = _domain_kok(row.get("domain") or row.get("url") or "")
        if not domain:
            continue
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        if not title and not snippet:
            continue
        # İlk görülen (üst sıra) öncelikli
        if domain not in meta:
            meta[domain] = {
                "title": title[:200],
                "snippet": snippet[:280],
                "url": str(row.get("url") or ""),
            }
    return meta


def _serp_meta_esle(domain: str, serp_meta: dict[str, dict] | None) -> dict:
    """Aday domain için SERP kaydı; alt alan / kök eşlemesi dener."""
    if not serp_meta:
        return {}
    d = _domain_kok(domain)
    if d in serp_meta:
        return serp_meta[d]
    for key, val in serp_meta.items():
        if d.endswith("." + key) or key.endswith("." + d):
            return val
    return {}


def llm_domain_sec(firma_adi, adaylar, ilce="", serp_meta=None):
    """Groq LLM ile en uygun domain'i seçer (Google title/snippet ile)."""
    if not groq_client:
        return None

    satirlar = []
    for skor, _, domain in adaylar[:5]:
        m = _serp_meta_esle(domain, serp_meta)
        title = (m.get("title") or "").strip() or "—"
        snippet = (m.get("snippet") or "").strip() or "—"
        satirlar.append(
            f"- {domain} (skor: {skor})\n"
            f"  Başlık: {title}\n"
            f"  Özet: {snippet}"
        )
    aday_text = "\n".join(satirlar)

    ilce_t = temiz_ilce(ilce)
    konum_satiri = f"\nİlçe: {ilce_t}" if ilce_t else ""

    prompt = f"""Aşağıdaki firma için en uygun web sitesini seç.

Firma Adı: {firma_adi}{konum_satiri}

Aday Domain'ler (Google sonuç başlığı ve özeti ile):
{aday_text}

Kurallar:
- Sadece firmanın KENDİ resmi web sitesini seç
- Firma rehberleri, sosyal medya, bayi / yetkili servis siteleri SEÇME
- Başlık veya özette marka geçiyorsa güçlü sinyal say
- "rehber", "firma listesi", "iletişim bilgileri", "şikayet" içeren sonuçları ele
- Ünvan inşaat/yapı/emlak ise vakıf, yatırım, holding, banka, üniversite sitelerini SEÇME
- Marka adı domain'de geçmeli veya çok benzer olmalı
- Kısa kısaltma (3 harf) ise çok dikkatli ol; emin değilsen NONE
- İlçe verildiyse, o bölgedeki firmayı tercih et (aynı isimli zincir/şube varsa)
- Türkçe karakterler (ş, ı, ğ vb.) İngilizce karşılıklarına (s, i, g) dönüşebilir
- Genel kelimeler (inşaat, ticaret, ltd) domain'de olmayabilir

Cevap formatı:
Eğer güvenilir bir eşleşme varsa: SEÇİLEN_DOMAIN
Eğer güvenilir eşleşme yoksa: NONE

Cevap:"""

    try:
        ham = groq_chat_metin(
            groq_client,
            config["llm"]["model"],
            [{"role": "user", "content": prompt}],
            temperature=config["llm"]["temperature"],
            max_tokens=config["llm"]["max_tokens"],
            logger=logger,
        )
        cevap = ham.strip().upper()

        if cevap == "NONE":
            return None

        for _, url, domain in adaylar:
            if domain.upper() in cevap or cevap in domain.upper():
                return url

        return None

    except LLMErisilemedi:
        # Kota/erişim sorunu "eşleşme yok" DEĞİLDİR; çağıran karar veremediğini
        # bilmeli ki firma reddedilmek yerine yeniden denenebilsin.
        raise
    except Exception as e:
        logger.error(f"LLM Hatası: {e}")
        return None


def title_veya_llm_onayla(firma, en_iyi_url, en_iyi_domain, adaylar, ilce="", driver=None, serp_meta=None):
    """Title veya LLM ile doğrula; ülke/sektör tüm markalarda aranır.

    Döner: (sonuc_url, skor_guncellemesi_veya_None, log_etiketi)
    skor_guncellemesi: LLM başka aday seçtiyse o adayın skoru, değilse None.
    """
    siki = zayif_tek_marka_tokeni(firma) or kisa_marka_mi(firma)

    # LLM yalnızca gerektiğinde SERP meta okur (ekstra gezinme yok)
    if serp_meta is None and driver is not None:
        serp_meta = google_serp_meta_topla(driver)
        if logger and serp_meta:
            logger.info(f"  📄 SERP meta: {len(serp_meta)} sonuç (title/snippet)")

    def _uyumsuz(domain: str) -> bool:
        m = _serp_meta_esle(domain, serp_meta)
        return sektor_uyumsuz_mu(
            firma,
            domain,
            title=m.get("title") or "",
            snippet=m.get("snippet") or "",
        )

    def _ek_sinyal(domain: str, sayfa_title: str = "", govde: str = "") -> bool:
        m = _serp_meta_esle(domain, serp_meta)
        title = sayfa_title or (m.get("title") or "")
        snippet = m.get("snippet") or ""
        return ulke_sektor_uyumlu_mu(
            firma, domain, title=title, snippet=snippet, ilce=ilce, govde=govde
        )

    # Sektör uyumsuz adayı title ile bile kabul etme
    ham_title = ""
    sayfa_govde = ""
    if not _uyumsuz(en_iyi_domain):
        ham_title, soup, sayfa_govde = _anasayfa_title_adres(en_iyi_url)
        if title_marka_uyumlu(en_iyi_url, firma, siki=siki, ham=ham_title):
            m = _serp_meta_esle(en_iyi_domain, serp_meta)
            snippet = m.get("snippet") or ""
            sektor_ok = _sektor_pozitif_sinyal(
                firma,
                en_iyi_domain,
                title=ham_title,
                snippet=snippet,
                govde=sayfa_govde,
            )
            if not sektor_ok:
                sayfa_govde = _iletisim_adres_ekle(
                    en_iyi_url, soup, sayfa_govde, ilce
                )
            if not _ek_sinyal(en_iyi_domain, ham_title, sayfa_govde):
                if logger:
                    logger.info(
                        f"  ⚠ title yetmedi (aday Türkiye/sektör uymadı: {en_iyi_domain})"
                    )
            else:
                return en_iyi_url, None, "title onaylı"
    elif logger:
        logger.info(f"  ⚠ Sektör uyumsuz aday elendi (title öncesi): {en_iyi_domain}")

    llm_sonuc = llm_domain_sec(firma, adaylar, ilce=ilce, serp_meta=serp_meta)
    if llm_sonuc:
        try:
            sec_domain = urlparse(llm_sonuc).netloc.lower()
        except Exception:
            sec_domain = ""
        if sec_domain and ignore_edilmeli(llm_sonuc, sec_domain):
            if logger:
                logger.info(f"  ⚠ kamu/rehber LLM seçimi elendi: {sec_domain}")
            return "", None, None
        if sec_domain and _uyumsuz(sec_domain):
            if logger:
                logger.info(f"  ⚠ Sektör uyumsuz LLM seçimi elendi: {sec_domain}")
            return "", None, None
        if sec_domain and not _ek_sinyal(sec_domain):
            if logger:
                logger.info(
                    f"  ⚠ LLM seçimi elendi (Türkiye/sektör uymadı): {sec_domain}"
                )
            return "", None, None
        yeni_skor = None
        for skor, url, _domain in adaylar:
            if url == llm_sonuc:
                yeni_skor = skor
                break
        return llm_sonuc, yeni_skor, "LLM onaylı"

    return "", None, None


class ChromeOturumDustu(Exception):
    """Sekme/oturum öldü; Chrome yeniden başlatılıp firma tekrar denenmeli."""


_OTURUM_DUSTU_ANAHTAR = (
    "tab crashed",
    "session deleted",
    "invalid session id",
    "chrome not reachable",
    "disconnected: not connected",
    "target window already closed",
    "no such window",
    "browsing context has been discarded",
    "web view not found",
)


def _kisa_selenium_hata(exc) -> str:
    """Chromedriver stacktrace'sini at; tek satırlık mesaj."""
    text = getattr(exc, "msg", None) or str(exc)
    return text.split("Stacktrace:")[0].replace("\n", " ").strip()[:220]


def _oturum_dustu_mu(exc) -> bool:
    """Tab crash / invalid session — page-load timeout değil."""
    if isinstance(exc, (NoSuchWindowException, ChromeOturumDustu)):
        return True
    text = (getattr(exc, "msg", None) or str(exc)).lower()
    if "timed out receiving message from renderer" in text:
        return False
    return any(a in text for a in _OTURUM_DUSTU_ANAHTAR)


def _sorgu_normalize(metin: str) -> str:
    return "".join((metin or "").replace("+", " ").casefold().split())


def _google_bu_sorgu_mu(driver, sorgu: str) -> bool:
    """Timeout sonrası açık sayfa bu aramanın Google SERP'i mi?

    Önceki firmanın Google sonucuyla devam etmeyi engeller.
    """
    try:
        url = driver.current_url or ""
    except Exception:
        return False
    low = url.lower()
    if "google." not in low:
        return False
    if "/sorry/" in low:
        return True
    try:
        q = unquote(parse_qs(urlparse(url).query).get("q", [""])[0])
    except Exception:
        q = ""
    hedef = _sorgu_normalize(sorgu)
    mevcut = _sorgu_normalize(q)
    if not hedef:
        return "/search" in low
    return hedef in mevcut or mevcut in hedef


def _chrome_timeouts(drv):
    try:
        drv.set_page_load_timeout(25)
        drv.set_script_timeout(20)
    except Exception:
        pass
    return drv


def _chrome_options(debugger_address=None):
    opts = Options()
    opts.page_load_strategy = "eager"
    if debugger_address:
        opts.add_experimental_option("debuggerAddress", debugger_address)
        return opts
    # Yalnız yeni pencerede (debug attach bu bayrakları yok sayar)
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-hang-monitor")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-features=Translate,MediaRouter")
    opts.add_argument("--no-first-run")
    return opts


def _pencere_canli_mi(driver) -> bool:
    try:
        handles = list(driver.window_handles)
    except Exception:
        return False
    for h in reversed(handles):
        try:
            driver.switch_to.window(h)
            _ = driver.current_url
            return True
        except Exception:
            continue
    return False


def _yeni_sekme_ac(driver) -> bool:
    try:
        driver.switch_to.new_window("tab")
        return _pencere_canli_mi(driver)
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Target.createTarget", {"url": "about:blank"})
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
            return _pencere_canli_mi(driver)
    except Exception:
        pass
    return False


def driver_baslat(cfg, *, debug_dene=True):
    """Chrome driver başlatır. Önce debug port'a bağlanmayı dener."""
    debug_port = cfg.get("chrome", {}).get("debug_port", 9222)

    if debug_dene:
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=_chrome_options(f"127.0.0.1:{debug_port}"),
            )
            _chrome_timeouts(driver)
            if _pencere_canli_mi(driver) or _yeni_sekme_ac(driver):
                if logger:
                    logger.info(f"Debug modundaki Chrome'a bağlanıldı (port {debug_port}).")
                return driver
            try:
                driver.quit()
            except Exception:
                pass
            if logger:
                logger.warning("Debug Chrome sekmesi ölü, yeni pencere deneniyor...")
        except Exception:
            if logger:
                logger.warning(
                    f"Debug Chrome bulunamadı (port {debug_port}), yeni Chrome başlatılıyor..."
                )

    return _chrome_timeouts(
        webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=_chrome_options(),
        )
    )


def chrome_kurtar(driver, cfg, *, bagimsiz=False):
    """Çöken sekme/oturumu toparla; gerekirse yeni Chrome."""
    if driver is not None and not bagimsiz:
        if _pencere_canli_mi(driver):
            if logger:
                logger.info("  ↪ Mevcut Chrome sekmesine geçildi.")
            return _chrome_timeouts(driver)
        if _yeni_sekme_ac(driver):
            if logger:
                logger.info("  ↪ Yeni sekme açıldı.")
            return _chrome_timeouts(driver)

    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass

    if logger:
        if bagimsiz:
            logger.info("  🔄 Ayrı Chrome başlatılıyor (debug port yok)...")
        else:
            logger.info("  🔄 Chrome yeniden bağlanıyor...")
    return driver_baslat(cfg, debug_dene=not bagimsiz)


def captcha_var_mi(driver):
    """Google doğrulama (captcha) sayfası mı gösteriliyor?"""
    try:
        if "/sorry/" in driver.current_url.lower():
            return True
        kaynak = driver.page_source.lower()
    except Exception as e:
        if _oturum_dustu_mu(e):
            raise ChromeOturumDustu(_kisa_selenium_hata(e)) from e
        return False

    return any(x in kaynak for x in CAPTCHA_ISARETLERI)


def google_ara(driver, firma, ilce=""):
    """Firmayı Google'da aratır (marka + 'resmi site' + opsiyonel ilçe)."""
    timeout = config["google"]["captcha_timeout"]
    sorgu = arama_sorgusu(firma, ilce=ilce)
    if logger:
        logger.info(f"  🔎 Sorgu: {sorgu}")
    while True:
        sayfa_ok = False
        arama_url = "https://www.google.com/search?q=" + quote(sorgu)
        for deneme in range(2):
            try:
                driver.get(arama_url)
                sayfa_ok = True
                break
            except Exception as e:
                if _oturum_dustu_mu(e):
                    raise ChromeOturumDustu(_kisa_selenium_hata(e)) from e
                if logger:
                    logger.warning(
                        f"  ⚠ Sayfa yükleme zaman aşımı "
                        f"({deneme + 1}/2): {_kisa_selenium_hata(e)}"
                    )
                try:
                    driver.execute_script("window.stop();")
                except Exception as stop_e:
                    if _oturum_dustu_mu(stop_e):
                        raise ChromeOturumDustu(_kisa_selenium_hata(stop_e)) from stop_e
                if _google_bu_sorgu_mu(driver, sorgu):
                    if logger:
                        logger.info("  ↪ Kısmi Google sayfası ile devam.")
                    sayfa_ok = True
                    break

        if not sayfa_ok:
            if logger:
                logger.warning("  ❌ Google sayfası açılamadı, firma atlanıyor.")
            return False

        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception as e:
            if _oturum_dustu_mu(e):
                raise ChromeOturumDustu(_kisa_selenium_hata(e)) from e
            if not _google_bu_sorgu_mu(driver, sorgu):
                if logger:
                    logger.warning("  ❌ Google body beklenemedi, firma atlanıyor.")
                return False

        if not captcha_var_mi(driver):
            return True

        logger.warning("  ⚠ GOOGLE CAPTCHA ÇIKTI!")
        logger.warning("  Chrome penceresinde doğrulamayı çözün...")
        logger.warning("  Çözülünce otomatik devam edilecek.")
        logger.warning("<<<CAPTCHA_START>>>")

        chrome_one_getir()
        captcha_durum_yaz(
            True,
            0,
            "Google CAPTCHA çıktı — Chrome penceresinde doğrulamayı çözün.",
        )

        beklendi = 0
        while captcha_var_mi(driver):
            time.sleep(5)
            beklendi += 5
            captcha_durum_yaz(
                True,
                beklendi,
                f"Google CAPTCHA bekleniyor ({beklendi} sn) — Chrome'da çözün.",
            )

            if beklendi % 30 == 0:
                logger.info(f"  ⏳ Captcha bekleniyor ({beklendi} sn)...")
                chrome_one_getir()  # tekrar öne getir

            if beklendi >= timeout:
                logger.warning(f"  ⏱ {timeout} saniye doldu. Firma atlanıyor.")
                captcha_durum_temizle()
                logger.warning("<<<CAPTCHA_END>>>")
                return False

        captcha_durum_temizle()
        logger.info("<<<CAPTCHA_END>>>")
        logger.info("  ✅ Captcha çözüldü, devam ediliyor.")
        time.sleep(3)


def islenmis_firmalari_yukle(output_file, df_girdi, sicil_var):
    """Çıktıdaki her yazılı satır işlenmiş sayılır (TIMEOUT dahil, tekrar yok)."""
    if not os.path.exists(output_file):
        return set()

    try:
        df_uretilen = normalize_columns(pd.read_excel(output_file))
        return islenmis_kaynak_satirlari(df_uretilen, df_girdi, sicil_var)
    except Exception:
        return set()


def _kayit_yaz(
    firma, web, skor, sicil, kaynak_satir, durum, aday_web="", red_neden=""
):
    """Standart çıktı satırı; kaynak satır anahtarı resume için benzersizdir.

    ADAY_WEB / RED_NEDEN yalnızca gözden geçirme içindir: reddedilen en iyi
    aday ve gerekçesi kaybolmasın diye yazılır, karara etkisi yoktur.
    """
    return {
        COL_KAYNAK_SATIR: kaynak_satir,
        COL_SICIL: sicil,
        COL_UNVAN: firma,
        COL_WEB: web,
        COL_SKOR: skor,
        COL_DURUM: durum,
        COL_ADAY_WEB: aday_web,
        COL_RED_NEDEN: red_neden,
    }


def main():
    global config, logger, groq_client

    # CLI argümanları
    parser = argparse.ArgumentParser(description="Firma web sitesi bulucu")
    parser.add_argument("--input", "-i", help="Girdi Excel dosyası (varsayılan: config.yaml'dan)")
    parser.add_argument("--output", "-o", help="Çıktı Excel dosyası (varsayılan: girdi adından türetilir)")
    args = parser.parse_args()

    config = load_config()
    logger = setup_logging(config["dosyalar"]["log"], name="sitebul")
    captcha_durum_temizle()
    
    YUKSEK_ESIK = config["skor"]["yuksek_esik"]
    DUSUK_ESIK = config["skor"]["dusuk_esik"]
    MIN_BEKLEME = config["bekleme"]["min_arasi"]
    MAX_BEKLEME = config["bekleme"]["max_arasi"]
    MAX_ADAY = config["google"]["max_aday"]
    LLM_ENABLED = config["llm"]["enabled"]
    IGNORE_MARKA = config.get("ignore_marka_domainleri", [])
    IGNORE_KALIP = [
        str(k).lower().strip()
        for k in config.get("ignore_domain_kaliplari", [])
        if k
    ]

    # Dosya yolları: CLI > config
    INPUT_FILE = args.input or config["dosyalar"]["input"]
    if args.output:
        OUTPUT_WEB_FILE = args.output
    elif args.input:
        # Girdi adından otomatik türet: firmalar_1.xlsx → firmalar_1_web.xlsx
        base = os.path.splitext(INPUT_FILE)[0]
        OUTPUT_WEB_FILE = f"{base}_web.xlsx"
    else:
        OUTPUT_WEB_FILE = config["dosyalar"]["output_web"]

    logger.info(f"Girdi: {INPUT_FILE} → Çıktı: {OUTPUT_WEB_FILE}")

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    if GROQ_API_KEY and not GROQ_API_KEY.startswith("gsk_"):
        logger.warning(
            "GROQ_API_KEY formatı beklenenden farklı (gsk_ ile başlamıyor). "
            "Yanlış key girilmiş olabilir."
        )
    if GROQ_API_KEY and LLM_ENABLED:
        groq_client = Groq(api_key=GROQ_API_KEY)
    else:
        groq_client = None
        logger.warning("GROQ_API_KEY bulunamadı veya LLM devre dışı. LLM özelliği kullanılmayacak.")

    driver = driver_baslat(config)

    logger.info(f"Input dosyası okunuyor: {INPUT_FILE}")
    try:
        df = normalize_columns(pd.read_excel(INPUT_FILE, header=0))
    except Exception as e:
        logger.error(f"Excel okunurken hata: {e}")
        driver.quit()
        return

    logger.info(f"Toplam {len(df)} firma bulundu.")

    SICIL_VAR = COL_SICIL in df.columns
    UNVAN_VAR = COL_UNVAN in df.columns
    ILCE_VAR = COL_ILCE in df.columns

    if not UNVAN_VAR:
        logger.error(
            f"HATA: Excel dosyasında '{COL_UNVAN}' sütunu bulunamadı "
            "(Firma / şirket alias'ları da kabul edilir)!"
        )
        driver.quit()
        return

    if SICIL_VAR:
        logger.info(f"{COL_SICIL} sütunu bulundu, resume özelliği aktif.")
    else:
        logger.info(
            f"{COL_SICIL} sütunu yok — satır indeksi {COL_SICIL} olarak kullanılacak "
            "(tekrarlı UNVAN'lar dahil her girdi satırı korunur)."
        )
    if ILCE_VAR:
        logger.info(f"{COL_ILCE} sütunu bulundu — Google sorgusuna eklenecek.")
    else:
        logger.info(f"{COL_ILCE} sütunu yok (opsiyonel) — sorgu yalnız marka ile yapılır.")

    sonuclar_liste = []
    # Her N firmada bir diske yaz — büyük listede her satırda Excel I/O olmasın
    ARA_KAYIT_ADIMI = max(1, int(config.get("ara_kayit_araligi", 10)))

    def _ara_kaydet():
        nonlocal sonuclar_liste
        if len(sonuclar_liste) < ARA_KAYIT_ADIMI:
            return
        if sonuclari_diske_yaz(sonuclar_liste, OUTPUT_WEB_FILE, logger):
            logger.info(f"  💾 {len(sonuclar_liste)} kayıt diske yazıldı.")
            sonuclar_liste = []

    def _onayla(firma, en_iyi_url, en_iyi_domain, adaylar, ilce, serp_meta):
        """title/LLM doğrulaması; kota tükendiyse dördüncü değer True döner.

        Kota hatası RED değildir — firma LLM_YOK ile işaretlenip bir sonraki
        çalıştırmada yeniden denenir.
        """
        try:
            sonuc, skor_guncel, etiket = title_veya_llm_onayla(
                firma, en_iyi_url, en_iyi_domain, adaylar,
                ilce=ilce, driver=driver, serp_meta=serp_meta,
            )
            return sonuc, skor_guncel, etiket, False
        except LLMErisilemedi as e:
            logger.warning(f"  ⏳ LLM erişilemedi, karar ertelendi: {e}")
            return "", None, None, True

    # Aynı UNVAN+İLÇE tekrarında Google'a tekrar gitme; sonucu kopyala (satır silinmez).
    unvan_cache = {}
    # domain → ilk atandığı ünvan. Aynı site iki farklı firmaya atanıyorsa
    # marka jenerik demektir (teknikyapi.com 4 firmaya atanmıştı); satır
    # silinmez, KABUL_SUPHELI ile işaretlenir.
    domain_sahibi = {}

    try:
        islenmis_satirlar = islenmis_firmalari_yukle(OUTPUT_WEB_FILE, df, SICIL_VAR)
        baslangic_sayisi = len(islenmis_satirlar)

        if baslangic_sayisi > 0:
            logger.info(f"  📂 {baslangic_sayisi} satır zaten işlenmiş, kaldığı yerden devam ediliyor...")
        else:
            logger.info("  🆕 Yeni işlem başlatılıyor...")

        toplam = len(df)
        progress_durum_yaz(baslangic_sayisi, toplam, "Site bul")

        for idx, row in tqdm(df.iterrows(), total=len(df), desc="Firmalar taranıyor", unit="firma", disable=False):
            # Resume / hiza anahtarı: gerçek SİCİL veya girdi satır indeksi.
            # UNVAN kullanılmaz — tekrarlı ünvanlar ayrı satır olarak kalır.
            sicil = str(row[COL_SICIL]) if SICIL_VAR else str(idx)
            kaynak_satir = kaynak_satir_anahtari(idx)
            firma = str(row[COL_UNVAN]).strip()
            ilce = temiz_ilce(row[COL_ILCE]) if ILCE_VAR else ""
            cache_key = (firma, ilce)

            if kaynak_satir in islenmis_satirlar:
                continue
            islenmis_satirlar.add(kaynak_satir)

            # Aynı UNVAN+İLÇE daha önce bu çalışmada bulunduysa sonucu kopyala
            if cache_key in unvan_cache:
                web_c, skor_c, durum_c, aday_c, neden_c = unvan_cache[cache_key]
                sonuclar_liste.append(
                    _kayit_yaz(
                        firma, web_c, skor_c, sicil, kaynak_satir, durum_c,
                        aday_web=aday_c, red_neden=neden_c,
                    )
                )
                logger.info(f"  ↪ {firma}  (önceki sonuç kopyalandı)")
                progress_durum_yaz(len(islenmis_satirlar), toplam, "Site bul", firma)
                _ara_kaydet()
                continue

            konum = f" [{ilce}]" if ilce else ""
            logger.info(f"  🔍 {firma}{konum}")
            progress_durum_yaz(len(islenmis_satirlar) - 1, toplam, "Site bul", firma)

            sonuclar = []
            atlandi = False
            max_retry = 3
            for retry in range(max_retry):
                try:
                    if not google_ara(driver, firma, ilce=ilce):
                        _neden = "Google sayfası açılamadı / CAPTCHA süresi doldu"
                        unvan_cache[cache_key] = ("", "", DURUM_TIMEOUT, "", _neden)
                        sonuclar_liste.append(
                            _kayit_yaz(
                                firma, "", "", sicil, kaynak_satir, DURUM_TIMEOUT,
                                red_neden=_neden,
                            )
                        )
                        logger.warning(f"  ❌ {DURUM_TIMEOUT}: {firma}")
                        atlandi = True
                        break

                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "a[href]"))
                    )
                    sonuclar = driver.execute_script(HREF_TOPLA_JS)
                    break
                    
                except StaleElementReferenceException:
                    if retry < max_retry - 1:
                        logger.warning(f"Stale element hatası, {retry + 1}. deneme...")
                        time.sleep(2)
                    else:
                        logger.error(f"Stale element hatası, {max_retry} deneme başarısız. Firma atlanıyor.")
                        _neden = f"stale element ({max_retry} deneme başarısız)"
                        unvan_cache[cache_key] = ("", "", DURUM_TIMEOUT, "", _neden)
                        sonuclar_liste.append(
                            _kayit_yaz(
                                firma, "", "", sicil, kaynak_satir, DURUM_TIMEOUT,
                                red_neden=_neden,
                            )
                        )
                        atlandi = True
                        break
                except SeleniumTimeoutException:
                    # Google sonuç vermedi — Chrome sağlam, sadece sonuç yok
                    logger.warning(f"  ⚠ Google sonuç bulunamadı (timeout): {firma}")
                    sonuclar = []
                    break
                except (ChromeOturumDustu, NoSuchWindowException, WebDriverException) as e:
                    kisa = _kisa_selenium_hata(e)
                    logger.warning(
                        f"  ⚠ Chrome sekmesi çöktü ({retry + 1}/{max_retry}): {kisa}"
                    )
                    driver = chrome_kurtar(
                        driver, config, bagimsiz=(retry >= 1)
                    )
                    if retry < max_retry - 1:
                        time.sleep(2)
                        continue
                    logger.error("  ❌ Chrome toparlanamadı, firma atlanıyor.")
                    _neden = f"Chrome toparlanamadı: {kisa}"
                    unvan_cache[cache_key] = ("", "", DURUM_TIMEOUT, "", _neden)
                    sonuclar_liste.append(
                        _kayit_yaz(
                            firma, "", "", sicil, kaynak_satir, DURUM_TIMEOUT,
                            red_neden=_neden,
                        )
                    )
                    atlandi = True
                    break

            if atlandi:
                progress_durum_yaz(len(islenmis_satirlar), toplam, "Site bul", firma)
                _ara_kaydet()
                time.sleep(random.uniform(MIN_BEKLEME, MAX_BEKLEME))
                continue

            adaylar = []
            gorulen_domainler = set()
            # Gözden geçirme alanları: reddedilen aday + gerekçe (karara etkisi yok)
            red_neden_parcalari = []
            en_iyi_url = ""

            for url in sonuclar:
                if not url:
                    continue

                url_lower = url.lower()

                if "/search?" in url_lower:
                    continue

                if "#" in url:
                    continue

                if url_lower.endswith(".pdf"):
                    continue

                try:
                    parsed = urlparse(url)
                    domain = parsed.netloc.lower()

                    if not domain:
                        continue

                    # Path ne olursa olsun (/iletisim, /bayi…) kök domain adaydır
                    if ignore_edilmeli(url, domain, IGNORE_KALIP):
                        continue

                    if any(domain.endswith(m) for m in IGNORE_MARKA):
                        continue

                    # İnşaat ünvanı ↔ vakıf/yatırım/holding domain — adaylara alma
                    if sektor_uyumsuz_mu(firma, domain):
                        continue

                    if domain in gorulen_domainler:
                        continue
                    gorulen_domainler.add(domain)

                    temiz = f"{parsed.scheme}://{parsed.netloc}/"

                except Exception:
                    continue

                skor = benzerlik_skoru(firma, domain)
                adaylar.append((skor, temiz, domain))

                if len(adaylar) >= MAX_ADAY:
                    break

            if adaylar:
                adaylar.sort(key=lambda x: x[0], reverse=True)
                en_iyi_skor, en_iyi_url, en_iyi_domain = adaylar[0]
                serp_meta = google_serp_meta_topla(driver)
                if serp_meta:
                    logger.info(f"  📄 SERP meta: {len(serp_meta)} sonuç (title/snippet)")
                serp_kayit = _serp_meta_esle(en_iyi_domain, serp_meta)
                zorunlu = dogrulama_zorunlu_mu(firma, en_iyi_domain)
                kisa = kisa_marka_mi(firma)
                sektor = sektor_uyumsuz_mu(
                    firma,
                    en_iyi_domain,
                    title=serp_kayit.get("title") or "",
                    snippet=serp_kayit.get("snippet") or "",
                )
                uyumlu = ulke_sektor_uyumlu_mu(
                    firma,
                    en_iyi_domain,
                    title=serp_kayit.get("title") or "",
                    snippet=serp_kayit.get("snippet") or "",
                    ilce=ilce,
                )
                if not uyumlu:
                    zorunlu = True

                if en_iyi_skor >= YUKSEK_ESIK and not zorunlu:
                    sonuc = en_iyi_url
                    durum = DURUM_KABUL
                    logger.info(f"  ✔ {sonuc}  (Skor: {en_iyi_skor} {DURUM_KABUL})")

                elif en_iyi_skor >= YUKSEK_ESIK and zorunlu:
                    neden = dogrulama_log_nedenleri(
                        firma,
                        en_iyi_domain,
                        kisa=kisa,
                        zayif=zayif_tek_marka_tokeni(firma),
                        sektor=sektor,
                        uyumlu=uyumlu,
                        title=serp_kayit.get("title") or "",
                        snippet=serp_kayit.get("snippet") or "",
                        ilce=ilce,
                    )
                    logger.info(
                        f"  ⚠ {', '.join(neden) or 'doğrulama'} — title/LLM zorunlu "
                        f"(skor {en_iyi_skor}, aday: {en_iyi_domain})"
                    )
                    sonuc, skor_guncel, etiket, llm_yok = _onayla(
                        firma, en_iyi_url, en_iyi_domain, adaylar, ilce, serp_meta
                    )
                    if llm_yok:
                        durum = DURUM_LLM_YOK
                        red_neden_parcalari = list(neden) + [
                            "LLM kotası tükendi — sonraki çalıştırmada denenecek"
                        ]
                        logger.warning(
                            f"  ⏳ {DURUM_LLM_YOK} (aday: {en_iyi_domain}, "
                            f"Skor: {en_iyi_skor})"
                        )
                    elif sonuc:
                        if skor_guncel is not None:
                            en_iyi_skor = skor_guncel
                        durum = DURUM_KABUL
                        logger.info(f"  ✔ {sonuc}  (Skor: {en_iyi_skor} - {etiket})")
                    else:
                        durum = red_dogrulama_durumu(
                            firma, sektor_uyumsuz=sektor, uyumlu=uyumlu
                        )
                        red_neden_parcalari = list(neden) + ["title/LLM onaylamadı"]
                        logger.warning(
                            f"  ❌ {durum} "
                            f"(en yüksek: {en_iyi_domain}, Skor: {en_iyi_skor})"
                        )

                elif en_iyi_skor >= DUSUK_ESIK:
                    hizli_kontrol_sonuc = (
                        False if zorunlu else hizli_domain_kontrol(firma, en_iyi_domain)
                    )

                    if hizli_kontrol_sonuc:
                        sonuc = en_iyi_url
                        durum = DURUM_KABUL
                        logger.info(f"  ✔ {sonuc}  (Skor: {en_iyi_skor} - hızlı kontrol)")
                    else:
                        neden = []
                        if zorunlu:
                            neden = dogrulama_log_nedenleri(
                                firma,
                                en_iyi_domain,
                                kisa=kisa,
                                zayif=zayif_tek_marka_tokeni(firma),
                                sektor=sektor,
                                uyumlu=uyumlu,
                                title=serp_kayit.get("title") or "",
                                snippet=serp_kayit.get("snippet") or "",
                                ilce=ilce,
                            )
                            logger.info(
                                f"  ⚠ {', '.join(neden) or 'doğrulama'} — title/LLM "
                                f"(skor {en_iyi_skor}, aday: {en_iyi_domain})"
                            )
                        sonuc, skor_guncel, etiket, llm_yok = _onayla(
                            firma, en_iyi_url, en_iyi_domain, adaylar, ilce, serp_meta
                        )
                        if llm_yok:
                            durum = DURUM_LLM_YOK
                            red_neden_parcalari = list(neden) + [
                                "LLM kotası tükendi — sonraki çalıştırmada denenecek"
                            ]
                            logger.warning(
                                f"  ⏳ {DURUM_LLM_YOK} (aday: {en_iyi_domain}, "
                                f"Skor: {en_iyi_skor})"
                            )
                        elif sonuc:
                            if skor_guncel is not None:
                                en_iyi_skor = skor_guncel
                            durum = DURUM_KABUL
                            logger.info(f"  ✔ {sonuc}  (Skor: {en_iyi_skor} - {etiket})")
                        else:
                            durum = red_dogrulama_durumu(
                                firma, sektor_uyumsuz=sektor, uyumlu=uyumlu
                            )
                            red_neden_parcalari = list(neden) + [
                                "title/LLM onaylamadı"
                            ]
                            logger.warning(
                                f"  ❌ {durum} "
                                f"(en yüksek: {en_iyi_domain}, Skor: {en_iyi_skor})"
                            )

                else:
                    sonuc = ""
                    durum = DURUM_RED_SKOR
                    red_neden_parcalari = [
                        f"skor {en_iyi_skor} < {DUSUK_ESIK} (düşük eşik)"
                    ]
                    logger.warning(
                        f"  ❌ {DURUM_RED_SKOR} (Skor: {en_iyi_skor}, en yüksek: {en_iyi_domain})"
                    )
            else:
                sonuc = ""
                en_iyi_skor = 0
                durum = DURUM_SITE_YOK
                red_neden_parcalari = ["Google sonuçlarında elenmemiş aday yok"]
                logger.warning(f"  ❌ {DURUM_SITE_YOK}")

            if durum == DURUM_KABUL and sonuc:
                _dom = _domain_kok(sonuc)
                _sahip = domain_sahibi.get(_dom)
                if _sahip is None:
                    domain_sahibi[_dom] = firma
                elif _sahip != firma:
                    durum = DURUM_KABUL_SUPHELI
                    red_neden_parcalari = [
                        f"aynı domain '{_sahip}' firmasına da atandı"
                    ]
                    logger.warning(
                        f"  ⚠ {DURUM_KABUL_SUPHELI}: {_dom} daha önce "
                        f"'{_sahip}' için kabul edilmişti"
                    )

            web_yaz = sonuc if durum in (DURUM_KABUL, DURUM_KABUL_SUPHELI) else ""
            skor_yaz = "" if durum in (DURUM_SITE_YOK, DURUM_TIMEOUT) else en_iyi_skor
            # Reddedilen aday kaybolmasın: gözden geçirme için sakla
            aday_yaz = "" if durum == DURUM_KABUL else en_iyi_url
            neden_yaz = "" if durum == DURUM_KABUL else "; ".join(red_neden_parcalari)
            if durum == DURUM_KABUL_SUPHELI:
                aday_yaz = ""   # WEB zaten dolu; aday sütununu tekrarlama
            unvan_cache[cache_key] = (web_yaz, skor_yaz, durum, aday_yaz, neden_yaz)
            sonuclar_liste.append(
                _kayit_yaz(
                    firma, web_yaz, skor_yaz, sicil, kaynak_satir, durum,
                    aday_web=aday_yaz, red_neden=neden_yaz,
                )
            )
            progress_durum_yaz(len(islenmis_satirlar), toplam, "Site bul", firma)
            _ara_kaydet()

            time.sleep(random.uniform(MIN_BEKLEME, MAX_BEKLEME))

    except KeyboardInterrupt:
        logger.warning("Kullanıcı tarafından durduruldu (Ctrl+C).")
        logger.info("Şu ana kadar bulunan sonuçlar kaydediliyor...")

    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}")
        logger.info("Sonuçlar kaydediliyor...")

    finally:
        try:
            # Kesilirse gerçek sayıyı koru; bitmişse total'e tamamla
            biten = len(islenmis_satirlar) if "islenmis_satirlar" in locals() else 0
            toplam_p = len(df) if "df" in locals() else biten
            progress_durum_yaz(biten, toplam_p, "Site bul", "Bitti")
        except Exception:
            pass
        captcha_durum_temizle()
        try:
            driver.quit()
            logger.info("Chrome kapatıldı.")
        except Exception:
            pass
        
        sonuc_df = final_kaydet(sonuclar_liste, OUTPUT_WEB_FILE, logger)
        if sonuc_df is not None:
            sonuc_df = girdi_sirasina_diz(sonuc_df, df, SICIL_VAR, OUTPUT_WEB_FILE, logger)
            logger.info("Bitti.")
            logger.info(f"Dosya oluşturuldu: {OUTPUT_WEB_FILE}")
            logger.info(f"Toplam satır: {len(sonuc_df)} (girdi: {len(df)})")

            if COL_WEB in sonuc_df.columns:
                logger.info(
                    f"Web sitesi bulunan: {dolu_hucre_sayisi(sonuc_df[COL_WEB])}"
                )
            if COL_DURUM in sonuc_df.columns:
                sayim = sonuc_df[COL_DURUM].astype(str).value_counts()
                ozet = ", ".join(f"{k} {v}" for k, v in sayim.items())
                logger.info(f"Durum: {ozet}")


if __name__ == "__main__":
    main()
