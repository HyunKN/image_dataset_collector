from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
import yaml
from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

try:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException
except ImportError:  # pragma: no cover - handled at runtime.
    DDGS = None
    DDGSException = RuntimeError

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - handled at runtime.
    PlaywrightTimeoutError = TimeoutError
    PlaywrightError = RuntimeError
    sync_playwright = None


USER_AGENT = "KoreanLandmarkDatasetCollector/1.0 (+local research dataset builder)"
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_RETRIES = 3

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    query: str
    page_url: str = ""
    title: str = ""


@dataclass(frozen=True)
class ClassConfig:
    class_id: str
    label_ko: str
    search_terms: list[str]
    google_maps_url: str = ""
    tripadvisor_url: str = ""


DEFAULT_CONFIG = {
    "output_dir": "dataset",
    "target_per_class": 1000,
    "min_width": 320,
    "min_height": 240,
    "request_delay_seconds": 0.25,
    "classes": [],
}


def load_config(path: Path) -> dict:
    if not path.exists():
        print(f"[config] {path} 파일이 없어 기본 설정으로 실행합니다.")
        return dict(DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    config = dict(DEFAULT_CONFIG)
    config.update(loaded)
    return config


def load_google_maps_api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if key:
        return key

    env_path = Path(".env")
    if not env_path.exists():
        return ""

    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "GOOGLE_MAPS_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


def normalize_filename_part(value: str) -> str:
    value = re.sub(r"[^\w가-힣-]+", "_", value.strip(), flags=re.UNICODE)
    return value.strip("_").lower() or "image"


def make_class_id(index: int, label: str) -> str:
    romanized = normalize_filename_part(label)
    if romanized == "image":
        romanized = f"class_{index}"
    return f"{index:02d}_{romanized}"


def clean_prompt_value(value: str) -> str:
    return value.replace("\ufeff", "").strip()


def prompt_interactive_class(config: dict) -> ClassConfig:
    print("수집할 장소/랜드마크 이름을 입력하세요.")
    label = ""
    while not label:
        label = clean_prompt_value(input("이름: "))

    extra_terms_input = clean_prompt_value(input("추가 검색어 또는 영어 이름(선택, 쉼표로 구분): "))
    google_maps_url = clean_prompt_value(input("Google Maps 리뷰 URL(선택, 비우면 이름으로 자동 검색): "))
    tripadvisor_url = clean_prompt_value(input("Tripadvisor URL(선택, 비우면 건너뜀): "))
    extra_terms = [clean_prompt_value(term) for term in extra_terms_input.split(",") if clean_prompt_value(term)]
    classes = config.get("classes", [])
    class_id = make_class_id(len(classes) + 1, label)
    search_terms = [label, f"{label} 사진", f"{label} 리뷰 사진", *extra_terms]
    return ClassConfig(
        class_id=class_id,
        label_ko=label,
        search_terms=search_terms,
        google_maps_url=google_maps_url,
        tripadvisor_url=tripadvisor_url,
    )


def prompt_interactive_target(default_target: int) -> int:
    value = clean_prompt_value(input(f"몇 장까지 가져올까요? 기본 {default_target}: "))
    if not value:
        return default_target
    try:
        target = int(value)
    except ValueError:
        print(f"[warn] 숫자가 아니라서 기본값 {default_target}장을 사용합니다.")
        return default_target
    if target <= 0:
        print(f"[warn] 1보다 작아서 기본값 {default_target}장을 사용합니다.")
        return default_target
    return target


def prompt_manual_browser_seconds(default_seconds: int) -> int:
    value = clean_prompt_value(input(f"Google Maps 수동 브라우저 수집 시간(초), 기본 {default_seconds}: "))
    if not value:
        return default_seconds
    try:
        seconds = int(value)
    except ValueError:
        print(f"[warn] 숫자가 아니라서 기본값 {default_seconds}초를 사용합니다.")
        return default_seconds
    if seconds < 10:
        print(f"[warn] 너무 짧아서 기본값 {default_seconds}초를 사용합니다.")
        return default_seconds
    return seconds


def prompt_google_delay(default_delay: float) -> float:
    value = clean_prompt_value(input(f"Google 수집 스크롤 텀(초), 기본 {default_delay}: "))
    if not value:
        return default_delay
    try:
        delay = float(value)
    except ValueError:
        print(f"[warn] 숫자가 아니라서 기본값 {default_delay}초를 사용합니다.")
        return default_delay
    if delay < 1.0:
        print(f"[warn] 너무 짧아서 기본값 {default_delay}초를 사용합니다.")
        return default_delay
    return delay


def prompt_capture_seconds(default_seconds: int) -> int:
    value = clean_prompt_value(input(f"열린 Chrome에서 이미지 캡처할 시간(초), 기본 {default_seconds}: "))
    if not value:
        return default_seconds
    try:
        seconds = int(value)
    except ValueError:
        print(f"[warn] 숫자가 아니라서 기본값 {default_seconds}초를 사용합니다.")
        return default_seconds
    if seconds < 10:
        print(f"[warn] 너무 짧아서 기본값 {default_seconds}초를 사용합니다.")
        return default_seconds
    return seconds


def read_existing_hashes(class_dir: Path) -> set[str]:
    hashes: set[str] = set()
    manifest = class_dir / "manifest.csv"
    if not manifest.exists():
        return hashes

    with manifest.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("sha256"):
                hashes.add(row["sha256"])
    return hashes


def count_existing_images(class_dir: Path) -> int:
    manifest = class_dir / "manifest.csv"
    if not manifest.exists():
        return 0

    with manifest.open("r", encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def append_csv(path: Path, fieldnames: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def unique_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        if not candidate.url or candidate.url in seen:
            continue
        seen.add(candidate.url)
        result.append(candidate)
    return result


def commons_candidates(search_terms: list[str], max_results_per_query: int) -> list[Candidate]:
    endpoint = "https://commons.wikimedia.org/w/api.php"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    candidates: list[Candidate] = []

    for term in search_terms:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": term,
            "gsrnamespace": 6,
            "gsrlimit": min(max_results_per_query, 50),
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "format": "json",
        }
        while True:
            response = None
            for attempt in range(MAX_RETRIES):
                response = session.get(endpoint, params=params, timeout=20)
                if response.status_code != 429:
                    break
                retry_after = response.headers.get("retry-after")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                time.sleep(wait_seconds)
            if response is None:
                break
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                print(f"[warn] commons query skipped: {term} ({exc})")
                break
            payload = response.json()
            pages = payload.get("query", {}).get("pages", {})
            for page in pages.values():
                for info in page.get("imageinfo", []):
                    mime = info.get("mime", "")
                    url = info.get("url", "")
                    if mime in IMAGE_EXTENSIONS and url:
                        candidates.append(
                            Candidate(
                                url=url,
                                source="wikimedia_commons",
                                query=term,
                                page_url=info.get("descriptionurl", ""),
                                title=page.get("title", ""),
                            )
                        )

            if len([c for c in candidates if c.query == term]) >= max_results_per_query:
                break
            continuation = payload.get("continue")
            if not continuation:
                break
            params.update(continuation)
            time.sleep(0.1)

    return unique_candidates(candidates)


def duckduckgo_candidates(search_terms: list[str], max_results_per_query: int) -> list[Candidate]:
    if DDGS is None:
        raise RuntimeError("ddgs is not installed. Run: pip install -r requirements.txt")

    candidates: list[Candidate] = []
    with DDGS() as ddgs:
        for term in search_terms:
            try:
                results = ddgs.images(
                    query=term,
                    max_results=max_results_per_query,
                    safesearch="moderate",
                    type_image="photo",
                )
            except DDGSException as exc:
                print(f"[warn] duckduckgo query skipped: {term} ({exc})")
                continue
            for item in results:
                url = item.get("image")
                if not url:
                    continue
                candidates.append(
                    Candidate(
                        url=url,
                        source="duckduckgo",
                        query=term,
                        page_url=item.get("url", ""),
                        title=item.get("title", ""),
                    )
                )
            time.sleep(0.2)

    return unique_candidates(candidates)


def google_places_api_candidates(class_config: ClassConfig, max_results: int) -> list[Candidate]:
    api_key = load_google_maps_api_key()
    if not api_key:
        print("[warn] GOOGLE_MAPS_API_KEY가 없어서 google_places_api source를 건너뜁니다.")
        return []

    session = requests.Session()
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.googleMapsUri,places.photos",
    }
    candidates: list[Candidate] = []
    seen_photo_names: set[str] = set()

    for term in class_config.search_terms:
        if len(candidates) >= max_results:
            break
        try:
            response = session.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers=headers,
                json={
                    "textQuery": term,
                    "languageCode": "ko",
                    "regionCode": "KR",
                },
                timeout=25,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"[warn] google places text search skipped: {term} ({exc.__class__.__name__})")
            continue

        places = response.json().get("places", [])
        if not places:
            continue

        # The first result is Google's best match for the text query.
        place = places[0]
        display_name = place.get("displayName", {}).get("text", term)
        page_url = place.get("googleMapsUri", "")
        for photo in place.get("photos", []):
            if len(candidates) >= max_results:
                break
            photo_name = photo.get("name", "")
            if not photo_name or photo_name in seen_photo_names:
                continue
            seen_photo_names.add(photo_name)

            media_url = f"https://places.googleapis.com/v1/{photo_name}/media"
            try:
                media_response = session.get(
                    media_url,
                    params={
                        "maxWidthPx": 1600,
                        "skipHttpRedirect": "true",
                        "key": api_key,
                    },
                    timeout=25,
                )
                media_response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[warn] google places photo skipped: {photo_name} ({exc.__class__.__name__})")
                continue

            photo_uri = media_response.json().get("photoUri", "")
            if not photo_uri:
                continue
            candidates.append(
                Candidate(
                    url=photo_uri,
                    source="google_places_api",
                    query=term,
                    page_url=page_url,
                    title=display_name,
                )
            )
        time.sleep(0.1)

    print(f"[api] google_places_api: found {len(candidates)} photo candidates")
    return unique_candidates(candidates)


