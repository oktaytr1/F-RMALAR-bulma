"""SERP CSS seçicileri config.yaml'dan okunur; JS'ye güvenli enjekte edilir."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sitebul
from utils import (
    DEFAULT_SERP_CARDS,
    DEFAULT_SERP_SNIPPET,
    google_serp_secicileri,
    load_config,
)


def test_config_yaml_selectors_dolu():
    cfg = load_config(str(ROOT / "config.yaml"))
    sel = cfg["google"]["selectors"]
    assert "div." in sel["cards"]
    assert "div." in sel["snippet"] or "span." in sel["snippet"]


def test_secici_google_selectors():
    cfg = {"google": {"selectors": {"cards": "div.FOO", "snippet": "div.BAR"}}}
    s = google_serp_secicileri(cfg)
    assert s["cards"] == "div.FOO"
    assert s["snippet"] == "div.BAR"


def test_secici_ust_duzey_google_selectors():
    cfg = {"google_selectors": {"cards": "div.X", "snippet": "span.Y"}}
    s = google_serp_secicileri(cfg)
    assert s["cards"] == "div.X"
    assert s["snippet"] == "span.Y"


def test_secici_eksik_snippet_varsayilan():
    cfg = {"google": {"selectors": {"cards": "div.SADECE_KART"}}}
    s = google_serp_secicileri(cfg)
    assert s["cards"] == "div.SADECE_KART"
    assert s["snippet"] == DEFAULT_SERP_SNIPPET


def test_secici_bos_config_varsayilan():
    s = google_serp_secicileri({})
    assert s["cards"] == DEFAULT_SERP_CARDS
    assert s["snippet"] == DEFAULT_SERP_SNIPPET


def test_serp_js_secici_enjekte():
    js = sitebul._serp_meta_js("div.FOO", "span.BAR")
    assert json.dumps("div.FOO") in js
    assert json.dumps("span.BAR") in js
    assert "__CARDS__" not in js
    assert "__SNIPPET__" not in js


def test_serp_js_tirnak_kacisi():
    tehlikeli = 'div.foo", alert(1), "'
    encoded = json.dumps(tehlikeli)
    js = sitebul._serp_meta_js(tehlikeli, "span.ok")
    assert encoded in js
    assert "alert(1)" not in js.replace(encoded, "")


class _SahteDriver:
    def __init__(self):
        self.js = ""

    def execute_script(self, js):
        self.js = js
        return [
            {
                "domain": "ornek.com",
                "url": "https://ornek.com/",
                "title": "Örnek",
                "snippet": "Açıklama",
            }
        ]


def test_serp_meta_topla_configden_okur():
    eski = sitebul.config
    sitebul.config = {
        "google": {"selectors": {"cards": "div.OZEL_KART", "snippet": "div.OZEL_SNIP"}}
    }
    try:
        driver = _SahteDriver()
        meta = sitebul.google_serp_meta_topla(driver)
    finally:
        sitebul.config = eski

    assert "div.OZEL_KART" in driver.js
    assert "div.OZEL_SNIP" in driver.js
    assert meta["ornek.com"]["title"] == "Örnek"
