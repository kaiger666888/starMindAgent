"""快速诊断：打开页面，截图，dump 所有 input/textarea 的 placeholder/属性。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})
        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1.5)
        await page.screenshot(path='/tmp/initial_dom.png')

        # dump inputs/textareas
        els = await page.query_selector_all('input, textarea')
        print(f"找到 {len(els)} 个 input/textarea")
        for i, el in enumerate(els):
            tag = await el.evaluate('e => e.tagName')
            ph = await el.get_attribute('placeholder') or '(无placeholder)'
            val = await el.input_value() if tag == 'INPUT' else ''
            vis = await el.is_visible()
            print(f"  [{i}] {tag} ph='{ph}' val='{val}' visible={vis}")

        # dump 所有 button 文本
        btns = await page.query_selector_all('button')
        print(f"\n找到 {len(btns)} 个 button")
        for i, b in enumerate(btns):
            t = (await b.text_content() or '').strip()
            vis = await b.is_visible()
            print(f"  [{i}] '{t}' visible={vis}")

        await browser.close()

asyncio.run(main())
