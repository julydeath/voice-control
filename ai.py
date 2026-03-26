import re
import subprocess
from urllib.parse import quote_plus
from typing import List

from ollama import Client
from playwright.sync_api import sync_playwright

MODEL = "deepseek-v3.1:671b-cloud"
NORMAL_SYSTEM = (
    "You are a helpful terminal voice assistant. "
    "Be practical, concise, and useful. "
    "When explaining coding or software topics, teach clearly with examples."
)

ORDINAL_WORDS = {
    "first": 1,
    "1st": 1,
    "one": 1,
    "second": 2,
    "2nd": 2,
    "two": 2,
    "third": 3,
    "3rd": 3,
    "three": 3,
    "fourth": 4,
    "4th": 4,
    "five": 5,
    "fifth": 5,
}

EXPLICIT_BROWSER_PREFIXES = (
    "search in browser",
    "search this in browser",
    "open in browser",
    "browser search",
    "browser open",
    "browser mode",
    "go to in browser",
)

client = Client()


def speak(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    try:
        subprocess.Popen(["say", text])
    except Exception:
        pass


def stream_chat(messages: List[dict]) -> str:
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


def ask_normal(history: List[dict], user_text: str) -> str:
    history.append({"role": "user", "content": user_text})
    reply = stream_chat(history)
    history.append({"role": "assistant", "content": reply})
    if len(history) > 14:
        system = history[0]
        history[:] = [system] + history[-13:]
    return reply


def looks_like_url(text: str) -> bool:
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return True
    if " " in text:
        return False
    if "." in text:
        return True
    return False


def normalize_url(text: str) -> str:
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return text
    if not text.startswith("www.") and "." not in text:
        return f"https://{text}.com"
    return f"https://{text}"


def safe_title(page) -> str:
    try:
        return page.title() or ""
    except Exception:
        return ""


def safe_text(page, max_chars: int = 6000) -> str:
    try:
        text = page.evaluate(
            """
            () => {
              const body = document.body;
              return body ? (body.innerText || '') : '';
            }
            """
        )
        text = " ".join((text or "").split())
        return text[:max_chars]
    except Exception:
        return ""


def refresh_pages(state) -> None:
    pages = state["context"].pages
    if not pages:
        state["page"] = state["context"].new_page()
        state["current_tab"] = 0
        return
    idx = max(0, min(state.get("current_tab", 0), len(pages) - 1))
    state["page"] = pages[idx]
    state["current_tab"] = idx


def current_page(state):
    refresh_pages(state)
    return state["page"]


def maybe_switch_to_newest_page(state, before_count: int) -> None:
    pages = state["context"].pages
    if len(pages) > before_count:
        state["current_tab"] = len(pages) - 1
        state["page"] = pages[-1]


def ddg_search(page, query: str) -> str:
    page.goto(f"https://duckduckgo.com/?q={quote_plus(query)}", wait_until="load", timeout=30000)
    return f"searched DuckDuckGo for: {query}"


def open_target(page, target: str) -> str:
    target = target.strip()
    lower = target.lower()
    aliases = {
        "youtube": "https://www.youtube.com",
        "github": "https://github.com",
        "gmail": "https://mail.google.com",
        "duckduckgo": "https://duckduckgo.com",
        "google": "https://www.google.com",
        "stackoverflow": "https://stackoverflow.com",
    }
    if lower in aliases:
        url = aliases[lower]
    elif looks_like_url(target):
        url = normalize_url(target)
    else:
        return ddg_search(page, target)
    page.goto(url, wait_until="load", timeout=30000)
    return f"opened {url}"


def open_nth_visible_link(page, index: int, state) -> str:
    try:
        links = page.locator("a:visible")
        count = links.count()
        if count >= index:
            before = len(state["context"].pages)
            links.nth(index - 1).click(timeout=5000)
            page.wait_for_timeout(1200)
            maybe_switch_to_newest_page(state, before)
            return f"opened visible link {index}"
    except Exception:
        pass
    return f"could not open link {index}"


def open_search_result(page, index: int, state) -> str:
    selectors = [
        "a[data-testid='result-title-a']",
        "article h2 a",
        "h2 a",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = loc.count()
            if count >= index:
                before = len(state["context"].pages)
                loc.nth(index - 1).click(timeout=5000)
                page.wait_for_timeout(1200)
                maybe_switch_to_newest_page(state, before)
                return f"opened result {index}"
        except Exception:
            pass
    return open_nth_visible_link(page, index, state)


def list_visible_links(page, limit: int = 12) -> List[str]:
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
                const visible = style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
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
        return lines
    except Exception:
        return []


def click_text(page, text: str, state) -> str:
    candidates = [
        page.get_by_role("link", name=text, exact=False),
        page.get_by_role("button", name=text, exact=False),
        page.get_by_text(text, exact=False),
    ]
    for locator in candidates:
        try:
            before = len(state["context"].pages)
            locator.first.click(timeout=4000)
            page.wait_for_timeout(1000)
            maybe_switch_to_newest_page(state, before)
            return f"clicked: {text}"
        except Exception:
            pass
    return f"could not click: {text}"


def scroll_page(page, direction: str, amount: int = 1400) -> str:
    delta = amount if direction == "down" else -amount
    page.mouse.wheel(0, delta)
    page.wait_for_timeout(600)
    return f"scrolled {direction}"


def video_control(page, command: str) -> str:
    cmd = command.lower().strip()
    try:
        result = page.evaluate(
            """(cmd) => {
                const v = Array.from(document.querySelectorAll('video')).find(x => !!x) || document.querySelector('video');
                if (!v) return {ok:false, message:'no video element found'};
                if (cmd === 'pause') { v.pause(); return {ok:true, message:'video paused'}; }
                if (cmd === 'play' || cmd === 'resume') { v.play().catch(() => {}); return {ok:true, message:'video playing'}; }
                if (cmd === 'mute') { v.muted = true; return {ok:true, message:'video muted'}; }
                if (cmd === 'unmute') { v.muted = false; return {ok:true, message:'video unmuted'}; }
                if (cmd === 'fullscreen') {
                    if (v.requestFullscreen) { v.requestFullscreen().catch(() => {}); return {ok:true, message:'entered fullscreen'}; }
                    return {ok:false, message:'fullscreen not supported'};
                }
                if (cmd === 'exit fullscreen') {
                    if (document.fullscreenElement && document.exitFullscreen) { document.exitFullscreen().catch(() => {}); return {ok:true, message:'exited fullscreen'}; }
                    return {ok:false, message:'not in fullscreen'};
                }
                return {ok:false, message:'unknown video command'};
            }""",
            cmd,
        )
        if result.get("ok"):
            return result["message"]
    except Exception:
        pass

    fallback = {
        "pause": "k",
        "play": "k",
        "resume": "k",
        "mute": "m",
        "unmute": "m",
        "fullscreen": "f",
        "exit fullscreen": "f",
    }.get(cmd)

    if fallback:
        try:
            page.keyboard.press(fallback)
            return f"sent fallback key for {cmd}"
        except Exception as e:
            return f"video control failed: {e}"

    return "video control failed"


def summarize_current_page(page) -> str:
    content = safe_text(page, 8000)
    prompt = (
        f"Summarize this web page for me.\n\n"
        f"Title: {safe_title(page)}\n"
        f"URL: {page.url}\n"
        f"Content:\n{content}\n\n"
        f"Give me: 1) what it is 2) key points 3) what matters most."
    )
    messages = [
        {"role": "system", "content": "You summarize web pages clearly and briefly."},
        {"role": "user", "content": prompt},
    ]
    return stream_chat(messages)


def list_tabs(state) -> str:
    lines = []
    for i, p in enumerate(state["context"].pages, start=1):
        marker = "*" if i - 1 == state["current_tab"] else " "
        title = ""
        try:
            title = p.title()
        except Exception:
            title = "(loading)"
        lines.append(f"{marker} {i}. {title} — {p.url}")
    return "\n".join(lines) if lines else "no tabs"


def switch_tab(state, n: int) -> str:
    pages = state["context"].pages
    if 1 <= n <= len(pages):
        state["current_tab"] = n - 1
        state["page"] = pages[n - 1]
        state["page"].bring_to_front()
        return f"switched to tab {n}"
    return f"tab {n} does not exist"


def close_current_tab(state) -> str:
    pages = state["context"].pages
    if len(pages) <= 1:
        return "cannot close the last tab"
    idx = state["current_tab"]
    pages[idx].close()
    refresh_pages(state)
    state["page"].bring_to_front()
    return "closed current tab"


def new_tab(state, target: str = "") -> str:
    page = state["context"].new_page()
    state["page"] = page
    state["current_tab"] = len(state["context"].pages) - 1
    if target:
        return open_target(page, target)
    page.goto("https://duckduckgo.com", wait_until="load", timeout=30000)
    return "opened new tab"


def ordinal_from_text(text: str):
    for word, num in ORDINAL_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return num
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)?\b", text)
    if m:
        return int(m.group(1))
    return None


def parse_browser_command(text: str, browser_mode_active: bool):
    raw = text.strip()
    lower = raw.lower().strip()

    if lower in {"exit browser", "stop browser mode", "close browser mode"}:
        return ("exit_browser_mode", None)

    if browser_mode_active and lower in {"pause", "play", "resume", "mute", "unmute", "fullscreen", "exit fullscreen"}:
        return ("video", lower)

    if browser_mode_active and lower in {"go back", "back"}:
        return ("back", None)

    if browser_mode_active and lower in {"go forward", "forward"}:
        return ("forward", None)

    if browser_mode_active and lower == "reload":
        return ("reload", None)

    if browser_mode_active and lower in {"read page", "read this page"}:
        return ("read_page", None)

    if browser_mode_active and lower in {"summarize page", "summarise page", "summarize this page", "summarise this page"}:
        return ("summarize_page", None)

    if browser_mode_active and lower in {"list tabs"}:
        return ("list_tabs", None)

    if browser_mode_active and lower in {"list links", "show links"}:
        return ("list_links", None)

    if browser_mode_active and lower in {"current url"}:
        return ("current_url", None)

    if browser_mode_active and lower == "close tab":
        return ("close_tab", None)

    if browser_mode_active and lower == "new tab":
        return ("new_tab", "")

    if browser_mode_active and lower.startswith("new tab "):
        return ("new_tab", raw[8:].strip())

    m = re.match(r"^(?:switch to|open)\s+tab\s+(\d+)$", lower)
    if browser_mode_active and m:
        return ("switch_tab", int(m.group(1)))

    if browser_mode_active and lower.startswith("scroll down"):
        return ("scroll", ("down", 1400))

    if browser_mode_active and lower.startswith("scroll up"):
        return ("scroll", ("up", 1400))

    if browser_mode_active and lower.startswith("click "):
        return ("click_text", raw[6:].strip())

    m = re.match(r"^open\s+(?:the\s+)?(.+?)\s+(?:link|result)$", lower)
    if browser_mode_active and m:
        n = ordinal_from_text(m.group(1))
        if n:
            return ("open_result", n)

    m = re.match(r"^open\s+link\s+(\d+)$", lower)
    if browser_mode_active and m:
        return ("open_result", int(m.group(1)))

    if lower.startswith("search in browser and play "):
        return ("youtube_play", raw[len("search in browser and play "):].strip())

    if browser_mode_active and lower.startswith("play "):
        return ("youtube_play", raw[5:].strip())

    if lower.startswith("search in browser"):
        return ("search", raw[len("search in browser"):].strip())

    if lower.startswith("search this in browser"):
        return ("search", raw[len("search this in browser"):].strip())

    if lower.startswith("browser search"):
        return ("search", raw[len("browser search"):].strip())

    if lower.startswith("open in browser"):
        return ("open", raw[len("open in browser"):].strip())

    if lower.startswith("browser open"):
        return ("open", raw[len("browser open"):].strip())

    if lower.startswith("go to in browser"):
        return ("open", raw[len("go to in browser"):].strip())

    if browser_mode_active and lower.startswith("search "):
        return ("search", raw[7:].strip())

    if browser_mode_active and lower.startswith("open "):
        return ("open", raw[5:].strip())

    return (None, None)


def youtube_play(state, query: str) -> str:
    page = current_page(state)
    page.goto(f"https://www.youtube.com/results?search_query={quote_plus(query)}", wait_until="load", timeout=30000)
    page.wait_for_timeout(1800)
    candidates = [
        page.locator("a#video-title").first,
        page.locator("ytd-video-renderer a#video-title").first,
    ]
    for c in candidates:
        try:
            before = len(state["context"].pages)
            c.click(timeout=5000)
            page.wait_for_timeout(2500)
            maybe_switch_to_newest_page(state, before)
            current_page(state).evaluate(
                """() => {
                    const v = document.querySelector('video');
                    if (v) v.play().catch(() => {});
                }"""
            )
            return f"playing YouTube result for: {query}"
        except Exception:
            pass
    return f"found YouTube results, but could not start playback for: {query}"


def handle_browser_command(state, raw_text: str, browser_mode_active: bool):
    cmd, arg = parse_browser_command(raw_text, browser_mode_active)
    if cmd is None:
        return False, "not a browser command"

    if cmd == "exit_browser_mode":
        return True, "BROWSER_MODE_OFF"

    page = current_page(state)

    if cmd == "search":
        return True, ddg_search(page, arg)

    if cmd == "open":
        return True, open_target(page, arg)

    if cmd == "open_result":
        return True, open_search_result(page, arg, state)

    if cmd == "click_text":
        return True, click_text(page, arg, state)

    if cmd == "scroll":
        direction, amount = arg
        return True, scroll_page(page, direction, amount)

    if cmd == "back":
        page.go_back(wait_until="load", timeout=30000)
        return True, "went back"

    if cmd == "forward":
        page.go_forward(wait_until="load", timeout=30000)
        return True, "went forward"

    if cmd == "reload":
        page.reload(wait_until="load", timeout=30000)
        return True, "reloaded page"

    if cmd == "video":
        return True, video_control(page, arg)

    if cmd == "read_page":
        return True, f"TITLE: {safe_title(page)}\nURL: {page.url}\n\n{safe_text(page, 2500)}"

    if cmd == "summarize_page":
        return True, summarize_current_page(page)

    if cmd == "list_tabs":
        return True, list_tabs(state)

    if cmd == "switch_tab":
        return True, switch_tab(state, arg)

    if cmd == "close_tab":
        return True, close_current_tab(state)

    if cmd == "new_tab":
        return True, new_tab(state, arg)

    if cmd == "list_links":
        links = list_visible_links(page)
        return True, "\n".join(links) if links else "no visible links found"

    if cmd == "current_url":
        return True, page.url

    if cmd == "youtube_play":
        return True, youtube_play(state, arg)

    return True, "command recognized but not implemented"


def main():
    normal_history = [{"role": "system", "content": NORMAL_SYSTEM}]
    browser_mode_active = False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=120,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://duckduckgo.com", wait_until="load", timeout=30000)

        state = {"context": context, "page": page, "current_tab": 0}

        print("Jarvis terminal ready.")
        print("Normal mode examples:")
        print("  what is event loop in javascript")
        print("  teach me system design from beginner level")
        print()
        print("Browser mode examples:")
        print("  search in browser next js tutorials")
        print("  open second result")
        print("  pause")
        print("  play")
        print("  list tabs")
        print("  switch to tab 2")
        print("  summarize this page")
        print("  exit browser")
        print()

        while True:
            try:
                user_text = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...")
                break

            if not user_text:
                continue

            lower = user_text.lower().strip()
            if lower in {"exit", "quit"}:
                break

            explicit_browser = any(lower.startswith(prefix) for prefix in EXPLICIT_BROWSER_PREFIXES)
            handled, result = handle_browser_command(state, user_text, browser_mode_active)

            if explicit_browser:
                browser_mode_active = True

            if handled:
                if result == "BROWSER_MODE_OFF":
                    browser_mode_active = False
                    print("\n[Browser] mode off\n")
                    speak("Browser mode off")
                    continue

                if explicit_browser:
                    browser_mode_active = True

                print(f"\n[Browser] {result}\n")
                if isinstance(result, str) and result and not result.startswith("TITLE:"):
                    speak(result[:400])
                continue

            reply = ask_normal(normal_history, user_text)
            if reply:
                speak(reply[:700])

        browser.close()


if __name__ == "__main__":
    main()