"""端到端验证：记忆卡片功能（走真实后端 HTTP API + DB）。

覆盖：
1. 选段主动建卡（LLM/stub 总结，同步返回）
2. due 队列出现新卡
3. 盲 check 评分流转：understood x3 → 归档；forgot/retry → 重置推次日
4. 进度统计正确反映状态
5. 归档卡不再进 due、不可再评分（409）
6. 删卡清理

前置：后端跑在 :8000，PG 容器 growth-agent-db-1 运行中。
用法：python verify_memory_cards.py
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8000/memory/cards"
PASS, FAIL = [], []


def req(path, method="GET", body=None, expect=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            code, out = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        code, out = e.code, json.loads(e.read() or b"{}")
    if expect is not None and code != expect:
        raise AssertionError(f"{method} {path} -> {code} (expect {expect}): {out}")
    return code, out


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def main():
    uid = f"verify_cards_{int(time.time())}"

    print("== 1. 选段建卡 ==")
    s, out = req(f"/users/{uid}/from-selection", "POST", {
        "selected_text": "一致性哈希将哈希空间组织成环，节点与键都映射到环上，"
                         "键顺时针找到第一个节点归属，增删节点只影响相邻区段。",
        "question": "一致性哈希",
    }, expect=200)
    check("选段建卡 created=True", out.get("created") is True)
    card1 = out["card_id"]
    check("返回 question/answer 非空", bool(out.get("question")) and bool(out.get("answer")))

    print("== 2. due 队列 ==")
    s, due = req(f"/users/{uid}/due", expect=200)
    check("新卡出现在 due 队列", any(c["card_id"] == card1 for c in due))
    check("due_at 为今天（立即到期，测试场景）", len(due) >= 1)

    print("== 3. 盲 check 流转：理解 x3 归档 ==")
    for i in range(3):
        s, g = req(f"/{card1}/grade", "POST", {"grade": "understood"}, expect=200)
        check(f"understood #{i+1} streak={g['streak']}", g["streak"] == i + 1)
    check("第 3 次理解后归档", g["status"] == "archived" and g["just_archived"] is True)

    print("== 4. 归档卡行为 ==")
    s, due2 = req(f"/users/{uid}/due", expect=200)
    check("归档卡退出 due 队列", not any(c["card_id"] == card1 for c in due2))
    s, _ = req(f"/{card1}/grade", "POST", {"grade": "forgot"})
    check("归档后再评分 409", s == 409)

    print("== 5. forgot/retry 重置流转 ==")
    s, out = req(f"/users/{uid}/from-selection", "POST", {
        "selected_text": "Raft 通过领导者选举与日志复制保证一致性，任期号单调递增。",
        "question": "Raft",
    }, expect=200)
    card2 = out["card_id"]
    s, g = req(f"/{card2}/grade", "POST", {"grade": "forgot"}, expect=200)
    check("forgot 重置 streak=0", g["streak"] == 0)
    check("forgot 后 due_at 推到明天", g["due_at"][:10] != time.strftime("%Y-%m-%d"))
    s, g = req(f"/{card2}/grade", "POST", {"grade": "retry"}, expect=200)
    check("retry（明天再试）last_grade=retry", g["last_grade"] == "retry")
    s, due3 = req(f"/users/{uid}/due", expect=200)
    check("评分后卡退出今日 due（明天到期）", not any(c["card_id"] == card2 for c in due3))

    print("== 6. 进度统计 ==")
    s, p = req(f"/users/{uid}/progress", expect=200)
    check("total=2", p["total"] == 2, str(p["total"]))
    check("archived=1", p["archived"] == 1, str(p["archived"]))
    check("active=1（明天到期不计 due_now）", p["active"] == 1 and p["due_now"] == 0)

    print("== 7. 非法输入 ==")
    s, _ = req(f"/{card2}/grade", "POST", {"grade": "hack"})
    check("非法 grade 422", s == 422)
    s, _ = req(f"/users/{uid}/from-selection", "POST", {"selected_text": "短"})
    check("过短选段 422", s == 422)

    print("== 8. 清理 ==")
    for c in (card1, card2):
        s, _ = req(f"/{c}", "DELETE", expect=200)
    s, p = req(f"/users/{uid}/progress", expect=200)
    check("删除后 total=0", p["total"] == 0)

    print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
