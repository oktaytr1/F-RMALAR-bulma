@echo off
REM Firma Bulucu — ilk kurulum (Windows)
cd /d "%~dp0"

echo ==^> Python sanal ortam
if not exist venv (
  py -3 -m venv venv
  if errorlevel 1 python -m venv venv
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist .env (
  echo.
  echo ==^> .env yok. Ornek olusturuluyor.
  echo GROQ_API_KEY=> .env
  echo     .env dosyasina Groq API key yazin: https://console.groq.com/
)

echo.
echo Kurulum tamam.
echo   1^) .env icine GROQ_API_KEY yazin
echo   2^) baslat.bat ile paneli acin
pause
