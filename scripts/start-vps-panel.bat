@echo off
rem ── Murmur VPS 面板：双击打开（状态 + 创建邀请码）──────────────
rem 详细说明见 docs\win-vps-panel.md
cd /d "%~dp0\.."

if not exist .venv\Scripts\murmur.exe (
  echo [X] 还没装依赖。先按 docs\win-vps-panel.md 做一次初始化。
  pause
  exit /b 1
)

rem 两秒后自动打开浏览器；murmur web 自己启动要一点时间
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765/vps"

.venv\Scripts\murmur.exe web --no-open
pause
