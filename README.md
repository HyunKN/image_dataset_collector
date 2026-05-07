# Korean Landmark Image Dataset Collector

청와대, 낙산공원, 국립현대미술관, 명동대성당 이미지를 클래스별 약 1000장까지 모으는 수집 도구입니다.

## 설치

```powershell
cd image_dataset_collector
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 가장 쉬운 실행

1. `launch_chrome_capture.bat`을 더블클릭합니다.
2. 열린 Chrome 안에서 Google Maps 리뷰 탭, Naver Map 방문자 리뷰, Google/Naver 이미지 검색 탭을 엽니다.
3. 사진이 계속 보이도록 아래로 스크롤합니다.
4. `run_collector.bat`을 더블클릭하면 수집할 장소 이름을 물어봅니다.

예:

```text
이름: 경복궁
추가 검색어 또는 영어 이름(선택, 쉼표로 구분): Gyeongbokgung Palace Seoul
Google Maps 리뷰 URL(선택, 비우면 이름으로 자동 검색): 
Tripadvisor URL(선택, 비우면 건너뜀): 
몇 장까지 가져올까요? 기본 1000: 1000
열린 Chrome에서 이미지 캡처할 시간(초), 기본 180: 180
```

입력한 장소는 `dataset/<번호_장소명>/` 아래에 저장됩니다.
수집 중에는 현재 장수, 목표 장수, 남은 장수가 함께 표시됩니다.
Tripadvisor URL은 직접 넣은 경우에만 사용합니다. 기본 실행에서는 Chrome 캡처가 Google/Naver 리뷰 사진을 담당합니다.

기본 리뷰 source는 API가 아니라, 사용자가 직접 열어둔 Chrome에 연결해서 현재 로드되는 Google/Naver 리뷰 사진 URL을 수집합니다. 프로그램이 Google/Naver를 자동으로 클릭하거나 검색하지 않습니다.

## 소량 테스트

```powershell
python collect_landmark_images.py --config landmarks.yaml --target 5 --max-search-results 30
```

## 1000장 수집

```powershell
python collect_landmark_images.py --config landmarks.yaml --target 1000 --max-search-results 1800
```

열린 Chrome 캡처, Wikimedia, 일반 이미지 검색을 모두 쓰려면:

```powershell
python collect_landmark_images.py --config landmarks.yaml --target 1000 --max-search-results 1800 --sources chrome_debug_capture,commons,duckduckgo
```

열린 Chrome 캡처 시간을 늘리려면:

```powershell
python collect_landmark_images.py --config landmarks.yaml --target 1000 --max-search-results 1800 --capture-seconds 300 --sources chrome_debug_capture,commons,duckduckgo
```

수집 결과는 기본적으로 `dataset/<class_id>/` 아래에 저장되고, 각 클래스 폴더에는 `manifest.csv`와 `rejected.csv`가 생성됩니다.

## 검증

```powershell
python collect_landmark_images.py --config landmarks.yaml --report-only
```

## 리뷰 사진을 추가로 넣기

`--sources chrome_debug_capture`를 켜면 `launch_chrome_capture.bat`으로 열어둔 Chrome에 연결해서, 현재 탭들에 로드되는 사용자 리뷰 사진 후보를 저장합니다.

수집 기준:

- Google 저장: `lh3.googleusercontent.com/grass-cs/...` 계열 또는 `리뷰에 포함된` label이 붙은 이미지, Google 이미지 탭의 `encrypted-tbn*.gstatic.com/images` 후보
- Naver 저장: `pup-review-phinf.pstatic.net` 방문자 리뷰 이미지
- Naver 이미지 검색 저장: `search.pstatic.net` proxy에서 원본 `src`를 추출한 후보
- 제외: 프로필 사진, 지도 타일, UI 아이콘, 너무 작은 이미지

`--sources google_image_search`를 켜면 Google 이미지 탭에서 이미지 후보를 추가로 가져옵니다. 이것도 브라우저가 보이는 상태로 열립니다.

`--sources tripadvisor`를 켜고 Tripadvisor URL을 직접 입력하면 해당 페이지를 headless browser로 열고 스크롤하면서 페이지 안에 로드된 이미지 후보를 같은 다운로드/중복 제거/해상도 검증 파이프라인으로 저장합니다. URL이 없으면 건너뜁니다.

직접 확보한 이미지 URL도 `review_sources/<class_id>.csv`에 넣어 추가할 수 있습니다.

예:

```csv
image_url,page_url,title
https://example.com/photo1.jpg,https://example.com/review-page,front view
https://example.com/photo2.jpg,https://example.com/review-page,night view
```

실행:

```powershell
python collect_landmark_images.py --config landmarks.yaml --target 1000 --sources chrome_debug_capture,commons,duckduckgo --import-url-csv-dir review_sources
```

## 이미 받은 로컬 이미지를 합치기

클래스별 폴더를 아래처럼 만든 뒤 import할 수 있습니다.

```text
local_sources/
  13_cheongwadae/
  14_naksan_park/
  15_mmca/
  16_myeongdong_cathedral/
```

실행:

```powershell
python collect_landmark_images.py --config landmarks.yaml --target 1000 --import-local-dir local_sources
```

## 주의

- Google 리뷰 수집은 `lh3.googleusercontent.com/grass-cs/...` 계열과 `aria-label`의 `리뷰에 포함된` 정보를 우선 사용하고, Google 프로필 사진과 지도/UI 이미지는 제외합니다.
- Google 이미지 탭 수집은 검색 결과 기반이라 오염 이미지가 섞일 수 있습니다.
- 웹 검색 결과는 정확도가 완벽하지 않으므로, 학습 전에 샘플을 눈으로 검수하거나 CLIP/분류기 기반 필터링을 추가하는 것이 좋습니다.
- 이 도구는 이미지 URL과 출처를 `manifest.csv`에 남깁니다. 공개 웹 이미지라도 라이선스와 사용 조건은 별도로 확인해야 합니다.
- Google 리뷰/Maps, Tripadvisor 후보는 페이지 구조와 사이트 차단 정책에 따라 수집량이 크게 달라질 수 있습니다.
- Tripadvisor는 자동 접근 시 captcha/anti-bot 페이지를 반환할 수 있으며, 이 경우 해당 source에서는 0장이 저장될 수 있습니다.
- `target=1000`은 “최대 1000장까지 시도”입니다. 검색 결과 품질, 중복, 서버 차단, 라이선스 문제 때문에 실제 저장 수가 더 적을 수 있습니다.
