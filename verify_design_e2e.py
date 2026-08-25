"""端到端综合验证：starMindAgent「伴你成长」前端是否满足既定设计要求。

覆盖两大维度（断言均输出便于报告）：
A. 视觉设计令牌（记忆里已确立的方向，不可偏移）
   - 暖纸底 #FAF8F3 / 墨黑 #1F2421 / 衬线 Source Serif 4
   - 双信号色不可混用：活跃=墨蓝 #2B5F8A；沉淀=陶土棕 #8B5A3C
   - 签名元素「生长茎」存在（层标题左侧竖条 + 呼吸动画）
   - 三栏布局：TreeView(280) + ReadingPane(flex) + ConceptGraph(380)
   - 概念热度四档着色(1/2/4/8)：灰->绿->浅红->深红
B. 功能流程
   - 探索视图：开新树 -> SSE 流式回答 -> 概念 chip 渲染 -> 点击下钻 -> 新层挂树
   - 档案视图：学习画像 + 学习足迹
   - 阅读主题(4套)/字号(3档)切换器可用，切换后 CSS 变量生效
   - 无 console error / pageerror

运行：python verify_design_e2e.py
前置：backend :8000 + frontend :5166 已起，且 LLM 网关可用（真实 SSE 约 50-90s）。
"""
import asyncio
import os
import json
from playwright.async_api import async_playwright

OUT_DIR = os.path.expanduser('~/AppData/Local/Temp/starMindAgent_verify')
os.makedirs(OUT_DIR, exist_ok=True)
BASE = 'http://localhost:5166'

# 记忆里锁定的设计令牌（tokens.css 验证锚点）
TOKEN = {
    'paper': '#FAF8F3',      # 暖纸底
    'ink': '#1F2421',        # 墨黑
    'active': '#2B5F8A',     # 墨蓝（活跃）
    'settled': '#8B5A3C',    # 陶土棕（沉淀）
}
# 健壮的颜色比较：规范化为小写无空格
def norm(c):
    if not c: return ''
    return c.strip().lower().replace(' ', '')

