@echo off
REM Firma Bulucu — Chrome (gerekirse) + Streamlit paneli
cd /d "%~dp0"

if not exist venv (
  echo venv yok. Once kurulum.bat calistirin.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat

set PORT=9222
if exist config.yaml (
  for /f "tokens=2 delims=: " %%A in ('findstr /R /C:"debug_port:" config.yaml') do set PORT=%%A
)

REM Port dinleniyor mu?
netstat -an | findstr ":%PORT% " | findstr "LISTENING" >nul
if errorlevel 1 (
  echo Chrome debug baslatiliyor (port %PORT%^)...
  if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=%PORT% --user-data-dir="%USERPROFILE%\chrome_selenium"
  ) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=%PORT% --user-data-dir="%USERPROFILE%\chrome_selenium"
  ) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    start "" "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=%PORT% --user-data-dir="%USERPROFILE%\chrome_selenium"
  ) else (
    echo UYARI: Chrome bulunamadi. Site bul icin manuel debug Chrome acin.
  )
  rem timeout stdin ile kirilir; ping daha guvenli
  ping -n 3 127.0.0.1 >nul
) else (
  echo Chrome debug zaten acik (port %PORT%^).
)

echo Panel aciliyor - http://localhost:8501
echo Tarayicida acilmazsa elle girin: http://localhost:8501
start "" http://localhost:8501
venv\Scripts\python.exe -m streamlit run panel.py --server.headless true
if errorlevel 1 (
  echo.
  echo Panel baslatilamadi. Hata yukarida.
)
pause
