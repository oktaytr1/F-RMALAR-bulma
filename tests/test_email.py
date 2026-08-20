"""E-posta regex ve filtreleme testleri."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mailbul import (
    EMAIL_REGEX,
    temizle,
    en_iyi_mail_sec,
    mail_sec,
    obfuscation_coz,
    tarama_url_gecerli_mi,
)


def test_email_regex_gecerli():
    assert EMAIL_REGEX.match("info@acmeyapi.com.tr")
    assert EMAIL_REGEX.match("contact@medema-insaat.com")
    assert EMAIL_REGEX.match("a.b.c@medema.co")


def test_email_regex_gecersiz():
    assert not EMAIL_REGEX.match("@acmeyapi.com")
    assert not EMAIL_REGEX.match("")
    assert not EMAIL_REGEX.match("noatsign")


def test_temizle_red_listesi():
    """RED listesindeki kalıplar filtrelenir, info@ geçer."""
    mailler = ["noreply@acmeyapi.com", "info@acmeyapi.com", "admin@acmeyapi.com"]
    sonuc = temizle(mailler)
    assert "info@acmeyapi.com" in sonuc
    assert "noreply@acmeyapi.com" not in sonuc
    assert "admin@acmeyapi.com" not in sonuc


def test_temizle_asset_uzantisi():
    mailler = ["contact@2x.png", "info@acmeyapi.com"]
    sonuc = temizle(mailler)
    assert "contact@2x.png" not in sonuc
    assert "info@acmeyapi.com" in sonuc


def test_temizle_placeholder():
    mailler = ["name@domain.com", "example@acmeyapi.com", "info@acmeyapi.com"]
    sonuc = temizle(mailler)
    assert "name@domain.com" not in sonuc
    assert "info@acmeyapi.com" in sonuc


def test_temizle_ardisik_nokta():
    """Local part'ta ardışık nokta filtrelenir."""
    mailler = ["a..b@acmeyapi.com", "info@acmeyapi.com"]
    sonuc = temizle(mailler)
    assert "a..b@acmeyapi.com" not in sonuc


def test_temizle_numerik_local():
    """Tamamen sayısal local part filtrelenir."""
    mailler = ["12345@acmeyapi.com", "info@acmeyapi.com"]
    sonuc = temizle(mailler)
    assert "12345@acmeyapi.com" not in sonuc


def test_en_iyi_mail_sec_oncelik():
    """info@ öncelik listesinde sales@'den önce gelir."""
    mailler = ["sales@acmeyapi.com", "info@acmeyapi.com", "muhasebe@diger.com"]
    sonuc = en_iyi_mail_sec(mailler, "acmeyapi.com")
    assert sonuc == "info@acmeyapi.com"


def test_en_iyi_mail_sec_bos():
    assert en_iyi_mail_sec([], "") == ""
    assert en_iyi_mail_sec(["noreply@acmeyapi.com"], "acmeyapi.com") == ""


def test_en_iyi_mail_sec_domain_filtre():
    """Site domain'i verildiğinde yalnız aynı domain'den mail seçer."""
    mailler = ["info@baskadomain.com", "contact@acmeyapi.com"]
    sonuc = en_iyi_mail_sec(mailler, "acmeyapi.com")
    assert sonuc == "contact@acmeyapi.com"


def test_obfuscation_coz():
    sonuc = obfuscation_coz("info [at] acmeyapi [dot] com")
    assert "info@acmeyapi.com" in sonuc


def test_obfuscation_parentheses():
    sonuc = obfuscation_coz("info (at) acmeyapi (dot) com")
    assert "info@acmeyapi.com" in sonuc


def test_tarama_url_cop_sitemap():
    assert tarama_url_gecerli_mi("###m/iletisim") is False
    assert tarama_url_gecerli_mi("###/hakkimizda") is False
    assert tarama_url_gecerli_mi("#contact") is False


def test_tarama_url_gmail_compose():
    assert tarama_url_gecerli_mi(
        "https://mail.google.com/mail/?view=cm&fs=1&to=a@b.com&su=iletisim"
    ) is False
    assert tarama_url_gecerli_mi("https://gmail.com/") is False


