@echo off
setlocal
chcp 65001 > nul
set PYTHONUTF8=1

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
)

echo [setup] Installing/updating required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m playwright install chromium

echo.
echo 수집할 장소 이름을 입력하면 자동으로 검색해서 이미지를 저장합니다.
echo 예: 경복궁
echo Google Maps URL은 현재 기본 수집에 사용하지 않습니다.
echo Tripadvisor URL은 필요할 때만 직접 넣으세요. 비우면 건너뜁니다.
echo 목표 장수도 직접 입력할 수 있습니다. 비우면 1000장입니다.
echo.

".venv\Scripts\python.exe" collect_landmark_images.py --config landmarks.yaml --interactive --target 1000 --max-search-results 1800 --capture-seconds 180 --sources chrome_debug_capture,commons,duckduckgo

echo.
pause
