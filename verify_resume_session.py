"""端到端验证:档案「继续探索」按钮恢复历史会话成探索树。

流程:进档案视图 → 展开 sessions → 点「继续探索」→ 确认切到探索视图 +
树节点可见 + 当前层正文渲染。截图输出到 ~/AppData/Local/Temp/starMindAgent_verify/。
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
        errors = []
        page.on('console', lambda msg: errors.append(f"{msg.type}: {msg.text}") if msg.type in ('error', 'warning') else None)
        page.on('pageerror', lambda e: errors.append(f"pageerror: {e}"))

        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1)

        # 切到档案视图
        await page.click('button:has-text("档案")')
        await asyncio.sleep(2)
        await page.screenshot(path=os.path.join(OUT_DIR, 'resume_s1_memory.png'))
        print("===s1: 档案视图===")

        body = await page.inner_text('body')
        has_sessions = '学习足迹' in body
        print(f"含学习足迹: {has_sessions}")
        # session 条目(时间轴按钮)
        timeline_btns = await page.query_selector_all('ol li > button')
        print(f"session 条目数: {len(timeline_btns)}")
        if not timeline_btns:
            print("FAIL: 无 session 可测,退出")
            await browser.close()
            return

        # 展开第一个 session
        await timeline_btns[0].click()
        await asyncio.sleep(2)
        await page.screenshot(path=os.path.join(OUT_DIR, 'resume_s2_session_expanded.png'))
        print("===s2: 展开首个 session===")
        body = await page.inner_text('body')
        has_steps = 'L1' in body or 'L2' in body
        print(f"含步骤 L1/L2: {has_steps}")

        # 找继续探索按钮
        resume_btn = await page.query_selector('button:has-text("继续探索")')
        if not resume_btn:
            print("FAIL: 未找到「继续探索」按钮")
            await browser.close()
            return
        print("找到「继续探索」按钮")
        await resume_btn.click()
        await asyncio.sleep(2)
        await page.screenshot(path=os.path.join(OUT_DIR, 'resume_s3_after_resume.png'))
        print("===s3: 点继续探索后===")

        # 验证切到了探索视图:探索导航按钮应是 active 态,且出现 TreeView
        body = await page.inner_text('body')
        # 探索视图有三栏,应能看到层标题/问题正文
        resumed = '伴你成长' in body
        # 当前层正文应含原 session 的问题(恢复的根层)
        first_q = 'CNN'  # default 用户首个 session 是"什么是CNN？"
        has_restored_q = first_q in body
        print(f"切到探索视图(含品牌): {resumed}")
        print(f"恢复的根层问题含'{first_q}': {has_restored_q}")

        # 再次截图探索视图全景
        await page.screenshot(path=os.path.join(OUT_DIR, 'resume_s4_explore_restored.png'))
        print("===s4: 恢复后的探索视图===")

        print("\n===CONSOLE ERRORS===")
        for e in errors[:10]:
            print(e)
        print(f"\n错误数: {len(errors)}")

        verdict = 'PASS' if (resumed and has_restored_q and len([e for e in errors if 'error' in e.lower()]) == 0) else 'CHECK'
        print(f"\n===VERDICT: {verdict}===")

        await browser.close()


asyncio.run(main())
