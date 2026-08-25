"""端到端验证「概念下钻」——基于已验证的前端 SSE 链路。

用 CNN 问题（实测 80s 内完成），等概念 chip 出现，点击下钻，验证新层挂树。
"""
import asyncio
import os
from playwright.async_api import async_playwright

OUT_DIR = os.path.expanduser('~/AppData/Local/Temp/starMindAgent_verify')
os.makedirs(OUT_DIR, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})

        logs = []
        page.on('pageerror', lambda e: logs.append(f"pageerror: {e}"))

        drill_resp = {}
        async def on_response(resp):
            if '/drilldown' in resp.url and resp.request.method == 'POST':
                try:
                    drill_resp['status'] = resp.status
                    drill_resp['body'] = await resp.text()
                except Exception as e:
                    drill_resp['err'] = str(e)
        page.on('response', on_response)

        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1)
        await (await page.query_selector('input[placeholder*="提一个问题"]')).fill('什么是CNN？')
        await (await page.query_selector('button:has-text("开新树")')).click()
        print("[1] 开新树 CNN，等概念 chip（最长 150s）...")

        # 等概念 chip 出现
        await page.wait_for_selector('[title="点击下钻"]', timeout=150000)
        await asyncio.sleep(1)
        await page.screenshot(path=os.path.join(OUT_DIR, 'drill_1_root_answer.png'))
        print("[2] 根层概念 chip 已渲染")

        chip = await page.query_selector('[title="点击下钻"]')
        chip_text = (await chip.text_content() or '').strip()
        print(f"[3] 点击概念 chip：'{chip_text}' 下钻")
        await chip.click()

        # 等 drilldown 响应
        for i in range(20):
            await asyncio.sleep(2)
            if drill_resp.get('status'):
                break
        print(f"[4] drilldown 响应: {drill_resp.get('status')}")

        # 等新层 SSE 完成（等新层概念 chip 出现 或 L2 标签）
        print("[5] 等新层 SSE（最长 150s）...")
        try:
            await page.wait_for_selector('text=L2', timeout=15000)
            l2_ok = True
        except Exception:
            l2_ok = False
        # 再等新层概念 chip（下钻后子层也有概念）
        try:
            await page.wait_for_function(
                "() => document.querySelectorAll('[title=\"点击下钻\"]').length > 0 || document.title === 'loaded'",
                timeout=130000
            )
            # 实际上等新层回答完成更好用：body 文本增长
        except Exception:
            pass

        await asyncio.sleep(5)
        await page.screenshot(path=os.path.join(OUT_DIR, 'drill_2_drilled.png'))

        body = await page.inner_text('body')
        print("\n===ASSERTIONS===")
        print(f"[drilldown 状态] {drill_resp.get('status')} (期望 200)")
        db = drill_resp.get('body', '')
        print(f"[返回含 child qa_id+parent_qa_id] {'qa_id' in db and 'parent_qa_id' in db}")
        has_l2 = 'L2' in body or '第 2 层' in body
        print(f"[树出现 L2 子层] {has_l2}")
        perrors = [l for l in logs if l.startswith('pageerror')]
        print(f"[pageerror] {len(perrors)} (期望 0)")
        for l in perrors[:5]:
            print(f"  {l}")
        print(f"\n截图: {OUT_DIR}\\drill_2_drilled.png")
        await browser.close()

asyncio.run(main())