def review_site_candidates(
    search_terms: list[str],
    max_results_per_query: int,
    site_name: str,
    site_queries: list[str],
    allowed_page_domains: list[str],
) -> list[Candidate]:
    if DDGS is None:
        raise RuntimeError("ddgs is not installed. Run: pip install -r requirements.txt")

    candidates: list[Candidate] = []
    with DDGS() as ddgs:
        for term in search_terms:
            for site_query in site_queries:
                query = f"{term} {site_query}"
                try:
                    results = ddgs.images(
                        query=query,
                        max_results=max_results_per_query,
                        safesearch="moderate",
                        type_image="photo",
                    )
                except DDGSException as exc:
                    print(f"[warn] {site_name} query skipped: {query} ({exc})")
                    continue
                for item in results:
                    url = item.get("image")
                    page_url = item.get("url", "")
                    page_host = urlparse(page_url).netloc.lower()
                    if not url:
                        continue
                    if allowed_page_domains and not any(domain in page_host for domain in allowed_page_domains):
                        continue
                    candidates.append(
                        Candidate(
                            url=url,
                            source=site_name,
                            query=query,
                            page_url=page_url,
                            title=item.get("title", ""),
                        )
                    )
                time.sleep(0.4)

    return unique_candidates(candidates)


def google_review_candidates(search_terms: list[str], max_results_per_query: int) -> list[Candidate]:
    return review_site_candidates(
        search_terms=search_terms,
        max_results_per_query=max_results_per_query,
        site_name="google_reviews",
        site_queries=[
            "Google Maps photos",
            "Google reviews photos",
            "site:google.com/maps",
        ],
        allowed_page_domains=["google."],
    )


