"""
PDF Generator: Convert HTML to PDF using Playwright.

Uses headless Chromium to render HTML with MathJax support.
Includes a simple browser pool so multiple PDFs can reuse browsers instead
of launching a new instance per file.
"""

from pathlib import Path
from typing import Optional
import asyncio

from playwright.async_api import async_playwright


class BrowserPool:
    """Manage a limited number of shared Playwright browsers."""

    def __init__(self, max_browsers: int = 1):
        self.max_browsers = max_browsers if max_browsers > 0 else 1
        self._playwright = None
        self._browser_queue: Optional[asyncio.Queue] = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser_queue = asyncio.Queue()

        for _ in range(self.max_browsers):
            browser = await self._playwright.chromium.launch()
            await self._browser_queue.put(browser)

        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._browser_queue:
            while not self._browser_queue.empty():
                browser = await self._browser_queue.get()
                await browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def generate_pdf(self, html_content: str, output_path: str, html_file_path: str = None) -> bool:
        """Render a PDF using one of the pooled browsers."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if not self._browser_queue:
            raise RuntimeError("BrowserPool must be used as an async context manager")

        browser = await self._browser_queue.get()
        try:
            page = await browser.new_page()

            if html_file_path:
                html_file_url = f"file://{Path(html_file_path).absolute()}"
                await page.goto(html_file_url)
            else:
                await page.set_content(html_content)

            await page.wait_for_load_state('networkidle')

            try:
                await page.wait_for_function(
                    'window.MathJax && window.MathJax.startup && window.MathJax.startup.promise',
                    timeout=10000
                )
                await page.evaluate('MathJax.startup.promise')
                await page.wait_for_timeout(1000)
            except Exception as e:
                print(f"      ⚠ MathJax timeout (continuing anyway): {e}")

            await page.pdf(
                path=output_path,
                format='Letter',
                margin={
                    'top': '0.75in',
                    'right': '0.75in',
                    'bottom': '0.75in',
                    'left': '0.75in'
                },
                print_background=True
            )

            await page.close()
            return True
        except Exception as e:
            print(f"      ✗ PDF generation failed: {e}")
            return False
        finally:
            await self._browser_queue.put(browser)


async def generate_pdf(html_content: str, output_path: str, html_file_path: str = None) -> bool:
    """
    Convert HTML to PDF using Playwright with MathJax support.

    This convenience wrapper spins up a one-browser pool to keep
    compatibility with existing call sites.
    """
    async with BrowserPool(max_browsers=1) as pool:
        return await pool.generate_pdf(html_content, output_path, html_file_path)


async def generate_pdf_batch(jobs: list) -> int:
    """
    Generate multiple PDFs in parallel using Playwright.

    Args:
        jobs: List of (html_content, output_path) tuples

    Returns:
        Number of successfully generated PDFs
    """
    async with BrowserPool(max_browsers=max(1, len(jobs))) as pool:
        tasks = [pool.generate_pdf(html, path) for html, path in jobs]
        results = await asyncio.gather(*tasks)
        return sum(results)
