# Korean Landmark Image Dataset Collector

## 사용법

0. 처음 실행이라면 `run_collector.bat`을 한 번 실행해서 필요한 환경을 설치합니다.
1. `launch_chrome_capture.bat`을 실행한 뒤, 열린 Chrome에서 원하는 페이지를 미리 띄워둡니다.
   예: 네이버에 `경복궁` 검색 후 이미지 탭 열기, Google Maps에서 경복궁 리뷰 탭 열기
2. 사진이 보이도록 페이지를 아래로 스크롤합니다.
3. Chrome 창을 닫지 말고 `run_collector.bat`을 실행합니다.
4. 수집할 이름과 장수를 입력하면 `dataset/` 폴더에 이미지가 저장됩니다.

## 입력 예시

```text
이름: 청와대
추가 검색어 또는 영어 이름(선택, 쉼표로 구분):
Google Maps 리뷰 URL(선택, 비우면 이름으로 자동 검색):
Tripadvisor URL(선택, 비우면 건너뜀):
몇 장까지 가져올까요? 기본 1000: 1000
열린 Chrome에서 이미지 캡처할 시간(초), 기본 180: 180
```

대부분은 `이름`, `몇 장`, `캡처 시간`만 입력하면 됩니다.
나머지는 비워도 실행됩니다.
