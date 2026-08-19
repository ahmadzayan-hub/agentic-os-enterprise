"""Model provider adapters.

Three adapters ship:

``DeterministicProvider``
    A real, rule-based text engine — extractive summarisation, lexical
    classification, pattern extraction and template drafting. It is **not** a
    language model and never pretends to be one: every response is labelled
    ``generative=False`` and it returns an explicit unsupported result with
    zero confidence rather than inventing an answer. It exists so the platform
    is fully executable offline, in CI and for RESTRICTED data.

``AnthropicProvider`` / ``OpenAICompatibleProvider``
    Real HTTP adapters. They stay inert until credentials are configured *and*
    ``AGENTIC_MODEL_ALLOW_EXTERNAL_PROVIDERS`` is true, so a misconfigured
    deployment cannot silently ship data to a third party.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from agentic_os.core.config import get_settings
from agentic_os.core.errors import (
    UpstreamTimeout,
    UpstreamUnavailable,
    ValidationError,
)

log = logging.getLogger("agentic_os.ai.providers")


@dataclass(slots=True)
class ModelRequest:
    """One inference request as it reaches a provider."""

    system: str
    user: str
    task_kind: str = "general"
    max_output_tokens: int = 2048
    temperature: float = 0.0
    response_format: str = "text"  # "text" | "json"
    payload: dict[str, Any] = field(default_factory=dict)
    stop: tuple[str, ...] = ()


@dataclass(slots=True)
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int
    provider: str
    model_id: str
    latency_ms: int = 0
    generative: bool = True
    confidence: float | None = None
    finish_reason: str = "stop"
    structured: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        if self.structured is not None:
            return self.structured
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "model response was not valid JSON", details={"snippet": self.text[:300]}
            ) from exc


class ModelProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, request: ModelRequest, model_id: str) -> ModelResponse: ...


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Approximate token count.

    Providers return authoritative counts; this is used for the deterministic
    provider and for pre-flight budget checks, and is labelled as an estimate
    wherever it is surfaced.
    """
    return max(1, len(_TOKEN_RE.findall(text)))