def tripadvisor_candidates(search_terms: list[str], max_results_per_query: int) -> list[Candidate]:
    return review_site_candidates(
        search_terms=search_terms,
        max_results_per_query=max_results_per_query,
        site_name="tripadvisor",
        site_queries=[
            "Tripadvisor photos",
            "Tripadvisor reviews photos",
            "site:tripadvisor.com",
        ],
        allowed_page_domains=["tripadvisor."],
    )


def google_maps_search_url(query: str) -> str:
    return f"https://www.google.com/maps/search/{quote_plus(query)}"


def google_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


def google_image_search_url(query: str) -> str:
    return f"https://www.google.com/search?udm=2&q={quote_plus(query)}"


def find_tripadvisor_page_url(search_terms: list[str]) -> str:
    if DDGS is None:
        return ""

    queries = []
    for term in search_terms:
        queries.extend(
            [
                f"{term} site:tripadvisor.co.kr/Attraction_Review",
                f"{term} site:tripadvisor.com/Attraction_Review",
                f"{term} Tripadvisor",
            ]
        )

    try:
        with DDGS() as ddgs:
            for query in queries:
                try:
                    results = ddgs.text(query=query, max_results=8)
                except DDGSException as exc:
                    print(f"[warn] tripadvisor page search skipped: {query} ({exc})")
                    continue
                for item in results:
                    href = item.get("href") or item.get("url") or ""
                    host = urlparse(href).netloc.lower()
                    if "tripadvisor." in host and "Attraction_Review" in href:
                        print(f"[page] tripadvisor: found candidate page {href}")
                        return href
    except DDGSException as exc:
        print(f"[warn] tripadvisor page search failed ({exc})")
    return ""


def extract_page_image_items(page) -> list[dict]:
    script = r"""
() => {
  const out = [];
  const pushUrl = (url, title, width, height) => {
    if (!url || !url.startsWith('http')) return;
    out.push({ url, title: title || '', width: width || 0, height: height || 0 });
  };
  const pushSrcset = (srcset, title, width, height) => {
    if (!srcset) return;
    for (const part of srcset.split(',')) {
      const url = part.trim().split(/\s+/)[0];
      pushUrl(url, title, width, height);
    }
  };

  for (const img of Array.from(document.images)) {
    const title = img.alt || img.getAttribute('aria-label') || document.title;
    pushUrl(
      img.currentSrc || img.src,
      title,
      img.naturalWidth,
      img.naturalHeight
    );
    for (const attr of ['data-src', 'data-lazyurl', 'data-original', 'data-media-url']) {
      pushUrl(img.getAttribute(attr), title, img.naturalWidth, img.naturalHeight);
    }
    pushSrcset(img.getAttribute('srcset'), title, img.naturalWidth, img.naturalHeight);
  }

  for (const el of Array.from(document.querySelectorAll('*'))) {
    const style = window.getComputedStyle(el);
    const bg = style.backgroundImage || '';
    const rect = el.getBoundingClientRect();
    const title = el.getAttribute('aria-label') || el.getAttribute('title') || document.title;
    for (const match of bg.matchAll(/url\(["']?(.+?)["']?\)/g)) {
      pushUrl(
        match[1],
        title,
        Math.round(rect.width),
        Math.round(rect.height)
      );
    }
    for (const attr of ['data-src', 'data-lazyurl', 'data-original', 'data-media-url']) {
      pushUrl(el.getAttribute(attr), title, Math.round(rect.width), Math.round(rect.height));
    }
  }

  return out;
}
"""
    try:
        return page.evaluate(script)
    except PlaywrightError as exc:
        print(f"[warn] image extraction skipped ({exc.__class__.__name__})")
        return []


def scroll_page_and_panels(page) -> None:
    try:
        page.evaluate(
            """
() => {
  window.scrollBy(0, 2200);
  const scrollables = Array.from(document.querySelectorAll('*')).filter((el) => {
    const style = window.getComputedStyle(el);
    return el.scrollHeight > el.clientHeight + 200 && style.overflowY !== 'hidden';
  });
  scrollables
    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))
    .slice(0, 8)
    .forEach((el) => { el.scrollTop += 2200; });
}
"""
        )
    except PlaywrightError:
        return