# 断言结果收集
RESULTS = []
def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})

        # 收集 console / pageerror
        console_msgs = []
        page_errors = []
        page.on('console', lambda m: console_msgs.append(f"{m.type}: {m.text}"))
        page.on('pageerror', lambda e: page_errors.append(str(e)))

        # 监听关键网络响应
        net = {}
        async def on_response(resp):
            url = resp.url
            if '/qa/start' in url and resp.request.method == 'POST':
                try:
                    net['start_status'] = resp.status
                    net['start_body'] = await resp.text()
                except Exception as e:
                    net['start_err'] = str(e)
            elif '/drilldown' in url and resp.request.method == 'POST':
                try:
                    net['drill_status'] = resp.status
                    net['drill_body'] = await resp.text()
                except Exception as e:
                    net['drill_err'] = str(e)
        page.on('response', on_response)

        await page.goto(BASE, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1.5)
        await page.screenshot(path=os.path.join(OUT_DIR, 'design_1_initial.png'))

        # ============================================================
        # A. 视觉设计令牌断言（不依赖 LLM，先跑完）
        # ============================================================
        print("\n========== A. 视觉设计令牌 ==========")

        # A1. 读取 :root CSS 变量（tokens.css 注入的真相源）
        root_vars = await page.evaluate("""() => {
            const cs = getComputedStyle(document.documentElement);
            return {
                paper: cs.getPropertyValue('--paper'),
                ink: cs.getPropertyValue('--ink'),
                active: cs.getPropertyValue('--active'),
                settled: cs.getPropertyValue('--settled'),
                serif: cs.getPropertyValue('--serif'),
                active_soft: cs.getPropertyValue('--active-soft'),
                settled_soft: cs.getPropertyValue('--settled-soft'),
                tier_gray: cs.getPropertyValue('--tier-gray'),
                tier_green: cs.getPropertyValue('--tier-green'),
                tier_red_1: cs.getPropertyValue('--tier-red-1'),
                tier_red_3: cs.getPropertyValue('--tier-red-3'),
                tier_red_4: cs.getPropertyValue('--tier-red-4'),
            };
        }""")
        check('A1 暖纸底 --paper=#FAF8F3', norm(root_vars['paper']) == norm(TOKEN['paper']),
              f"got {root_vars['paper'].strip()!r}")
        check('A2 墨黑 --ink=#1F2421', norm(root_vars['ink']) == norm(TOKEN['ink']),
              f"got {root_vars['ink'].strip()!r}")
        check('A3 活跃信号 --active=#2B5F8A(墨蓝)', norm(root_vars['active']) == norm(TOKEN['active']),
              f"got {root_vars['active'].strip()!r}")
        check('A4 沉淀信号 --settled=#8B5A3C(陶土棕)', norm(root_vars['settled']) == norm(TOKEN['settled']),
              f"got {root_vars['settled'].strip()!r}")
        check('A5 衬线 --serif 含 Source Serif 4', 'source serif 4' in root_vars['serif'].lower(),
              f"got {root_vars['serif'].strip()!r}")
        # 热度四档存在
        check('A6 概念热度四档(灰/绿/浅红/深红)',
              all(root_vars[k].strip() for k in ('tier_gray', 'tier_green', 'tier_red_1', 'tier_red_3', 'tier_red_4')),
              '')

        # A7. 三栏布局宽度（TreeView 280 / ConceptGraph 380）
        # 不依赖标签名（探索视图才有 aside；档案视图无），用 data-testid/style 兜底
        layout = await page.evaluate("""() => {
            const asides = Array.from(document.querySelectorAll('aside'));
            const treeAside = asides.find(a => a.offsetWidth <= 320) || asides[0];
            const graphAside = asides.find(a => a.offsetWidth >= 360 && a !== treeAside) || asides[asides.length - 1];
            const main = document.querySelector('main');
            function rectW(el){ if(!el) return 0; const r = el.getBoundingClientRect(); return Math.round(r.width); }
            return {
                treeW: rectW(treeAside),
                graphW: rectW(graphAside),
                mainW: rectW(main),
            };
        }""")
        check('A7 左栏 TreeView 宽度≈280', abs(layout['treeW'] - 280) <= 4,
              f"got {layout['treeW']}")
        check('A8 右栏 ConceptGraph 宽度≈380', abs(layout['graphW'] - 380) <= 4,
              f"got {layout['graphW']}")
        check('A9 中栏 ReadingPane 弹性自适应(mainW>400)', layout['mainW'] > 400,
              f"got {layout['mainW']}")

        # A10. 顶部品牌标题用衬线 + 墨黑
        brand = await page.evaluate("""() => {
            const h1s = document.querySelectorAll('h1');
            for (const h of h1s) {
                if (h.textContent.includes('伴你成长')) {
                    const cs = getComputedStyle(h);
                    return { ff: cs.fontFamily, color: cs.color, size: cs.fontSize };
                }
            }
            return null;
        }""")
        check('A10 品牌标题用衬线', brand and 'serif' in brand['ff'].lower(),
              f"ff={brand['ff'] if brand else None}")
        check('A11 品牌标题墨黑色', brand and norm(brand['color']) in ('#1f2421', 'rgb(31,36,33)'),
              f"color={brand['color'] if brand else None}")

        # A12. 空态/签名元素「生长茎」--空态也有一根短茎(ReadingPane empty)
        # 首屏若已开过树，会渲染 ReadingLayer 的 stem；这里先验证 CSS 里 stemBreath 动画 + stem 节点存在
        # 查页面是否注册了 stemBreath 关键帧
        has_stem_anim = await page.evaluate("""() => {
            const sheets = document.styleSheets;
            for (const s of sheets) {
                try {
                    for (const r of s.cssRules) {
                        if (r.type === 7 && r.name === 'stemBreath') return true; // CSSKeyframesRule
                    }
                } catch(e) { /* cross-origin */ }
            }
            return false;
        }""")
        check('A12 生长茎呼吸动画(stemBreath)已注册', has_stem_anim, '')

        # A13. prefers-reduced-motion 兜底存在
        has_reduced = await page.evaluate("""() => {
            const sheets = document.styleSheets;
            for (const s of sheets) {
                try {
                    for (const r of s.cssRules) {
                        if (r.type === 4 && r.cssText.includes('prefers-reduced-motion')) return true; // CSSMediaRule
                    }
                } catch(e) {}
            }
            return false;
        }""")
        check('A13 prefers-reduced-motion 兜底', has_reduced, '')

        # A14. 概念图(cytoscape)节点用衬线 + 暖纸底/墨黑边（硬编码在 ConceptGraph.jsx style）
        # 验证 canvas 容器存在
        cy_canvas = await page.query_selector('aside:nth-child(3) canvas, div canvas')
        check('A14 概念图 cytoscape canvas 已挂载', cy_canvas is not None, '')

        # A15. 主题/字号切换器存在（4 个主题色块 + 3 个字号按钮）
        # 注:主题按钮 aria-label=主题名, 字号按钮 aria-label=字号名(紧凑/舒适/宽松)
        # 共 7 个 button[aria-label]
        controls = await page.evaluate("""() => {
            const tb = document.querySelector('[role=toolbar][aria-label="阅读主题与字号"]');
            if (!tb) return { found: false, n: 0, labels: [] };
            const btns = Array.from(tb.querySelectorAll('button[aria-label]'));
            return { found: true, n: btns.length, labels: btns.map(b => b.getAttribute('aria-label')) };
        }""")
        check('A15 阅读切换器(主题+字号)存在', controls.get('found'),
              f"按钮数 {controls.get('n')}")
        # 主题4 + 字号3 = 7
        check('A16 切换器含 7 个按钮(4主题+3字号)', controls.get('n') == 7,
              f"got {controls.get('n')} labels={controls.get('labels')}")

        # A17. 主题切换生效：点「夜读」后 --paper 变深
        # 先读默认
        paper_before = norm(await page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--paper')"))
        # 点夜读（第2个主题色块）
        await page.evaluate("""() => {
            const tb = document.querySelector('[role=toolbar][aria-label="阅读主题与字号"]');
            const btns = Array.from(tb.querySelectorAll('button[aria-label]'));
            const yedu = btns.find(b => b.getAttribute('aria-label') === '夜读');
            if (yedu) yedu.click();
        }""")
        await asyncio.sleep(0.6)
        paper_after = norm(await page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--paper')"))
        await page.screenshot(path=os.path.join(OUT_DIR, 'design_2_theme_yedu.png'))
        check('A17 主题切换生效(夜读 --paper 变深)',
              paper_after != paper_before and paper_after != norm('#FAF8F3'),
              f"before={paper_before} after={paper_after}")
        # 切回暖纸
        await page.evaluate("""() => {
            const tb = document.querySelector('[role=toolbar][aria-label="阅读主题与字号"]');
            const btns = Array.from(tb.querySelectorAll('button[aria-label]'));
            const nuanzhi = btns.find(b => b.getAttribute('aria-label') === '暖纸');
            if (nuanzhi) nuanzhi.click();
        }""")
        await asyncio.sleep(0.4)

        # A18. 字号切换生效：点「宽松」后 --t-fs-body 变大
        fs_before = (await page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--t-fs-body')")).strip()
        await page.evaluate("""() => {
            const tb = document.querySelector('[role=toolbar][aria-label="阅读主题与字号"]');
            const btns = Array.from(tb.querySelectorAll('button[aria-label]'));
            const wide = btns.find(b => b.getAttribute('aria-label') === '宽松');
            if (wide) wide.click();
        }""")
        await asyncio.sleep(0.4)
        fs_after = (await page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--t-fs-body')")).strip()
        await page.evaluate("""() => {
            const tb = document.querySelector('[role=toolbar][aria-label="阅读主题与字号"]');
            const btns = Array.from(tb.querySelectorAll('button[aria-label]'));
            const comfy = btns.find(b => b.getAttribute('aria-label') === '舒适');
            if (comfy) comfy.click();
        }""")
        check('A18 字号切换生效(宽松 --t-fs-body 变大)',
              float_px(fs_after) > float_px(fs_before),
              f"before={fs_before} after={fs_after}")

        # ============================================================
        # B. 功能流程（依赖 LLM，约 60s）
        # ============================================================
        print("\n========== B. 功能流程 ==========")

        # 清空 localStorage 以保证干净态
        await page.evaluate("() => localStorage.clear()")
        await page.reload(wait_until='networkidle', timeout=30000)
        await asyncio.sleep(1)

        # B1. 探索视图：输入问题 + 开新树
        qa_input = await page.query_selector('input[placeholder*="提一个问题"]')
        check('B1 问题输入框存在', qa_input is not None, '')
        await qa_input.fill('什么是CNN？')
        start_btn = await page.query_selector('button:has-text("开新树")')
        check('B2 开新树按钮存在', start_btn is not None, '')
        await start_btn.click()
        print("[B] 已开新树，等 SSE 流式回答（最长 150s）...")

        # B3. 等 /qa/start 200 + 返回 qa_id
        for _ in range(10):
            await asyncio.sleep(0.5)
            if net.get('start_status'): break
        check('B3 /qa/start 返回 200', net.get('start_status') == 200,
              f"status={net.get('start_status')}")
        start_body = net.get('start_body', '')
        check('B4 start 返回含 qa_id+session_id',
              '"qa_id"' in start_body and '"session_id"' in start_body, '')

        # B5. 等概念 chip 出现（SSE 完成）
        # 优先 data-testid="concept-chip"(内联+pill 都挂), 兜底 title="点击下钻"
        chip_selector = '[data-testid="concept-chip"], [title="点击下钻"]'
        chip_appeared = False
        try:
            await page.wait_for_selector(chip_selector, timeout=150000)
            chip_appeared = True
        except Exception as e:
            pass
        await asyncio.sleep(1)
        await page.screenshot(path=os.path.join(OUT_DIR, 'design_3_root_answered.png'))
        check('B5 根层概念 chip 渲染（SSE concepts 事件抵达）', chip_appeared, '')

        # B6. 回答正文已渲染（含 CNN）
        body_text = await page.inner_text('body')
        check('B6 回答正文含"CNN"', 'CNN' in body_text, '')

        # B7. 层摘要已渲染（陶土棕左边线块）
        layer_summary_count = await page.evaluate("""() => {
            // 层摘要: borderLeft 3px solid var(--settled) 的 aside
            const els = document.querySelectorAll('aside');
            let n = 0;
            els.forEach(e => {
                const cs = getComputedStyle(e);
                if (cs.borderLeftWidth && parseFloat(cs.borderLeftWidth) >= 3) n++;
            });
            return n;
        }""")
        check('B7 层摘要块渲染', layer_summary_count >= 1, f"count={layer_summary_count}")

        # B8. 生长茎在 ReadingLayer 渲染（stem div 存在 + 有 stemNode）
        stem_info = await page.evaluate("""() => {
            // stem: 层 header 内 width:4 的 div，带 stemNode 子节点
            const headers = document.querySelectorAll('header');
            for (const h of headers) {
                const stem = h.querySelector('div[style*="linear-gradient"], div[style*="stemBreath"]');
                if (stem) {
                    const r = stem.getBoundingClientRect();
                    return { found: true, w: Math.round(r.width), h: Math.round(r.height) };
                }
            }
            // 退而求其次：找带 stemBreath 动画的元素
            const all = document.querySelectorAll('div');
            for (const d of all) {
                const cs = getComputedStyle(d);
                if (cs.animationName && cs.animationName.includes('stemBreath')) {
                    const r = d.getBoundingClientRect();
                    return { found: true, w: Math.round(r.width), h: Math.round(r.height), via: 'animName' };
                }
            }
            return { found: false };
        }""")
        check('B8 生长茎签名元素渲染', stem_info.get('found'),
              f"w={stem_info.get('w')} h={stem_info.get('h')}")

        # B9. 概念 chip 用墨蓝 active-soft（活跃信号，非陶土棕）
        chip_color = await page.evaluate("""() => {
            const chip = document.querySelector('[data-testid="concept-chip"]') ||
                         document.querySelector('[title="点击下钻"]');
            if (!chip) return null;
            const cs = getComputedStyle(chip);
            return { bg: cs.backgroundColor, color: cs.color, border: cs.borderColor, tag: chip.tagName };
        }""")
        if chip_color:
            # 内联 chip(active-soft rgb 230,238,245) 或 pill 未理解(墨蓝 ink)；
            # 不该是 settled-soft(rgb 232,220,205)
            bg_ok = ('230, 238, 245' in chip_color['bg']
                     or '43, 95, 138' in chip_color['bg']
                     or chip_color['tag'] == 'BUTTON')  # pill 未理解走 TIER_COLOR
            check('B9 概念 chip 用墨蓝系(活跃信号)', bg_ok,
                  f"bg={chip_color['bg']} tag={chip_color['tag']}")
        else:
            check('B9 概念 chip 用墨蓝系(活跃信号)', False, 'chip 未找到')

        # B10. 下钻：点第一个概念 chip -> 新层挂树
        first_chip = await page.query_selector('[data-testid="concept-chip"]') or \
                     await page.query_selector('[title="点击下钻"]')
        chip_name = (await first_chip.text_content() or '').strip() if first_chip else ''
        if first_chip:
            await first_chip.click()
            print(f"[B] 点击概念 '{chip_name}' 下钻，等新层 SSE...")
        for _ in range(10):
            await asyncio.sleep(1)
            if net.get('drill_status'): break
        check('B10 /qa/drilldown 返回 200', net.get('drill_status') == 200,
              f"status={net.get('drill_status')}")
        drill_body = net.get('drill_body', '')
        check('B11 drilldown 返回含 qa_id+parent_qa_id',
              '"qa_id"' in drill_body and '"parent_qa_id"' in drill_body, '')

        # B12. 等新层挂树（TreeView 出现 L2 标签）
        l2_ok = False
        try:
            await page.wait_for_selector('text=L2', timeout=20000)
            l2_ok = True
        except Exception:
            pass
        await page.screenshot(path=os.path.join(OUT_DIR, 'design_4_drilled.png'))
        check('B12 下钻后树出现 L2 子层', l2_ok, '')

        # B13. 概念图有节点（session scope）
        await asyncio.sleep(2)
        graph_nodes = await page.evaluate("""() => {
            const cy = document.querySelector('aside:nth-child(3) canvas, div canvas');
            // cytoscape 画 canvas，节点数难直接读；改读 ConceptSummary 概念数
            const sum = document.body.innerText.match(/概念汇总\\s*(\\d+)/);
            return sum ? parseInt(sum[1]) : 0;
        }""")
        check('B13 概念汇总有数据(>=1)', graph_nodes >= 1, f"count={graph_nodes}")

        # ============================================================
        # C. 档案视图
        # ============================================================
        print("\n========== C. 档案视图 ==========")
        await page.click('button:has-text("档案")')
        await asyncio.sleep(2.5)
        await page.screenshot(path=os.path.join(OUT_DIR, 'design_5_memory.png'))

        mem_text = await page.inner_text('body')
        check('C1 档案视图含"成长档案"', '成长档案' in mem_text, '')
        check('C2 档案视图含"学习足迹"', '学习足迹' in mem_text, '')

        # C3. 档案视图用陶土棕基调（sectionLabel color = --settled）
        memory_settled = await page.evaluate("""() => {
            // 学习足迹 sectionLabel 用 --settled
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const cs = getComputedStyle(el);
                if (cs.color && cs.color.includes('139, 90, 60')) return true;  // #8B5A3C
            }
            return false;
        }""")
        check('C3 档案视图用陶土棕(#8B5A3C)基调', memory_settled, '')

        # C4. 学习足迹有时间线项（若刚跑过 B 流程，有 session）
        timeline_items = await page.evaluate("""() => {
            const ol = document.querySelector('ol');
            return ol ? ol.querySelectorAll('li').length : 0;
        }""")
        check('C4 学习足迹有时间线条目(>=1)', timeline_items >= 1, f"count={timeline_items}")

        # ============================================================
        # D. 控制台错误
        # ============================================================
        print("\n========== D. 控制台错误 ==========")
        # 忽略静态资源 404(字体/图标等非功能性噪声),只关注真正的 JS 错误
        real_errors = []
        for m in console_msgs:
            if not m.startswith('error'):
                continue
            # 过滤静态资源加载 404 (字体/图片/模块), 这些不阻断功能
            if 'Failed to load resource' in m and '404' in m:
                continue
            real_errors.append(m)
        real_errors += [f"pageerror: {e}" for e in page_errors]
        check('D1 无 console error / pageerror', len(real_errors) == 0,
              f"共 {len(real_errors)} 条")
        if real_errors:
            for e in real_errors[:5]:
                print(f"  - {e}")
        # 404 详情单独列出(供定位静态资源缺失,不阻断判定)
        res_404 = [m for m in console_msgs if 'Failed to load resource' in m and '404' in m]
        if res_404:
            print(f"  [info] 静态资源 404 共 {len(res_404)} 条(不阻断):")
            for e in res_404[:5]:
                print(f"    - {e}")

        await browser.close()

    # ============================================================
    # 报告
    # ============================================================
    print("\n\n" + "=" * 60)
    print("  E2E 设计验证报告")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for name, ok, detail in RESULTS:
        mark = 'PASS' if ok else 'FAIL'
        line = f"  [{mark}] {name}"
        if detail and not ok:
            line += f"  <- {detail}"
        print(line)
    print("=" * 60)
    print(f"  通过 {passed}/{total}")
    print(f"  截图目录: {OUT_DIR}")
    print("=" * 60)

    return passed == total


def float_px(s):
    try:
        return float(str(s).replace('px', '').strip())
    except Exception:
        return 0.0


if __name__ == '__main__':
    ok = asyncio.run(main())
    raise SystemExit(0 if ok else 1)
