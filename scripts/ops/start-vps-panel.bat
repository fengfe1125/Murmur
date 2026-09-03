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

rem 面板通过当前生产别名连接（见 %USERPROFILE%\.ssh\config）。
set MURMUR_VPS_SSH=murmur-new-vps

rem 两秒后自动打开浏览器；murmur web 自己启动要一点时间
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765/vps"

.venv\Scripts\murmur.exe web --no-open
pause
