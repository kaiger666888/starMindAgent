"""端到端验证「开新树」流程：点击 → SSE 流式回答 → 概念渲染。

聚焦验证之前"点击没反应"的根因是否已消除：
- 前端 startNewTree 不再因 ReferenceError 静默中断
- 后端 /qa/start 不再 500（PG 已起）
- SSE 流式推送 answer_delta / concepts / done 正常抵达前端并渲染
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

        # 收集 console + pageerror + 网络请求
        logs = []
        page.on('console', lambda m: logs.append(f"{m.type}: {m.text}"))
        page.on('pageerror', lambda e: logs.append(f"pageerror: {e}"))

        # 监听 /qa/start 响应
        start_responses = {}
        async def on_response(resp):
            if '/qa/start' in resp.url and resp.request.method == 'POST':
                try:
                    start_responses['status'] = resp.status
                    start_responses['body'] = await resp.text()
                except Exception as e:
                    start_responses['err'] = str(e)
        page.on('response', on_response)

        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1.5)
        await page.screenshot(path=os.path.join(OUT_DIR, 'e2e_1_initial.png'))

        # 填问题（placeholder 实测为"提一个问题…"）
        qa_input = await page.query_selector('input[placeholder*="提一个问题"]')
        assert qa_input, "找不到问题输入框"
        await qa_input.fill('什么是梯度下降？')
        await asyncio.sleep(0.5)

        # 点开新树
        start_btn = await page.query_selector('button:has-text("开新树")')
        assert start_btn, "找不到'开新树'按钮"
        await start_btn.click()
        print("[1] 已点击开新树，等待 SSE 流式回答...")

        # 等待回答正文出现（answer_delta 累积到 DOM）
        body_text = ''
        waited = 0
        for i in range(40):
            await asyncio.sleep(2)
            waited += 2
            body_text = await page.inner_text('body')
            # done 信号：loading 消失 或 出现概念 chip 或 出现层摘要关键词
            if '梯度下降' in body_text and waited > 8:
                # 等概念或层摘要渲染
                if waited > 20 or '理解' in body_text or '相关概念' in body_text:
                    break
            if waited >= 70:
                break

        await page.screenshot(path=os.path.join(OUT_DIR, 'e2e_2_answered.png'))
        print(f"[2] 等待 {waited}s，回答已渲染")

        # —— 断言 ——
        print("\n===ASSERTIONS===")
        print(f"[后端 /qa/start 状态] {start_responses.get('status')} (期望 200)")
        qa_body = start_responses.get('body', '')
        has_qa_id = '"qa_id"' in qa_body
        print(f"[start 返回含 qa_id] {has_qa_id}")
        print(f"[页面含回答'梯度下降'] {'梯度下降' in body_text}")
        # 概念渲染：页面应出现相关概念名
        has_concept = any(k in body_text for k in ['梯度', '损失函数', '神经网络', '反向传播'])
        print(f"[页面含概念词] {has_concept}")

        # 收集 startNewTree 相关 console
        start_logs = [l for l in logs if 'startNewTree' in l]
        print(f"\n[startNewTree console 日志] {len(start_logs)} 条")
        for l in start_logs[:6]:
            print(f"  {l}")
        # 是否有 ReferenceError（之前的根因）
        ref_errors = [l for l in logs if 'ReferenceError' in l or 'activeSessionId' in l]
        print(f"[ReferenceError 残留] {len(ref_errors)} 条（期望 0）")
        for l in ref_errors[:3]:
            print(f"  {l}")

        # 所有 pageerror
        perrors = [l for l in logs if l.startswith('pageerror')]
        print(f"[pageerror 总数] {len(perrors)}")
        for l in perrors[:5]:
            print(f"  {l}")

        print("\n===截图已保存===")
        print(f"  {OUT_DIR}\\e2e_1_initial.png")
        print(f"  {OUT_DIR}\\e2e_2_answered.png")

        await browser.close()

asyncio.run(main())
