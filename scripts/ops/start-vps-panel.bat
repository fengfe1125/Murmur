@echo off
chcp 65001 >nul
rem ── Murmur VPS 面板：双击打开（状态 + 创建邀请码）──────────────
rem 详细说明见 docs\operations\windows-panel.md
cd /d "%~dp0\..\.."

if not exist .venv\Scripts\murmur.exe (
  echo [X] 还没装依赖。先按 docs\operations\windows-panel.md 做一次初始化。
  pause
  exit /b 1
)

rem 面板通过别名 murmur-vps 连 VPS（见 %USERPROFILE%\.ssh\config）。
set MURMUR_VPS_SSH=fye816368@murmur-vps

rem 本机直连 VPS 的 22 会被掐握手，走 IAP 隧道：127.0.0.1:2222 → VPS:22。
rem 已经在跑就不重复起（上次的窗口还开着）。
netstat -an | findstr /C:"127.0.0.1:2222" | findstr LISTENING >nul
if errorlevel 1 (
  echo 启动 IAP 隧道（127.0.0.1:2222 -^> VPS:22），那个最小化窗口别关...
  start "murmur-iap-tunnel" /min gcloud compute start-iap-tunnel instance-20260516-162140 22 --local-host-port=127.0.0.1:2222 --zone=us-west1-b
  for /l %%i in (1,1,20) do (
    netstat -an | findstr /C:"127.0.0.1:2222" | findstr LISTENING >nul && goto tunnel_ok
    timeout /t 1 /nobreak >nul
  )
  echo [X] 隧道 20 秒没就绪。先手动跑一遍：
  echo     gcloud compute start-iap-tunnel instance-20260516-162140 22 --local-host-port=127.0.0.1:2222 --zone=us-west1-b
  pause
  exit /b 1
)
:tunnel_ok

rem 两秒后自动打开浏览器；murmur web 自己启动要一点时间
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765/vps"

.venv\Scripts\murmur.exe web --no-open
pause
