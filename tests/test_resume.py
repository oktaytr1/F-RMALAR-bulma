"""Tekrarlı sicil numaralarında resume davranışı testleri."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import COL_KAYNAK_SATIR, COL_SICIL, islenmis_kaynak_satirlari


def test_legacy_cikti_tekrarli_sicilin_ilk_satirina_eslenir():
    girdi = pd.DataFrame({COL_SICIL: ["100", "100", "200"]})
    eski_cikti = pd.DataFrame({COL_SICIL: ["100"]})

    assert islenmis_kaynak_satirlari(eski_cikti, girdi, sicil_var=True) == {"0"}


def test_kaynak_satir_anahtari_tekrarli_sicilleri_ayri_tutar():
    girdi = pd.DataFrame({COL_SICIL: ["100", "100", "200"]})
    yeni_cikti = pd.DataFrame({
        COL_SICIL: ["100", "100"],
        COL_KAYNAK_SATIR: [0, 1],
    })

    assert islenmis_kaynak_satirlari(yeni_cikti, girdi, sicil_var=True) == {"0", "1"}
