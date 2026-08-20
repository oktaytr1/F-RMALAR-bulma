"""firmalar.xlsx -> firmalar_web.xlsx

Girdi dosyasında Web sütunu zaten dolu olduğu için sitebul.py'ye gerek yok.
Bu script sadece sütun isimlerini mailbul.py'nin beklediği formata çevirir
ve bozuk URL'leri temizler.
"""
import re
import pandas as pd

INPUT = "firmalar.xlsx"
OUTPUT = "firmalar_web.xlsx"


def url_temizle(ham):
    """Bozuk URL'leri düzeltir.
    'www.http://turkoglu.com.tr' -> 'turkoglu.com.tr'
    'http://www.abc.com/' -> 'abc.com'"""
    if not isinstance(ham, str):
        return ""
    s = ham.strip().lower()
    if not s or s == "nan":
        return ""

    # 'www.http://' gibi hatalı önekleri at
    s = re.sub(r"^(www\.)?(https?://)?", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.rstrip("/")

    # Domain'de en az bir nokta olmalı
    if "." not in s.split("/")[0]:
        return ""
    return s


df = pd.read_excel(INPUT)

cikti = pd.DataFrame({
    "SİCİL": [str(i + 1) for i in range(len(df))],
    "UNVAN": df["Firma"].fillna("").astype(str).str.strip(),
    "WEB": df["Web"].apply(url_temizle),
})

cikti.to_excel(OUTPUT, index=False)

dolu = (cikti["WEB"] != "").sum()
print(f"{OUTPUT} oluşturuldu.")
print(f"  Toplam firma : {len(cikti)}")
print(f"  Web'i olan   : {dolu}")
print(f"  Web'i olmayan: {len(cikti) - dolu}")
