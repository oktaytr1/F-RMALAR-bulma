"""Marka çıkarımı unit testleri."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    marka_tokenlari,
    marka_metinde_kelime_dizisi,
    zayif_tek_marka_tokeni,
    kisa_marka_mi,
    unvan_faaliyet_kelimeleri,
)
from sitebul import arama_sorgusu


def test_marka_tokenlari_basit():
    """Normal firma adından marka tokenlarını çıkarır."""
    sonuc = marka_tokenlari("MEDEMA İNŞAAT LTD ŞTİ")
    assert "medema" in sonuc
    assert "insaat" not in sonuc  # GENEL_KELIMELER'de
    assert "ltd" not in sonuc


def test_marka_tokenlari_coklu():
    sonuc = marka_tokenlari("POLAT ÇELİK YAPI SANAYİ")
    assert "polat" in sonuc
    assert "celik" not in sonuc  # çelik artık GENEL_KELIMELER'de
    assert "yapi" not in sonuc
    assert "sanayi" not in sonuc


def test_marka_tokenlari_fallback():
    """Tüm kelimeler genel ise ilk kelime fallback olarak kullanılır."""
    sonuc = marka_tokenlari("İNŞAAT SANAYİ TİCARET LTD")
    # Tüm kelimeler genel — fallback ilk kelimeyi almalı
    assert len(sonuc) >= 1


def test_marka_tokenlari_bos():
    assert marka_tokenlari("") == []
    assert marka_tokenlari("A") == []  # tek harf, < 2


def test_zayif_tek_marka_tokeni():
    assert zayif_tek_marka_tokeni("AS İLERİ") is True
    assert zayif_tek_marka_tokeni("MEDEMA İNŞAAT") is False
    assert zayif_tek_marka_tokeni("PRO TİCARET LTD") is True


def test_kisa_marka():
    assert kisa_marka_mi("VKV İNŞAAT") is True  # 'vkv' <= 3
    assert kisa_marka_mi("MEDEMA YAPI") is False


def test_marka_metinde_kelime_dizisi():
    assert marka_metinde_kelime_dizisi("medemainsaat", "Medema İnşaat A.Ş.") is True
    assert marka_metinde_kelime_dizisi("medema", "Bu bir test") is False
    assert marka_metinde_kelime_dizisi("", "test") is False
    assert marka_metinde_kelime_dizisi("ab", "test") is False  # < 4 harf


def test_faaliyet_skora_karismasın():
    """Google'a inşaat eklenir; marka tokenlarına eklenmez."""
    assert "insaat" not in marka_tokenlari("ENSAR MİMARLIK İNŞAAT SANAYİ LTD")
    assert unvan_faaliyet_kelimeleri("ENSAR MİMARLIK İNŞAAT SANAYİ LTD") == [
        "MİMARLIK",
        "İNŞAAT",
    ]


def test_arama_sorgusu_ensar():
    assert (
        arama_sorgusu("ENSAR MİMARLIK İNŞAAT SANAYİ TİCARET LİMİTED ŞİRKETİ")
        == "ENSAR MİMARLIK İNŞAAT resmi site"
    )


def test_arama_sorgusu_sanayi_yok():
    q = arama_sorgusu("SAK GRUP İNŞAAT LİMİTED ŞİRKETİ")
    assert q == "SAK İNŞAAT resmi site"
    assert "SANAYİ" not in q
    assert "TİCARET" not in q


def test_arama_sorgusu_ilce():
    assert arama_sorgusu("ENSAR MİMARLIK LTD", ilce="Kadıköy") == (
        "ENSAR MİMARLIK resmi site Kadıköy"
    )


def test_faaliyet_yazilim_bilisim_teknoloji():
    """Yazılım marka tokenı değil; Google sorgusuna faaliyet olarak girer."""
    unvan = "KORKMAZ YAZILIM TİCARET LİMİTED ŞİRKETİ"
    assert "yazilim" not in marka_tokenlari(unvan)
    assert unvan_faaliyet_kelimeleri(unvan) == ["YAZILIM"]
    assert arama_sorgusu(unvan, ilce="Küçükçekmece") == (
        "KORKMAZ YAZILIM resmi site Küçükçekmece"
    )
    assert arama_sorgusu("HAMKO TEKNOLOJİ SAVUNMA SANAYİ LTD") == (
        "HAMKO SAVUNMA TEKNOLOJİ resmi site"
    )
    assert arama_sorgusu("ATA BİLİŞİM LTD") == "ATA BİLİŞİM resmi site"


def test_faaliyet_reklam_kargo_makine():
    """GENEL'de atılan faaliyetler sorguya geri konur; ticaret/ltd girmez."""
    assert unvan_faaliyet_kelimeleri("KADIOĞLU REKLAM MATBAA LTD") == [
        "REKLAM",
        "MATBAA",
    ]
    assert arama_sorgusu("KADIOĞLU REKLAM AJANS LTD") == (
        "KADIOĞLU REKLAM AJANS resmi site"
    )
    assert arama_sorgusu("ŞEREF KARGO TAŞIMACILIK LTD") == (
        "ŞEREF KARGO TAŞIMACILIK resmi site"
    )
    assert arama_sorgusu("BAŞOĞLU İNŞAAT HAFRİYAT LTD") == (
        "BAŞOĞLU İNŞAAT HAFRİYAT resmi site"
    )
    assert arama_sorgusu("AYDIN MERMER LTD") == "AYDIN MERMER resmi site"
    assert arama_sorgusu("POLAT AMBALAJ LTD") == "POLAT AMBALAJ resmi site"
    assert arama_sorgusu("YILMAZ MEDİKAL LTD") == "YILMAZ MEDİKAL resmi site"
    assert arama_sorgusu("STAR TEMİZLİK LTD") == "STAR TEMİZLİK resmi site"
    assert arama_sorgusu("STAR GÜVENLİK LTD") == "STAR GÜVENLİK resmi site"
    assert arama_sorgusu("ORHAN AHŞAP LTD") == "ORHAN AHŞAP resmi site"
    assert arama_sorgusu("DEMİR MAKİNE LTD") == "DEMİR MAKİNE resmi site"
