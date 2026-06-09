#!/usr/bin/env python3
"""pinnow — Pinterest batch image downloader"""

import json
import re
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urlparse, urlunparse
import click
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
_THREAD_LOCAL = threading.local()

RESOLUTION_FALLBACKS = ["originals", "736x", "474x", "236x"]
DOWNLOAD_WORKERS = 8
BOARD_SCAN_VIEWPORT = {"width": 1280, "height": 1800}


def _default_data_dir() -> str:
    if os.environ.get("PINNOW_DATA_DIR"):
        return os.environ["PINNOW_DATA_DIR"]
    if sys.platform == "win32":
        root = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
        return os.path.join(root, "ChoiC0re", "pinnow")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/pinnow")
    return os.path.expanduser("~/.local/share/pinnow")


DATA_DIR = _default_data_dir()
COOKIES_FILE = os.path.join(DATA_DIR, "cookies.json")
BROWSER_DATA_DIR = os.path.join(DATA_DIR, "browser_data")


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _session() -> requests.Session:
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        _THREAD_LOCAL.session = sess
    return sess


def normalize_pinterest_url(url: str) -> str:
    """Pinterest/pin.it URL variants → browser-friendly canonical URL."""
    url = (url or "").strip()
    if not url:
        return url
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        if url.startswith("com/"):
            url = "https://www.pinterest." + url
        else:
            url = "https://" + url.lstrip("/")

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www.pin.it"):
        host = "pin.it"
    elif "pinterest." in host:
        host = "www.pinterest.com"

    path = re.sub(r"/+", "/", parsed.path or "/")
    return urlunparse(("https", host, path, "", parsed.query, ""))


def resolve_short_url(url: str) -> str:
    """pin.it 단축 URL을 실제 URL로 변환"""
    url = normalize_pinterest_url(url)
    try:
        r = SESSION.head(url, allow_redirects=True, timeout=8)
        if r.url and "pin.it" not in urlparse(r.url).netloc:
            return normalize_pinterest_url(r.url)
    except Exception:
        pass
    r = SESSION.get(url, allow_redirects=True, timeout=12)
    r.raise_for_status()
    return normalize_pinterest_url(r.url)


