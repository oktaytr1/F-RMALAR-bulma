"""Puanlama sistemi unit testleri."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import benzerlik_skoru, domain_marka_etiketleri, domain_tld_cezasi
from sitebul import hizli_domain_kontrol


def test_tam_eslesmesi():
    """Marka = domain çekirdeği → 100."""
    assert benzerlik_skoru("MEDEMA İNŞAAT", "medema.com.tr") == 100


def test_kismen_eslesmesi():
    """Marka domain'de geçiyor ama tam değil."""
    skor = benzerlik_skoru("POLAT ÇELİK", "polatcelik.com")
    assert skor >= 60  # çelik artık genel kelime, skor düşer


def test_hic_eslesmeme():
    """Hiç eşleşme yok → düşük skor."""
    skor = benzerlik_skoru("MEDEMA İNŞAAT", "google.com")
    assert skor < 40


def test_bos_giris():
    assert benzerlik_skoru("", "test.com") == 0
    assert benzerlik_skoru("TEST", "") == 0


def test_domain_marka_etiketleri():
    assert domain_marka_etiketleri("www.afy-insaat.com.tr") == ["afy", "insaat"]
    assert domain_marka_etiketleri("medema.com.tr") == ["medema"]
    assert domain_marka_etiketleri("") == []


def test_domain_tld_cezasi():
    assert domain_tld_cezasi("test.store") == 30
    assert domain_tld_cezasi("test.com.tr") == 0
    assert domain_tld_cezasi("test.xyz") == 30


def test_supheli_tld_skor_cezasi():
    """Şüpheli TLD skor cezası."""
    normal = benzerlik_skoru("MEDEMA İNŞAAT", "medema.com.tr")
    supheli = benzerlik_skoru("MEDEMA İNŞAAT", "medema.store")
    assert supheli < normal


def test_kisa_tek_marka_s_z_bulanik_dusuk():
    """İSKA ≠ izka: 4 harfte tek fark 75 olmasın, eşik altı kalsın."""
    unvan = "İSKA İNŞAAT EMLAK NAKLİYAT TURİZM TİCARET LİMİTED ŞİRKETİ"
    assert benzerlik_skoru(unvan, "izka.com.tr") <= 35
    assert benzerlik_skoru("ARBAŞ İNŞAAT LTD ŞTİ", "erbas.com.tr") <= 35
    assert benzerlik_skoru("SAÇAK ENERJİ İNŞAAT TURİZM", "sazak.com.tr") <= 35
    assert hizli_domain_kontrol(unvan, "izka.com.tr") is False


def test_kisa_tek_marka_tam_ve_onek_serbest():
    """Aynı yazım ve marka+sektör eki (iskainsaat) bulanık yasağa girmez."""
    unvan = "İSKA İNŞAAT LTD ŞTİ"
    assert benzerlik_skoru(unvan, "iska.com.tr") == 100
    assert benzerlik_skoru(unvan, "iska-insaat.com.tr") == 100
    assert benzerlik_skoru(unvan, "iskainsaat.com") > 35
    assert hizli_domain_kontrol(unvan, "iska.com.tr") is True
    # 6 harf: kural yok, MEDEMA/MEDENA bulanık kalır
    assert benzerlik_skoru("MEDEMA İNŞAAT", "medena.com.tr") > 35
