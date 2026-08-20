"""Mail aşamasının girdi sütunlarını çıktıya taşıması.

Site aşaması SKOR / DURUM / ADAY_WEB / RED_NEDEN üretiyor. mailbul kaydı
sıfırdan kurduğu için bu sütunlar eskiden düşüyordu; panelde "Site bul +
Mail bul" birlikte çalıştırıldığında son dosyada gözden geçirme katmanı
kayboluyordu.
"""

import logging
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mailbul
from utils import (
    COL_ADAY_WEB,
    COL_DURUM,
    COL_EMAIL,
    COL_KAYNAK_SATIR,
    COL_RED_NEDEN,
    COL_SICIL,
    COL_SKOR,
    COL_UNVAN,
    COL_WEB,
    kaynak_satir_anahtari,
)


def _tasinacak(df):
    """main() içindeki seçimin aynısı."""
    uretilen = {COL_UNVAN, COL_WEB, COL_EMAIL, COL_SICIL, COL_KAYNAK_SATIR}
    return [c for c in df.columns if c not in uretilen]


def test_tasinacak_sutunlar_uretilenleri_dislar():
    df = pd.DataFrame(
        columns=[
            COL_KAYNAK_SATIR, COL_SICIL, COL_UNVAN, COL_WEB,
            COL_SKOR, COL_DURUM, COL_ADAY_WEB, COL_RED_NEDEN, "İLÇE",
        ]
    )
    assert _tasinacak(df) == [
        COL_SKOR, COL_DURUM, COL_ADAY_WEB, COL_RED_NEDEN, "İLÇE",
    ]


def test_firma_isle_ek_sutunlari_tasir(monkeypatch):
    """Site boş olan satırda bile ek sütunlar çıktıya geçmeli (ağ gerekmez)."""
    monkeypatch.setattr(mailbul, "logger", logging.getLogger("test_tasima"))

    ekstra = {
        COL_SKOR: 75,
        COL_DURUM: "RED_DOGRULAMA",
        COL_ADAY_WEB: "https://turksar.com.tr/",
        COL_RED_NEDEN: "kısa marka; title/LLM onaylamadı",
        "İLÇE": "KADIKÖY",
    }
    kayit = mailbul.firma_isle(("TÜRKSAR LTD", "", "368438-5", "1", ekstra))

    for sutun, deger in ekstra.items():
        assert kayit[sutun] == deger, f"{sutun} taşınmadı"
    # Bu aşamanın kendi ürettikleri korunur
    assert kayit[COL_UNVAN] == "TÜRKSAR LTD"
    assert kayit[COL_EMAIL] == ""
    assert kayit[COL_SICIL] == "368438-5"
    assert kayit[COL_KAYNAK_SATIR] == "1"


def test_firma_isle_ekstra_uretilen_sutunu_ezmez(monkeypatch):
    """Girdide EMAIL/WEB olsa bile mail aşamasının sonucu üstte kalmalı."""
    monkeypatch.setattr(mailbul, "logger", logging.getLogger("test_tasima"))

    # TASINACAK bunları zaten dışlar; yine de savunma amaçlı doğrula
    kayit = mailbul.firma_isle(
        ("X LTD", "", "1", "0", {COL_SKOR: 40})
    )
    assert kayit[COL_WEB] == ""
    assert kayit[COL_EMAIL] == ""
    assert kayit[COL_SKOR] == 40


def test_kaynak_satir_tekrar_etmez(monkeypatch):
    """_KAYNAK_SATIR üretilenlerde; ekstra olarak ikinci kez yazılmamalı."""
    monkeypatch.setattr(mailbul, "logger", logging.getLogger("test_tasima"))

    df = pd.DataFrame(
        {
            COL_KAYNAK_SATIR: [0],
            COL_SICIL: ["100"],
            COL_UNVAN: ["A LTD"],
            COL_WEB: [""],
            COL_DURUM: ["SITE_YOK"],
        }
    )
    tasinacak = _tasinacak(df)
    assert COL_KAYNAK_SATIR not in tasinacak

    satir = df.iloc[0]
    kayit = mailbul.firma_isle(
        (
            "A LTD",
            "",
            "100",
            kaynak_satir_anahtari(0),
            {c: satir[c] for c in tasinacak},
        )
    )
    assert kayit[COL_KAYNAK_SATIR] == "0"
    assert kayit[COL_DURUM] == "SITE_YOK"
