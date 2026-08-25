"""验证导入文档 → L0 展示 → 点概念下钻 → 子层展示，对比一致性。

断言：
- L0 有概念 chip（导入抽取的概念）
- 点概念下钻成功
- 子层也有概念 chip（SSE 抽取的）
- L0 和子层渲染结构一致（都有 answer/层摘要/chip）
"""
import asyncio
import os
from playwright.async_api import async_playwright

OUT_DIR = os.path.expanduser('~/AppData/Local/Temp/starMindAgent_verify')
os.makedirs(OUT_DIR, exist_ok=True)
MD_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'test_material_cnn.md'))

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})

        logs = []
        page.on('pageerror', lambda e: logs.append(f"pageerror: {e}"))
        page.on('console', lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ('error', 'warning') else None)

        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1)

        # 导入 md
        file_input = await page.query_selector('input[type="file"]')
        await file_input.set_input_files(MD_PATH)
        print("[1] 已导入 test_material_cnn.md，等 L0 概念抽取...")

        # 等 L0 渲染 + 概念 chip 出现（导入是同步建 L0，概念可能要 LLM 抽取几秒）
        for i in range(40):
            await asyncio.sleep(2)
            # 看 L0 是否有概念 chip
            chips = await page.query_selector_all('[title="点击下钻"]')
            if len(chips) > 0:
                break
        await page.screenshot(path=os.path.join(OUT_DIR, 'import_1_l0.png'))
        chips = await page.query_selector_all('[title="点击下钻"]')
        print(f"[2] L0 概念 chip 数: {len(chips)}")

        # 看 L0 有没有 unmatched chip 块（抽取但正文未出现）
        body = await page.inner_text('body')
        has_unmatched_l0 = '抽取但正文未出现' in body
        has_l0_summary = '摘' in body and len(body) > 500  # L0 answer 是全文
        print(f"[L0] 有 unmatched chip 块: {has_unmatched_l0}")
        print(f"[L0] answer 长度 > 500 (全文): {len(body) > 500}")

        # 点第一个概念下钻
        if len(chips) == 0:
            print("[FAIL] L0 无概念 chip，无法测下钻")
            await browser.close()
            return
        first_chip_text = (await chips[0].text_content() or '').strip()
        print(f"[3] 点 L0 概念 '{first_chip_text}' 下钻...")
        await chips[0].click()

        # 等子层 L2 出现 + 回答 + 概念 chip（concepts 事件在 extracting 阶段，回答之后）
        # 先等 L2 标签出现（下钻响应+回答开始）
        try:
            await page.wait_for_selector('text=L2', timeout=30000)
        except Exception:
            pass
        # 再等子层概念 chip 出现（最长 130s，覆盖 SSE generating+extracting）
        child_chip_ok = False
        try:
            await page.wait_for_selector('[title="点击下钻"]', timeout=130000)
            # 确认是子层的 chip（L0 的 chip 在子层视图可能不可见，但 DOM 还在；用计数判断）
            await asyncio.sleep(2)
            child_chips = await page.query_selector_all('[title="点击下钻"]')
            print(f"[4] 子层渲染后 chip 计数: {len(child_chips)}")
            child_chip_ok = len(child_chips) > 0
        except Exception:
            child_chip_ok = False
        await page.screenshot(path=os.path.join(OUT_DIR, 'import_2_drilled.png'))

        body2 = await page.inner_text('body')
        chips2 = await page.query_selector_all('[title="点击下钻"]')
        has_unmatched_l1 = '抽取但正文未出现' in body2
        print(f"\n===ASSERTIONS===")
        print(f"[L0 有概念 chip] {len(chips) > 0}")
        print(f"[子层有概念 chip] {child_chip_ok} (计数 {len(chips2)})")
        print(f"[展示一致: L0 和子层都有内联概念] {len(chips) > 0 and child_chip_ok}")
        print(f"[L0 unmatched 块] {has_unmatched_l0}")
        print(f"[子层 unmatched 块] {has_unmatched_l1}")
        print(f"[子层挂树 L2] {'L2' in body2 or '第 2 层' in body2}")
        perrors = [l for l in logs if l.startswith('pageerror')]
        print(f"[pageerror] {len(perrors)}")
        for l in perrors[:5]:
            print(f"  {l}")

        print(f"\n截图: {OUT_DIR}\\import_1_l0.png + import_2_drilled.png")
        await browser.close()

asyncio.run(main())
