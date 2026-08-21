"""Gerçek sicil örnekleri: var olan siteler bulunabilmeli."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sitebul import arama_sorgusu
from utils import (
    benzerlik_skoru,
    tam_marka_eslesmesi_mi,
    ulke_sektor_uyumlu_mu,
)


_ENB = (
    "ENB ENGİN BANT ZIMPARA VE POLİSAJ MALZEMELERİ SANAYİ DIŞ TİCARET "
    "LİMİTED ŞİRKETİ"
)


def test_alternatif_plastik():
    unvan = "ALTERNATİF PLASTİK SANAYİ VE DIŞ TİCARET LİMİTED ŞİRKETİ"
    assert benzerlik_skoru(unvan, "alternatifplastik.com.tr") >= 65
    assert (
        ulke_sektor_uyumlu_mu(
            unvan,
            "alternatifplastik.com.tr",
            title="Alternatif Plastik",
            snippet="",
            ilce="ESENYURT",
        )
        is True
    )
    assert "ESENYURT" in arama_sorgusu(unvan, ilce="ESENYURT")


def test_strong_zimpara():
    unvan = "STRONG ZIMPARA TEKNİK AŞINDIRICI SANAYİ VE TİCARET LİMİTED ŞİRKETİ"
    assert tam_marka_eslesmesi_mi(unvan, "strongzimpara.com")
    assert benzerlik_skoru(unvan, "strongzimpara.com") == 100


def test_egeli_zimpara():
    unvan = "EGELİ ZIMPARA SANAYİİ ANONİM ŞİRKETİ"
    assert tam_marka_eslesmesi_mi(unvan, "egeli.com.tr")
    assert benzerlik_skoru(unvan, "egeli.com.tr") == 100
    q = arama_sorgusu(unvan, ilce="BEYOĞLU")
    assert "SANAYİİ" not in q
    assert "BEYOĞLU" in q


def test_elmas_kimya():
    unvan = "ELMAS KİMYA SANAYİ VE TİCARET ANONİM ŞİRKETİ"
    assert tam_marka_eslesmesi_mi(unvan, "elmaskimya.com")
    assert benzerlik_skoru(unvan, "elmaskimya.com") == 100


def test_enb_enginbant_kisa_onek():
    """Gerçek site enginbant.com; ENB öneki skoru 40 altına düşürmemeli."""
    assert tam_marka_eslesmesi_mi(_ENB, "enginbant.com")
    assert benzerlik_skoru(_ENB, "enginbant.com") == 100
    assert benzerlik_skoru(_ENB, "enbenginbant.com") == 100
    q = arama_sorgusu(_ENB, ilce="KAĞITHANE")
    assert "KAĞITHANE" in q
    assert "ENB" in q


def test_saint_gobain():
    unvan = (
        "SAİNT GOBAİN İNOVATİF MALZEMELER VE AŞINDIRICI SANAYİ TİCARET "
        "ANONİM ŞİRKETİ"
    )
    assert benzerlik_skoru(unvan, "saint-gobain-abrasives.com") >= 65
    assert benzerlik_skoru(unvan, "saint-gobain.com.tr") >= 65


def test_aslan_zimpara():
    unvan = "ASLAN ZIMPARA TEKNİK AŞINDIRICILARI SANAYİ TİCARET LİMİTED ŞİRKETİ"
    assert tam_marka_eslesmesi_mi(unvan, "aslanzimpara.com")
    assert benzerlik_skoru(unvan, "aslanzimpara.com") == 100


def test_ata_silah_arms():
    unvan = "ATA SİLAH SANAYİ ANONİM ŞİRKETİ"
    assert tam_marka_eslesmesi_mi(unvan, "ataarms.com")
    assert benzerlik_skoru(unvan, "ataarms.com") == 100
    q = arama_sorgusu(unvan, ilce="ÇEKMEKÖY")
    assert "ÇEKMEKÖY" in q
    assert "ATA" in q
