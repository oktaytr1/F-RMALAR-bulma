"""Sicil ilçesi: opsiyonel sütun, sektör ikamesi ve ters red."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from utils import (
    COL_ILCE,
    _sutun_anahtar,
    dogrulama_log_nedenleri,
    ilce_metinde_mi,
    ilce_sinyali_uygun_mu,
    normalize_columns,
    ulke_sektor_uyumlu_mu,
)


def test_ilce_jenerik_ve_kisa_uygun_degil():
    assert ilce_sinyali_uygun_mu("") is False
    assert ilce_sinyali_uygun_mu("Of") is False
    assert ilce_sinyali_uygun_mu("Merkez") is False
    assert ilce_sinyali_uygun_mu("Kadıköy") is True
    assert ilce_sinyali_uygun_mu("Bornova") is True


def test_ilce_yoksa_sektor_kurali_ayni():
    """İLÇE sütunu yok/boş: inşaat ünvanında title sektörü şart."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM",
            snippet="",
        )
        is False
    )
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM İnşaat",
            snippet="",
            ilce="",
        )
        is True
    )


def test_footer_ilce_eksik_insaat_title_ikame():
    """Marka domain'de, footer'da sicil ilçesi → title'da inşaat olmasa da uyum."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM",
            snippet="",
            ilce="Kadıköy",
            govde="Caferağa Mah. Kadıköy / İstanbul Tel: 0216",
        )
        is True
    )


def test_ilce_title_snippet_ikame_etmez():
    """İkame yalnız gövde/iletişim; SERP snippet'te ilçe yetmez."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM Kadıköy",
            snippet="Kadıköy'de hizmet",
            ilce="Kadıköy",
            govde="",
        )
        is False
    )


def test_jenerik_ilce_ikame_etmez():
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM",
            snippet="",
            ilce="Merkez",
            govde="Merkez Mahallesi No:1",
        )
        is False
    )


def test_baska_ilce_baska_sektor_red():
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com",
            title="NAM",
            snippet="",
            ilce="Kadıköy",
            govde="Bornova tekstil atölyesi İzmir",
        )
        is False
    )


def test_sube_iki_ilce_red_yok():
    """Sicil ilçesi sayfada varsa başka ilçe reddi yok."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM İnşaat",
            snippet="",
            ilce="Kadıköy",
            govde="Kadıköy merkez, Bornova şube",
        )
        is True
    )


def test_ayni_sektor_baska_ilce_red_yok():
    """Yalnız proje ilçesi: kendi sektör varken red yok."""
    assert (
        ulke_sektor_uyumlu_mu(
            "NAM İNŞAAT LTD ŞTİ",
            "nam.com.tr",
            title="NAM İnşaat",
            snippet="",
            ilce="Kadıköy",
            govde="Bornova konut projesi",
        )
        is True
    )


def test_ilce_metinde_kelime():
    assert ilce_metinde_mi("Kadıköy", "Adres: Kadıköy / İstanbul") is True
    assert ilce_metinde_mi("Kadıköy", "kadıköylü değiliz") is False


def test_sutun_ilce_turkce_i():
    """'İlçe' / 'İLÇE' Excel başlığı İLÇE olarak tanınır."""
    assert _sutun_anahtar("İLÇE") == "ilce"
    assert _sutun_anahtar("İlçe") == "ilce"
    assert _sutun_anahtar("Ilce") == "ilce"
    df = pd.DataFrame({"SİCİL": [1], "UNVAN": ["X"], "İlçe": ["Kadıköy"]})
    out = normalize_columns(df)
    assert COL_ILCE in out.columns
    assert out[COL_ILCE].iloc[0] == "Kadıköy"


def test_log_yabanci_site_excel_ulke_degil():
    """`.uk` Excel sütunu değil; log 'yabancı site' der, 'ülke' demez."""
    neden = dogrulama_log_nedenleri(
        "EPG LTD ŞTİ",
        "www.epigeneticcoaching.co.uk",
        kisa=True,
        zayif=False,
        sektor=False,
        uyumlu=False,
        title="Epigenetic Coaching",
        snippet="",
        ilce="Kadıköy",
    )
    assert "yabancı site" in neden
    assert all("ülke" not in n for n in neden)
