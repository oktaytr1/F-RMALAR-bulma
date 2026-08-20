"""DURUM sütunu testleri."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    COL_DURUM,
    COL_KAYNAK_SATIR,
    DURUM_KABUL,
    DURUM_RED_DOGRULAMA,
    DURUM_RED_SEKTOR,
    DURUM_RED_SKOR,
    DURUM_SITE_YOK,
    DURUM_TIMEOUT,
    islenmis_kaynak_satirlari,
    COL_SICIL,
    red_dogrulama_durumu,
    dolu_hucre_sayisi,
)
import pandas as pd


def test_timeout_satiri_islenmis_sayilir():
    """TIMEOUT da yazılı durumdur; resume tekrar denemez."""
    girdi = pd.DataFrame({COL_SICIL: ["1", "2"]})
    cikti = pd.DataFrame({
        COL_SICIL: ["1"],
        COL_KAYNAK_SATIR: [0],
        COL_DURUM: [DURUM_TIMEOUT],
    })
    assert islenmis_kaynak_satirlari(cikti, girdi, sicil_var=True) == {"0"}


def test_red_dogrulama_sektor():
    assert (
        red_dogrulama_durumu("MEDEMA İNŞAAT", sektor_uyumsuz=True, uyumlu=False)
        == DURUM_RED_SEKTOR
    )
    assert (
        red_dogrulama_durumu("MEDEMA İNŞAAT", sektor_uyumsuz=False, uyumlu=False)
        == DURUM_RED_SEKTOR
    )


def test_red_dogrulama_onaylanmadi():
    assert (
        red_dogrulama_durumu("NAM LTD ŞTİ", sektor_uyumsuz=False, uyumlu=True)
        == DURUM_RED_DOGRULAMA
    )


def test_durum_sabitleri():
    assert DURUM_KABUL == "KABUL"
    assert DURUM_SITE_YOK == "SITE_YOK"
    assert DURUM_RED_SKOR == "RED_SKOR"
    assert DURUM_RED_SEKTOR == "RED_SEKTOR"
    assert DURUM_RED_DOGRULAMA == "RED_DOGRULAMA"
    assert DURUM_TIMEOUT == "TIMEOUT"
    assert COL_DURUM == "DURUM"
    assert COL_KAYNAK_SATIR


def test_dolu_hucre_na_sayilmaz():
    """pandas NA / 'nan' dolu sayılmaz (eski log 225 yalanı)."""
    s = pd.Series(["info@a.com", None, "", "nan", "None", pd.NA], dtype="object")
    assert dolu_hucre_sayisi(s) == 1
    assert dolu_hucre_sayisi(pd.Series([pd.NA] * 3, dtype="object")) == 0
