"""浏览器端到端验证：截图 starMindAgent 前端新功能。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})
        # 收集 console 错误
        errors = []
        page.on('console', lambda msg: errors.append(f"{msg.type}: {msg.text}") if msg.type in ('error', 'warning') else None)
        page.on('pageerror', lambda e: errors.append(f"pageerror: {e}"))

        await page.goto('http://localhost:5173', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        import os
        out_dir = os.path.expanduser('~/AppData/Local/Temp/starMindAgent_verify')
        os.makedirs(out_dir, exist_ok=True)

        # 截图1：初始探索视图
        await page.screenshot(path=os.path.join(out_dir, 's1_explore_initial.png'))
        print("===s1: 探索视图初始===")

        # 验证 nav 按钮存在
        nav_btns = await page.query_selector_all('button')
        nav_texts = [await b.text_content() for b in nav_btns]
        nav_texts = [t.strip() if t else '' for t in nav_texts]
        print(f"按钮: {nav_texts[:8]}")
        has_archive = any('档案' in t for t in nav_texts)
        has_explore = any('探索' in t for t in nav_texts)
        print(f"探索按钮: {has_explore}, 档案按钮: {has_archive}")

        # 切到档案视图
        if has_archive:
            await page.click('button:has-text("档案")')
            await asyncio.sleep(2)
            await page.screenshot(path=os.path.join(out_dir, 's2_memory_view.png'))
            print("===s2: 档案视图===")
            # 检查画像卡/学习足迹
            memory_text = await page.inner_text('body')
            print(f"档案页含'学习画像': {'学习画像' in memory_text}")
            print(f"档案页含'学习足迹': {'学习足迹' in memory_text}")

        # 切回探索，输入问题
        await page.click('button:has-text("探索")')
        await asyncio.sleep(1)
        qa_input = await page.query_selector('input[placeholder*="新问题"]')
        if qa_input:
            await qa_input.fill('什么是梯度下降？')
            await asyncio.sleep(1)
            await page.screenshot(path=os.path.join(out_dir, 's3_question_typed.png'))
            print("===s3: 输入问题===")
            # 点开新树按钮
            start_btn = await page.query_selector('button:has-text("开新树")')
            if start_btn:
                await start_btn.click()
                print("===点击开新树,等待 SSE 流===")
                # 等待回答+概念+层摘要（glm-5.2 约 10-30s）
                waited = 0
                for i in range(20):
                    await asyncio.sleep(3)
                    waited += 3
                    body = await page.inner_text('body')
                    if '←' in body or '未抽取' in body or '即将' in body or waited >= 45:
                        break
                await page.screenshot(path=os.path.join(out_dir, 's4_answer_concepts.png'))
                print(f"===s4: 回答+概念 (等待{waited}s)===")
                # 检查层摘要
                has_summary = '理解' in body or '这层' in body
                print(f"含层摘要: {has_summary}")
                # 检查概念 chip
                chips = await page.query_selector_all('button')
                chip_count = 0
                for b in chips:
                    t = await b.text_content()
                    if t and len(t.strip()) < 20 and t.strip() not in ('探索', '档案', '开新树', '本会话', '全局', '合并', '取消'):
                        chip_count += 1
                print(f"chip 数: {chip_count}")

        # 验证概念图工具栏（三状态切换/搜索/全局）
        graph_btns = await page.query_selector_all('button')
        graph_texts = [await b.text_content() for b in graph_btns]
        graph_texts = [t.strip() if t else '' for t in graph_texts]
        has_state1 = any('状态一' in t for t in graph_texts)
        has_state2 = any('状态二' in t for t in graph_texts)
        has_state3 = any('状态三' in t for t in graph_texts)
        has_global = any('全局' in t for t in graph_texts)
        print(f"三状态按钮: {has_state1}/{has_state2}/{has_state3}, 全局: {has_global}")

        search_input = await page.query_selector('input[placeholder*="搜索"]')
        print(f"搜索框存在: {search_input is not None}")

        await page.screenshot(path=os.path.join(out_dir, 's5_concept_graph.png'))
        print("===s5: 概念图===")

        # —— 关键功能 DOM 断言 ——
        # 层摘要折叠预览块（layerSummary 样式：斜体灰底，含"理解"关键词）
        body_text = await page.inner_text('body')
        has_layer_summary = '理解' in body_text and ('梯度' in body_text or '下降' in body_text)
        print(f"层摘要文本: {has_layer_summary}")
        # 用 CSS 选择器找 layerSummary 样式块（borderLeft 2px solid #d1d5db 的 div）
        ls_blocks = await page.query_selector_all('div[style*="border-left: 2px"]')
        if not ls_blocks:
            ls_blocks = await page.query_selector_all('div[style*="borderLeft"]')
        print(f"层摘要样式块: {len(ls_blocks)} 个")
        # 概念 chip（带 × 删除按钮的 chipWrap）
        del_btns = await page.query_selector_all('button')
        del_count = 0
        for b in del_btns:
            t = await b.text_content()
            if t and t.strip() == '×':
                del_count += 1
        print(f"纠标注×删除按钮: {del_count} 个")
        # +补抽按钮
        add_btns = await page.query_selector_all('button')
        add_count = 0
        for b in add_btns:
            t = await b.text_content()
            if t and '补抽' in t:
                add_count += 1
                break
        print(f"补抽按钮: {add_count > 0}")

        print("\n===CONSOLE ERRORS===")
        for e in errors[:10]:
            print(e)

        await browser.close()

asyncio.run(main())
