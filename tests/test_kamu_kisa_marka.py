"""Yabancı kamu TLD ve ülke/sektör uyumu testleri."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    _kamu_domain_mi,
    ignore_edilmeli,
    kisa_marka_mi,
    tam_marka_eslesmesi_mi,
    ulke_sektor_uyumlu_mu,
)


def test_kamu_nam_go_ug():
    """Canlı hata: NAM → nam.go.ug kamu alanı elenmeli."""
    assert _kamu_domain_mi("nam.go.ug") is True
    assert ignore_edilmeli("https://nam.go.ug/", "nam.go.ug") is True


def test_kamu_go_jp_kr():
    assert _kamu_domain_mi("mofa.go.jp") is True
    assert _kamu_domain_mi("korea.go.kr") is True
    assert _kamu_domain_mi("www.go.th") is True


def test_kamu_gov_gouv_gob():
    assert _kamu_domain_mi("nasa.gov") is True
    assert _kamu_domain_mi("www.gov.uk") is True
    assert _kamu_domain_mi("hmrc.gov.uk") is True
    assert _kamu_domain_mi("economie.gouv.fr") is True
    assert _kamu_domain_mi("www.gob.mx") is True
    assert _kamu_domain_mi("presidencia.gob.ar") is True
    assert _kamu_domain_mi("army.mil") is True


def test_kamu_tr_mevcut_davranis():
    assert _kamu_domain_mi("gib.gov.tr") is True
    assert _kamu_domain_mi("istanbul.bel.tr") is True
    assert _kamu_domain_mi("tevfikileriihl.meb.k12.tr") is True


def test_sirket_domain_kalir():
    assert _kamu_domain_mi("nam.com.tr") is False
    assert _kamu_domain_mi("medema.com") is False
    assert _kamu_domain_mi("go.com") is False
    assert _kamu_domain_mi("go.com.tr") is False
    assert ignore_edilmeli("https://nam.com.tr/", "nam.com.tr") is False


def test_kisa_marka_nam():
    assert kisa_marka_mi("NAM İNŞAAT LTD ŞTİ") is True


def test_yabanci_cctld_sektor_yok():
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.go.ug",
            title="NAM - Government of Uganda",
            snippet="National Agricultural Museum Uganda",
        )
        is False
    )


def test_kisa_marka_sadece_title_yetersiz():
    """Sektörsüz kısa marka: jenerik TLD + yalnız title yetmez."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM LTD ŞTİ",
            "nam.com",
            title="NAM",
            snippet="",
        )
        is False
    )


def test_insaat_tr_domain_title_yetmez():
    """Ünvanda inşaat varsa .tr tek başına yetmez; sektör de görünmeli."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM",
            snippet="",
        )
        is False
    )


def test_insaat_tr_domain_sektor_title():
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM İnşaat",
            snippet="",
        )
        is True
    )


def test_sektor_jenerik_tld():
    assert (
        ulke_sektor_uyumlu_mu(
            "ATA YAPI LTD ŞTİ",
            "ata.com",
            title="ATA Yapı – İnşaat",
            snippet="Konut projeleri",
        )
        is True
    )


def test_kisa_marka_istanbul_sinyali():
    """Sektör yok, kısa marka: TR coğrafyası yeter."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM LTD ŞTİ",
            "nam.com",
            title="NAM",
            snippet="İstanbul merkezli firma",
        )
        is True
    )


def test_kisa_marka_ilce_sinyali():
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM LTD ŞTİ",
            "nam.com",
            title="NAM Kadıköy",
            snippet="",
            ilce="Kadıköy",
        )
        is True
    )


def test_tam_marka_domaini_sektor_kelimesi_gerektirmez():
    """MEDEMA İNŞAAT + medema.com — domain markanın birebir kendisi.

    Eskiden sayfada 'inşaat' geçmediği için reddediliyordu; marka adını
    domain yapan firmalar (nilpa.com.tr, duzey.com.tr) bu yüzden toplu
    hâlde eleniyordu. Kimlik zaten kanıtlı olduğu için sektör kelimesi
    ayrıca şart değil; çelişki hâlâ sektor_uyumsuz_mu ile yakalanır.
    """
    assert tam_marka_eslesmesi_mi("MEDEMA İNŞAAT", "medema.com") is True
    # .tr → TR sinyali var, muafiyet geçerli
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT",
            "medema.com.tr",
            title="Medema",
            snippet="",
        )
        is True
    )
    # Jenerik TLD + TR sinyali yok → muafiyet YOK (HOMES İNŞAAT → homes.com
    # riski). Aday reddedilmez, title/LLM doğrulamasına gider.
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT",
            "medema.com",
            title="Medema",
            snippet="",
        )
        is False
    )
    # Jenerik TLD ama sayfada TR coğrafya sinyali → muafiyet geçerli
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT",
            "medema.com",
            title="Medema",
            snippet="İstanbul merkezli firma",
        )
        is True
    )


