simple browser follow-ups are handled locally, not by the model. So once browser mode is active, commands like pause, play, open second result, switch to tab 2, and summarize this page go straight to Playwright instead of falling back to normal chat. Playwright can run a visible browser with headless=False, manage multiple pages/tabs, and use resilient role/text locators and keyboard input; DuckDuckGo search works cleanly with ?q=...; and the Ollama Python client supports streamed output with stream=True.

Run it with:

python3 -m pip install playwright ollama
python3 -m playwright install chromium
python3 jarvis_terminal.py

Use it like this:

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
