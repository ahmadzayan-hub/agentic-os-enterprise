"""Open-weights model support: self-hosted and cloud-hosted.

Everything here runs against a stub HTTP transport. A test that needed a real
model server would be skipped in CI, and a skipped test evidences nothing.
"""

from __future__ import annotations

import json

import httpx
import pytest
from agentic_os.ai import providers
from agentic_os.ai.providers import (
    ModelRequest,
    OpenAICompatibleProvider,
    provider_for_model,
)
from agentic_os.core.config import get_settings
from agentic_os.core.errors import UpstreamUnavailable
from agentic_os.core.registry import load_registries

pytestmark = [pytest.mark.unit]

ONPREM = {
    "key": "private-general",
    "provider": "openai-compatible",
    "endpoint": "onprem-vllm",
    "provider_model_id": "meta-llama/Llama-3.3-70B-Instruct",
}
CLOUD = {
    "key": "oss-cloud-fast",
    "provider": "openai-compatible",
    "endpoint": "oss-cloud",
    "provider_model_id": "llama-3.3-70b-versatile",
}

ENDPOINTS = {
    "onprem-vllm": {"base_url": "http://vllm.internal/v1", "external": False},
    "oss-cloud": {
        "base_url": "https://oss-host.example/v1",
        "api_key_ref": "env://OSS_CLOUD_KEY",
        "external": True,
    },
}


@pytest.fixture()
def endpoints(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "model_endpoints", json.dumps(ENDPOINTS), raising=False)
    return settings


def _stub(capture: dict, headers: dict[str, str] | None = None) -> httpx.Client:
    """A client that records the request the provider actually made.

    It carries the provider's own headers so the credential path is exercised
    rather than bypassed.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        capture["url"] = str(request.url)
        capture["auth"] = request.headers.get("authorization", "")
        capture["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "grounded answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
        )

    return httpx.Client(
        base_url="http://stub.invalid/v1",
        headers=headers or {},
        transport=httpx.MockTransport(handler),
    )


# ------------------------------------------------------------------ registry
def test_the_registry_offers_self_hosted_and_cloud_open_weights() -> None:
    models = load_registries().models
    open_weights = {k: m for k, m in models.items() if m.get("open_weights")}
    assert "private-general" in open_weights, "no self-hosted open-weights model registered"
    assert "oss-cloud-fast" in open_weights, "no cloud-hosted open-weights model registered"
    assert open_weights["private-general"]["deployment"] == "private"
    assert open_weights["oss-cloud-fast"]["deployment"] == "cloud"


def test_only_operator_controlled_deployments_may_hold_restricted_data() -> None:
    """The open-weights cloud model is fast and cheap and still may not see RESTRICTED."""
    models = load_registries().models
    for key, model in models.items():
        if model["max_classification"] == "RESTRICTED":
            assert model["deployment"] in ("local", "private"), (
                f"{key} is cleared for RESTRICTED on a {model['deployment']} deployment"
            )
    assert models["oss-cloud-fast"]["max_classification"] == "CONFIDENTIAL"


def test_no_model_permits_training_on_input() -> None:
    for key, model in load_registries().models.items():
        assert model.get("allows_training_on_input") is False, (
            f"{key} does not declare that input is excluded from training"
        )


# ------------------------------------------------------- endpoint resolution
def test_each_model_reaches_its_own_endpoint(endpoints) -> None:
    """The bug this prevents: two models sharing one base URL from settings."""
    onprem = provider_for_model(ONPREM)
    cloud = provider_for_model(CLOUD)
    assert onprem._resolve()[0] == "http://vllm.internal/v1"
    assert cloud._resolve()[0] == "https://oss-host.example/v1"
    assert onprem.name != cloud.name


def test_a_self_hosted_endpoint_needs_no_external_provider_switch(endpoints, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "model_allow_external_providers", False, raising=False)
    assert provider_for_model(ONPREM).available() is True
    # The same switch still gates the third-party host.
    assert provider_for_model(CLOUD).available() is False


def test_an_unconfigured_endpoint_is_unavailable_not_misrouted(monkeypatch) -> None:
    """It must not fall back to some other server the entry never named."""
    monkeypatch.setattr(get_settings(), "model_endpoints", "", raising=False)
    provider = provider_for_model(ONPREM)
    assert provider.available() is False
    with pytest.raises(UpstreamUnavailable, match="not configured"):
        provider.complete(ModelRequest(system="s", user="u"), ONPREM["provider_model_id"])


def test_a_malformed_endpoint_map_is_a_loud_configuration_error(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "model_endpoints", "{not json", raising=False)
    with pytest.raises(ValueError, match="not valid JSON"):
        get_settings().endpoints()


# ------------------------------------------------------------- the wire call
def test_a_self_hosted_call_uses_the_openai_chat_completions_path(endpoints, monkeypatch) -> None:
    capture: dict = {}
    monkeypatch.setattr(
        OpenAICompatibleProvider, "_client", lambda self, base, headers: _stub(capture, headers)
    )
    provider = provider_for_model(ONPREM)
    response = provider.complete(
        ModelRequest(system="be precise", user="summarise the incident"),
        ONPREM["provider_model_id"],
    )
    assert response.text == "grounded answer"
    assert response.input_tokens == 11 and response.output_tokens == 3
    assert capture["url"].endswith("/chat/completions")
    assert capture["body"]["model"] == "meta-llama/Llama-3.3-70B-Instruct"
    # A privately operated endpoint carries no bearer token.
    assert capture["auth"] == ""


def test_the_default_local_base_url_includes_the_v1_prefix() -> None:
    """Ollama and vLLM serve the compatible surface under /v1.

    Without it every call 404s, which is how a self-hosted deployment fails
    silently rather than loudly.
    """
    assert get_settings().local_model_base_url.rstrip("/").endswith("/v1")


def test_a_cloud_endpoint_sends_its_resolved_key_and_never_the_reference(endpoints, monkeypatch) -> None:
    capture: dict = {}
    monkeypatch.setattr(get_settings(), "model_allow_external_providers", True, raising=False)
    monkeypatch.setattr(providers, "_endpoint_api_key", lambda endpoint, name: "resolved-key")
    monkeypatch.setattr(
        OpenAICompatibleProvider, "_client", lambda self, base, headers: _stub(capture, headers)
    )
    provider = provider_for_model(CLOUD)
    provider.complete(ModelRequest(system="s", user="u"), CLOUD["provider_model_id"])

    assert capture["auth"] == "Bearer resolved-key"
    # The reference itself must never travel to the provider.
    assert "env://" not in json.dumps(capture["body"])
    assert "env://" not in capture["auth"]
