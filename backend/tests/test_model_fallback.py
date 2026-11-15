"""模型自动换档（agent/agent.py 的 _generate）：额度用完就一格一格换下一个模型

用假的 Gemini：可以指定"哪个模型会回什么错误"，不花额度。
"""

import pytest
from google.genai import errors, types

from agent import agent as A

DAILY = "Quota exceeded for metric: generate_content_free_tier_requests, GenerateRequestsPerDayPerProjectPerModel"
MINUTE = "Quota exceeded: GenerateRequestsPerMinutePerProjectPerModel. Please retry in 7s."


def api_error(code, message):
    return errors.APIError(code, {"error": {"message": message, "status": "X"}})


def ok(text="好的"):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]))])


@pytest.fixture
def gemini(monkeypatch):
    """fail = {模型: 错误}；calls 记录依序试了哪些模型。"""
    state = type("Fake", (), {"fail": {}, "calls": []})()

    def generate_content(model, contents, config):
        state.calls.append(model)
        if model in state.fail:
            raise state.fail[model]
        return ok(f"我是 {model}")

    monkeypatch.setattr(A.client.models, "generate_content", generate_content)
    monkeypatch.setattr(A, "MODELS", ["m1", "m2", "m3"])
    monkeypatch.setattr(A, "_unavailable", {})
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    return state


def test_daily_quota_used_up_moves_to_next_model_and_remembers(gemini):
    gemini.fail = {"m1": api_error(429, DAILY)}
    assert A.CustomerServiceAgent._generate([]).text == "我是 m2"
    gemini.calls.clear()
    A.CustomerServiceAgent._generate([])                    # 下一题：直接用 m2，不再浪费一次去试 m1
    assert gemini.calls == ["m2"] and A.current_model() == "m2"


def test_goes_down_the_list_one_by_one(gemini):
    gemini.fail = {"m1": api_error(429, DAILY), "m2": api_error(429, DAILY)}
    assert A.CustomerServiceAgent._generate([]).text == "我是 m3"
    assert gemini.calls == ["m1", "m2", "m3"]


def test_daily_skip_lasts_until_quota_reset(gemini, monkeypatch):
    gemini.fail = {"m1": api_error(429, DAILY)}
    A.CustomerServiceAgent._generate([])
    monkeypatch.setattr(A.time, "time", lambda: A._unavailable["m1"] + 1)   # 过了美国太平洋时间午夜
    gemini.fail = {}
    assert A.current_model() == "m1"                        # 额度恢复，自动回到第一个


def test_per_minute_limit_switches_but_only_for_a_minute(gemini):
    gemini.fail = {"m1": api_error(429, MINUTE)}
    assert A.CustomerServiceAgent._generate([]).text == "我是 m2"
    assert A._unavailable["m1"] - A.time.time() <= 60


def test_model_not_available_to_this_key_is_skipped(gemini):
    gemini.fail = {"m1": api_error(404, "models/m1 is not found")}
    assert A.CustomerServiceAgent._generate([]).text == "我是 m2"


def test_busy_server_retries_same_model_first(gemini):
    errors_left = [api_error(503, "overloaded")]

    def flaky(model, contents, config):
        gemini.calls.append(model)
        if errors_left:
            raise errors_left.pop()
        return ok()

    A.client.models.generate_content = flaky
    A.CustomerServiceAgent._generate([])
    assert gemini.calls == ["m1", "m1"]                     # 服务器忙：同一个模型再试，不用换


def test_bad_request_does_not_switch_model(gemini):
    gemini.fail = {"m1": api_error(400, "invalid argument")}
    with pytest.raises(errors.APIError):
        A.CustomerServiceAgent._generate([])
    assert gemini.calls == ["m1"]                           # 是我们的请求有问题，换模型也没用


def test_all_models_used_up_gives_normal_fallback_answer(gemini):
    gemini.fail = {m: api_error(429, DAILY) for m in ["m1", "m2", "m3"]}
    result = A.CustomerServiceAgent().chat("星期一有开吗")
    assert result["answer"] == A.FALLBACK_ANSWER            # 不会当机，顾客看到"系统暂时没办法回答"


def test_never_switch_after_text_is_on_screen(gemini, monkeypatch):
    def half_then_quota(model, contents, config):
        gemini.calls.append(model)
        yield ok("您好，")
        raise api_error(429, DAILY)

    monkeypatch.setattr(A.client.models, "generate_content_stream", half_then_quota)
    with pytest.raises(errors.APIError):
        A.CustomerServiceAgent._generate([], on_text=lambda t: None)
    assert gemini.calls == ["m1"]                           # 已经显示了字，换模型会重复显示


def test_model_still_busy_after_retry_is_skipped_for_a_while(gemini):
    """实测：3.8-flash 一直 503，原本一直等同一个模型（一题 163 秒）。现在再试一次还忙就换下一个。"""
    gemini.fail = {"m1": api_error(503, "This model is currently experiencing high demand")}
    assert A.CustomerServiceAgent._generate([]).text == "我是 m2"
    assert gemini.calls == ["m1", "m1", "m2"]                # 同一个只多试一次
    assert 0 < A._unavailable["m1"] - A.time.time() <= A.BUSY_SKIP_SECONDS


# Google 真的回过的"每分钟上限"错误（2026-11-10 实际收到，节录）
REAL_MINUTE = ("You exceeded your current quota ... limit: 15, model: gemini-3.5-flash-lite, description: Generate content "
               "requests per minute limit exceeded on Free Tier. Please retry in 28.096797439s. 'quotaId': "
               "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'")


@pytest.mark.parametrize("message, daily", [
    (REAL_MINUTE, False),                                                              # 真的错误：每分钟 → 只停 60 秒
    ("'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'", True),          # 每天（quotaId）
    ("Generate content requests per day limit exceeded on Free Tier.", True),          # 每天（说明文字）
])
def test_reads_google_quota_type_correctly(message, daily):
    assert A._is_daily_quota(message) is daily
