"""İnşaat ünvanı ↔ sektör uyumsuzluğu (snippet proje vs gerçek sapma)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    _domain_insaat_etiketi_var,
    dogrulama_zorunlu_mu,
    domain_yabanci_sektor_eki_mi,
    sektor_uyumsuz_mu,
    zayif_tek_marka_tokeni,
)

_DOSSA = "DOSSA YAPI İNŞAAT SANAYİ VE TİCARET LİMİTED ŞİRKETİ"
_DOSSA_SNIPPET = (
    "DOSSA Yapı otel ve hastane projeleri, konut yatırımı. Holding grup şirketleri."
)


def test_dossa_yapi_snippet_otel_yatirim_red_degil():
    """SERP'teki otel/yatırım müteahhit cümlesidir, host yapi olmasa da RED değil."""
    assert _domain_insaat_etiketi_var("dossayapi.com.tr") is True
    assert (
        sektor_uyumsuz_mu(
            _DOSSA,
            "dossayapi.com.tr",
            title="DOSSA Yapı",
            snippet=_DOSSA_SNIPPET,
        )
        is False
    )
    assert sektor_uyumsuz_mu(_DOSSA, "www.dossayapi.com.tr") is False
    assert (
        sektor_uyumsuz_mu(
            _DOSSA,
            "dossa.com.tr",
            title="DOSSA",
            snippet=_DOSSA_SNIPPET,
        )
        is False
    )


def test_dossa_zayif_token_title_hâlâ_şart():
    """5 harflik tek marka: sektör red kalksa da direkt KABUL yok."""
    assert zayif_tek_marka_tokeni(_DOSSA) is True
    assert dogrulama_zorunlu_mu(_DOSSA, "dossayapi.com.tr") is True


def test_atayatirim_domain_hâlâ_red():
    assert sektor_uyumsuz_mu("ATA İNŞAAT LTD ŞTİ", "atayatirim.com.tr") is True
    assert sektor_uyumsuz_mu("ATA İNŞAAT LTD ŞTİ", "www.ziraatyatirim.com.tr") is True


def test_snippet_otel_red_degil_domain_otel_red():
    """Özette otel red değil; host'ta hotel/yatirim red."""
    assert _domain_insaat_etiketi_var("medema.com") is False
    assert (
        sektor_uyumsuz_mu(
            "MEDEMA İNŞAAT LTD ŞTİ",
            "medema.com",
            title="Medema Hotel",
            snippet="5 yıldızlı otel rezervasyon",
        )
        is False
    )
    assert (
        sektor_uyumsuz_mu("MEDEMA İNŞAAT LTD ŞTİ", "medemahotel.com") is True
    )


def test_yapi_domain_vakif_banka_petshop_hâlâ_red():
    assert (
        sektor_uyumsuz_mu(
            _DOSSA,
            "dossayapi.com.tr",
            title="DOSSA Vakfı",
            snippet="vakıf bursu",
        )
        is True
    )
    assert (
        sektor_uyumsuz_mu(
            _DOSSA,
            "dossayapi.com.tr",
            snippet="anlaşmalı banka ve sigorta",
        )
        is True
    )
    assert (
        sektor_uyumsuz_mu(
            _DOSSA,
            "dossayapi.com.tr",
            snippet="petshop ve veteriner",
        )
        is True
    )


def test_av_tr_ve_otelinsaat_domain():
    assert sektor_uyumsuz_mu("NAM İNŞAAT", "nam.av.tr") is True
    # otelinsaat: proje kelimesi + inşaat ailesi → domain red değil
    assert sektor_uyumsuz_mu("NAM İNŞAAT", "otelinsaat.com.tr") is False


def test_insaat_olmayan_unvan_uyumsuz_sayilmaz():
    assert (
        sektor_uyumsuz_mu(
            "NAM GIDA LTD ŞTİ",
            "nam.com.tr",
            snippet="otel restoran",
        )
        is False
    )


def test_sektor_eki_insaat_vs_oto_reklam_nakliyat():
    """Soyad + başka faaliyet eki: inşaat/tüketim ünvanı için red."""
    assert domain_yabanci_sektor_eki_mi(
        "BAŞOĞLU İNŞAAT HAFRİYAT LTD. ŞTİ.", "basogluoto.com"
    )
    assert domain_yabanci_sektor_eki_mi(
        "KADIOĞULLARI İNŞAAT HAFRİYAT LTD. ŞTİ.", "kadioglureklam.com"
    )
    assert domain_yabanci_sektor_eki_mi(
        "ŞEREF DAYANIKLI TÜKETİM MALLARI LTD. ŞTİ.", "serefnakliyat.net"
    )
    assert sektor_uyumsuz_mu("BAŞOĞLU İNŞAAT HAFRİYAT LTD. ŞTİ.", "basogluoto.com")
    assert sektor_uyumsuz_mu(
        "KADIOĞULLARI İNŞAAT HAFRİYAT LTD. ŞTİ.", "kadioglureklam.com"
    )
    assert sektor_uyumsuz_mu(
        "ŞEREF DAYANIKLI TÜKETİM MALLARI LTD. ŞTİ.", "serefnakliyat.net"
    )


def test_sektor_eki_ayni_faaliyet_kalir():
    """Ünvandaki sektör domain ekine yapışık olsa da red değil."""
    assert not domain_yabanci_sektor_eki_mi("GÜNAY İNŞAAT LTD ŞTİ", "gunayinsaat.com")
    assert not domain_yabanci_sektor_eki_mi("GÜNAY İNŞAAT LTD ŞTİ", "gunay-insaat.com.tr")
    assert not domain_yabanci_sektor_eki_mi("DOSSA YAPI İNŞAAT", "dossayapi.com.tr")
    assert not domain_yabanci_sektor_eki_mi("ŞEREF NAKLİYAT LTD ŞTİ", "serefnakliyat.net")
    assert not domain_yabanci_sektor_eki_mi(
        "BAŞOĞLU OTOMOTİV LTD ŞTİ", "basogluoto.com"
    )
    assert not sektor_uyumsuz_mu("GÜNAY İNŞAAT LTD ŞTİ", "gunayinsaat.com")


def test_sektor_eki_tireli_ve_kisa_kalan():
    assert domain_yabanci_sektor_eki_mi("BAŞOĞLU İNŞAAT", "basoglu-oto.com")
    # Kalan marka < 4: foto / koto yanlış pozitif olmasın
    assert not domain_yabanci_sektor_eki_mi("BAŞOĞLU İNŞAAT", "foto.com")
    # Ünvan sektörsüz + inşaat eki serbest
    assert not domain_yabanci_sektor_eki_mi("AHMET LTD ŞTİ", "ahmetinsaat.com")
    # Sektörsüz ünvan + nakliyat eki yine red
    assert domain_yabanci_sektor_eki_mi("AHMET LTD ŞTİ", "ahmetnakliyat.com")
