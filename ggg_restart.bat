@echo off
setlocal
cd /d "%~dp0"

set "SERVER_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8000 .*LISTENING"') do set "SERVER_PID=%%P"

if defined SERVER_PID (
    echo Stopping GongGongGo server PID %SERVER_PID%...
    taskkill /PID %SERVER_PID% /F >nul 2>&1
    if errorlevel 1 (
        echo Failed to stop PID %SERVER_PID%.
        exit /b 1
    )
)

for /L %%I in (1,1,10) do (
    netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" >nul
    if errorlevel 1 goto start_server
    timeout /t 1 /nobreak >nul
)

echo Port 127.0.0.1:8000 is still in use.
exit /b 1

:start_server
echo Starting GongGongGo server...
wscript.exe "%~dp0ggg_startup.vbs"

for /L %%I in (1,1,15) do (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 1; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
    if not errorlevel 1 (
        echo GongGongGo restarted successfully.
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)

echo Server did not become healthy. Check the latest file in logs.
exit /b 1
