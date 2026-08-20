#!/usr/bin/env bash
# Firma Bulucu — Chrome (gerekirse) + Streamlit paneli
set -e
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
  echo "venv yok veya Windows ortamı. Önce ./kurulum.sh çalıştırın."
  exit 1
fi
# shellcheck disable=SC1091
source venv/bin/activate

PORT=9222
if [ -f config.yaml ]; then
  P=$(grep -E '^\s*debug_port:' config.yaml | head -1 | awk '{print $2}' || true)
  if [ -n "$P" ]; then PORT="$P"; fi
fi

chrome_up() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
  else
    nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1
  fi
}

if ! chrome_up; then
  echo "Chrome debug başlatılıyor (port $PORT)..."
  if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
      --remote-debugging-port="$PORT" \
      --user-data-dir="$HOME/chrome_selenium" \
      >/dev/null 2>&1 &
  elif command -v google-chrome >/dev/null 2>&1; then
    google-chrome --remote-debugging-port="$PORT" \
      --user-data-dir="$HOME/chrome_selenium" \
      >/dev/null 2>&1 &
  elif command -v chromium-browser >/dev/null 2>&1; then
    chromium-browser --remote-debugging-port="$PORT" \
      --user-data-dir="$HOME/chrome_selenium" \
      >/dev/null 2>&1 &
  else
    echo "UYARI: Chrome bulunamadı. Site bul için manuel debug Chrome açın."
  fi
  sleep 2
else
  echo "Chrome debug zaten açık (port $PORT)."
fi

# macOS pencere yerleşimi — arka planda (izin diyaloğunda paneli bloklamasın)
if [ "$(uname)" = "Darwin" ]; then
  (
    osascript >/dev/null 2>&1 <<'EOF' || true
tell application "Google Chrome"
  activate
end tell
EOF
    sleep 3
    open "http://localhost:8501" >/dev/null 2>&1 || true
  ) &
fi

echo "Panel açılıyor → http://localhost:8501"
echo "Tarayıcıda açılmazsa elle girin: http://localhost:8501"
exec "$(pwd)/venv/bin/python" -m streamlit run panel.py --server.headless true
