"""Read currently listed creators from the three public live directories in isolated Chrome tabs."""
import asyncio
from urllib.parse import urlsplit

DIRECTORIES = {
    "twitch": "https://www.twitch.tv/directory/category/grand-theft-auto-vi",
    "kick": "https://kick.com/category/grand-theft-auto-vi",
    "youtube": "https://www.youtube.com/results?search_query=GTA+VI+gameplay&sp=EgJAAQ%253D%253D",
}

async def directory(platform):
    from playwright.async_api import async_playwright
    from .hermes_live import channel_url
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(channel="chrome")
        try:
            page = await browser.new_page()
            response = await page.goto(DIRECTORIES[platform], wait_until="domcontentloaded", timeout=45000)
            if not response or response.status >= 400:
                raise ValueError(f"Directorio inaccesible (HTTP {response.status if response else 'sin respuesta'})")
            selector = {"youtube": "ytd-video-renderer ytd-channel-name a[href]",
                        "twitch": 'a[data-a-target="preview-card-channel-link"], a[data-a-target="preview-card-image-link"]',
                        "kick": 'main a[href]'}[platform]
            channels = set()
            previous = -1
            for _ in range(16):
                await asyncio.sleep(2)
                hrefs = await page.locator(selector).evaluate_all("nodes => nodes.map(a => a.href)")
                host = {"youtube": "www.youtube.com", "twitch": "twitch.tv", "kick": "kick.com"}[platform]
                channels.update(u for h in hrefs if (u := channel_url(h)) and urlsplit(u).hostname == host)
                height = await page.evaluate("document.documentElement.scrollHeight")
                if height == previous:
                    break
                await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                previous = height
            if not channels and await page.get_by_text("Verify you are human", exact=False).count():
                raise ValueError("El directorio solicitó verificación")
            return channels
        finally:
            await browser.close()
