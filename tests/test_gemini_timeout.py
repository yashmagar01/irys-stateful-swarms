import time

from src.providers.gemini import GeminiCaller


class HangingModels:
    calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        time.sleep(5)


class HangingClient:
    models = HangingModels()


def test_gemini_watchdog_times_out_hanging_sdk_call():
    caller = GeminiCaller.__new__(GeminiCaller)
    caller.client = HangingClient()
    caller.model = "fake-gemini"
    caller.timeout_s = 0.01

    started = time.perf_counter()
    try:
        caller._generate_content_with_timeout("prompt", object())
    except TimeoutError as exc:
        elapsed = time.perf_counter() - started
        assert elapsed < 1
        assert "watchdog timeout" in str(exc)
    else:
        raise AssertionError("Expected watchdog timeout")


def test_gemini_complete_does_not_retry_timeout_by_default():
    models = HangingModels()

    class Client:
        pass

    client = Client()
    client.models = models
    caller = GeminiCaller.__new__(GeminiCaller)
    caller.client = client
    caller.model = "fake-gemini"
    caller.timeout_s = 0.01
    caller.timeout_attempts = 1

    try:
        caller.complete("prompt", max_tokens=8, json_mode=False)
    except RuntimeError as exc:
        assert "timed out after 1 attempts" in str(exc)
    else:
        raise AssertionError("Expected complete() timeout")

    assert models.calls == 1
