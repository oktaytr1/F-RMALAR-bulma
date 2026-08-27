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


def test_yapiskik_hukuki_ek_ayrilir():
    """uscoltd → usco + ltd; tireli olan zaten ayrıydı."""
    assert domain_marka_etiketleri("uscoltd.com.tr") == ["usco", "ltd"]
    assert domain_marka_etiketleri("usco-ltd.com.tr") == ["usco", "ltd"]
    assert domain_marka_etiketleri("medemasti.com") == ["medema", "sti"]
    assert domain_marka_etiketleri("erturkltd.com.tr") == ["erturk", "ltd"]
    # Hukuki ek yoksa dokunma
    assert domain_marka_etiketleri("medema.com.tr") == ["medema"]
    # atlas yanlış bölünmesin (as ek listesinde yok)
    assert domain_marka_etiketleri("atlas.com.tr") == ["atlas"]


def test_usco_uscoltd_skor():
    """USCO ENDÜSTRİYEL ↔ uscoltd.com.tr artık RED_SKOR olmamalı."""
    unvan = "USCO ENDÜSTRİYEL ÇÖZÜMLER"
    assert benzerlik_skoru(unvan, "uscoltd.com.tr") >= 65
    assert benzerlik_skoru(unvan, "usco-ltd.com.tr") >= 65
    assert hizli_domain_kontrol(unvan, "uscoltd.com.tr") is True


def test_yapiskik_sektor_ek_ayrilir():
    """modelambalaj → model + ambalaj; temaofset → tema + ofset."""
    assert domain_marka_etiketleri("modelambalaj.com.tr") == ["model", "ambalaj"]
    assert domain_marka_etiketleri("ozenambalaj.com") == ["ozen", "ambalaj"]
    assert domain_marka_etiketleri("camisambalaj.com.tr") == ["camis", "ambalaj"]
    assert domain_marka_etiketleri("temaofset.com") == ["tema", "ofset"]
    assert domain_marka_etiketleri("iskainsaat.com") == ["iska", "insaat"]
    assert domain_marka_etiketleri("dnckimya.com") == ["dnc", "kimya"]
    assert domain_marka_etiketleri("ikizlerotomotiv.com") == ["ikizler", "otomotiv"]
    assert domain_marka_etiketleri("koclarmetal.com") == ["koclar", "metal"]
    assert domain_marka_etiketleri("ayescelik.com") == ["ayes", "celik"]
    # Kısa kök sektörle bölünmesin
    assert domain_marka_etiketleri("abcyapi.com") == ["abcyapi"]


def test_yapiskik_sektor_skor_ambalaj_ofset():
    assert benzerlik_skoru(
        "MODEL AMBALAJ ÜRÜNLERİ SANAYİ VE TİCARET A.Ş.",
        "www.modelambalaj.com.tr",
    ) >= 65
    assert benzerlik_skoru("ÖZEN KUTU AMBALAJ", "ozenambalaj.com") >= 65
    assert benzerlik_skoru("CAMİŞ AMBALAJ SANAYİ", "camisambalaj.com.tr") >= 65
    assert benzerlik_skoru("DNC KİMYA LTD", "dnckimya.com") >= 65
    assert benzerlik_skoru("İKİZLER OTOMOTİV LTD", "ikizlerotomotiv.com") >= 65
    assert hizli_domain_kontrol(
        "MODEL AMBALAJ ÜRÜNLERİ SANAYİ", "modelambalaj.com.tr"
    ) is True
    assert hizli_domain_kontrol("CAMİŞ AMBALAJ", "camisambalaj.com.tr") is True


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


def test_enb_kisa_onek_enginbant():
    unvan = (
        "ENB ENGİN BANT ZIMPARA VE POLİSAJ MALZEMELERİ SANAYİ DIŞ TİCARET "
        "LİMİTED ŞİRKETİ"
    )
    assert benzerlik_skoru(unvan, "enginbant.com") == 100
    assert hizli_domain_kontrol(unvan, "enginbant.com") is True


def test_ata_silah_ataarms():
    assert benzerlik_skoru("ATA SİLAH SANAYİ ANONİM ŞİRKETİ", "ataarms.com") == 100
