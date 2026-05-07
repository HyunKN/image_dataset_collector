# Korean Landmark Image Dataset Collector

한국 랜드마크 이미지를 자동으로 저장하는 간단한 수집 도구입니다.

Google Maps 리뷰, Naver Map 리뷰, Google/Naver 이미지 검색 화면을 직접 열어두면, 프로그램이 열린 Chrome에서 이미지 URL을 찾아 다운로드합니다.

## 가장 쉬운 사용법

### 1. Chrome 수집 창 열기

`launch_chrome_capture.bat`을 더블클릭합니다.

새 Chrome 창이 열리면 그 창에서 원하는 페이지를 직접 열면 됩니다.

예:

- Google Maps에서 장소 검색 후 리뷰 탭 열기
- Naver Map에서 장소 검색 후 방문자 리뷰 열기
- Google 이미지 검색에서 `청와대 고화질` 검색
- Naver 이미지 검색에서 `청와대` 검색

### 2. 사진이 보이게 스크롤하기

리뷰나 이미지 검색 결과를 아래로 계속 스크롤합니다.

화면에 사진이 많이 로드될수록 프로그램이 가져올 후보도 많아집니다.

### 3. 수집기 실행하기

Chrome 창은 닫지 말고 그대로 둔 상태에서 `run_collector.bat`을 더블클릭합니다.

그러면 아래처럼 물어봅니다.

```text
이름: 청와대
추가 검색어 또는 영어 이름(선택, 쉼표로 구분):
Google Maps 리뷰 URL(선택, 비우면 이름으로 자동 검색):
Tripadvisor URL(선택, 비우면 건너뜀):
몇 장까지 가져올까요? 기본 1000: 1000
열린 Chrome에서 이미지 캡처할 시간(초), 기본 180: 180
```

보통은 이렇게 입력하면 됩니다.

- `이름`: 수집할 장소 이름
- `추가 검색어`: 몰라도 비워도 됨
- `Google Maps 리뷰 URL`: 비워도 됨
- `Tripadvisor URL`: 비워도 됨
- `몇 장`: 원하는 이미지 수
- `캡처 시간`: 기본값 그대로 두거나, 더 오래 스크롤할 거면 300 정도 입력

## 저장 위치

이미지는 아래 폴더에 저장됩니다.

```text
dataset/<장소명>/
```

예를 들어 `청와대`를 입력하면 대략 이런 폴더가 생깁니다.

```text
dataset/05_청와대/
```

각 폴더 안에는 이미지와 함께 아래 파일이 생깁니다.

- `manifest.csv`: 저장된 이미지 기록
- `rejected.csv`: 중복, 너무 작은 이미지, 오류 이미지 등으로 제외된 기록

## 중복 방지

이미 받은 이미지는 다시 저장하지 않습니다.

프로그램은 이미지 파일 내용을 `sha256`으로 기록합니다. 그래서 같은 이미지가 다시 나오면 `duplicate_sha256`으로 판단하고 `rejected.csv`에만 기록합니다.

주의할 점:

- 이미지를 직접 삭제해도 `manifest.csv`를 그대로 두면, 그 이미지는 “이미 받은 것”으로 취급됩니다.
- 같은 사진이라도 크롭, 리사이즈, 압축이 다르면 다른 이미지로 판단될 수 있습니다.

## 데이터 검증

중복이나 누락 상태를 확인하려면 PowerShell에서 실행합니다.

```powershell
python verify_dataset.py --dataset-dir dataset
```

가상환경을 쓰는 경우:

```powershell
.\.venv\Scripts\python.exe verify_dataset.py --dataset-dir dataset
```

## 설치가 필요한 경우

처음 실행할 때 `run_collector.bat`이 필요한 패키지를 설치합니다.

직접 설치하고 싶으면 아래 명령을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## 고급 실행

명령어로 직접 실행할 수도 있습니다.

```powershell
python collect_landmark_images.py --interactive --target 1000 --max-search-results 1800 --capture-seconds 180 --sources chrome_debug_capture,commons,duckduckgo
```

Chrome 캡처 시간을 늘리려면:

```powershell
python collect_landmark_images.py --interactive --target 1000 --capture-seconds 300 --sources chrome_debug_capture,commons,duckduckgo
```

## GitHub에 올릴 때 주의

아래 폴더와 파일은 `.gitignore`에 들어가 있으므로 기본적으로 GitHub에 올라가지 않습니다.

```text
.venv/
dataset/
.env
__pycache__/
chrome_capture_launch.log
```

## 주의사항

- 검색 결과 기반 수집이라 잘못된 이미지가 섞일 수 있습니다.
- 학습 데이터로 쓰기 전에는 샘플을 직접 확인하는 것이 좋습니다.
- `manifest.csv`에는 이미지 URL과 출처 URL이 기록됩니다.
- 공개 웹 이미지라도 라이선스와 사용 조건은 별도로 확인해야 합니다.
- Google/Naver/Tripadvisor 페이지 구조나 차단 정책에 따라 수집량이 달라질 수 있습니다.