# ---------------------------------------------------------------------------
# Deterministic provider
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have if in into is it its of on or
    that the their then there these they this to was were which will with would
    should could may can not no do does did been being had were our your""".split()
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _tokenise(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOPWORDS and len(w) > 1]


class DeterministicProvider:
    """Rule-based, reproducible text operations. Never generative."""

    name = "deterministic"

    def available(self) -> bool:
        return True

    def complete(self, request: ModelRequest, model_id: str) -> ModelResponse:
        handler = {
            "summarise": self._summarise,
            "classify": self._classify,
            "extract": self._extract,
            "draft": self._draft,
            "answer": self._answer,
            "plan": self._plan,
        }.get(request.task_kind)

        if handler is None:
            structured = {
                "supported": False,
                "reason": (
                    f"the deterministic provider has no rule for task kind "
                    f"'{request.task_kind}'; configure an approved generative model "
                    f"or use a deterministic skill"
                ),
                "confidence": 0.0,
            }
        else:
            structured = handler(request)

        text = json.dumps(structured, ensure_ascii=False)
        return ModelResponse(
            text=text,
            structured=structured,
            input_tokens=estimate_tokens(request.system) + estimate_tokens(request.user),
            output_tokens=estimate_tokens(text),
            provider=self.name,
            model_id=model_id,
            generative=False,
            confidence=float(structured.get("confidence", 0.0)),
        )

    # -- operations --------------------------------------------------------
    def _summarise(self, request: ModelRequest) -> dict[str, Any]:
        """Extractive summarisation: rank sentences by TF-IDF-weighted overlap.

        Every sentence in the output is quoted verbatim from a source, so the
        summary is grounded by construction and citations are exact.
        """
        sources: list[dict] = request.payload.get("sources", [])
        max_sentences = int(request.payload.get("max_sentences", 5))
        if not sources:
            return {"supported": False, "reason": "no sources supplied", "confidence": 0.0}

        sentences: list[tuple[str, str]] = []  # (source_id, sentence)
        for source in sources:
            for sentence in _SENTENCE_SPLIT.split(source.get("text", "").strip()):
                clean = sentence.strip()
                if len(clean.split()) >= 4:
                    sentences.append((str(source.get("id", "")), clean))
        if not sentences:
            return {"supported": False, "reason": "sources contained no usable text", "confidence": 0.0}

        document_freq: Counter[str] = Counter()
        tokenised = [_tokenise(s) for _, s in sentences]
        for tokens in tokenised:
            document_freq.update(set(tokens))
        total = len(sentences)

        query_terms = set(_tokenise(request.payload.get("focus", "") or request.user))
        scored: list[tuple[float, int]] = []
        for index, tokens in enumerate(tokenised):
            if not tokens:
                continue
            counts = Counter(tokens)
            score = sum(
                (count / len(tokens)) * math.log(total / (1 + document_freq[term]))
                for term, count in counts.items()
            )
            if query_terms:
                overlap = len(query_terms & set(tokens)) / len(query_terms)
                score *= 1.0 + overlap
            # Mild positional prior: leading sentences carry more topic weight.
            score *= 1.0 + (0.15 if index < 2 else 0.0)
            scored.append((score, index))

        scored.sort(reverse=True)
        chosen = sorted(index for _, index in scored[:max_sentences])
        summary = " ".join(sentences[i][1] for i in chosen)
        citations = sorted({sentences[i][0] for i in chosen if sentences[i][0]})
        coverage = len(chosen) / min(max_sentences, len(sentences))
        return {
            "supported": True,
            "summary": summary,
            "citations": citations,
            "method": "extractive_tfidf",
            "sentences_selected": len(chosen),
            "confidence": round(min(0.85, 0.5 + 0.35 * coverage), 3),
        }

    def _classify(self, request: ModelRequest) -> dict[str, Any]:
        text = request.payload.get("text", request.user)
        labels: list[str] = request.payload.get("labels", [])
        if not labels:
            return {"supported": False, "reason": "no labels supplied", "confidence": 0.0}
        tokens = Counter(_tokenise(text))
        hints: dict[str, list[str]] = request.payload.get("label_hints", {})
        scores: dict[str, float] = {}
        for label in labels:
            terms = _tokenise(label) + _tokenise(" ".join(hints.get(label, [])))
            scores[label] = float(sum(tokens[t] for t in set(terms)))
        best = max(scores, key=lambda k: scores[k])
        total = sum(scores.values())
        score = (scores[best] / total) if total else 0.0
        return {
            "supported": True,
            "label": best if total else labels[0],
            "score": round(score, 3),
            "scores": {k: round(v, 3) for k, v in scores.items()},
            "rationale": "lexical overlap with label terms and hints",
            "confidence": round(score, 3) if total else 0.0,
        }

    def _extract(self, request: ModelRequest) -> dict[str, Any]:
        text = request.payload.get("text", request.user)
        fields: list[str] = request.payload.get("fields", [])
        if not fields:
            return {"supported": False, "reason": "no fields requested", "confidence": 0.0}
        values: dict[str, Any] = {}
        spans: dict[str, list[int]] = {}
        for name in fields:
            pattern = re.compile(rf"{re.escape(name)}\s*[:=-]\s*(?P<value>[^\n,;]{{1,200}})", re.IGNORECASE)
            match = pattern.search(text)
            if match:
                values[name] = match.group("value").strip()
                spans[name] = [match.start("value"), match.end("value")]
        missing = [f for f in fields if f not in values]
        found_ratio = len(values) / len(fields)
        return {
            "supported": True,
            "values": values,
            "missing": missing,
            "spans": spans,
            "method": "labelled_field_pattern",
            "confidence": round(found_ratio, 3),
        }

    def _draft(self, request: ModelRequest) -> dict[str, Any]:
        evidence: list[dict] = request.payload.get("evidence", [])
        document_type = request.payload.get("document_type", "note")
        subject = request.payload.get("subject", request.user[:120])
        if not evidence:
            return {"supported": False, "reason": "no evidence supplied", "confidence": 0.0}
        lines = [f"Subject: {subject}", "", f"This {document_type} summarises the following findings:"]
        citations: list[str] = []
        for item in evidence[:12]:
            source = str(item.get("id", ""))
            statement = str(item.get("statement", item.get("text", ""))).strip()
            if not statement:
                continue
            lines.append(f"- {statement} [{source}]" if source else f"- {statement}")
            if source:
                citations.append(source)
        lines += ["", "Prepared for human review. No action has been taken."]
        body = "\n".join(lines)
        return {
            "supported": True,
            "draft": body,
            "citations": sorted(set(citations)),
            "requires_human_send": True,
            "word_count": len(body.split()),
            "method": "evidence_template",
            "confidence": 0.6,
        }

    def _answer(self, request: ModelRequest) -> dict[str, Any]:
        """Answer strictly from supplied context, or decline."""
        summary = self._summarise(request)
        if not summary.get("supported"):
            return {
                "supported": True,
                "answer": ("The authorised evidence available does not support an answer to this question."),
                "citations": [],
                "grounded": False,
                "confidence": 0.0,
            }
        return {
            "supported": True,
            "answer": summary["summary"],
            "citations": summary["citations"],
            "grounded": True,
            "method": "extractive_answer",
            "confidence": summary["confidence"],
        }

    #: Skills whose inputs a planner can derive from a prose objective plus the
    #: output of earlier retrieval steps.
    #:
    #: Skills outside this set — calculate, compare, forecast, optimise,
    #: reconcile, transform, draft — need structured inputs (an expression, a
    #: weighted criteria set, a numeric series) that no amount of reading the
    #: objective will produce. They remain fully available through workflows,
    #: where the definition supplies their inputs explicitly. Proposing them
    #: from free text would only produce a step that fails at validation.
    PROSE_DERIVABLE_SKILLS = frozenset(
        {"search", "retrieve", "summarise", "analyse", "classify", "extract", "verify"}
    )

    def _plan(self, request: ModelRequest) -> dict[str, Any]:
        """Rule-based planning over the capabilities the caller actually holds.

        The plan is assembled by matching intent verbs in the objective to
        available skills. It never names a capability that was not supplied,
        and never proposes a step whose inputs cannot be built.
        """
        objective: str = request.payload.get("objective", request.user)
        capabilities: dict[str, Any] = request.payload.get("capabilities", {})
        available_skills: list[str] = capabilities.get("skills", [])
        available_tools: list[str] = capabilities.get("tools", [])
        agent_key: str = capabilities.get("agent", "")

        if not available_skills:
            return {
                "supported": True,
                "steps": [],
                "rationale": "no skills are available to this agent for the objective",
                "confidence": 0.0,
            }

        verbs = {
            "search": ("search", "find", "look", "locate", "which", "what", "where", "list"),
            "retrieve": ("retrieve", "fetch", "open", "read", "get"),
            "analyse": ("analyse", "analyze", "assess", "evaluate", "review", "investigate", "why"),
            "calculate": (
                "calculate",
                "comput",
                "total",
                "average",
                "percentage",
                "how much",
                "how many",
            ),
            "compare": ("compare", "versus", "against", "rank", "prioritise", "prioritize"),
            "forecast": ("forecast", "project", "predict", "trend", "next"),
            "reconcile": ("reconcile", "match", "difference", "discrepanc"),
            "classify": ("classify", "categorise", "categorize", "triage", "label"),
            "extract": ("extract", "pull", "parse", "field"),
            "summarise": ("summarise", "summarize", "summary", "brief", "overview"),
            "draft": ("draft", "write", "prepare", "compose", "letter", "email", "report"),
            "optimise": ("optimise", "optimize", "best", "budget", "select", "allocate"),
            "validate": ("validate", "check", "verify", "conform", "comply"),
            "verify": ("verify", "confirm", "substantiate"),
            "transform": ("transform", "convert", "map", "normalise", "normalize"),
        }
        # Match on word prefixes, not raw substrings. A substring test makes
        # "summarise" trigger the "sum" calculate verb, which then produces a
        # step whose inputs cannot be supplied.
        lowered = objective.lower()
        words = re.findall(r"[a-z]+", lowered)

        def triggered(triggers: tuple[str, ...]) -> bool:
            return any(
                any(word.startswith(trigger) for word in words) if " " not in trigger else trigger in lowered
                for trigger in triggers
            )

        matched = [
            skill
            for skill, triggers in verbs.items()
            if skill in available_skills and skill in self.PROSE_DERIVABLE_SKILLS and triggered(triggers)
        ]

        # Grounded work always starts by retrieving evidence.
        if "search" in available_skills and "search" not in matched:
            matched.insert(0, "search")
        # And ends by summarising what was found, when that is permitted.
        if "summarise" in available_skills and "summarise" not in matched:
            matched.append("summarise")
        if not matched:
            derivable = [s for s in available_skills if s in self.PROSE_DERIVABLE_SKILLS]
            matched = derivable[:1] or available_skills[:1]

        steps = []
        for index, skill in enumerate(matched[:6]):
            tool = None
            if skill == "search" and "knowledge.search" in available_tools:
                tool = "knowledge.search"
            elif skill == "retrieve" and "knowledge.fetch_document" in available_tools:
                tool = "knowledge.fetch_document"
            elif skill == "calculate" and "calc.evaluate" in available_tools:
                tool = "calc.evaluate"
            steps.append(
                {
                    "index": index,
                    "key": f"step-{index + 1}-{skill}",
                    "agent": agent_key,
                    "skill": skill,
                    "tool": tool,
                    "description": f"Apply the '{skill}' skill toward: {objective[:160]}",
                    "requires_approval": False,
                    "produces": f"{skill}_result",
                }
            )
        return {
            "supported": True,
            "steps": steps,
            "rationale": (
                "Deterministic plan: intent verbs in the objective matched to the skills "
                "this agent's contract permits, bracketed by evidence retrieval and "
                "summarisation."
            ),
            "method": "rule_based_intent_match",
            "confidence": round(min(0.75, 0.4 + 0.08 * len(steps)), 3),
        }


# ---------------------------------------------------------------------------
# HTTP providers
# ---------------------------------------------------------------------------
class _HttpProviderBase:
    name = "http"

    def _client(self, base_url: str, headers: dict[str, str]) -> httpx.Client:
        settings = get_settings()
        return httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=settings.model_request_timeout_seconds,
            follow_redirects=False,
        )

    def _post(self, client: httpx.Client, path: str, body: dict) -> dict:
        try:
            response = client.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(f"{self.name} request timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(f"{self.name} request failed: {exc}") from exc

        if response.status_code == 429:
            from agentic_os.core.errors import RateLimited

            raise RateLimited(f"{self.name} rate limited")
        if response.status_code >= 500:
            raise UpstreamUnavailable(
                f"{self.name} returned {response.status_code}",
                details={"status": response.status_code},
            )
        if response.status_code >= 400:
            raise ValidationError(
                f"{self.name} rejected the request ({response.status_code})",
                details={"body": response.text[:500]},
            )
        return response.json()


class AnthropicProvider(_HttpProviderBase):
    name = "anthropic"

    def available(self) -> bool:
        settings = get_settings()
        return bool(settings.model_allow_external_providers and settings.anthropic_api_key)

    def complete(self, request: ModelRequest, model_id: str) -> ModelResponse:
        import time

        settings = get_settings()
        if not self.available():
            raise UpstreamUnavailable(
                "Anthropic provider is not enabled: set AGENTIC_ANTHROPIC_API_KEY and "
                "AGENTIC_MODEL_ALLOW_EXTERNAL_PROVIDERS=true"
            )
        started = time.perf_counter()
        with self._client(
            settings.anthropic_base_url,
            {
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        ) as client:
            body = {
                "model": model_id,
                "max_tokens": request.max_output_tokens,
                "temperature": request.temperature,
                "system": request.system,
                "messages": [{"role": "user", "content": request.user}],
            }
            if request.stop:
                body["stop_sequences"] = list(request.stop)
            data = self._post(client, "/v1/messages", body)

        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return ModelResponse(
            text=text,
            input_tokens=int(usage.get("input_tokens", estimate_tokens(request.user))),
            output_tokens=int(usage.get("output_tokens", estimate_tokens(text))),
            provider=self.name,
            model_id=model_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=data.get("stop_reason", "stop"),
        )


class OpenAICompatibleProvider(_HttpProviderBase):
    """Works against OpenAI, vLLM, Ollama and any compatible endpoint."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        endpoint: dict[str, Any] | None = None,
        endpoint_name: str = "",
        external: bool | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._endpoint = endpoint
        self._endpoint_name = endpoint_name
        self._external = external
        if endpoint_name:
            self.name = f"openai-compatible[{endpoint_name}]"

    def _resolve(self) -> tuple[str, str]:
        settings = get_settings()
        base = self._base_url or settings.openai_base_url or settings.local_model_base_url
        if self._api_key is not None:
            key = self._api_key
        elif self._endpoint is not None:
            key = _endpoint_api_key(self._endpoint, self._endpoint_name)
        else:
            key = settings.openai_api_key
        return base, key

    def available(self) -> bool:
        settings = get_settings()
        base, key = self._resolve()
        if not base:
            return False
        if self._external is False:
            # An operator has declared this endpoint privately operated. It is
            # reachable without the external-provider switch, and needs no key.
            return True
        if self._external is None:
            is_local = base.startswith(("http://127.0.0.1", "http://localhost", "http://host.docker"))
            if is_local:
                # A privately operated endpoint is not an external provider.
                return True
        return bool(settings.model_allow_external_providers and key)

    def complete(self, request: ModelRequest, model_id: str) -> ModelResponse:
        import time

        base, key = self._resolve()
        if not self.available():
            raise UpstreamUnavailable("OpenAI-compatible provider is not configured or not permitted")
        started = time.perf_counter()
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        with self._client(base, headers) as client:
            body: dict[str, Any] = {
                "model": model_id,
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
            }
            if request.response_format == "json":
                body["response_format"] = {"type": "json_object"}
            if request.stop:
                body["stop"] = list(request.stop)
            data = self._post(client, "/chat/completions", body)

        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content", "")
        usage = data.get("usage", {})
        return ModelResponse(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", estimate_tokens(request.user))),
            output_tokens=int(usage.get("completion_tokens", estimate_tokens(text))),
            provider=self.name,
            model_id=model_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=choice.get("finish_reason", "stop"),
        )