def fetch_page(url: str) -> BeautifulSoup:
    r = SESSION.get(normalize_pinterest_url(url), timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def extract_pws_data(soup: BeautifulSoup) -> dict:
    """페이지 HTML에 내장된 __PWS_DATA__ JSON 추출"""
    tag = soup.find("script", {"id": "__PWS_DATA__"})
    if tag:
        return json.loads(tag.string)
    for script in soup.find_all("script"):
        text = script.string or ""
        m = re.search(r"P\.start\.start\((\{.+\})\)", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    return {}


def best_image_url(images: dict) -> Optional[str]:
    """orig > 736x > 474x 순으로 가장 큰 이미지 URL 반환"""
    for key in ("orig", "736x", "474x", "236x"):
        item = images.get(key) if isinstance(images, dict) else None
        if isinstance(item, dict) and item.get("url"):
            return item["url"]
    return None


def to_resolution(url: str, res: str) -> str:
    return re.sub(r"pinimg\.com/[^/]+/", f"pinimg.com/{res}/", url)


# ── 핀 단건 ───────────────────────────────────────────────────────────────────

def get_pin_image_url(pin_url: str) -> tuple:
    """핀 URL → (이미지 URL, 핀 ID)"""
    pin_url = normalize_pinterest_url(pin_url)
    if "pin.it" in pin_url:
        pin_url = resolve_short_url(pin_url)

    soup = fetch_page(pin_url)

    data = extract_pws_data(soup)
    if data:
        try:
            pin_data = (
                data.get("resourceResponses", [{}])[0]
                .get("response", {})
                .get("data", {})
            )
            images = pin_data.get("images", {})
            url = best_image_url(images)
            pin_id = pin_data.get("id")
            if url:
                return url, pin_id
        except (IndexError, KeyError, TypeError):
            pass

    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        m = re.search(r"/(?:pin|idea-pin)/(\d+)", pin_url)
        pin_id = m.group(1) if m else re.sub(r"[^a-zA-Z0-9]", "_", pin_url[-12:])
        return to_resolution(og["content"].split("?")[0], "originals"), pin_id

    return None, None


# ── 다운로드 (해상도 폴백 포함) ───────────────────────────────────────────────

def download_with_fallback(base_url: str, dest: str) -> bool:
    """originals → 736x → 474x → 236x 순으로 시도"""
    for res in RESOLUTION_FALLBACKS:
        url = to_resolution(base_url, res)
        try:
            r = _session().get(url, stream=True, timeout=20)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                return True
        except Exception:
            continue
    return False


def _pin_dest(pin: dict, output: str) -> str:
    clean_url = pin["url"].split("?")[0]
    ext = clean_url.rsplit(".", 1)[-1].lower() if "." in clean_url else "jpg"
    if not re.match(r"^[a-z0-9]{2,5}$", ext):
        ext = "jpg"
    return os.path.join(output, f"pin_{pin['id']}.{ext}")


def _download_pin(pin: dict, output: str) -> tuple[bool, str]:
    dest = _pin_dest(pin, output)
    if os.path.exists(dest):
        return True, ""
    ok = download_with_fallback(pin["url"], dest)
    return ok, "" if ok else f"https://www.pinterest.com/pin/{pin['id']}/"


def download_pin_batch(pins: list, output: str, stop_cb=None, progress_cb=None, workers: int = DOWNLOAD_WORKERS) -> tuple[int, list]:
    """Download pins concurrently with bounded workers."""
    os.makedirs(output, exist_ok=True)
    total = len(pins)
    if total == 0:
        return 0, []

    ok = 0
    failed_urls = []
    done = 0
    max_workers = max(1, min(workers, total))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {executor.submit(_download_pin, pin, output): pin for pin in pins}
    try:
        for future in as_completed(futures):
            done += 1
            try:
                success, failed_url = future.result()
            except Exception:
                pin = futures[future]
                success = False
                failed_url = f"https://www.pinterest.com/pin/{pin['id']}/"
            if success:
                ok += 1
            elif failed_url:
                failed_urls.append(failed_url)
            if progress_cb:
                progress_cb(done, total)
            if stop_cb and stop_cb():
                for pending in futures:
                    pending.cancel()
                break
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return ok, failed_urls


# ── 보드 핀 목록 수집 ─────────────────────────────────────────────────────────

_PIN_FEED_RESOURCES = {
    "BoardFeedResource",
    "BoardSectionPinsResource",
    "SectionFeedResource",
}

_DOM_JS = """() => {
    const root = document.querySelector("[data-test-id='board-feed']");
    if (!root) return [];
    const seen = new Set();
    const out = [];
    const bestFromSrcset = (srcset) => {
        if (!srcset) return '';
        const entries = srcset.split(',').map(x => x.trim().split(/\\s+/)[0]).filter(Boolean);
        return entries.length ? entries[entries.length - 1] : '';
    };
    root.querySelectorAll("a[href*='/pin/'], a[href*='/idea-pin/']").forEach(a => {
        const m = a.href.match(/\\/(?:pin|idea-pin)\\/(\\d+)/);
        if (!m || seen.has(m[1])) return;
        seen.add(m[1]);
        const img = a.querySelector("img");
        const src = img
            ? (bestFromSrcset(img.srcset) || img.currentSrc || img.src || img.dataset.src || '')
            : '';
        if (src && !src.startsWith('data:')) out.push({id: m[1], url: src});
    });
    return out;
}"""


def fetch_board_pins(board_url: str, max_pins: int, log_cb=None) -> list:
    """
    Playwright로 Pinterest API 응답을 인터셉트해 핀 전체 수집.
    browser_data/ 디렉토리가 있으면 로그인 세션을 재사용해 비공개 핀도 수집.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    def _log(msg):
        if log_cb:
            log_cb(msg)
        else:
            click.echo(msg)

    normalized = normalize_pinterest_url(board_url)
    if "pin.it" in normalized:
        normalized = resolve_short_url(normalized)
    logged_in = os.path.isdir(BROWSER_DATA_DIR)

    pins = []
    seen = set()
    api_batches = [0]
    seen_resources: set = set()

    def _add_pin(pin_id: str, img_url: str) -> bool:
        if not pin_id or not img_url or pin_id in seen:
            return False
        seen.add(pin_id)
        pins.append({"id": pin_id, "url": img_url})
        return True

    def _extract_pin_items(obj):
        if isinstance(obj, list):
            for child in obj:
                yield from _extract_pin_items(child)
            return
        if not isinstance(obj, dict):
            return

        pin_id = str(obj.get("id", ""))
        images = obj.get("images", {})
        if pin_id and isinstance(images, dict) and best_image_url(images):
            yield obj

        for key in ("data", "items", "pins", "results", "pin", "grid_items"):
            if key in obj:
                yield from _extract_pin_items(obj[key])

    def on_response(response):
        # 진단: 모든 Pinterest resource URL 기록 (어떤 API가 호출되는지 확인)
        rname = ""
        if "pinterest.com/resource/" in response.url:
            m = re.search(r"/resource/([^/]+)/", response.url)
            if m:
                rname = m.group(1)
                if rname not in seen_resources:
                    seen_resources.add(rname)
                    _log(f"  [감지] {rname}")

        if rname not in _PIN_FEED_RESOURCES:
            return
        try:
            if response.status != 200:
                return
            data = response.json()
            resource_resp = data.get("resource_response", {})
            payload = resource_resp.get("data", data)
            bookmark = resource_resp.get("bookmark")

            before = len(pins)
            for item in _extract_pin_items(payload):
                pin_id = str(item.get("id", ""))
                images = item.get("images", {})
                img_url = best_image_url(images)
                _add_pin(pin_id, img_url)
            gained = len(pins) - before

            end_flag = " ← 마지막" if bookmark is None else ""
            if gained > 0:
                api_batches[0] += 1
                _log(f"  [{rname}] +{gained} → {len(pins)}개{end_flag}")
        except Exception:
            pass

    def _flush_initial_props(page):
        try:
            raw = page.locator("script#__PWS_INITIAL_PROPS__").text_content(timeout=1000)
        except Exception:
            return
        if not raw:
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        before = len(pins)
        for item in _extract_pin_items(data):
            pin_id = str(item.get("id", ""))
            img_url = best_image_url(item.get("images", {}))
            _add_pin(pin_id, img_url)
        gained = len(pins) - before
        if gained > 0:
            _log(f"  [초기 데이터] +{gained} → {len(pins)}개")

    def _flush_dom(page):
        for item in page.evaluate(_DOM_JS):
            pid = str(item.get("id", ""))
            raw = item.get("url", "")
            _add_pin(pid, to_resolution(raw, "originals") if raw else "")

    with sync_playwright() as p:
        if logged_in:
            _log("  로그인 세션 적용됨")
            ctx = p.chromium.launch_persistent_context(
                BROWSER_DATA_DIR,
                headless=True,
                user_agent=HEADERS["User-Agent"],
                viewport=BOARD_SCAN_VIEWPORT,
                args=["--no-sandbox"],
            )
            page = ctx.new_page()
        else:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport=BOARD_SCAN_VIEWPORT,
            )
            ctx.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9"})
            page = ctx.new_page()

        page.on("response", on_response)

        _log("  브라우저로 보드 로딩 중...")
        page.goto(normalized, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector(
                "[data-test-id='board-feed'] a[href*='/pin/'], "
                "[data-test-id='board-feed'] a[href*='/idea-pin/']",
                timeout=12000,
            )
        except PWTimeout:
            _flush_dom(page)
            if not pins:
                ctx.close()
                raise click.ClickException("핀을 찾을 수 없습니다. URL이 올바른지, 보드/프로필이 공개인지 확인하세요.")

        page.wait_for_timeout(1000)
        _flush_initial_props(page)
        _flush_dom(page)
        _log(f"  초기 로드: {len(pins)}개")

        stall = 0
        bar = tqdm(desc="핀 수집", unit="핀", initial=len(pins))
        try:
            while len(pins) < max_pins:
                prev = len(pins)
                page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 2, 1200))")
                page.wait_for_timeout(900)
                _flush_dom(page)
                added = len(pins) - prev
                if added > 0:
                    bar.update(added)
                    stall = 0
                else:
                    stall += 1
                    if stall >= 6:
                        break
                    page.wait_for_timeout(500)
        finally:
            bar.close()

        page.wait_for_timeout(800)
        _flush_dom(page)
        _log(f"  최종 {len(pins)}개 / API 배치 {api_batches[0]}회")
        _log(f"  감지된 API 타입: {', '.join(sorted(seen_resources)) or '없음'}")

        ctx.close()

    return pins[:max_pins]


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """pinnow — Pinterest 이미지 다운로더"""


@cli.command()
@click.option("--timeout", default=120, show_default=True, help="로그인 대기 시간(초)")
def login(timeout):
    """Pinterest 로그인 후 브라우저 세션 저장 (브라우저 창이 열립니다)"""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    click.echo("브라우저가 열립니다. Pinterest에 로그인하면 자동으로 세션이 저장됩니다.")
    click.echo(f"저장 위치: {BROWSER_DATA_DIR}")
    click.echo(f"(최대 대기 {timeout}초)\n")

    os.makedirs(BROWSER_DATA_DIR, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            BROWSER_DATA_DIR,
            headless=False,
            user_agent=HEADERS["User-Agent"],
            args=["--no-sandbox"],
        )
        page = ctx.new_page()
        page.goto("https://www.pinterest.com/login/", timeout=30000)

        try:
            page.wait_for_url(
                re.compile(r"pinterest\.com(?!/login)"),
                timeout=timeout * 1000,
            )
            page.wait_for_timeout(2000)
        except PWTimeout:
            ctx.close()
            raise click.ClickException(f"{timeout}초 내에 로그인이 감지되지 않았습니다.")

        ctx.close()

    click.echo(f"세션 저장 완료 → {BROWSER_DATA_DIR}")
    click.echo(f"확인: {os.path.isdir(BROWSER_DATA_DIR)}")


@cli.command()
@click.argument("url")
@click.option("-o", "--output", default=".", help="저장 디렉토리 (기본: 현재 폴더)")
def pin(url, output):
    """핀 단건 다운로드\n\n  URL: 핀 URL 또는 pin.it 단축 URL"""
    os.makedirs(output, exist_ok=True)
    click.echo(f"핀 정보 가져오는 중: {url}")
    img_url, pin_id = get_pin_image_url(url)
    if not img_url:
        raise click.ClickException("이미지 URL을 찾을 수 없습니다.")

    clean_url = img_url.split("?")[0]
    ext = clean_url.rsplit(".", 1)[-1] or "jpg"
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(pin_id))
    dest = os.path.join(output, f"pin_{safe_id}.{ext}")

    click.echo(f"다운로드 중: {img_url}")
    if download_with_fallback(img_url, dest):
        click.echo(f"저장 완료: {dest}")
    else:
        click.echo("  ✗ 모든 해상도에서 다운로드 실패", err=True)


@cli.command()
@click.argument("board_url")
@click.option("-o", "--output", default=".", help="저장 디렉토리")
@click.option("-n", "--max-pins", default=50, show_default=True, help="최대 다운로드 핀 수")
def board(board_url, output, max_pins):
    """보드 전체 배치 다운로드\n\n  BOARD_URL: pinterest.com/username/boardname/ 형식"""
    os.makedirs(output, exist_ok=True)
    click.echo(f"보드 수집 중: {board_url}  (최대 {max_pins}개)")

    pins = fetch_board_pins(board_url, max_pins)
    if not pins:
        raise click.ClickException("핀을 찾을 수 없습니다. URL을 확인하거나 보드가 공개인지 확인하세요.")

    click.echo(f"\n{len(pins)}개 핀 다운로드 시작 → {output}/")
    with tqdm(total=len(pins), desc="다운로드", unit="핀") as bar:
        ok, failed_urls = download_pin_batch(
            pins,
            output,
            progress_cb=lambda _cur, _total: bar.update(1),
        )

    # 실패한 핀 목록을 파일로 저장
    if failed_urls:
        failed_path = os.path.join(output, "failed_pins.txt")
        with open(failed_path, "w", encoding="utf-8") as f:
            f.write("\n".join(failed_urls) + "\n")
        click.echo(f"\n완료: 성공 {ok}개 / 실패 {len(failed_urls)}개")
        click.echo(f"실패한 핀 목록 → {failed_path}")
    else:
        click.echo(f"\n완료: 전체 {ok}개 성공")


if __name__ == "__main__":
    cli()
