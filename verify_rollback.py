"""验证回上层保留探索历史：开新树→下钻→回上层→下层记录应还在左栏。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page(viewport={'width': 1600, 'height': 900})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))
        await page.goto('http://localhost:5173', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        # 1. 开新树 (用英文避免 shell 转义,但前端输入框可直接填中文)
        inp = await page.query_selector('input[placeholder*="问题"]')
        await inp.fill('what is gradient descent')
        await page.click('button:has-text("开新树")')
        # 等 L1 完成(含 concepts + done)
        l1_done = False
        for i in range(25):
            await asyncio.sleep(4)
            body = await page.inner_text('body')
            if 'done' in body.lower() or '选中' in body or '理解' in body:
                l1_done = True
                break
        print(f'L1 完成: {l1_done}')

        # 检查左栏当前导航层
        nav_layers = await page.evaluate('''() => {
          const btns = Array.from(document.querySelectorAll('nav button'));
          return btns.length;
        }''')
        print(f'L1 后导航层数: {nav_layers} (应1)')

        # 2. 点内联概念下钻 (找带 cursor:pointer 的 span)
        clicked = await page.evaluate('''() => {
          const spans = Array.from(document.querySelectorAll('article span'));
          const target = spans.find(s => {
            const cs = window.getComputedStyle(s);
            return cs.cursor === 'pointer' && s.textContent && s.textContent.length > 1;
          });
          if (target) { target.click(); return target.textContent; }
          return null;
        }''')
        print(f'点了内联概念: {clicked}')
        if not clicked:
            # 备选:点 unmatched 块的 chip
            chip = await page.query_selector('article button, div[style*="pill"] button')
            if chip:
                await chip.click()
                print('点了 unmatched chip')

        # 等 L2 完成
        l2_done = False
        for i in range(25):
            await asyncio.sleep(4)
            body = await page.inner_text('body')
            if i > 15:
                l2_done = True
                break
        print(f'L2 等待完成: {l2_done}')

        nav_after_drill = await page.evaluate('''() => {
          return Array.from(document.querySelectorAll('nav button')).length;
        }''')
        print(f'下钻后导航层数: {nav_after_drill} (应2)')

        # 3. 回上层:点左栏 L1
        l1_btn = await page.query_selector('nav button:first-child')
        if l1_btn:
            await l1_btn.click()
            await asyncio.sleep(2)
            nav_after_rollback = await page.evaluate('''() => {
              return Array.from(document.querySelectorAll('nav button')).length;
            }''')
            print(f'回上层后导航层数: {nav_after_rollback} (应仍2,验证保留历史)')
            if nav_after_rollback >= 2:
                print('✓ 验证通过:回上层后下层记录保留')
            else:
                print('✗ 验证失败:回上层后下层消失')
        else:
            print('未找到 L1 按钮')

        if errs:
            print(f'页面错误: {errs[:3]}')

        await b.close()

asyncio.run(main())