def looks_like_google_captcha(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=1500)
    except PlaywrightError:
        return False
    markers = [
        "로봇이 아닙니다",
        "비정상적인 트래픽",
        "not a robot",
        "unusual traffic",
        "reCAPTCHA",
    ]
    return any(marker.lower() in text.lower() for marker in markers)


def dismiss_common_popups(page) -> None:
    labels = [
        "Accept",
        "I agree",
        "동의",
        "모두 동의",
        "나중에",
        "닫기",
        "Close",
    ]
    for label in labels:
        try:
            locator = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if locator.count() > 0:
                locator.first.click(timeout=1200)
                page.wait_for_timeout(800)
        except Exception:
            continue


def open_google_photo_surfaces(page) -> None:
    labels = [
        "사진",
        "Photos",
        "사진 모두 보기",
        "See photos",
        "View all photos",
    ]
    for label in labels:
        try:
            locator = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if locator.count() > 0:
                locator.first.click(timeout=1800)
                page.wait_for_timeout(2500)
                return
        except Exception:
            continue


def click_google_reviews_surface(page) -> str:
    script = r"""
() => {
  const review = '\uB9AC\uBDF0';
  const els = Array.from(document.querySelectorAll('a, button, [role=button], [aria-label]'));
  const scored = els.map((el) => {
    const text = (el.getAttribute('aria-label') || el.innerText || el.textContent || '').trim();
    const rect = el.getBoundingClientRect();
    let score = 0;
    if (text.includes(review)) score += 10;
    if (/\d[\d,.\s]*\s*리뷰/.test(text)) score += 20;
    if (text.includes('\uD3C9\uC810') || text.includes('\uBCC4\uD45C')) score += 3;
    if (rect.width > 20 && rect.height > 20) score += 1;
    return {el, text, score};
  }).filter((x) => x.score >= 10).sort((a, b) => b.score - a.score);
  if (!scored.length) return '';
  scored[0].el.click();
  return scored[0].text;
}
"""
    try:
        clicked = page.evaluate(script)
        if clicked:
            page.wait_for_timeout(3500)
        return clicked or ""
    except Exception:
        return ""


def normalize_page_image_url(url: str, source: str) -> str:
    if source == "google_image_search":
        if "google.com/logos" in url or "gstatic.com" in url and "encrypted" not in url:
            return ""
        if "googleusercontent.com/a-" in url or "googleusercontent.com/a/" in url:
            return ""
        return url
    if "google" in source:
        if "lh3.googleusercontent.com" not in url:
            return ""
        if "lh3.googleusercontent.com/a-" in url or "lh3.googleusercontent.com/a/" in url:
            return ""
        if "lh3.googleusercontent.com" in url:
            return re.sub(r"=[^/?#]+$", "=s1600", url)
    return url


def normalize_naver_review_image_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if "pup-review-phinf.pstatic.net" in parsed.netloc:
        return url
    if "search.pstatic.net" not in parsed.netloc:
        return ""
    src = parse_qs(parsed.query).get("src", [""])[0]
    src = unquote(src)
    if "pup-review-phinf.pstatic.net" in src:
        return src
    return ""


def normalize_naver_search_image_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if "search.pstatic.net" not in parsed.netloc:
        return ""
    query = parse_qs(parsed.query)
    src = unquote(query.get("src", [""])[0])
    if src and src.startswith("http"):
        blocked = [
            "pup-review-phinf.pstatic.net",
            "myplace-phinf.pstatic.net",
            "phinf.pstatic.net/contact",
        ]
        if any(item in src for item in blocked):
            return ""
        return src
    image_type = query.get("type", [""])[0]
    if image_type.startswith("f48_48"):
        return ""
    return url


def normalize_google_search_image_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("encrypted-tbn") and host.endswith(".gstatic.com") and parsed.path.startswith("/images"):
        return url
    return ""


def normalize_capture_image_url(url: str, title: str = "") -> str:
    google_url = normalize_page_image_url(url, "google_review_scroll")
    if google_url and is_google_review_image(google_url, title):
        return google_url
    if "googleusercontent.com" in url and "lh3.googleusercontent.com/a" not in url:
        google_image_url = re.sub(r"=[^/?#]+$", "=s1600", url)
        return google_image_url
    google_search_url = normalize_google_search_image_url(url)
    if google_search_url:
        return google_search_url
    naver_url = normalize_naver_review_image_url(url)
    if naver_url:
        return naver_url
    naver_search_url = normalize_naver_search_image_url(url)
    if naver_search_url:
        return naver_search_url
    return ""


def is_google_review_image(url: str, title: str) -> bool:
    if "lh3.googleusercontent.com/a-" in url or "lh3.googleusercontent.com/a/" in url:
        return False
    if "grass-cs" in url:
        return True
    if "\uB9AC\uBDF0\uC5D0 \uD3EC\uD568\uB41C" in title:
        return True
    return False


