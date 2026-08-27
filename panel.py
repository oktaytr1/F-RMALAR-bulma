"""Firma Bulucu — Yerel ekip paneli (Streamlit).

Her bilgisayarda çalışır: Excel yükle → Site bul / Mail bul → sonuç indir.
Mevcut sitebul.py ve mailbul.py scriptlerini alt süreç olarak çağırır.
Durdur: SIGINT (Ctrl+C gibi) → o ana kadar kayıt + kısmi indirme.
"""

from __future__ import annotations

import atexit
import html
import importlib
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv

import utils as _utils_mod

importlib.reload(_utils_mod)

from utils import captcha_durum_oku, captcha_durum_temizle, chrome_one_getir
from utils import progress_durum_oku, progress_durum_temizle
from utils import web_mail_aday_satirlari, supheli_eslesmeleri_llm_bul
from utils import (
    normalize_columns,
    dolu_hucre_sayisi,
    COL_SICIL,
    COL_UNVAN,
    COL_WEB,
    COL_EMAIL,
    COL_SKOR,
    COL_ILCE,
    COL_DURUM,
    COL_ADAY_WEB,
    COL_RED_NEDEN,
    COL_ADAY_EMAIL,
    DURUM_KABUL,
    DURUM_KABUL_SUPHELI,
    DURUM_LLM_YOK,
)

ROOT = Path(__file__).resolve().parent
JOBS_DIR = ROOT / "panel_jobs"
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
PYTHON = sys.executable

load_dotenv(ENV_PATH)

def eski_joblari_temizle(max_gun: int = 7) -> int:
    """panel_jobs/ altındaki eski job klasörlerini temizler."""
    if not JOBS_DIR.exists():
        return 0
    temizlenen = 0
    sinir = datetime.now() - timedelta(days=max_gun)
    for d in list(JOBS_DIR.iterdir()):
        if not d.is_dir():
            continue
        # captcha_status.json ve progress_status.json gibi durum dosyalarına dokunma
        if d.name.startswith("."):
            continue
        try:
            # Klasördeki en son değiştirilen dosyanın zamanını kontrol et
            en_yeni = max(
                (f.stat().st_mtime for f in d.rglob("*") if f.is_file()),
                default=d.stat().st_mtime,
            )
            if datetime.fromtimestamp(en_yeni) < sinir:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
                temizlenen += 1
        except Exception:
            continue
    return temizlenen

JOBS_DIR.mkdir(exist_ok=True)
_temizlenen = eski_joblari_temizle()

