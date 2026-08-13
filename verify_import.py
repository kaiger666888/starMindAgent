"""端到端验证导入 markdown：点导入按钮→选文件→L0根层显示全文→可下钻。"""
import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page(viewport={'width': 1600, 'height': 900})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)[:150]))
        await page.goto('http://localhost:5166', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        # 1. 检查导入按钮存在
        import_btn = await page.query_selector('button:has-text("导入学习文件")')
        print(f'1. 导入按钮存在: {import_btn is not None}')

        # 2. 准备测试 md 文件
        md_path = os.path.expanduser('~/AppData/Local/Temp/test_material.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('# 梯度下降\n\n梯度下降是一种优化算法，通过沿损失函数梯度的反方向迭代更新参数。\n\n## 学习率\n\n学习率控制每步步长，过大导致发散，过小收敛慢。\n')

        # 3. 上传文件
        file_input = await page.query_selector('input[type="file"]')
        if file_input:
            await file_input.set_input_files(md_path)
            # 等待导入完成
            for i in range(20):
                await asyncio.sleep(3)
                # 检查是否显示了文件内容
                info = await page.evaluate('''async () => {
                  const store = await import('/src/store/qaStore.js');
                  const cur = store.getCurrentLayer();
                  return {
                    hasTree: !!store.getState().tree,
                    answerLen: cur?.answer?.length || 0,
                    question: cur?.question,
                    concepts: (cur?.concepts || []).length,
                  };
                }''')
                print(f'  等待{i*3}s: {info}')
                if info['answerLen'] > 50 or i > 15:
                    break

            print(f'2. 导入后根层: answer长={info["answerLen"]}, 问题={info["question"]}, 概念数={info["concepts"]}')
            # answer 应含文件内容（梯度下降），question 是文件名
            answer_text = await page.evaluate('''async () => {
              const store = await import('/src/store/qaStore.js');
              return store.getCurrentLayer()?.answer || '';
            }''')
            ok = info['answerLen'] > 50 and '梯度' in answer_text
            print(f'3. {"✓ 文件内容显示在根层(answer含\"梯度\")" if ok else "✗ 文件未显示"}')

        if errs:
            print(f'页面错误: {errs[:3]}')

        await b.close()

asyncio.run(main())
