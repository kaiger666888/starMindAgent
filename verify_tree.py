"""验证树状分支结构：L1 点A 加 L2(A)，回 L1 点B 加 L2(B) 兄弟分支，不替换。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page(viewport={'width': 1600, 'height': 900})
        await page.goto('http://localhost:5173', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        # 纯 store 端到端验证：模拟树状分支
        result = await page.evaluate('''async () => {
          const store = await import('/src/store/qaStore.js');
          // L1 根
          store.resetStack();
          store.setRoot({qa_id: 'L1', question: 'q1', answer: 'a1', concepts: [], layer_summary: '', loading: false, status: 'waiting'});
          // L1 点A → 加子层 L2A
          store.addChildLayer('L1', {qa_id: 'L2A', question: 'A', answer: 'aA', concepts: [], layer_summary: '', loading: false, status: 'waiting'});
          const afterA = {path: [...store.getState().currentPath], depth: store.getState().currentPath.length};
          // 回 L1
          store.popToLayer('L1');
          const afterBack = {path: [...store.getState().currentPath], depth: store.getState().currentPath.length};
          // L1 点B → 加子层 L2B（兄弟分支，L2A 应仍在）
          store.addChildLayer('L1', {qa_id: 'L2B', question: 'B', answer: 'aB', concepts: [], layer_summary: '', loading: false, status: 'waiting'});
          const afterB = {path: [...store.getState().currentPath], depth: store.getState().currentPath.length};
          // 检查 L1 的 children：应有 L2A + L2B 两个
          const l1 = store.findNode('L1');
          const childrenCount = l1?.children?.length || 0;
          const hasL2A = l1?.children?.some(c => c.qa_id === 'L2A');
          const hasL2B = l1?.children?.some(c => c.qa_id === 'L2B');
          return {afterA, afterBack, afterB, childrenCount, hasL2A, hasL2B};
        }''')

        print('L1点A:', result['afterA'])
        print('回L1:', result['afterBack'])
        print('L1点B:', result['afterB'])
        print(f'L1 children 数: {result["childrenCount"]} (应2)')
        print(f'L2A 仍在: {result["hasL2A"]}, L2B 在: {result["hasL2B"]}')

        ok = (
            result['afterA']['depth'] == 2 and  # L1->L2A 加深
            result['afterBack']['depth'] == 1 and  # 回 L1
            result['afterB']['depth'] == 2 and  # L1->L2B 加深
            result['childrenCount'] == 2 and  # 两个兄弟分支
            result['hasL2A'] and result['hasL2B']  # 都在
        )
        if ok:
            print('✓ 树状分支验证通过: L1 下两个子分支 L2A/L2B 并存,非追加到末尾')
        else:
            print('✗ 验证失败')
        await b.close()

asyncio.run(main())