st.set_page_config(
    page_title="Firma Bulucu",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def _atexit_temizle():
    """Panel kapanırken çalışan subprocess'leri temizler."""
    run = st.session_state.get("run") if hasattr(st, "session_state") else None
    if not run:
        return
    proc = run.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            sinyal_ile_durdur(proc)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    captcha_durum_temizle()
    progress_durum_temizle()


atexit.register(_atexit_temizle)


def chrome_acik_mi(port: int = 9222) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def load_cfg() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def patch_config_scalars(updates: dict[str, object]) -> None:
    """config.yaml içindeki skaler anahtarları yerinde günceller (yorumlar korunur)."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    for key, value in updates.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)

        def _repl(m: re.Match, val: str = rendered) -> str:
            return f"{m.group(1)}{val}{m.group(2)}"

        pattern = rf"^(\s*{re.escape(key)}:\s*)[^\s#]+(.*)$"
        text, n = re.subn(pattern, _repl, text, count=1, flags=re.MULTILINE)
        if n == 0:
            raise KeyError(f"config.yaml içinde bulunamadı: {key}")
    CONFIG_PATH.write_text(text, encoding="utf-8")


def url_temizle(ham) -> str:
    if not isinstance(ham, str):
        return ""
    s = ham.strip().lower()
    if not s or s == "nan":
        return ""
    s = re.sub(r"^(www\.)?(https?://)?", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.rstrip("/")
    if "." not in s.split("/")[0]:
        return ""
    return s


def job_slug(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{stamp}" if stem else stamp


def file_download_bytes(path: Path) -> bytes | None:
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    return None


def _preview_ozet(preview: pd.DataFrame) -> str:
    """Tek satırlık özet: 13 satır · Web 10/13 · Email 7/13"""
    n = len(preview)
    parts = [f"{n} satır"]
    if COL_WEB in preview.columns:
        parts.append(f"Web {dolu_hucre_sayisi(preview[COL_WEB])}/{n}")
    if COL_EMAIL in preview.columns:
        parts.append(f"Email {dolu_hucre_sayisi(preview[COL_EMAIL])}/{n}")
    if COL_ADAY_EMAIL in preview.columns:
        aday = dolu_hucre_sayisi(preview[COL_ADAY_EMAIL])
        if aday:
            parts.append(f"⚠ Aday mail {aday}")
    if COL_ILCE in preview.columns:
        parts.append(f"İlçe {dolu_hucre_sayisi(preview[COL_ILCE])}/{n}")
    if COL_DURUM in preview.columns:
        durumlar = preview[COL_DURUM].astype(str)
        kabul = (durumlar == DURUM_KABUL).sum()
        parts.append(f"Kabul {kabul}/{n}")
        supheli = (durumlar == DURUM_KABUL_SUPHELI).sum()
        if supheli:
            parts.append(f"⚠ Şüpheli {supheli}")
        llm_yok = (durumlar == DURUM_LLM_YOK).sum()
        if llm_yok:
            parts.append(f"⏳ LLM bekliyor {llm_yok}")
    return " · ".join(parts)


def show_result_downloads(outputs: list[tuple[str, str]], *, partial: bool = False) -> None:
    """Çıktı dosyalarını indir + önizle + şüpheli eşleşme analizi."""
    title = "Kısmi sonuçlar (durduruldu)" if partial else "Sonuçlar"
    st.subheader(title)
    if partial:
        st.info(
            "O ana kadar kaydedilen satırlar hazır. "
            "Aynı dosyayla tekrar Başlat → kaldığı yerden devam eder."
        )

    dosyalar: list[tuple[str, Path, pd.DataFrame]] = []
    for label, path_str in outputs:
        path = Path(path_str)
        if not path.exists() or path.stat().st_size <= 0:
            continue
        try:
            preview = normalize_columns(pd.read_excel(path))
            # None / NaN gösterimini sadeleştir
            preview = preview.fillna("")
        except Exception:
            continue
        dosyalar.append((label, path, preview))

    if not dosyalar:
        st.warning("Henüz indirilebilir çıktı dosyası yok (hiç satır kaydedilmeden durdurulmuş olabilir).")
        return

    tum_yollar = [(label, path) for label, path, _ in dosyalar]
    analiz_kaynak: pd.DataFrame | None = None
    analiz_adi = ""
    analiz_path: Path | None = None

    if len(dosyalar) == 1:
        label, path, preview = dosyalar[0]
        st.caption(_preview_ozet(preview))
        st.download_button(
            f"İndir: {label}",
            data=path.read_bytes(),
            file_name=path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_{label}_{path.name}_{partial}_{path.stat().st_mtime_ns}",
        )
        st.dataframe(preview.head(30), use_container_width=True)
        if COL_EMAIL in preview.columns:
            analiz_kaynak, analiz_adi, analiz_path = preview, label, path
        else:
            analiz_kaynak, analiz_adi, analiz_path = preview, label, path
    else:
        tab_labels = [label for label, _, _ in dosyalar]
        tabs = st.tabs(tab_labels)
        for tab, (label, path, preview) in zip(tabs, dosyalar):
            with tab:
                st.caption(_preview_ozet(preview))
                st.download_button(
                    f"İndir: {label}",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_{label}_{path.name}_{partial}_{path.stat().st_mtime_ns}",
                )
                st.dataframe(preview.head(30), use_container_width=True)
            if COL_EMAIL in preview.columns:
                analiz_kaynak, analiz_adi, analiz_path = preview, label, path
            elif analiz_kaynak is None:
                analiz_kaynak, analiz_adi, analiz_path = preview, label, path

    # AI şüpheli analizi yalnızca mail sonucu varken
    if (
        analiz_kaynak is not None
        and analiz_path is not None
        and COL_EMAIL in analiz_kaynak.columns
    ):
        render_supheli_analiz(
            analiz_kaynak,
            kaynak_adi=analiz_adi,
            kaynak_path=analiz_path,
            tum_yollar=tum_yollar,
            key_suffix=str(partial),
        )


def _satir_anahtarlari(row: pd.Series) -> set[str]:
    """Eşleştirme için SİCİL / UNVAN anahtarları."""
    keys: set[str] = set()
    if COL_SICIL in row.index and pd.notna(row[COL_SICIL]):
        keys.add(f"sicil:{str(row[COL_SICIL]).strip()}")
    if COL_UNVAN in row.index and pd.notna(row[COL_UNVAN]):
        keys.add(f"unvan:{str(row[COL_UNVAN]).strip()}")
    return keys


def secilen_web_email_temizle(
    kaynak_path: Path,
    secilen: pd.DataFrame,
    ekstra_yollar: list[Path] | None = None,
) -> int:
    """Seçilen şüpheli satırlarda WEB ve EMAIL sütunlarını boşaltır. Dosyaya yazar."""
    hedefler = [kaynak_path]
    for p in ekstra_yollar or []:
        if p.resolve() != kaynak_path.resolve() and p.exists():
            hedefler.append(p)

    secim_keys: set[str] = set()
    for _, row in secilen.iterrows():
        secim_keys |= _satir_anahtarlari(row)

    if not secim_keys:
        return 0

    toplam = 0
    for path in hedefler:
        df = normalize_columns(pd.read_excel(path))
        if df.empty:
            continue
        mask = pd.Series(False, index=df.index)
        for idx, row in df.iterrows():
            if _satir_anahtarlari(row) & secim_keys:
                mask.at[idx] = True
        n = int(mask.sum())
        if n == 0:
            continue
        if COL_WEB in df.columns:
            df.loc[mask, COL_WEB] = ""
        if COL_EMAIL in df.columns:
            df.loc[mask, COL_EMAIL] = ""
        df.to_excel(path, index=False)
        toplam += n
    return toplam


def render_supheli_analiz(
    df: pd.DataFrame,
    *,
    kaynak_adi: str,
    kaynak_path: Path,
    tum_yollar: list[tuple[str, Path]],
    key_suffix: str = "",
) -> None:
    """AI şüpheli satırları; seçilenlerde WEB/EMAIL temizlenip tam sonuç indirilir."""
    st.divider()
    st.subheader("Şüpheli eşleşmeler (AI)")
    st.caption(
        f"Kaynak: {kaynak_adi} — şüpheli bulduğun satırları seç; "
        "WEB ve EMAIL boşaltılır, sonra temiz sonuç Excel’ini indirirsin."
    )

    aday_sayisi = len(web_mail_aday_satirlari(df))
    st.caption(f"LLM’e gidecek satır: **{aday_sayisi}**")

    state_key = f"supheli_llm_{key_suffix}_{kaynak_adi}_{aday_sayisi}_{kaynak_path.name}"

    if aday_sayisi == 0:
        st.info("Analiz edilecek web/mail dolu satır yok.")
        return

    if not os.getenv("GROQ_API_KEY"):
        st.warning("GROQ_API_KEY yok — AI analizi için .env dosyasına key ekleyin.")
        return

    if st.button(
        "AI ile şüpheli satırları bul",
        key=f"btn_ai_supheli_{state_key}",
    ):
        with st.spinner("Groq web/mail satırlarını inceliyor…"):
            # Dosyadan taze oku (önceki temizlik sonrası güncel olsun)
            try:
                df_fresh = normalize_columns(pd.read_excel(kaynak_path))
            except Exception:
                df_fresh = df
            # Görüntü için None yerine boş
            if isinstance(df_fresh, pd.DataFrame):
                df_fresh = df_fresh.fillna("")
            supheli, msg = supheli_eslesmeleri_llm_bul(df_fresh)
        st.session_state[state_key] = {"df": supheli, "msg": msg}
        st.session_state.pop(f"temiz_{state_key}", None)

    sonuc = st.session_state.get(state_key)
    if not sonuc:
        st.caption("Başlamak için butona tıkla (her tıklamada API çağrısı yapılır).")
        return

    st.caption(sonuc.get("msg", ""))
    supheli = sonuc.get("df")
    if supheli is None or getattr(supheli, "empty", True):
        st.success("AI şüpheli eşleşme bulmadı.")
        return

    st.warning(f"{len(supheli)} şüpheli satır (AI). Temizlemek istediklerini işaretle.")

    # Seçim sütunu
    goster = supheli.copy().reset_index(drop=True)
    if "SEÇ" not in goster.columns:
        goster.insert(0, "SEÇ", False)

    edited = st.data_editor(
        goster,
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in goster.columns if c != "SEÇ"],
        column_config={
            "SEÇ": st.column_config.CheckboxColumn("Seç", default=False),
        },
        key=f"editor_supheli_{state_key}_{len(goster)}",
    )

    secilen = edited.loc[edited["SEÇ"] == True] if "SEÇ" in edited.columns else edited.iloc[0:0]

    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            f"Seçilenlerde WEB/EMAIL temizle ({len(secilen)})",
            disabled=secilen.empty,
            key=f"btn_temizle_{state_key}",
            use_container_width=True,
        ):
            ekstra = [p for _, p in tum_yollar]
            n = secilen_web_email_temizle(kaynak_path, secilen, ekstra_yollar=ekstra)
            if n > 0:
                st.session_state[f"temiz_{state_key}"] = {
                    "n": n,
                    "mtime": kaynak_path.stat().st_mtime_ns,
                }
                # Listeden temizlenenleri düş (yeniden seçilmesin)
                kalan = edited[edited["SEÇ"] != True].drop(columns=["SEÇ"], errors="ignore")
                st.session_state[state_key] = {
                    "df": kalan.reset_index(drop=True),
                    "msg": sonuc.get("msg", "") + f" | {n} satırda WEB/EMAIL temizlendi.",
                }
                st.success(f"{n} satırda WEB ve EMAIL boşaltıldı. Aşağıdan güncel Excel’i indir.")
                st.rerun()
            else:
                st.error("Eşleşen satır bulunamadı — SİCİL/UNVAN kontrol edin.")

    with c2:
        st.caption("Temizlik sonrası yukarıdaki / aşağıdaki indirme güncel dosyayı verir.")

    temiz = st.session_state.get(f"temiz_{state_key}")
    if temiz and kaynak_path.exists():
        data = file_download_bytes(kaynak_path)
        if data:
            st.download_button(
                f"Temizlenmiş sonuçları indir ({kaynak_path.name})",
                data=data,
                file_name=kaynak_path.name.replace(".xlsx", "_temiz.xlsx"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_temiz_{state_key}_{temiz.get('mtime')}",
            )
            # Diğer çıktı dosyaları da güncellenmiş olabilir
            for label, path in tum_yollar:
                if path.resolve() == kaynak_path.resolve():
                    continue
                d2 = file_download_bytes(path)
                if d2:
                    st.download_button(
                        f"İndir (güncel): {label}",
                        data=d2,
                        file_name=path.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_temiz_extra_{label}_{temiz.get('mtime')}",
                    )


# ---------------------------------------------------------------------------
# Arka plan iş yönetimi (Durdur için st.rerun döngüsü)
# ---------------------------------------------------------------------------

def _proc_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


def sinyal_ile_durdur(proc: subprocess.Popen) -> None:
    """Ctrl+C benzeri SIGINT — script finally ile kaydeder."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.killpg(proc.pid, signal.SIGINT)
    except Exception:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            proc.terminate()

    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def drain_logs(run: dict) -> None:
    q: queue.Queue = run["log_q"]
    while True:
        try:
            line = q.get_nowait()
        except queue.Empty:
            break
        if line is None:
            run["log_done"] = True
            break
        # tqdm \r ile tek satırı günceller; paneli kirletmesin (ayrı progress bar var)
        clean = line.replace("\r", "\n").rstrip()
        if not clean:
            continue
        if "Firmalar taranıyor:" in clean or "Email'ler taranıyor:" in clean:
            continue
        if re.search(r"\|\s*\d+%\|", clean):
            continue
        if "<<<CAPTCHA_START>>>" in clean:
            run["captcha_seen"] = True
            chrome_one_getir()
            continue
        if "<<<CAPTCHA_END>>>" in clean:
            run["captcha_cleared"] = True
            continue
        run["lines"].append(clean)
        if len(run["lines"]) > 400:
            run["lines"] = run["lines"][-300:]


def start_step(run: dict) -> None:
    step = run["steps"][run["step_idx"]]
    script, inp, out, label = step
    captcha_durum_temizle()
    progress_durum_temizle()
    run["captcha_seen"] = False
    run["captcha_cleared"] = False
    run["log_done"] = False
    run["current_label"] = label
    run["current_output"] = out
    run["message"] = f"Çalışıyor: {label}"

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Panelde zaten progress bar var; tqdm logu çift ilerleme gibi görünmesin
    env["TQDM_DISABLE"] = "1"

    proc = subprocess.Popen(
        [PYTHON, str(ROOT / script), "--input", str(inp), "--output", str(out)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        **_proc_kwargs(),
    )
    run["proc"] = proc

    q: queue.Queue = queue.Queue()
    run["log_q"] = q

    def _reader():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=_reader, daemon=True).start()


def collect_output_if_any(run: dict) -> None:
    out = Path(run["current_output"])
    label = run["current_label"]
    if out.exists() and out.stat().st_size > 0:
        entry = (label, str(out))
        if entry not in run["outputs"]:
            # aynı label varsa güncelle
            run["outputs"] = [(l, p) for l, p in run["outputs"] if l != label]
            run["outputs"].append(entry)


def advance_or_finish(run: dict) -> None:
    """Süreç bittiğinde sonraki adıma geç veya bitir."""
    proc: subprocess.Popen = run["proc"]
    code = proc.returncode if proc.returncode is not None else proc.poll()
    collect_output_if_any(run)
    captcha_durum_temizle()

    if run.get("stop_requested"):
        run["status"] = "stopped"
        run["message"] = "Durduruldu — kısmi sonuçlar kaydedildi."
        run["proc"] = None
        return

    # Adım hata ile bittiyse sonraki adıma geçme (kısmi çıktılar yine indirilebilir)
    if code not in (0, None):
        run["proc"] = None
        run["status"] = "error"
        run["message"] = f"İş hata ile bitti (kod {code})."
        return

    # Sonraki adım
    run["step_idx"] += 1
    if run["step_idx"] < len(run["steps"]):
        start_step(run)
        return

    run["proc"] = None
    run["status"] = "done"
    run["message"] = "Tamamlandı."


def tick_run(run: dict) -> None:
    """Her panel yenilemesinde iş durumunu ilerlet."""
    if run["status"] not in ("running", "stopping"):
        return

    drain_logs(run)

    proc = run.get("proc")
    if proc is None:
        return

    if run.get("stop_requested") and run["status"] == "running":
        run["status"] = "stopping"
        run["message"] = "Durduruluyor — kayıt yazılıyor, bekleyin…"
        sinyal_ile_durdur(proc)
        drain_logs(run)
        collect_output_if_any(run)
        run["status"] = "stopped"
        run["message"] = "Durduruldu — kısmi sonuçlar kaydedildi."
        run["proc"] = None
        captcha_durum_temizle()
        return

    if proc.poll() is not None:
        # log kuyruğunu boşalt
        for _ in range(50):
            drain_logs(run)
            if run.get("log_done"):
                break
            time.sleep(0.05)
        advance_or_finish(run)


def _chrome_profil_yolu() -> str:
    return str(Path.home() / "chrome_selenium")


def _chrome_odak_metni() -> tuple[str, str, str]:
    """CAPTCHA / öne getir uyarıları için OS'a göre kısa yönerge."""
    profil = _chrome_profil_yolu()
    if os.name == "nt":
        nereden = "Görev çubuğu → Google Chrome"
        ekstra = ""
    else:
        nereden = "Dock → Google Chrome"
        ekstra = (
            "\n3. İsteğe bağlı: Sistem Ayarları → Erişilebilirlik → "
            "Terminal/Cursor/Python izni."
        )
    return nereden, profil, ekstra


def render_captcha_from_status() -> None:
    data = captcha_durum_oku()
    if not data.get("active"):
        return
    waited = int(data.get("waited") or 0)
    msg = data.get("message") or "Google CAPTCHA çıktı — Chrome'da çözün."
    nereden, profil, _ = _chrome_odak_metni()
    st.error(
        f"### ⚠ CAPTCHA bekleniyor\n\n"
        f"{msg}\n\n"
        f"**Panel tarayıcısında değil** — ayrı debug Chrome penceresinde "
        f"({nereden}, profil: `{profil}`).\n\n"
        f"Sol menüden **Chrome'u öne getir** deneyin; gelmezse "
        f"{nereden.split('→')[0].strip()} üzerinden tıklayın.\n\n"
        f"Beklenen süre: **{waited} sn**"
    )
    if not st.session_state.get("_captcha_focused"):
        chrome_one_getir()
        st.session_state["_captcha_focused"] = True
    return


def render_progress_bar() -> None:
    data = progress_durum_oku()
    total = int(data.get("total") or 0)
    if total <= 0:
        return
    current = max(0, min(int(data.get("current") or 0), total))
    label = data.get("label") or "İlerleme"
    name = (data.get("current_name") or "").strip()
    pct = current / total
    st.progress(pct, text=f"{label}: {current} / {total} firma")
    if name and name != "Bitti":
        st.caption(f"Şu an: {name}")


def render_scroll_log(lines: list[str], height: int = 340) -> None:
    """Sabit yükseklikli log.

    Alta yakınken yeni satırlarla birlikte kayar; kullanıcı yukarı kaydırdıysa
    konumu korunur (inceleme için). Alta dönünce canlı takip yeniden açılır.
    """
    text = "\n".join(lines[-200:]) if lines else "Log bekleniyor…"
    safe = html.escape(text)
    components.html(
        f"""
        <div id="firma-log" style="
            box-sizing: border-box;
            height: {height}px;
            overflow-y: auto;
            background: #0e1117;
            color: #fafafa;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12.5px;
            line-height: 1.45;
            padding: 12px 14px;
            border-radius: 8px;
            border: 1px solid rgba(250,250,250,0.12);
            white-space: pre-wrap;
            word-break: break-word;
        ">{safe}</div>
        <script>
          (function () {{
            const KEY = "firma-log-scroll-v1";
            const el = document.getElementById("firma-log");
            if (!el) return;

            function nearBottom() {{
              return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
            }}

            function save() {{
              try {{
                sessionStorage.setItem(KEY, JSON.stringify({{
                  stick: nearBottom(),
                  top: el.scrollTop
                }}));
              }} catch (e) {{}}
            }}

            let stick = true;
            let top = 0;
            try {{
              const saved = JSON.parse(sessionStorage.getItem(KEY) || "{{}}");
              if (typeof saved.stick === "boolean") stick = saved.stick;
              if (typeof saved.top === "number") top = saved.top;
            }} catch (e) {{}}

            if (stick) {{
              el.scrollTop = el.scrollHeight;
            }} else {{
              el.scrollTop = Math.min(top, Math.max(0, el.scrollHeight - el.clientHeight));
            }}

            el.addEventListener("scroll", save, {{ passive: true }});
            // İçerik boyutu değişince de kaydet (stick güncel kalsın)
            save();
          }})();
        </script>
        """,
        height=height + 8,
        scrolling=False,
    )


@st.fragment(run_every=timedelta(seconds=1))
def live_job_panel() -> None:
    """Tek log kutusu; sleep+rerun yerine fragment (çift terminal hayaletini önler)."""
    r = st.session_state.get("run")
    if not r or r.get("status") not in ("running", "stopping"):
        return

    tick_run(r)

    if r["status"] not in ("running", "stopping"):
        st.rerun()
        return

    st.caption(r.get("message", ""))
    c1, c2 = st.columns([1, 3])
    with c1:
        if r["status"] == "running":
            if st.button("Durdur", type="primary", use_container_width=True, key="btn_durdur"):
                r["stop_requested"] = True
                st.rerun()
        else:
            st.button("Kaydediliyor…", disabled=True, use_container_width=True, key="btn_kaydediliyor")
    with c2:
        st.caption(
            "Durdur = güvenli iptal. Bitmiş satırlar kaydedilir; "
            "sonra kısmi sonucu indirebilirsiniz."
        )

    render_progress_bar()
    render_captcha_from_status()
    if r.get("captcha_cleared"):
        st.success("CAPTCHA çözüldü — devam ediliyor.")
        r["captcha_cleared"] = False
        st.session_state.pop("_captcha_focused", None)

    lines = r.get("lines") or []
    render_scroll_log(lines)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

cfg = load_cfg()
chrome_port = int(cfg.get("chrome", {}).get("debug_port", 9222))

with st.sidebar:
    st.title("Firma Bulucu")
    st.caption("Yerel ekip paneli")

    chrome_ok = chrome_acik_mi(chrome_port)
    groq_ok = bool(os.getenv("GROQ_API_KEY"))

    st.subheader("Durum")
    st.write(f"{'🟢' if chrome_ok else '🔴'} Chrome debug (`:{chrome_port}`)")
    st.write(f"{'🟢' if groq_ok else '🟡'} Groq API key (`.env`)")

    nereden, chrome_profil, ekstra = _chrome_odak_metni()
    if st.button("Chrome'u öne getir", use_container_width=True):
        if chrome_one_getir(chrome_port):
            st.toast(f"Tarama sekmesi seçildi — {nereden}")
        else:
            st.warning(
                "Pencere öne getirilemedi (yeni pencere açılmaz).\n\n"
                "CAPTCHA **panel Chrome’unda değil**; debug profilde "
                f"(`{chrome_profil}`).\n\n"
                f"1. {nereden} (tarama yapılan pencere).\n"
                "2. Google / CAPTCHA sekmesine geçin."
                f"{ekstra}"
            )
            st.caption(f"Debug profil: `{chrome_profil}` · port `{chrome_port}`")

    if not chrome_ok:
        baslat = "baslat.bat" if os.name == "nt" else "./baslat.sh"
        st.warning(
            "Site bul için Chrome debug modda açık olmalı.\n\n"
            f"`{baslat}` çalıştırın veya README'deki komutu kullanın."
        )

    st.divider()
    with st.expander("Ayarlar", expanded=False):
        skor = cfg.setdefault("skor", {})
        bekleme = cfg.setdefault("bekleme", {})
        mail = cfg.setdefault("mail", {})
        llm = cfg.setdefault("llm", {})

        yuksek = st.number_input("Yüksek skor eşiği", 0, 100, int(skor.get("yuksek_esik", 65)))
        dusuk = st.number_input("Düşük skor eşiği", 0, 100, int(skor.get("dusuk_esik", 40)))
        min_b = st.number_input("Min bekleme (sn)", 0.0, 60.0, float(bekleme.get("min_arasi", 6)))
        max_b = st.number_input("Max bekleme (sn)", 0.0, 120.0, float(bekleme.get("max_arasi", 14)))
        workers = st.number_input("Mail işçileri", 1, 32, int(mail.get("workers", 8)))
        llm_on = st.toggle("LLM (Groq) aktif", bool(llm.get("enabled", True)))

        if st.button("Ayarları kaydet", use_container_width=True):
            try:
                patch_config_scalars({
                    "yuksek_esik": int(yuksek),
                    "dusuk_esik": int(dusuk),
                    "min_arasi": float(min_b),
                    "max_arasi": float(max_b),
                    "workers": int(workers),
                    "enabled": bool(llm_on),
                })
                st.success("config.yaml güncellendi.")
            except Exception as e:
                st.error(f"Ayar kaydı başarısız: {e}")


# ---------------------------------------------------------------------------
# Aktif / bitmiş iş paneli
# ---------------------------------------------------------------------------

run = st.session_state.get("run")

if run is not None:
    st.header("İş durumu")

    if run["status"] in ("running", "stopping"):
        live_job_panel()
        st.stop()  # altındaki yükleme alanını bu turda çizme

    tick_run(run)

    if run["status"] in ("stopped", "done", "error"):
        if run["status"] == "stopped":
            st.warning(run.get("message", "Durduruldu."))
            show_result_downloads(run.get("outputs") or [], partial=True)
        elif run["status"] == "done":
            st.success(run.get("message", "Tamamlandı."))
            show_result_downloads(run.get("outputs") or [], partial=False)
        else:
            st.error(run.get("message", "Hata."))
            if run.get("outputs"):
                show_result_downloads(run["outputs"], partial=True)

        st.session_state["last_outputs"] = list(run.get("outputs") or [])
        st.caption(f"İş klasörü: `{run.get('job_dir', '')}`")

        job_dir = Path(run.get("job_dir") or "")
        web_path = job_dir / "sonuc_web.xlsx"
        mail_path = job_dir / "sonuc_mail.xlsx"
        mail_zaten_var = any(
            label == "Mail sonuçları" for label, _ in (run.get("outputs") or [])
        )
        steps_now = run.get("steps") or []
        idx = int(run.get("step_idx") or 0)
        mail_adiminda_durdu = (
            run["status"] == "stopped"
            and idx < len(steps_now)
            and steps_now[idx][0] == "mailbul.py"
        )
        mail_e_gec = (
            web_path.exists()
            and web_path.stat().st_size > 0
            and not mail_zaten_var
            and not mail_adiminda_durdu
            and run["status"] in ("done", "stopped", "error")
        )

        aksiyonlar: list[str] = []
        if run["status"] == "stopped":
            aksiyonlar.append("devam")
        if mail_e_gec:
            aksiyonlar.append("mail")
        aksiyonlar.append("yeni")

        cols = st.columns(len(aksiyonlar))
        for i, aksiyon in enumerate(aksiyonlar):
            with cols[i]:
                if aksiyon == "devam":
                    if st.button(
                        "Kaldığı yerden devam",
                        type="primary",
                        use_container_width=True,
                        key="btn_devam",
                    ):
                        run["stop_requested"] = False
                        run["status"] = "running"
                        run["message"] = "Devam ediliyor…"
                        run["lines"] = []
                        start_step(run)
                        st.rerun()
                elif aksiyon == "mail":
                    if st.button(
                        "Bu sonuçlarla mail bul",
                        type="primary",
                        use_container_width=True,
                        key="btn_mail_gec",
                    ):
                        run["steps"] = [
                            ("mailbul.py", web_path, mail_path, "Mail sonuçları")
                        ]
                        run["step_idx"] = 0
                        run["stop_requested"] = False
                        run["status"] = "running"
                        run["message"] = "Mail bul başlatılıyor…"
                        run["lines"] = []
                        run["outputs"] = [
                            (l, p)
                            for l, p in (run.get("outputs") or [])
                            if l != "Mail sonuçları"
                        ]
                        if not any(l == "Web sonuçları" for l, _ in run["outputs"]):
                            run["outputs"].insert(
                                0, ("Web sonuçları", str(web_path))
                            )
                        start_step(run)
                        st.rerun()
                else:
                    if st.button(
                        "Yeni işe geç",
                        use_container_width=True,
                        key="btn_yeni",
                    ):
                        st.session_state.pop("run", None)
                        st.session_state.pop("last_outputs", None)
                        st.session_state.pop("_captcha_focused", None)
                        # AI analiz / temizleme oturum state'lerini de sil
                        for k in list(st.session_state.keys()):
                            if (
                                k.startswith("supheli_llm_")
                                or k.startswith("temiz_")
                            ):
                                st.session_state.pop(k, None)
                        captcha_durum_temizle()
                        progress_durum_temizle()
                        st.rerun()

        if mail_e_gec:
            st.caption(
                "Excel indirip tekrar yüklemene gerek yok — "
                "bulunan sitelerle doğrudan mail taramasına geçebilirsin."
            )

        # Bitiş ekranında yükleme alanını gizle — "Yeni işe geç" ile açılır
        st.stop()


# ---------------------------------------------------------------------------
# Ana alan — yükleme / başlat
# ---------------------------------------------------------------------------

st.header("Excel yükle ve çalıştır")

busy = bool(run and run["status"] in ("running", "stopping"))

uploaded = st.file_uploader(
    "Firma listesi (.xlsx)",
    type=["xlsx"],
    help="Site bul: UNVAN (veya Firma) + isteğe bağlı SİCİL / İLÇE. Mail bul: UNVAN + WEB.",
    disabled=busy,
)

islem = st.radio(
    "İşlem",
    [
        "Site bul (Google → web sitesi)",
        "Mail bul (web → e-posta)",
        "İkisi birden (önce site, sonra mail)",
    ],
    horizontal=False,
    disabled=busy,
)

if uploaded is None:
    if not run and st.session_state.get("last_outputs"):
        st.divider()
        show_result_downloads(st.session_state["last_outputs"], partial=False)
    elif not run:
        st.info("Başlamak için bir Excel dosyası yükleyin.")
    st.stop()

try:
    raw_df = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Excel okunamadı: {e}")
    st.stop()

df = normalize_columns(raw_df)

# Dosya boyutu kontrolü (50MB)
MAX_DOSYA_MB = 50
if uploaded.size > MAX_DOSYA_MB * 1024 * 1024:
    st.error(f"Dosya çok büyük ({uploaded.size / 1024 / 1024:.1f} MB > {MAX_DOSYA_MB} MB).")
    st.stop()

# Boş dosya kontrolü
if df.empty:
    st.error("Excel dosyası boş — en az bir satır gerekli.")
    st.stop()

# Satır limiti uyarısı
if len(df) > 10000:
    st.warning(f"⚠ {len(df)} satır — çok büyük dosyalar uzun sürebilir.")

st.subheader("Önizleme")
st.caption(f"{len(df)} satır · Sütunlar: {', '.join(map(str, df.columns))}")
st.dataframe(df.head(20), use_container_width=True)

has_unvan = COL_UNVAN in df.columns
has_web = COL_WEB in df.columns
needs_site = islem.startswith("Site") or islem.startswith("İkisi")
needs_mail = islem.startswith("Mail") or islem.startswith("İkisi")

errors = []
if needs_site and not has_unvan:
    errors.append(f"Site bul için **{COL_UNVAN}** (veya Firma) sütunu gerekli.")
if needs_mail and not has_unvan:
    errors.append(f"Mail bul için **{COL_UNVAN}** sütunu gerekli.")
if needs_mail and not needs_site and not has_web:
    errors.append(f"Mail bul için **{COL_WEB}** sütunu gerekli (veya önce Site bul çalıştırın).")
if needs_site and not chrome_ok:
    errors.append("Chrome debug kapalı — Site bul çalışmaz.")

for err in errors:
    st.error(err)

if has_web and needs_mail and not needs_site:
    df = df.copy()
    df[COL_WEB] = df[COL_WEB].apply(url_temizle)

col_run, col_info = st.columns([1, 2])
with col_run:
    baslat = st.button(
        "Başlat",
        type="primary",
        use_container_width=True,
        disabled=bool(errors) or busy,
    )
with col_info:
    if needs_site:
        st.caption(
            "Site bul uzun sürebilir. CAPTCHA → panel uyarır. "
            "Durdur → kayıt + kısmi indirme."
        )

if not baslat:
    st.stop()

# ---------------------------------------------------------------------------
# Yeni iş başlat
# ---------------------------------------------------------------------------

if busy:
    st.warning("Zaten bir iş çalışıyor.")
    st.stop()

slug = job_slug(uploaded.name)
job_dir = JOBS_DIR / slug
job_dir.mkdir(parents=True, exist_ok=True)

input_site = job_dir / "girdi.xlsx"
output_web = job_dir / "sonuc_web.xlsx"
output_mail = job_dir / "sonuc_mail.xlsx"

site_df = df.copy()
site_cols = [c for c in [COL_SICIL, COL_UNVAN, COL_ILCE] if c in site_df.columns]
if COL_UNVAN not in site_cols:
    st.error(f"{COL_UNVAN} sütunu yok.")
    st.stop()

if needs_site:
    site_df[site_cols].to_excel(input_site, index=False)
else:
    mail_cols = [
        c
        for c in [
            COL_SICIL,
            COL_UNVAN,
            COL_WEB,
            COL_SKOR,
            COL_DURUM,
            COL_ADAY_WEB,
            COL_RED_NEDEN,
        ]
        if c in df.columns
    ]
    prep = df[mail_cols].copy()
    if COL_SICIL not in prep.columns:
        prep.insert(0, COL_SICIL, [str(i) for i in range(len(prep))])
    if COL_WEB in prep.columns:
        prep[COL_WEB] = prep[COL_WEB].apply(url_temizle)
    prep.to_excel(input_site, index=False)

steps: list[tuple[str, Path, Path, str]] = []
if needs_site:
    steps.append(("sitebul.py", input_site, output_web, "Web sonuçları"))
if needs_mail:
    mail_in = output_web if needs_site else input_site
    steps.append(("mailbul.py", mail_in, output_mail, "Mail sonuçları"))

new_run = {
    "job_dir": str(job_dir),
    "steps": steps,
    "step_idx": 0,
    "proc": None,
    "log_q": queue.Queue(),
    "lines": [],
    "outputs": [],
    "status": "running",
    "message": "Başlatılıyor…",
    "stop_requested": False,
    "current_label": "",
    "current_output": "",
    "log_done": False,
    "captcha_seen": False,
    "captcha_cleared": False,
}
st.session_state.pop("_captcha_focused", None)
st.session_state["run"] = new_run
start_step(new_run)
st.rerun()