def test_tarama_url_gecerli():
    assert tarama_url_gecerli_mi("https://ornek.com/iletisim") is True
    assert tarama_url_gecerli_mi("/hakkimizda") is True
    assert tarama_url_gecerli_mi("https://www.ornek.com.tr/contact") is True


# ---------------------------------------------------------------------------
# RED listesi alt dizge yerine tam yerel kısım eşleşmesi
# ---------------------------------------------------------------------------

def test_red_ik_mesru_adresleri_elemez():
    """'ik@' girdisi teknik@ / mühendislik@ gibi adresleri yememeli."""
    for local in ("teknik", "mekanik", "muhendislik", "lojistik", "grafik"):
        mail = f"{local}@acmeyapi.com"
        assert temizle([mail]) == [mail], f"{mail} yanlışlıkla elendi"


def test_red_isim_iletisimi_elemez():
    """'isim@' girdisi ONCELIK'teki iletisim@'i yememeli."""
    assert temizle(["iletisim@acmeyapi.com"]) == ["iletisim@acmeyapi.com"]


def test_red_tam_yerel_kisim_hala_eleniyor():
    """Tam eşleşenler elenmeye devam etmeli."""
    for mail in (
        "ik@acmeyapi.com", "hr@acmeyapi.com", "isim@acmeyapi.com",
        "kvkk@acmeyapi.com", "demo@acmeyapi.com", "dev@acmeyapi.com",
    ):
        assert temizle([mail]) == [], f"{mail} elenmedi"


def test_red_alt_dizge_girdileri_korunur():
    """'@' ile bitmeyen girdiler alt dizge olarak çalışmaya devam etmeli."""
    for mail in (
        "noreply@acmeyapi.com", "kariyer@acmeyapi.com",
        "muhasebe@acmeyapi.com", "webmaster@acmeyapi.com",
    ):
        assert temizle([mail]) == [], f"{mail} elenmedi"


# ---------------------------------------------------------------------------
# mail_sec: farklı domain + marka eşleşmesi
# ---------------------------------------------------------------------------

def test_mail_sec_ayni_domain_oncelikli():
    secilen, aday = mail_sec(
        ["medemainsaat@gmail.com", "info@medemainsaat.com.tr"],
        "medemainsaat.com.tr",
        "MEDEMA İNŞAAT",
    )
    assert secilen == "info@medemainsaat.com.tr"
    assert aday == ""


def test_mail_sec_marka_uyan_freemail_kabul():
    """Site firmanın kendisi; markayı taşıyan gmail adresi onun sayılır."""
    secilen, aday = mail_sec(
        ["medemainsaat@gmail.com"], "medemainsaat.com.tr", "MEDEMA İNŞAAT LTD"
    )
    assert secilen == "medemainsaat@gmail.com"
    assert aday == ""


def test_mail_sec_kardes_domain_kabul():
    secilen, aday = mail_sec(
        ["info@medema.com.tr"], "medemainsaat.com.tr", "MEDEMA İNŞAAT LTD"
    )
    assert secilen == "info@medema.com.tr"
    assert aday == ""


def test_mail_sec_marka_uymayan_aday_olur():
    """Seçilmez ama kaybolmaz — ADAY_EMAIL'e yazılır."""
    secilen, aday = mail_sec(
        ["iletisim@ajansx.com"], "medemainsaat.com.tr", "MEDEMA İNŞAAT LTD"
    )
    assert secilen == ""
    assert aday == "iletisim@ajansx.com"


def test_mail_sec_kisa_marka_freemail_kabul_etmez():
    """3 harfli marka rastgele yerel kısımlara yapışır — kabul yok."""
    secilen, aday = mail_sec(["ycd@gmail.com"], "ycd.com.tr", "YCD GROUP İNŞAAT")
    assert secilen == ""
    assert aday == "ycd@gmail.com"


def test_mail_sec_unvansiz_eski_davranis():
    """Ünvan verilmezse marka eşleşmesi devreye girmez."""
    assert mail_sec(["info@baska.com"], "acmeyapi.com")[0] == ""
