"""SentinelDetector 跨 chunk 边界检测测试（协议 3.2）。

覆盖：
- sentinel 完整出现在一个 chunk 内；
- sentinel 被 chunk 边界从任意位置切断；
- 正文末尾换行与 sentinel 前缀叠加；
- 无 sentinel 的纯正文（降级）；
- sentinel 后 JSON 跨 chunk 累积。
"""
import pytest
from app.inference.sentinel import SentinelDetector, build_full_sentinel
from app.config import settings

CORE = settings.concept_sentinel  # ≡≡CONCEPT_BLOCK≡≡
FULL = build_full_sentinel(CORE)  # \n≡≡CONCEPT_BLOCK≡≡\n


def _feed_all(detector, text, step):
    answers, saw, jsons = [], False, []
    for i in range(0, len(text), step):
        a, hit = detector.feed(text[i:i + step])
        if a:
            answers.append(a)
        if hit:
            saw = True
    a, hit = detector.flush()
    if a:
        answers.append(a)
    if hit:
        saw = True
    return "".join(answers), saw, detector.json_text


def test_sentinel_single_chunk():
    det = SentinelDetector(FULL)
    body = "梯度下降是一种优化算法。"
    raw = f"{body}{FULL}{{\"concepts\":[]}}"
    ans, saw, js = _feed_all(det, raw, 1000)
    assert saw
    assert ans == body
    assert js == '{"concepts":[]}'


@pytest.mark.parametrize("step", [1, 2, 3, 5, 7, 11, 13])
def test_sentinel_cross_chunk_arbitrary_split(step):
    """sentinel 从任意位置被切断都能正确检测，正文不泄漏 sentinel。"""
    det = SentinelDetector(FULL)
    body = "反向传播通过链式法则计算梯度。它是训练神经网络的核心。"
    raw = f"{body}{FULL}{{\"concepts\":[{{\"name\":\"BP\"}}]}}"
    ans, saw, js = _feed_all(det, raw, step)
    assert saw, f"sentinel missed at step={step}"
    assert ans == body, f"answer corrupted at step={step}"
    assert CORE not in ans
    assert "BP" in js


def test_answer_trailing_newline_before_sentinel():
    """正文末尾的换行不应被当 sentinel 吞掉，也不应泄漏进 JSON。"""
    det = SentinelDetector(FULL)
    body = "第一行\n第二行"
    raw = f"{body}\n{CORE}\n{{\"x\":1}}"
    ans, saw, js = _feed_all(det, raw, 4)
    assert saw
    assert ans == body  # 注意：正文末尾的 \n 属于 sentinel 的一部分，不算正文
    assert js == '{"x":1}'


def test_no_sentinel_pure_answer():
    """无 sentinel：全部当正文吐出，json_text 为空。"""
    det = SentinelDetector(FULL)
    raw = "只有正文，没有任何结构化标记。"
    ans, saw, js = _feed_all(det, raw, 3)
    assert not saw
    assert ans == raw
    assert js == ""


def test_sentinel_at_stream_end_no_trailing_newline():
    """sentinel 行出现在流末尾、无尾随换行也无 JSON：flush 行级兜底补判，正文不泄漏。"""
    det = SentinelDetector(FULL)
    body = "正文"
    # 流末尾就是 sentinel 行，无尾换行、无 JSON
    raw = f"{body}\n{CORE}"
    ans, saw, js = _feed_all(det, raw, 2)
    assert saw
    assert ans == body
    assert js == ""


def test_json_accumulated_across_chunks():
    det = SentinelDetector(FULL)
    body = "ans"
    json_payload = '{"concepts":[{"name":"c1"},{"name":"c2"}]}'
    raw = f"{body}{FULL}{json_payload}"
    ans, saw, js = _feed_all(det, raw, 5)
    assert saw
    assert ans == body
    assert js == json_payload


def test_double_feed_after_match_appends_to_json():
    det = SentinelDetector(FULL)
    a, hit = det.feed(f"body{FULL}{{\"a\":")
    assert hit
    assert a == "body"
    a2, hit2 = det.feed("1}")
    assert not hit2
    assert a2 == ""
    a3, hit3 = det.flush()
    assert not hit3
    assert det.json_text == '{"a":1}'


def test_inner_equals_not_false_split():
    """正文中出现零散 ≡ 不应触发切分。"""
    det = SentinelDetector(FULL)
    body = "a ≡ b ≡ c"
    raw = f"{body}{FULL}{{\"z\":0}}"
    ans, saw, js = _feed_all(det, raw, 2)
    assert saw
    assert ans == body