PROVIDERS: dict[str, ModelProvider] = {
    "deterministic": DeterministicProvider(),
    "anthropic": AnthropicProvider(),
    "openai-compatible": OpenAICompatibleProvider(),
}


def get_provider(name: str) -> ModelProvider:
    if name not in PROVIDERS:
        raise ValidationError(f"unknown model provider '{name}'")
    return PROVIDERS[name]


def _endpoint_api_key(endpoint: dict[str, Any], name: str) -> str:
    """Resolve an endpoint's credential through the secret broker.

    The endpoint map holds a *reference*, never a key. An endpoint that needs
    no credential — a self-hosted vLLM on a private network, typically — omits
    it and gets an empty string.

    Resolution is lazy and non-fatal: a reference that will not resolve makes
    the endpoint unavailable so the gateway routes on, rather than raising out
    of a routing decision that was only asking whether the model could be used.
    """
    reference = str(endpoint.get("api_key_ref", "")).strip()
    if not reference:
        return ""
    from agentic_os.tools.secrets import SecretBroker

    try:
        value, _handle = SecretBroker().resolve(reference)
        return value
    except Exception:  # noqa: BLE001 - unresolvable means unavailable, not broken
        log.warning("model endpoint %r declares %s, which did not resolve", name, reference)
        return ""