def google_browser_collect_candidates(
    page_url: str,
    source: str,
    query: str,
    max_results: int,
    review_only: bool,
    open_reviews: bool,
    google_delay_seconds: float,
) -> list[Candidate]:
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed. Run: pip install -r requirements.txt")

    candidates: list[Candidate] = []
    seen: set[str] = set()

    def add_url(url: str, title: str = "") -> None:
        if len(candidates) >= max_results:
            return
        normalized = normalize_page_image_url(url, source)
        if not normalized or normalized in seen:
            return
        if review_only and not is_google_review_image(normalized, title):
            return
        seen.add(normalized)
        candidates.append(
            Candidate(
                url=normalized,
                source=source,
                query=query,
                page_url=page_url,
                title=title or query,
            )
        )

    print(f"[google] {source}: opening {page_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(
            locale="ko-KR",
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        def on_response(response) -> None:
            add_url(response.url)

        page.on("response", on_response)
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            dismiss_common_popups(page)
            print("[google] 브라우저가 열렸습니다. CAPTCHA나 리뷰 패널이 보이면 직접 클릭/스크롤해도 됩니다.")
            if looks_like_google_captcha(page):
                print("[warn] Google CAPTCHA 감지됨. 이 Google source를 건너뜁니다.")
                return []
            if open_reviews:
                clicked = click_google_reviews_surface(page)
                print(f"[google] review click: {clicked[:120] if clicked else 'not found'}")
            for _ in range(min(180, max(30, max_results // 3))):
                if looks_like_google_captcha(page):
                    print("[warn] Google CAPTCHA 감지됨. 현재 Google source를 중단합니다.")
                    break
                for item in extract_page_image_items(page):
                    add_url(item.get("url", ""), item.get("title", ""))
                    if len(candidates) >= max_results:
                        break
                if len(candidates) >= max_results:
                    break
                scroll_page_and_panels(page)
                try:
                    page.mouse.wheel(0, 450)
                    page.wait_for_timeout(int(google_delay_seconds * 1000))
                except PlaywrightError:
                    print(f"[warn] {source} page changed while scrolling; stopping this source.")
                    break
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            print(f"[warn] {source} failed ({exc.__class__.__name__}). 다른 source로 계속 진행합니다.")
        finally:
            browser.close()

    print(f"[google] {source}: found {len(candidates)} image candidates")
    return unique_candidates(candidates)


def google_image_search_candidates(
    class_config: ClassConfig,
    max_results: int,
    google_delay_seconds: float,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for term in class_config.search_terms:
        if len(candidates) >= max_results:
            break
        candidates.extend(
            google_browser_collect_candidates(
                page_url=google_image_search_url(term),
                source="google_image_search",
                query=term,
                max_results=max(1, max_results - len(candidates)),
                review_only=False,
                open_reviews=False,
                google_delay_seconds=google_delay_seconds,
            )
        )
    return unique_candidates(candidates)


def google_review_scroll_candidates(
    class_config: ClassConfig,
    max_results: int,
    google_delay_seconds: float,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    terms = [class_config.label_ko, *class_config.search_terms]
    for term in dict.fromkeys(term for term in terms if term):
        if len(candidates) >= max_results:
            break
        query = f"{term} 리뷰"
        candidates.extend(
            google_browser_collect_candidates(
                page_url=google_search_url(query),
                source="google_review_scroll",
                query=query,
                max_results=max(1, max_results - len(candidates)),
                review_only=True,
                open_reviews=True,
                google_delay_seconds=google_delay_seconds,
            )
        )
    return unique_candidates(candidates)


def google_maps_manual_candidates(
    class_config: ClassConfig,
    max_results: int,
    duration_seconds: int,
) -> list[Candidate]:
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed. Run: pip install -r requirements.txt")

    page_url = class_config.google_maps_url or google_maps_search_url(class_config.label_ko)
    candidates: list[Candidate] = []
    seen: set[str] = set()

    def add_url(url: str, title: str = "") -> None:
        normalized = normalize_page_image_url(url, "google_maps_manual")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(
            Candidate(
                url=normalized,
                source="google_maps_manual",
                query=class_config.label_ko,
                page_url=page_url,
                title=title or class_config.label_ko,
            )
        )

    print(f"[manual] Google Maps 브라우저를 엽니다: {page_url}")
    print("[manual] 열린 브라우저에서 사진/리뷰 사진을 클릭하고 계속 스크롤하세요.")
    print(f"[manual] {duration_seconds}초 동안 로드되는 Google 사진 URL을 수집합니다.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            locale="ko-KR",
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def on_response(response) -> None:
            if len(candidates) >= max_results:
                return
            add_url(response.url)

        page.on("response", on_response)
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_common_popups(page)
        open_google_photo_surfaces(page)

        deadline = time.time() + duration_seconds
        while time.time() < deadline and len(candidates) < max_results:
            for item in extract_page_image_items(page):
                if len(candidates) >= max_results:
                    break
                add_url(item.get("url", ""), item.get("title", ""))
            print(
                f"[manual] 후보 {len(candidates)}개 수집됨 / 목표 후보 {max_results}개 / 남은 시간 {int(deadline - time.time())}초"
            )
            page.wait_for_timeout(2000)

        browser.close()

    print(f"[manual] google_maps_manual: found {len(candidates)} image candidates")
    return unique_candidates(candidates)


def chrome_debug_capture_candidates(
    class_config: ClassConfig,
    max_results: int,
    capture_seconds: int,
    cdp_url: str,
) -> list[Candidate]:
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed. Run: pip install -r requirements.txt")

    candidates: list[Candidate] = []
    seen: set[str] = set()

    def add_url(url: str, title: str = "", page_url: str = "") -> None:
        if len(candidates) >= max_results:
            return
        normalized = normalize_capture_image_url(url, title)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(
            Candidate(
                url=normalized,
                source="chrome_debug_capture",
                query=class_config.label_ko,
                page_url=page_url,
                title=title or class_config.label_ko,
            )
        )

    print(f"[chrome] 기존 Chrome에 연결합니다: {cdp_url}")
    print("[chrome] Chrome에서 Google 리뷰 화면을 직접 열고 아래로 스크롤하세요.")
    print(f"[chrome] {capture_seconds}초 동안 현재 열린 탭들에서 리뷰 사진 URL을 수집합니다.")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            print(f"[warn] Chrome debug 연결 실패: {exc.__class__.__name__}")
            print("[hint] 먼저 launch_chrome_capture.bat을 실행한 뒤 다시 수집기를 실행하세요.")
            return []

        def attach_page(page) -> None:
            def on_response(response) -> None:
                try:
                    current_url = page.url
                except PlaywrightError:
                    current_url = ""
                add_url(response.url, "", current_url)

            page.on("response", on_response)

        for context in browser.contexts:
            for page in context.pages:
                attach_page(page)
            context.on("page", attach_page)

        deadline = time.time() + capture_seconds
        while time.time() < deadline and len(candidates) < max_results:
            for context in browser.contexts:
                for page in context.pages:
                    try:
                        for item in extract_page_image_items(page):
                            add_url(item.get("url", ""), item.get("title", ""), page.url)
                            if len(candidates) >= max_results:
                                break
                    except PlaywrightError:
                        continue
                    if len(candidates) >= max_results:
                        break
            print(
                f"[chrome] 후보 {len(candidates)}개 수집됨 / 목표 후보 {max_results}개 / 남은 시간 {int(deadline - time.time())}초"
            )
            time.sleep(2)

        # Do not close the user's Chrome. Ending sync_playwright drops our connection.

    print(f"[chrome] chrome_debug_capture: found {len(candidates)} image candidates")
    return unique_candidates(candidates)


def page_image_candidates(
    page_url: str,
    source: str,
    query: str,
    max_results: int,
    min_preview_width: int = 120,
    min_preview_height: int = 120,
) -> list[Candidate]:
    if not page_url:
        return []
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed. Run: pip install -r requirements.txt")

    candidates: list[Candidate] = []
    seen: set[str] = set()
    scroll_rounds = min(160, max(24, max_results // 4))
    print(f"[page] {source}: opening {page_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            dismiss_common_popups(page)
            if "google_maps" in source:
                open_google_photo_surfaces(page)
            for _ in range(scroll_rounds):
                for item in extract_page_image_items(page):
                    url = normalize_page_image_url(item.get("url", ""), source)
                    if url in seen:
                        continue
                    width = int(item.get("width") or 0)
                    height = int(item.get("height") or 0)
                    if width < min_preview_width or height < min_preview_height:
                        continue
                    seen.add(url)
                    candidates.append(
                        Candidate(
                            url=url,
                            source=source,
                            query=query,
                            page_url=page_url,
                            title=item.get("title", ""),
                        )
                    )
                    if len(candidates) >= max_results:
                        break
                if len(candidates) >= max_results:
                    break
                scroll_page_and_panels(page)
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1200)
        except PlaywrightTimeoutError as exc:
            print(f"[warn] page timed out: {source} ({exc})")
        finally:
            browser.close()

    print(f"[page] {source}: found {len(candidates)} image candidates")
    return unique_candidates(candidates)


def tripadvisor_page_candidates(class_config: ClassConfig, max_results: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    tripadvisor_url = class_config.tripadvisor_url
    if not tripadvisor_url:
        print("[warn] Tripadvisor URL이 비어 있어 tripadvisor source를 건너뜁니다.")
        return candidates
    if tripadvisor_url:
        candidates.extend(
            page_image_candidates(
                page_url=tripadvisor_url,
                source="tripadvisor_review_page",
                query=class_config.label_ko,
                max_results=max_results,
                min_preview_width=24,
                min_preview_height=24,
            )
        )
    return unique_candidates(candidates)


def csv_import_candidates(class_config: ClassConfig, csv_dir: Path) -> list[Candidate]:
    csv_path = csv_dir / f"{class_config.class_id}.csv"
    if not csv_path.exists():
        return []

    candidates: list[Candidate] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            url = row.get("image_url") or row.get("url")
            if not url:
                continue
            candidates.append(
                Candidate(
                    url=url,
                    source="url_csv_import",
                    query=row.get("query") or class_config.label_ko,
                    page_url=row.get("page_url", ""),
                    title=row.get("title", ""),
                )
            )
    return unique_candidates(candidates)


def infer_extension(content_type: str, url: str) -> str:
    content_type = content_type.split(";")[0].strip().lower()
    if content_type in IMAGE_EXTENSIONS:
        return IMAGE_EXTENSIONS[content_type]

    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def validate_image_bytes(
    data: bytes,
    known_hashes: set[str],
    min_width: int,
    min_height: int,
) -> tuple[bool, dict]:
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 in known_hashes:
        return False, {"reason": "duplicate_sha256", "sha256": sha256}

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format or ""
    except (UnidentifiedImageError, OSError):
        return False, {"reason": "invalid_image", "sha256": sha256}

    if width < min_width or height < min_height:
        return False, {
            "reason": "too_small",
            "sha256": sha256,
            "width": width,
            "height": height,
        }

    return True, {
        "sha256": sha256,
        "width": width,
        "height": height,
        "format": image_format,
    }


def download_image(
    session: requests.Session,
    candidate: Candidate,
    output_dir: Path,
    known_hashes: set[str],
    index: int,
    min_width: int,
    min_height: int,
) -> tuple[bool, dict]:
    try:
        response = session.get(candidate.url, timeout=25, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        data = response.content
    except requests.RequestException as exc:
        return False, {"reason": f"download_error: {exc.__class__.__name__}"}

    valid, row = validate_image_bytes(data, known_hashes, min_width, min_height)
    if not valid:
        return False, row

    extension = infer_extension(content_type, candidate.url)
    sha256 = row["sha256"]
    filename = f"{index:05d}_{sha256[:12]}{extension}"
    image_path = output_dir / filename
    image_path.write_bytes(data)
    known_hashes.add(sha256)

    return True, {
        "filename": filename,
        "sha256": sha256,
        "width": row["width"],
        "height": row["height"],
        "format": row["format"],
        "source": candidate.source,
        "query": candidate.query,
        "image_url": candidate.url,
        "page_url": candidate.page_url,
        "title": candidate.title,
    }


def import_local_images(
    class_config: ClassConfig,
    local_root: Path,
    output_root: Path,
    target: int,
    min_width: int,
    min_height: int,
) -> int:
    source_dir = local_root / class_config.class_id
    class_dir = output_root / class_config.class_id
    class_dir.mkdir(parents=True, exist_ok=True)
    existing_count = count_existing_images(class_dir)
    known_hashes = read_existing_hashes(class_dir)
    if not source_dir.exists() or existing_count >= target:
        return existing_count

    manifest_fields = [
        "filename",
        "sha256",
        "width",
        "height",
        "format",
        "source",
        "query",
        "image_url",
        "page_url",
        "title",
    ]
    rejected_fields = [
        "reason",
        "sha256",
        "width",
        "height",
        "source",
        "query",
        "image_url",
        "page_url",
        "title",
    ]
    image_paths = [
        path
        for path in source_dir.rglob("*")
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ]

    saved_count = existing_count
    with tqdm(image_paths, desc=f"{class_config.label_ko} local", unit="file") as progress:
        for source_path in progress:
            if saved_count >= target:
                break
            data = source_path.read_bytes()
            valid, row = validate_image_bytes(data, known_hashes, min_width, min_height)
            if not valid:
                reject_row = {
                    "source": "local_import",
                    "query": class_config.label_ko,
                    "image_url": str(source_path),
                    "page_url": "",
                    "title": source_path.name,
                }
                reject_row.update(row)
                append_csv(class_dir / "rejected.csv", rejected_fields, reject_row)
                continue

            extension = ".jpg" if source_path.suffix.lower() == ".jpeg" else source_path.suffix.lower()
            sha256 = row["sha256"]
            filename = f"{saved_count + 1:05d}_{sha256[:12]}{extension}"
            (class_dir / filename).write_bytes(data)
            known_hashes.add(sha256)
            append_csv(
                class_dir / "manifest.csv",
                manifest_fields,
                {
                    "filename": filename,
                    "sha256": sha256,
                    "width": row["width"],
                    "height": row["height"],
                    "format": row["format"],
                    "source": "local_import",
                    "query": class_config.label_ko,
                    "image_url": str(source_path),
                    "page_url": "",
                    "title": source_path.name,
                },
            )
            saved_count += 1
            progress.set_postfix(saved=f"{saved_count}/{target}")

    return saved_count


def collect_for_class(
    class_config: ClassConfig,
    output_root: Path,
    target: int,
    max_search_results: int,
    min_width: int,
    min_height: int,
    request_delay_seconds: float,
    sources: list[str],
    import_url_csv_dir: Path | None,
    google_delay_seconds: float,
    capture_seconds: int,
    chrome_cdp_url: str,
) -> int:
    class_dir = output_root / class_config.class_id
    class_dir.mkdir(parents=True, exist_ok=True)
    existing_count = count_existing_images(class_dir)
    known_hashes = read_existing_hashes(class_dir)

    if existing_count >= target:
        print(f"[skip] {class_config.label_ko}: already has {existing_count}/{target}")
        return existing_count

    print(
        f"[start] {class_config.label_ko}: 현재 {existing_count}장 / 목표 {target}장 / 남은 {target - existing_count}장"
    )

    per_query = max(1, max_search_results // max(1, len(class_config.search_terms)))
    remaining_target = max(0, target - existing_count)
    chrome_candidate_target = max(per_query, min(max_search_results, remaining_target * 2))
    candidate_batches: list[Candidate] = []
    if import_url_csv_dir is not None:
        candidate_batches.extend(csv_import_candidates(class_config, import_url_csv_dir))
    if "chrome_debug_capture" in sources:
        candidate_batches.extend(
            chrome_debug_capture_candidates(
                class_config=class_config,
                max_results=chrome_candidate_target,
                capture_seconds=capture_seconds,
                cdp_url=chrome_cdp_url,
            )
        )
    if "google_places_api" in sources:
        candidate_batches.extend(google_places_api_candidates(class_config, per_query))
    if "google_reviews" in sources:
        candidate_batches.extend(google_review_scroll_candidates(class_config, per_query, google_delay_seconds))
    if "google_image_search" in sources:
        candidate_batches.extend(google_image_search_candidates(class_config, per_query, google_delay_seconds))
    if "tripadvisor" in sources:
        candidate_batches.extend(tripadvisor_page_candidates(class_config, per_query))
    if "commons" in sources:
        candidate_batches.extend(commons_candidates(class_config.search_terms, per_query))
    if "duckduckgo" in sources:
        candidate_batches.extend(duckduckgo_candidates(class_config.search_terms, per_query))

    candidates = unique_candidates(candidate_batches)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    manifest_fields = [
        "filename",
        "sha256",
        "width",
        "height",
        "format",
        "source",
        "query",
        "image_url",
        "page_url",
        "title",
    ]
    rejected_fields = [
        "reason",
        "sha256",
        "width",
        "height",
        "source",
        "query",
        "image_url",
        "page_url",
        "title",
    ]

    saved_count = existing_count
    with tqdm(candidates, desc=class_config.label_ko, unit="url") as progress:
        for candidate in progress:
            if saved_count >= target:
                break
            ok, row = download_image(
                session=session,
                candidate=candidate,
                output_dir=class_dir,
                known_hashes=known_hashes,
                index=saved_count + 1,
                min_width=min_width,
                min_height=min_height,
            )
            if ok:
                append_csv(class_dir / "manifest.csv", manifest_fields, row)
                saved_count += 1
                progress.set_postfix(
                    current=saved_count,
                    target=target,
                    remaining=max(0, target - saved_count),
                )
            else:
                reject_row = {
                    "source": candidate.source,
                    "query": candidate.query,
                    "image_url": candidate.url,
                    "page_url": candidate.page_url,
                    "title": candidate.title,
                }
                reject_row.update(row)
                append_csv(class_dir / "rejected.csv", rejected_fields, reject_row)
            time.sleep(request_delay_seconds)

    print(
        f"[done] {class_config.label_ko}: 현재 {saved_count}장 / 목표 {target}장 / 남은 {max(0, target - saved_count)}장"
    )
    return saved_count


def report(output_root: Path, classes: list[ClassConfig]) -> None:
    rows = []
    for item in classes:
        class_dir = output_root / item.class_id
        rows.append(
            {
                "class_id": item.class_id,
                "label_ko": item.label_ko,
                "images": count_existing_images(class_dir),
                "path": str(class_dir),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Korean landmark images.")
    parser.add_argument("--config", type=Path, default=Path("landmarks.yaml"))
    parser.add_argument("--target", type=int, default=None)
    parser.add_argument("--max-search-results", type=int, default=1800)
    parser.add_argument("--sources", default="commons,duckduckgo")
    parser.add_argument("--import-url-csv-dir", type=Path, default=None)
    parser.add_argument("--import-local-dir", type=Path, default=None)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--google-delay-seconds", type=float, default=5.0)
    parser.add_argument("--capture-seconds", type=int, default=180)
    parser.add_argument("--chrome-cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--report-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    output_root = Path(config.get("output_dir", "dataset"))
    target = args.target or int(config.get("target_per_class", 1000))
    min_width = int(config.get("min_width", 320))
    min_height = int(config.get("min_height", 240))
    request_delay_seconds = float(config.get("request_delay_seconds", 0.25))
    google_delay_seconds = float(args.google_delay_seconds)
    capture_seconds = int(args.capture_seconds)
    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    if args.interactive:
        classes = [prompt_interactive_class(config)]
        target = prompt_interactive_target(target)
        if "google_reviews" in sources or "google_image_search" in sources:
            google_delay_seconds = prompt_google_delay(google_delay_seconds)
        if "chrome_debug_capture" in sources:
            capture_seconds = prompt_capture_seconds(capture_seconds)
    else:
        classes = [
            ClassConfig(
                class_id=item["id"],
                label_ko=item["label_ko"],
                search_terms=list(item["search_terms"]),
                google_maps_url=item.get("google_maps_url", ""),
                tripadvisor_url=item.get("tripadvisor_url", ""),
            )
            for item in config["classes"]
        ]

    if args.report_only:
        report(output_root, classes)
        return

    for class_config in classes:
        if args.import_local_dir is not None:
            import_local_images(
                class_config=class_config,
                local_root=args.import_local_dir,
                output_root=output_root,
                target=target,
                min_width=min_width,
                min_height=min_height,
            )
        collect_for_class(
            class_config=class_config,
            output_root=output_root,
            target=target,
            max_search_results=args.max_search_results,
            min_width=min_width,
            min_height=min_height,
            request_delay_seconds=request_delay_seconds,
            sources=sources,
            import_url_csv_dir=args.import_url_csv_dir,
            google_delay_seconds=google_delay_seconds,
            capture_seconds=capture_seconds,
            chrome_cdp_url=args.chrome_cdp_url,
        )

    report(output_root, classes)


if __name__ == "__main__":
    main()
