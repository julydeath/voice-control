import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

from ollama import Client
from playwright.sync_api import BrowserContext, Page, sync_playwright

MODEL = "deepseek-v3.1:671b-cloud"

KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google mail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com",
    "web.whatsapp.com": "https://web.whatsapp.com",
    "hotstar": "https://www.hotstar.com",
    "disney hotstar": "https://www.hotstar.com",
    "netflix": "https://www.netflix.com",
    "duckduckgo": "https://duckduckgo.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "amazon prime": "https://www.primevideo.com",
    "prime video": "https://www.primevideo.com",
}

ORDINALS = {
    "first": 1, "1st": 1, "one": 1,
    "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
}

NORMAL_SYSTEM = (
    "You are a helpful terminal voice assistant. "
    "Answer clearly, practically, and briefly. "
    "For coding or software topics, teach step by step."
)


@dataclass
class BrowserState:
    browser_mode: bool = False
    current_tab: int = 0
    last_media_site: str = ""
    normal_history: List[dict] = field(
        default_factory=lambda: [{"role": "system", "content": NORMAL_SYSTEM}]
    )


def speak(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    try:
        subprocess.Popen(["say", text[:700]])
    except Exception:
        pass


def stream_chat(client: Client, messages: List[dict]) -> str:
    stream = client.chat(model=MODEL, messages=messages, stream=True)
    out = []
    print("\nAssistant> ", end="", flush=True)

    for part in stream:
        chunk = ""
        try:
            chunk = part["message"]["content"]
        except Exception:
            try:
                chunk = part.message.content
            except Exception:
                chunk = ""

        if chunk:
            print(chunk, end="", flush=True)
            out.append(chunk)

    print()
    return "".join(out).strip()


def ask_normal(client: Client, state: BrowserState, user_text: str) -> str:
    state.normal_history.append({"role": "user", "content": user_text})
    reply = stream_chat(client, state.normal_history)
    state.normal_history.append({"role": "assistant", "content": reply})

    if len(state.normal_history) > 14:
        system = state.normal_history[0]
        state.normal_history = [system] + state.normal_history[-13:]

    return reply


def is_real_tab(page):
    try:
        url = (page.url or "").strip().lower()
    except Exception:
        return False

    if not url:
        return False

    bad_prefixes = (
        "devtools://",
        "chrome://",
        "chrome-search://",
        "chrome-extension://",
        "edge-extension://",
        "about:",
    )

    if url.startswith(bad_prefixes):
        return False

    if "omnibox-popup.top-chrome" in url:
        return False

    if "new-tab-page" in url:
        return False

    return True


def pick_best_page(context: BrowserContext) -> Page:
    pages = context.pages

    real_pages = [p for p in pages if is_real_tab(p)]
    if real_pages:
        page = real_pages[-1]
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    if pages:
        page = pages[-1]
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    page = context.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


def connect_existing_chrome():
    playwright = sync_playwright().start()

    browser = playwright.chromium.connect_over_cdp(
        "http://127.0.0.1:9222",
        slow_mo=120,
        timeout=30000,
    )

    if not browser.contexts:
        raise RuntimeError(
            "No Chrome context found. Start Chrome first with remote debugging."
        )

    context = browser.contexts[0]

    print("\nAttached pages:")
    for i, p in enumerate(context.pages, start=1):
        try:
            print(f"{i}. {p.url}")
        except Exception:
            print(f"{i}. <unavailable>")

    # find first real webpage tab
    real_pages = [p for p in context.pages if is_real_tab(p)]

    if real_pages:
        page = real_pages[-1]
    else:
        print("\nNo real webpage tab found. Creating one...")
        page = context.new_page()
        page.goto("https://duckduckgo.com", wait_until="load", timeout=30000)

    try:
        page.bring_to_front()
    except Exception:
        pass

    print(f"\nUsing tab: {page.url}\n")
    return playwright, browser, context, page


def current_page(context: BrowserContext, state: BrowserState) -> Page:
    pages = context.pages
    if not pages:
        page = context.new_page()
        state.current_tab = 0
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    state.current_tab = max(0, min(state.current_tab, len(pages) - 1))
    candidate = pages[state.current_tab]

    if not is_real_tab(candidate):
        candidate = pick_best_page(context)
        try:
            state.current_tab = context.pages.index(candidate)
        except Exception:
            state.current_tab = 0

    try:
        candidate.bring_to_front()
    except Exception:
        pass

    return candidate


def switch_to_newest_page(context: BrowserContext, state: BrowserState, before_count: int) -> None:
    if len(context.pages) > before_count:
        newest = pick_best_page(context)
        try:
            state.current_tab = context.pages.index(newest)
        except Exception:
            state.current_tab = len(context.pages) - 1


def current_domain(page: Page) -> str:
    try:
        return urlparse(page.url).netloc.lower()
    except Exception:
        return ""


def looks_like_url(text: str) -> bool:
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return True
    if " " in text:
        return False
    return "." in text


def normalize_url(text: str) -> str:
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return text
    return f"https://{text}"


def safe_title(page: Page) -> str:
    try:
        return page.title() or ""
    except Exception:
        return ""


def safe_text(page: Page, max_chars: int = 5000) -> str:
    try:
        text = page.evaluate("""() => (document.body ? (document.body.innerText || '') : '')""")
        text = " ".join((text or "").split())
        return text[:max_chars]
    except Exception:
        return ""


def open_known_or_search(page: Page, target: str) -> str:
    raw = target.strip()
    lower = raw.lower()

    if lower in KNOWN_SITES:
        page.goto(KNOWN_SITES[lower], wait_until="load", timeout=30000)
        return f"opened {lower}"

    if looks_like_url(raw):
        page.goto(normalize_url(raw), wait_until="load", timeout=30000)
        return f"opened {raw}"

    page.goto(
        f"https://duckduckgo.com/?q={quote_plus(raw)}",
        wait_until="load",
        timeout=30000,
    )
    return f"searched DuckDuckGo for {raw}"


def search_ddg(page: Page, query: str) -> str:
    page.goto(
        f"https://duckduckgo.com/?q={quote_plus(query.strip())}",
        wait_until="load",
        timeout=30000,
    )
    return f"searched DuckDuckGo for {query.strip()}"


def search_within_site(page: Page, query: str) -> str:
    host = current_domain(page).replace("www.", "")
    scoped_query = f"site:{host} {query.strip()}"
    page.goto(
        f"https://duckduckgo.com/?q={quote_plus(scoped_query)}",
        wait_until="load",
        timeout=30000,
    )
    return f"searched {host} for {query.strip()}"


def ordinal_from_text(text: str) -> Optional[int]:
    lower = text.lower()

    for word, num in ORDINALS.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return num

    m = re.search(r"\b(\d+)(?:st|nd|rd|th)?\b", lower)
    return int(m.group(1)) if m else None


def open_result(context: BrowserContext, state: BrowserState, index: int) -> str:
    page = current_page(context, state)
    selectors = [
        "a[data-testid='result-title-a']",
        "article h2 a",
        "h2 a",
        "a:visible",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = loc.count()
            if count >= index:
                before = len(context.pages)
                loc.nth(index - 1).click(timeout=5000)
                page.wait_for_timeout(1200)
                switch_to_newest_page(context, state, before)
                return f"opened result {index}"
        except Exception:
            pass

    return f"could not open result {index}"


def click_text(context: BrowserContext, state: BrowserState, text: str) -> str:
    page = current_page(context, state)

    candidates = [
        page.get_by_role("link", name=text, exact=False),
        page.get_by_role("button", name=text, exact=False),
        page.get_by_text(text, exact=False),
    ]

    for locator in candidates:
        try:
            before = len(context.pages)
            locator.first.click(timeout=4000)
            page.wait_for_timeout(900)
            switch_to_newest_page(context, state, before)
            return f"clicked {text}"
        except Exception:
            pass

    return f"could not click {text}"


def focus_video_player(page: Page) -> None:
    for selector in ["#movie_player", "video", "body"]:
        try:
            page.locator(selector).first.click(timeout=1500)
            page.wait_for_timeout(200)
            return
        except Exception:
            pass


def youtube_next_prev(page: Page, command: str) -> str:
    focus_video_player(page)

    if command == "next":
        selectors = [
            "button.ytp-next-button",
            "a.ytp-next-button",
            'button[aria-keyshortcuts="SHIFT+n"]',
            'a[aria-keyshortcuts="SHIFT+n"]',
            'button[aria-label*="Next"]',
            'a[aria-label*="Next"]',
        ]
    else:
        selectors = [
            "button.ytp-prev-button",
            "a.ytp-prev-button",
            'button[aria-keyshortcuts="SHIFT+p"]',
            'a[aria-keyshortcuts="SHIFT+p"]',
            'button[aria-label*="Previous"]',
            'a[aria-label*="Previous"]',
        ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                loc.first.click(timeout=2000)
                page.wait_for_timeout(1000)
                return f"{command} triggered"
        except Exception:
            pass

    try:
        if command == "next":
            page.keyboard.press("Shift+N")
            page.wait_for_timeout(1000)
            return "next triggered"
        else:
            page.keyboard.press("Shift+P")
            page.wait_for_timeout(1000)
            return "previous triggered"
    except Exception:
        return f"could not {command}"

def media_control(page: Page, command: str) -> str:
    cmd = command.lower().strip()
    host = current_domain(page)

    if ("youtube.com" in host or "music.youtube.com" in host) and cmd in {"next", "previous"}:
        return youtube_next_prev(page, cmd)

    try:
        result = page.evaluate(
            """(cmd) => {
                const norm = s => (s || '').trim().toLowerCase();
                const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));

                const clickByHints = (hints) => {
                  for (const el of buttons) {
                    const bag = [
                      el.innerText,
                      el.textContent,
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('aria-description')
                    ].map(norm).join(' ');

                    if (hints.some(h => bag.includes(h))) {
                      el.click();
                      return true;
                    }
                  }
                  return false;
                };

                const v = document.querySelector('video');

                if (cmd === 'pause') {
                  if (v) { v.pause(); return 'video paused'; }
                  if (clickByHints(['pause'])) return 'pause clicked';
                }

                if (cmd === 'play' || cmd === 'resume') {
                  if (v) { v.play().catch(() => {}); return 'video playing'; }
                  if (clickByHints(['play', 'resume'])) return 'play clicked';
                }

                if (cmd === 'mute') {
                  if (v) { v.muted = true; return 'video muted'; }
                  if (clickByHints(['mute'])) return 'mute clicked';
                }

                if (cmd === 'unmute') {
                  if (v) { v.muted = false; return 'video unmuted'; }
                  if (clickByHints(['unmute'])) return 'unmute clicked';
                }

                if (cmd === 'fullscreen') {
                  if (v && v.requestFullscreen) {
                    v.requestFullscreen().catch(() => {});
                    return 'entered fullscreen';
                  }
                  if (clickByHints(['fullscreen', 'full screen'])) return 'fullscreen clicked';
                }

                if (cmd === 'exit fullscreen') {
                  if (document.fullscreenElement && document.exitFullscreen) {
                    document.exitFullscreen().catch(() => {});
                    return 'exited fullscreen';
                  }
                }

                return 'no media control found';
            }""",
            cmd,
        )

        if result and result != "no media control found":
            return result
    except Exception:
        pass

    if "youtube.com" in host:
        focus_video_player(page)
        keymap = {
            "pause": "k",
            "play": "k",
            "resume": "k",
            "mute": "m",
            "unmute": "m",
            "fullscreen": "f",
        }
        if cmd in keymap:
            try:
                page.keyboard.press(keymap[cmd])
                return f"sent YouTube shortcut for {cmd}"
            except Exception:
                pass

    return f"could not {cmd}"


def youtube_search_and_play(page: Page, query: str) -> str:
    page.goto(
        f"https://www.youtube.com/results?search_query={quote_plus(query)}",
        wait_until="load",
        timeout=30000,
    )
    page.wait_for_timeout(1800)

    for selector in ["a#video-title", "ytd-video-renderer a#video-title"]:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                loc.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                media_control(page, "play")
                return f"playing {query} on YouTube"
        except Exception:
            pass

    return f"found YouTube results but could not start {query}"


def site_aware_play(context: BrowserContext, state: BrowserState, query: str) -> str:
    page = current_page(context, state)
    host = current_domain(page)
    query = query.strip()

    if "youtube.com" in host:
        state.last_media_site = "youtube"
        return youtube_search_and_play(page, query)

    if "hotstar.com" in host or "netflix.com" in host or "primevideo.com" in host:
        search_within_site(page, query)
        opened = open_result(context, state, 1)
        page = current_page(context, state)
        media_control(page, "play")
        state.last_media_site = host
        return f"{opened}; tried to play {query} on {host}"

    state.last_media_site = "youtube"
    return youtube_search_and_play(page, query)


def list_tabs(context: BrowserContext, state: BrowserState) -> str:
    lines = []
    for i, p in enumerate(context.pages, start=1):
        marker = "*" if i - 1 == state.current_tab else " "
        lines.append(f"{marker} {i}. {safe_title(p) or '(untitled)'} — {p.url}")
    return "\n".join(lines) if lines else "no tabs"


def list_links(page: Page, limit: int = 12) -> str:
    try:
        data = page.evaluate(
            """(limit) => {
              const nodes = Array.from(document.querySelectorAll('a'));
              const out = [];

              for (const a of nodes) {
                const style = window.getComputedStyle(a);
                const rect = a.getBoundingClientRect();
                const text = (a.innerText || a.textContent || '').trim();
                const href = a.href || '';
                const visible =
                  style &&
                  style.visibility !== 'hidden' &&
                  style.display !== 'none' &&
                  rect.width > 0 &&
                  rect.height > 0;

                if (visible && (text || href)) out.push({text, href});
                if (out.length >= limit) break;
              }

              return out;
            }""",
            limit,
        )

        lines = []
        for i, item in enumerate(data, start=1):
            text = (item.get("text") or "").strip() or "[no text]"
            href = (item.get("href") or "").strip()
            lines.append(f"{i}. {text} — {href}")

        return "\n".join(lines) if lines else "no visible links found"
    except Exception:
        return "no visible links found"


def summarize_page(client: Client, page: Page) -> str:
    messages = [
        {"role": "system", "content": "You summarize web pages briefly and clearly."},
        {
            "role": "user",
            "content": (
                f"Summarize this page.\n"
                f"Title: {safe_title(page)}\n"
                f"URL: {page.url}\n"
                f"Content:\n{safe_text(page, 8000)}\n\n"
                f"Give me 1) what it is 2) main points 3) what matters most."
            ),
        },
    ]
    return stream_chat(client, messages)


def new_tab(context: BrowserContext, state: BrowserState, target: Optional[str] = None) -> str:
    page = context.new_page()
    state.current_tab = len(context.pages) - 1
    try:
        page.bring_to_front()
    except Exception:
        pass

    if target:
        return open_known_or_search(page, target)

    page.goto("https://duckduckgo.com", wait_until="load", timeout=30000)
    return "opened new tab"


def switch_tab(context: BrowserContext, state: BrowserState, num: int) -> str:
    if 1 <= num <= len(context.pages):
        state.current_tab = num - 1
        try:
            context.pages[state.current_tab].bring_to_front()
        except Exception:
            pass
        return f"switched to tab {num}"
    return f"tab {num} does not exist"


def close_tab(context: BrowserContext, state: BrowserState) -> str:
    if len(context.pages) <= 1:
        return "cannot close the last tab"

    current_page(context, state).close()
    state.current_tab = min(state.current_tab, len(context.pages) - 2)
    try:
        current_page(context, state).bring_to_front()
    except Exception:
        pass
    return "closed current tab"


def parse_command(text: str, browser_mode: bool) -> Tuple[Optional[str], Optional[str]]:
    raw = text.strip()
    lower = raw.lower()

    if lower in {"exit browser", "stop browser mode", "close browser mode"}:
        return "exit_browser", None

    if lower in {"browser mode", "enter browser mode"}:
        return "enter_browser", None

    if lower in {"new tab", "open new tab"}:
        return "new_tab", ""

    if lower.startswith("new tab "):
        return "new_tab", raw[8:].strip()

    m = re.match(r"^(?:switch to|open)\s+tab\s+(\d+)$", lower)
    if m:
        return "switch_tab", m.group(1)

    if lower == "close tab":
        return "close_tab", None

    if lower in {"list tabs", "show tabs"}:
        return "list_tabs", None

    if lower in {"list links", "show links"}:
        return "list_links", None

    if lower in {"go back", "back"}:
        return "back", None

    if lower in {"go forward", "forward"}:
        return "forward", None

    if lower == "reload":
        return "reload", None

    if lower in {"pause", "play", "resume", "mute", "unmute", "fullscreen", "exit fullscreen"}:
        return "media", lower

    if lower in {"next song", "next video", "next", "skip"}:
        return "media", "next"

    if lower in {"previous song", "previous video", "previous", "prev"}:
        return "media", "previous"

    if lower in {"read page", "read this page"}:
        return "read_page", None

    if lower in {"summarize page", "summarize this page", "summarise page", "summarise this page"}:
        return "summarize_page", None

    m = re.match(r"^open\s+(?:the\s+)?(.+?)\s+(?:result|link)$", lower)
    if m:
        n = ordinal_from_text(m.group(1))
        if n:
            return "open_result", str(n)

    m = re.match(r"^open\s+result\s+(\d+)$", lower)
    if m:
        return "open_result", m.group(1)

    if lower.startswith("click "):
        return "click_text", raw[6:].strip()

    if lower.startswith("scroll down"):
        return "scroll", "down"

    if lower.startswith("scroll up"):
        return "scroll", "up"

    if lower.startswith("search in browser and play "):
        return "play_query", raw[len("search in browser and play "):].strip()

    if browser_mode and lower.startswith("play "):
        return "play_query", raw[5:].strip()

    if lower.startswith("search in browser"):
        return "search", raw[len("search in browser"):].strip()

    if lower.startswith("search this in browser"):
        return "search", raw[len("search this in browser"):].strip()

    if lower.startswith("browser search"):
        return "search", raw[len("browser search"):].strip()

    if lower.startswith("open in browser"):
        return "open", raw[len("open in browser"):].strip()

    if lower.startswith("browser open"):
        return "open", raw[len("browser open"):].strip()

    if lower.startswith("go to in browser"):
        return "open", raw[len("go to in browser"):].strip()

    if lower.startswith("open "):
        target = raw[5:].strip()
        if target.lower() in KNOWN_SITES or looks_like_url(target):
            return "open", target
        if browser_mode:
            return "open", target

    if browser_mode and lower.startswith("search "):
        return "search", raw[7:].strip()

    return None, None


def handle_browser_command(
    client: Client,
    context: BrowserContext,
    state: BrowserState,
    user_text: str,
) -> Tuple[bool, str]:
    cmd, arg = parse_command(user_text, state.browser_mode)

    if cmd is None:
        return False, "not a browser command"

    if cmd == "exit_browser":
        state.browser_mode = False
        return True, "Browser mode off"

    if cmd == "enter_browser":
        state.browser_mode = True
        return True, "Browser mode on"

    state.browser_mode = True
    page = current_page(context, state)

    if cmd == "new_tab":
        return True, new_tab(context, state, arg or None)

    if cmd == "switch_tab":
        return True, switch_tab(context, state, int(arg))

    if cmd == "close_tab":
        return True, close_tab(context, state)

    if cmd == "list_tabs":
        return True, list_tabs(context, state)

    if cmd == "list_links":
        return True, list_links(page)

    if cmd == "back":
        page.go_back(wait_until="load", timeout=30000)
        return True, "went back"

    if cmd == "forward":
        page.go_forward(wait_until="load", timeout=30000)
        return True, "went forward"

    if cmd == "reload":
        page.reload(wait_until="load", timeout=30000)
        return True, "reloaded page"

    if cmd == "media":
        return True, media_control(page, arg)

    if cmd == "read_page":
        return True, f"TITLE: {safe_title(page)}\nURL: {page.url}\n\n{safe_text(page, 2500)}"

    if cmd == "summarize_page":
        return True, summarize_page(client, page)

    if cmd == "open_result":
        return True, open_result(context, state, int(arg))

    if cmd == "click_text":
        return True, click_text(context, state, arg)

    if cmd == "scroll":
        page.mouse.wheel(0, 1400 if arg == "down" else -1400)
        page.wait_for_timeout(600)
        return True, f"scrolled {arg}"

    if cmd == "search":
        return True, search_ddg(page, arg)

    if cmd == "open":
        return True, open_known_or_search(page, arg)

    if cmd == "play_query":
        return True, site_aware_play(context, state, arg)

    return True, "command recognized but not implemented"


def main():
    client = Client()
    state = BrowserState()

    try:
        playwright, browser, context, page = connect_existing_chrome()
    except Exception as e:
        print(f"Failed to connect to Chrome: {e}")
        print("Start Chrome first with remote debugging enabled.")
        sys.exit(1)

    try:
        try:
            if page.url in ("", "about:blank", "chrome://newtab/"):
                page.goto("https://duckduckgo.com", wait_until="load", timeout=30000)
        except Exception:
            pass

        print("Jarvis terminal ready.")
        print("Connected to existing Chrome via CDP.")
        print()
        print("Examples:")
        print("  open youtube")
        print("  play hi nanna songs")
        print("  next song")
        print("  pause")
        print("  open new tab")
        print("  open hotstar")
        print("  play game of thrones")
        print("  exit browser")
        print("  what is event loop in javascript")
        print()

        while True:
            try:
                user_text = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...")
                break

            if not user_text:
                continue

            if user_text.lower() in {"exit", "quit"}:
                break

            handled, result = handle_browser_command(client, context, state, user_text)

            if handled:
                print(f"\n[Browser] {result}\n")
                if result and not result.startswith("TITLE:"):
                    speak(result)
                continue

            reply = ask_normal(client, state, user_text)
            if reply:
                speak(reply)

    finally:
        # Keep Chrome running; just detach Playwright.
        playwright.stop()


if __name__ == "__main__":
    main()