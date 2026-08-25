"""探测根层回答后的概念 chip 真实 DOM，找到可下钻元素的选择器。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})
        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1)

        # 开新树
        await (await page.query_selector('input[placeholder*="提一个问题"]')).fill('什么是梯度下降？')
        await (await page.query_selector('button:has-text("开新树")')).click()
        print("等待根层回答+概念...")

        # 等回答完成 + concepts 事件抵达
        for i in range(30):
            await asyncio.sleep(2)
            body = await page.inner_text('body')
            if '梯度下降' in body and i > 4:
                break

        # 多等一会让概念 chip 渲染
        await asyncio.sleep(3)

        # 探测所有 title 属性
        titled = await page.query_selector_all('[title]')
        print(f"\n[title 元素] {len(titled)} 个:")
        for el in titled[:15]:
            t = await el.get_attribute('title')
            tag = await el.evaluate('e => e.tagName')
            txt = (await el.text_content() or '').strip()[:30]
            print(f"  <{tag} title='{t}'> {txt}")

        # 探测 inline chip 样式（active-soft）
        inline = await page.query_selector_all('span[style*="active"]')
        print(f"\n[span style含active] {len(inline)} 个:")
        for el in inline[:10]:
            txt = (await el.text_content() or '').strip()[:30]
            print(f"  '{txt}'")

        # 探测 button chip（TIER_COLOR）
        btns = await page.query_selector_all('button')
        print(f"\n[button] {len(btns)} 个:")
        for b in btns[:15]:
            t = (await b.text_content() or '').strip()[:25]
            dis = await b.is_disabled()
            print(f"  '{t}' disabled={dis}")

        # body 文本片段
        body = await page.inner_text('body')
        print(f"\n[body 片段] ...{body[200:600]}...")

        await page.screenshot(path='/tmp/drill_probe.png')
        await browser.close()

asyncio.run(main())
