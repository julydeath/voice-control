# Jarvis Terminal – Browser Mode Notes

Simple browser follow-ups are handled **locally**, not by the model. Once browser mode is active, commands like `pause`, `play`, `open second result`, `switch to tab 2`, and `summarize this page` go straight to **Playwright** instead of falling back to normal chat.

## Key Capabilities

- Playwright can run a visible browser with `headless=False`
- Manage multiple pages/tabs
- Use resilient role/text locators and keyboard input
- DuckDuckGo search works cleanly with `?q=...`
- The Ollama Python client supports streamed output with `stream=True`

## Installation & Setup

```bash
python3 -m pip install playwright ollama
python3 -m playwright install chromium
python3 jarvis_terminal.py
```

## Example Commands

```
search in browser next js basic videos
open first result
pause
play
mute
unmute
list tabs
switch to tab 1
summarize this page
exit browser
what is the difference between SSR and CSR
```