def test_tam_marka_muafiyeti_fazladan_kelimeyi_kapsamaz():
    """Domain ünvanda olmayan kelime taşıyorsa muafiyet yok."""
    # SDK GAYRİMENKUL → çekirdek 'aisdk', ünvan öneki 'sdk' ile eşleşmez
    assert tam_marka_eslesmesi_mi("SDK GAYRİMENKUL İNŞAAT", "ai-sdk.dev") is False
    # BOSA MÜHENDİSLİK → çekirdek 'bosabelgium'
    assert tam_marka_eslesmesi_mi("BOSA MÜHENDİSLİK İNŞAAT", "bosa.belgium.be") is False
    # Marka domain'in SONUNDA; baştan eşleşme değil
    assert tam_marka_eslesmesi_mi("TUĞBA EFEOĞLU İNŞAAT", "efeoglu.com") is False


def test_tam_marka_muafiyeti_kisa_kisaltmayi_kapsamaz():
    """YCD / VKV gibi 3 harfli çekirdekler büyük kurum domain'ine yapışır."""
    assert tam_marka_eslesmesi_mi("YCD GROUP İNŞAAT MİMARLIK", "ycd.com.tr") is False
    assert tam_marka_eslesmesi_mi("VKV VAKIF İNŞAAT", "vkv.org.tr") is False


def test_tam_marka_muafiyeti_yabanci_cctld_kurtarmaz():
    """Muafiyet ülke kontrolünden ÖNCE gelmez; .de + TR sinyali yok → hayır."""
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT",
            "medema.de",
            title="Medema",
            snippet="",
        )
        is False
    )


def test_uzun_marka_insaat_snippet():
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT",
            "medema.com",
            title="Medema",
            snippet="İnşaat ve taahhüt hizmetleri",
        )
        is True
    )


def test_uzun_marka_insaat_domain():
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT",
            "medemainsaat.com.tr",
            title="Medema",
            snippet="",
        )
        is True
    )


def test_sektor_ailesi_yapi_insaat():
    """Ünvanda inşaat, aday title'da yapı — aynı aile."""
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA İNŞAAT LTD",
            "medema.com.tr",
            title="Medema Yapı",
            snippet="",
        )
        is True
    )


def test_ticaret_sanayi_sektor_sayilmaz():
    """Ticaret/sanayi evrak kelimesi; sayfada aranmaz."""
    assert (
        ulke_sektor_uyumlu_mu(
            "MEDEMA TİCARET SANAYİ LTD ŞTİ",
            "medema.com",
            title="Medema",
            snippet="",
        )
        is True
    )


def test_ignore_yol_uzerinden_elemez():
    """Meşru sitenin YOLU rehber kelimesi içerse de firma elenmemeli."""
    assert (
        ignore_edilmeli(
            "https://ornekinsaat.com.tr/urunler/isrehberi", "ornekinsaat.com.tr"
        )
        is False
    )
    assert (
        ignore_edilmeli(
            "https://ornekyapi.com/bayi/firmalar.com-hakkinda", "ornekyapi.com"
        )
        is False
    )


def test_ignore_host_alt_dizgesi_korunur():
    """Config kalıpları host içinde alt dizge olarak eşleşmeye devam etmeli."""
    kaliplar = ["tso.org.tr", "n11.com"]
    # istanbultso.org.tr kasten elenir (oda sitesi)
    assert (
        ignore_edilmeli(
            "https://www.istanbultso.org.tr/", "www.istanbultso.org.tr", kaliplar
        )
        is True
    )
    assert ignore_edilmeli("https://www.n11.com/urun/x", "www.n11.com", kaliplar) is True


def test_ignore_yol_kalibi_url_de_aranir():
    """Kalıp '/' içeriyorsa (.org.tr/vakif) tüm URL'de aranmalı."""
    kaliplar = [".org.tr/vakif"]
    assert (
        ignore_edilmeli(
            "https://ornek.org.tr/vakif/hakkinda", "ornek.org.tr", kaliplar
        )
        is True
    )
    assert (
        ignore_edilmeli("https://ornek.org.tr/projeler", "ornek.org.tr", kaliplar)
        is False
    )
