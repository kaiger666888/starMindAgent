"""验证回上层保留探索历史：通过浏览器 import 真实 store 模块做端到端验证。"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page(viewport={'width': 1600, 'height': 900})
        await page.goto('http://localhost:5173', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        # 纯 store 端到端验证：模拟下钻 L1->L2 + 回上层 L1
        result = await page.evaluate('''async () => {
          const store = await import('/src/store/qaStore.js');
          // 模拟下钻 L1->L2(纯 store,不依赖后端 SSE)
          store.resetStack();
          store.pushLayer({qa_id: 'L1', question: 'q1', answer: 'a1', concepts: [], layer_summary: 's1', loading: false, status: 'waiting'});
          store.pushLayer({qa_id: 'L2', question: 'q2', answer: 'a2', concepts: [], layer_summary: 's2', loading: false, status: 'waiting'});
          const afterDrill = {
            stack: store.getState().stack.length,
            idx: store.getState().currentIdx,
            current: store.getState().stack[store.getState().currentIdx]?.question
          };
          // 回 L1
          store.popToLayer('L1');
          const afterRollback = {
            stack: store.getState().stack.length,
            idx: store.getState().currentIdx,
            current: store.getState().stack[store.getState().currentIdx]?.question,
            l2StillExists: store.getState().stack.some(l => l.qa_id === 'L2')
          };
          return {afterDrill, afterRollback};
        }''')

        print('下钻后:', result['afterDrill'])
        print('回上层后:', result['afterRollback'])

        ok = (
            result['afterDrill']['stack'] == 2 and
            result['afterDrill']['idx'] == 1 and
            result['afterDrill']['current'] == 'q2' and
            result['afterRollback']['stack'] == 2 and  # 核心保留
            result['afterRollback']['idx'] == 0 and     # 切回 L1
            result['afterRollback']['current'] == 'q1' and
            result['afterRollback']['l2StillExists']   # L2 还在
        )
        if ok:
            print('✓ 验证通过: 回上层后 stack 长度保留(2), currentIdx 切回 L1(0), L2 记录仍在')
        else:
            print('✗ 验证失败')
        await b.close()

asyncio.run(main())