def provider_for_model(model: dict[str, Any]) -> ModelProvider:
    """The provider instance a specific registry entry should use.

    Several OpenAI-compatible backends can be registered at once — a
    self-hosted open-weights deployment for RESTRICTED work and a cloud
    open-weights host for everything else — because each registry entry names
    its own ``endpoint``. Without this every such model would share one base
    URL from settings and the second one registered would silently talk to the
    first one's server.
    """
    provider_name = model.get("provider", "")
    endpoint_name = str(model.get("endpoint", "") or "").strip()
    if not endpoint_name:
        return get_provider(provider_name)

    if provider_name != "openai-compatible":
        raise ValidationError(
            f"model '{model.get('key')}' names an endpoint but provider '{provider_name}' does not take one"
        )

    endpoints = get_settings().endpoints()
    if endpoint_name not in endpoints:
        # Not configured is not the same as broken: the gateway treats the
        # model as unavailable and routes on, rather than failing the run.
        return _UnconfiguredEndpoint(endpoint_name)

    endpoint = endpoints[endpoint_name]
    base_url = str(endpoint.get("base_url", "")).strip()
    if not base_url:
        return _UnconfiguredEndpoint(endpoint_name)
    return OpenAICompatibleProvider(
        base_url=base_url,
        endpoint=endpoint,
        endpoint_name=endpoint_name,
        external=bool(endpoint.get("external", True)),
    )


class _UnconfiguredEndpoint:
    """Stands in for a registered model whose endpoint is not configured.

    It reports itself unavailable and refuses to complete. The alternative —
    falling through to some other endpoint — would send data to a server the
    registry entry never named.
    """

    def __init__(self, endpoint_name: str) -> None:
        self.name = f"openai-compatible[{endpoint_name}]"
        self._endpoint_name = endpoint_name

    def available(self) -> bool:
        return False

    def complete(self, request: ModelRequest, model_id: str) -> ModelResponse:
        raise UpstreamUnavailable(
            f"model endpoint '{self._endpoint_name}' is not configured; add it to AGENTIC_MODEL_ENDPOINTS"
        )
