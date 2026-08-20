#!/usr/bin/env bash
# Firma Bulucu — ilk kurulum (macOS / Linux)
set -e
cd "$(dirname "$0")"

# Windows'tan kopyalanan venv (Scripts/python.exe) Mac/Linux'ta çalışmaz
if [ -d venv ] && [ ! -x venv/bin/python ]; then
  echo "==> Uyumsuz (Windows) sanal ortam siliniyor..."
  rm -rf venv
fi

echo "==> Python sanal ortam"
if [ ! -d venv ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
  echo
  echo "==> .env yok. Örnek oluşturuluyor."
  echo "GROQ_API_KEY=" > .env
  echo "    .env dosyasına Groq API key yazın: https://console.groq.com/"
fi

chmod +x baslat.sh kurulum.sh 2>/dev/null || true

echo
echo "Kurulum tamam."
echo "  1) .env içine GROQ_API_KEY yazın (LLM için)"
echo "  2) ./baslat.sh  ile paneli açın"
