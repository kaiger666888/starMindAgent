"""探测前端 EventSource SSE 是否连上并收到数据。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})

        sse_events = []
        async def on_request(req):
            if '/stream' in req.url:
                sse_events.append(f"REQUEST: {req.url}")
        page.on('request', on_request)

        # 监听 SSE 响应流
        async def on_response(resp):
            if '/stream' in resp.url:
                sse_events.append(f"RESPONSE: {resp.status} type={resp.headers.get('content-type','?')}")
        page.on('response', on_response)

        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1)
        await (await page.query_selector('input[placeholder*="提一个问题"]')).fill('什么是CNN？')
        await (await page.query_selector('button:has-text("开新树")')).click()
        print("已点击开新树，监听 SSE...")

        # 等 90s 看 SSE 是否有数据抵达前端
        for i in range(45):
            await asyncio.sleep(2)
            body = await page.inner_text('body')
            # 看是否有回答内容
            if 'CNN' in body and i > 3:
                print(f"[{i*2}s] body 含 CNN")
            if i % 10 == 9:
                print(f"[{i*2}s] SSE 事件: {len(sse_events)} 条")
                for e in sse_events[-3:]:
                    print(f"  {e}")

        # 看 body 文本长度（回答是否渲染）
        body = await page.inner_text('body')
        print(f"\n最终 body 长度: {len(body)}")
        print(f"含 'CNN': {'CNN' in body}")
        print(f"含 '卷积': {'卷积' in body}")
        print(f"含 '点击下钻': {'点击下钻' in body}")
        print(f"含 '生成中/落笔中': {'生成中' in body or '落笔中' in body}")

        await browser.close()

asyncio.run(main())
