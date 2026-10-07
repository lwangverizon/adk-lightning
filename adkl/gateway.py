#!/usr/bin/env python3
"""Project-configured model and embeddings through LiteLLM.

Choose `agent.provider: gemini` for the public Gemini API, or `gateway` for a
host-supplied OpenAI-compatible endpoint. Injected native SDK/host models do not
use this factory. New project templates explicitly select gemini. Omitting the
provider retains gateway for compatibility; no gateway URL or credentials are
bundled, and missing settings fail rather than silently changing providers.

Gemini reads the first available GEMINI_API_KEY, GOOGLE_API_KEY or
GOOGLE_AI_API_KEY. GEMINI_MODEL selects the completion model;
SKILL_EMBEDDING_MODEL and SKILL_EMBEDDING_DIMENSIONS select embeddings.

Gateway settings use the retained LITELLM_GATEWAY_API_BASE,
LITELLM_GATEWAY_API_KEY and LITELLM_GATEWAY_MODEL3P5 names. Embedding settings
are separate: SKILL_EMBEDDING_GATEWAY, SKILL_EMBEDDING_GATEWAY_MODEL and
SKILL_EMBEDDING_GATEWAY_DIMENSIONS. Deployment-specific model identifiers must
exist on the host's gateway; defaults are compatibility values, not availability
or performance guarantees.

Vector files must match the configured backend, model and dimensions. Equal
width does not imply a shared vector space. Zero dimensions means omit the
parameter; only supported Matryoshka models accept an explicit width. Queries
use RETRIEVAL_QUERY and index documents use RETRIEVAL_DOCUMENT. Rebuild and
validate indexes when changing model/backend/task conventions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Any, Sequence

logger = logging.getLogger("adkl.gateway")

# Models that accept a `dimensions` value, and which values. Anything absent
# here must not be sent one; validate before calling the embedding service.
_MATRYOSHKA: dict[str, frozenset] = {
    "gemini-embedding-001": frozenset({768, 1536, 3072}),
}


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _base_model(model_id: str) -> str:
    """Strip a region/deployment suffix: text-embedding-005_standard-us-east4."""
    return model_id.split("_", 1)[0]


class GatewayError(RuntimeError):
    """Configuration is wrong in a way that would produce bad vectors."""


# ------------------------------------------------------------------ providers
#
# Every call in this module goes through LiteLLM, on purpose: one wire path, one
# thinking-forwarding wrapper, one embedder. What differs per provider is only
# the route prefix, whether an `api_base` is sent, and which env var carries the
# key. Adding a provider must not add a second code path -- that is how the
# thinking_config bug below got to exist in the first place.
#
# `gemini` routes to Google's Gemini API (AI Studio) with an ordinary API key,
# using the same LiteLLM wrapper, retry/timeout surface and embedder as gateways.


@dataclass(frozen=True)
class Provider:
    """How one backend is addressed. Data, not behaviour -- see `build_model`."""

    name: str
    # LiteLLM refuses a bare model id ("LLM Provider NOT provided"), so both the
    # completion and embedding routes need a prefix.
    completion_prefix: str
    embedding_prefix: str
    uses_api_base: bool
    # First var that is set wins. Several are accepted for `gemini` because the
    # ecosystem never settled on one: litellm reads GEMINI_API_KEY, google-genai
    # reads GOOGLE_API_KEY. GOOGLE_AI_API_KEY is an additional compatibility alias.
    key_vars: tuple
    base_vars: tuple
    model_vars: tuple
    default_model: str
    embedding_model_vars: tuple
    default_embedding_model: str
    embedding_dimensions_vars: tuple
    embedding_enabled_vars: tuple
    # Which kwarg carries the thinking config to this route. See
    # `_thinking_args` -- the two are not interchangeable.
    thinking: str

    @property
    def backend(self) -> str:
        """The value stamped into a vector file's `embedding_metadata.backend`.

        Vector spaces are not interchangeable, so this is what `check_vectors`
        compares. It is the provider name because that is what actually
        determines the space.
        """
        return self.name


PROVIDERS: dict[str, Provider] = {
    "gateway": Provider(
        name="gateway",
        # OpenAI-compatible gateways require the `openai/` LiteLLM prefix.
        completion_prefix="openai",
        embedding_prefix="openai",
        uses_api_base=True,
        key_vars=("LITELLM_GATEWAY_API_KEY",),
        base_vars=("LITELLM_GATEWAY_API_BASE",),
        model_vars=("LITELLM_GATEWAY_MODEL3P5",),
        default_model="gemini-3.6-flash",
        embedding_model_vars=("SKILL_EMBEDDING_GATEWAY_MODEL",),
        default_embedding_model="text-embedding-005_standard-us-east4",
        embedding_dimensions_vars=("SKILL_EMBEDDING_GATEWAY_DIMENSIONS",),
        embedding_enabled_vars=("SKILL_EMBEDDING_GATEWAY",),
        thinking="extra_body",
    ),
    "gemini": Provider(
        name="gemini",
        completion_prefix="gemini",
        embedding_prefix="gemini",
        # Google's endpoint is the default for the route; sending an api_base
        # would only be a way to get it wrong.
        uses_api_base=False,
        key_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_AI_API_KEY"),
        base_vars=(),
        model_vars=("GEMINI_MODEL",),
        default_model="gemini-3.6-flash",
        embedding_model_vars=("SKILL_EMBEDDING_MODEL",),
        # Native embedding width is 3072.
        default_embedding_model="gemini-embedding-001",
        embedding_dimensions_vars=("SKILL_EMBEDDING_DIMENSIONS",),
        embedding_enabled_vars=("SKILL_EMBEDDING_ENABLED",),
        thinking="thinking_config",
    ),
}

DEFAULT_PROVIDER = "gateway"


def get_provider(name: str | None) -> Provider:
    """Look up a provider, naming the alternatives when it is not one."""
    key = name or DEFAULT_PROVIDER
    try:
        return PROVIDERS[key]
    except KeyError:
        raise GatewayError(
            f"unknown provider {key!r}; expected one of: "
            f"{', '.join(sorted(PROVIDERS))}") from None


def _first(env: dict, names: tuple) -> str:
    for n in names:
        val = env.get(n)
        if val:
            return val
    return ""


def read_env(root: pathlib.Path) -> dict:
    """Read only this project's declared credential sources.

    No repository-root fallback or ambient process credentials. Managed callers
    pass an explicit settings dictionary; profiles may name inherited secrets.
    """
    from adkl import execution
    if (root / "execution.yaml").is_file():
        name, entry = execution.profile(root)
        execution.require_local(root, name)
        local = execution.dotenv(execution.inside(root, entry.get("env_file", ".env")))
        env = dict(entry.get("env", {}))
        env.update({k: local[k] for k in entry.get("secrets", []) if k in local})
        env.update({k: os.environ[k] for k in entry.get("inherit", []) if k in os.environ})
        return env
    return execution.dotenv(root / ".env")


@dataclass
class GatewaySettings:
    # Which backend these settings address. Declared in agent.yaml as
    # `agent.provider`, because it is a property of the project rather than of
    # the machine -- the .env carries only the credential.
    provider: str = DEFAULT_PROVIDER
    api_base: str = ""
    api_key: str = ""
    # Retained compatibility defaults; explicitly provision supported model IDs.
    model: str = "gemini-3.6-flash"
    embedding_model: str = "text-embedding-005_standard-us-east4"
    embedding_dimensions: int = 0
    embedding_enabled: bool = True
    timeout_seconds: float = 5.0

    @property
    def spec(self) -> Provider:
        return get_provider(self.provider)

    @classmethod
    def from_env(cls, env: dict | None = None,
                 provider: str | None = None) -> "GatewaySettings":
        """Read settings for `provider`, defaulting to the gateway.

        `ADKL_PROVIDER` is honoured when no provider is passed, so a one-off
        probe script can switch backends without an agent.yaml. An explicit
        argument always wins: the project's own declaration is not something an
        ambient env var should be able to override.
        """
        e = env if env is not None else os.environ
        p = get_provider(provider or e.get("ADKL_PROVIDER"))
        return cls(
            provider=p.name,
            api_base=_first(e, p.base_vars),
            api_key=_first(e, p.key_vars),
            model=_first(e, p.model_vars) or p.default_model,
            embedding_model=(_first(e, p.embedding_model_vars)
                             or p.default_embedding_model),
            embedding_dimensions=int(
                _first(e, p.embedding_dimensions_vars) or 0),
            embedding_enabled=_truthy(
                _first(e, p.embedding_enabled_vars) or None, True),
            timeout_seconds=float(
                e.get("SKILL_EMBEDDING_TIMEOUT_SECONDS", "5.0") or 5.0),
        )

    @property
    def configured(self) -> bool:
        p = self.spec
        return bool(self.api_key and (self.api_base or not p.uses_api_base))

    def missing(self) -> list[str]:
        """Env var names to set, most-preferred first.

        Alternates are shown rather than hidden: told only "GEMINI_API_KEY is
        missing", someone with GOOGLE_API_KEY already exported would set a
        second variable to the same value.
        """
        p = self.spec
        out = []
        if p.uses_api_base and not self.api_base:
            out.append(_name_hint(p.base_vars))
        if not self.api_key:
            out.append(_name_hint(p.key_vars))
        return out


def _name_hint(names: tuple) -> str:
    if len(names) == 1:
        return names[0]
    return f"{names[0]} (or {', '.join(names[1:])})"


class GatewayEmbedder:
    """Embeddings via LiteLLM. Satisfies `retrieval.EmbeddingService`.

    Named for the gateway because that is where it started; it now serves every
    provider in `PROVIDERS`, which is why the settings object carries the route
    rather than this class hardcoding it.

    `task_type` defaults to RETRIEVAL_QUERY because the runtime path embeds
    queries; `adkl embed` constructs a second instance with RETRIEVAL_DOCUMENT.
    """

    def __init__(
        self,
        settings: GatewaySettings,
        task_type: str = "RETRIEVAL_QUERY",
        litellm_module: Any = None,
    ):
        if not settings.configured:
            raise GatewayError(
                f"provider {settings.provider!r} is not configured; missing "
                + ", ".join(settings.missing()))

        dims = settings.embedding_dimensions
        base = _base_model(settings.embedding_model)
        if dims:
            allowed = _MATRYOSHKA.get(base)
            if allowed is None:
                raise GatewayError(
                    f"{settings.embedding_model!r} does not support a "
                    f"`dimensions` value (only Matryoshka models do: "
                    f"{', '.join(sorted(_MATRYOSHKA))}). Set "
                    f"SKILL_EMBEDDING_GATEWAY_DIMENSIONS=0 to use the model's "
                    f"native width.")
            if dims not in allowed:
                raise GatewayError(
                    f"{base} accepts dimensions "
                    f"{sorted(allowed)}, not {dims}.")

        self.settings = settings
        self.task_type = task_type
        self._litellm = litellm_module   # injected in tests

    def _client(self):
        if self._litellm is None:
            try:
                import litellm
            except ImportError as exc:  # pragma: no cover
                raise GatewayError(
                    "litellm is not installed. Install the ADKL runtime extra; "
                    "install it here or pass litellm_module=") from exc
            self._litellm = litellm
        return self._litellm

    @property
    def _model(self) -> str:
        """litellm needs a provider prefix or it refuses the call outright:
        "LLM Provider NOT provided". Which prefix depends on the provider --
        `openai/` for the gateway (it speaks the OpenAI API; production does the
        same at utils/gateway_embedder.py:110), `gemini/` for Google's own
        endpoint. Left alone if a prefix is already there.
        """
        model = self.settings.embedding_model
        prefix = self.settings.spec.embedding_prefix
        return model if "/" in model else f"{prefix}/{model}"

    def _kwargs(self) -> dict:
        p = self.settings.spec
        kw: dict[str, Any] = {
            "model": self._model,
            "api_key": self.settings.api_key,
            # Retrieval embeddings are asymmetric: queries and documents are
            # embedded differently by the same model, and dropping this degrades
            # ranking without any error.
            "task_type": self.task_type,
        }
        if p.uses_api_base:
            kw["api_base"] = self.settings.api_base
        if self.settings.embedding_dimensions:
            if p.name == "gateway":
                # NOT a top-level kwarg here: litellm rejects `dimensions`
                # client-side for text-embedding-00x, so it has to ride in
                # extra_body.
                kw["extra_body"] = {
                    "dimensions": self.settings.embedding_dimensions}
            else:
                # On the gemini route the model IS Matryoshka, so litellm
                # accepts the top-level param and maps it to
                # output_dimensionality. extra_body would bypass that mapping.
                kw["dimensions"] = self.settings.embedding_dimensions
        return kw

    async def embed(self, text: str) -> Sequence[float]:
        """One query vector. The interface `SkillRetrieval` expects."""
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[Sequence[float]]:
        """Batch, used by `adkl embed` to build a whole index in one call."""
        client = self._client()
        response = await client.aembedding(input=texts, **self._kwargs())
        data = response["data"] if isinstance(response, dict) else response.data
        # Order is not guaranteed to match input order; the API returns an index.
        out: list[Any] = [None] * len(texts)
        for i, item in enumerate(data):
            emb = item["embedding"] if isinstance(item, dict) else item.embedding
            idx = (item.get("index", i) if isinstance(item, dict)
                   else getattr(item, "index", i))
            out[idx if idx is not None else i] = emb
        missing = [i for i, v in enumerate(out) if v is None]
        if missing:
            raise GatewayError(
                f"{self.settings.provider} returned {len(data)} embeddings for "
                f"{len(texts)} inputs; positions {missing} are missing")
        return out


# ADK ThinkingLevel -> Gemini's own thinkingLevel. Gemini 3 cannot fully disable
# thinking, so NONE becomes the lowest tier the model has: "minimal" on the 3.x
# flash line, "low" elsewhere. That is litellm's own rule for reasoning_effort
# "disable" (vertex_and_google_ai_studio_gemini.py), mirrored here so the two
# cannot disagree about what "off" means.
_LEVELS = {"low", "medium", "high"}


def _gemini_thinking_level(level: str, model: str | None) -> str:
    if level in _LEVELS:
        return level
    m = (model or "").lower()
    return "minimal" if ("gemini-3" in m and "flash" in m) else "low"


def _thinking_config(llm_request: Any) -> dict | None:
    """Read a request's ThinkingConfig into a plain dict, or None.

    ADK does not put `thinking_config` on the OpenAI-compatible wire -- it is a
    Vertex-native SDK field -- so on any LiteLLM path a per-agent thinking_level
    is silently dropped. With a thinking model that produced an EMPTY REPLY:
    finish_reason=STOP, no parts, no error. Production hit the same thing and
    forwards it via extra_body (utils/pt_aware_lite_llm.py).
    """
    try:
        cfg = getattr(llm_request, "config", None)
        tc = getattr(cfg, "thinking_config", None) if cfg is not None else None
        if tc is None:
            return None
        body: dict = {}
        if getattr(tc, "include_thoughts", None) is not None:
            body["include_thoughts"] = tc.include_thoughts
        level = getattr(tc, "thinking_level", None)
        if level is not None:
            body["thinking_level"] = getattr(level, "value", None) or str(level)
        return body or None
    except Exception:  # forwarding must never break a turn
        logger.debug("could not read thinking_config", exc_info=True)
        return None


def _thinking_args(llm_request: Any, spec: Provider,
                   model: str | None = None) -> dict:
    """Extra completion kwargs that carry the thinking config to this provider.

    Two shapes, because the two routes read different fields:

      * `extra_body` -- the gateway honours `thinking_config` verbatim.
      * `thinkingConfig` -- litellm's Gemini transform merges this straight into
        `generationConfig`, camelCase, both fields intact.

    ## Why not reasoning_effort

    It was the obvious choice and it is wrong. litellm maps every effort tier
    except "disable"/"none" to `includeThoughts: True`
    (`_map_reasoning_effort_to_thinking_level`), so an agent that set
    `include_thoughts: false` would silently start streaming the model's
    reasoning to the caller -- the thing agent_schema warns is "useful in a
    console, usually wrong in production". `thinking={"type": "enabled", ...}`
    is no better: on Gemini 3 it sets includeThoughts and drops the level
    entirely unless a global litellm flag is on.

    Passing `thinkingConfig` directly avoids both. Verified against litellm
    1.99.0 with `get_optional_params`, which returns it unchanged under
    drop_params either way, and ADK forwards `_additional_args` to the call
    verbatim (lite_llm.py: `completion_args.update(self._additional_args)`).
    """
    body = _thinking_config(llm_request)
    if not body:
        return {}

    if spec.thinking == "extra_body":
        return {"extra_body": {"thinking_config": body}}

    cfg: dict = {}
    level = str(body.get("thinking_level") or "").lower()
    if level:
        cfg["thinkingLevel"] = _gemini_thinking_level(level, model)
    if body.get("include_thoughts") is not None:
        cfg["includeThoughts"] = bool(body["include_thoughts"])
    return {"thinkingConfig": cfg} if cfg else {}


def build_model(settings: GatewaySettings, model: str | None = None) -> Any:
    """An ADK model object for the configured provider.

    Always a LiteLlm, whichever provider: one wire path means one place where
    thinking is forwarded, one retry surface, and one thing to debug. The
    provider only decides the route prefix and whether an api_base is sent.
    """
    name = model or settings.model
    spec = settings.spec
    if not name:
        raise GatewayError(
            f"no completion model for provider {spec.name!r}; set "
            f"{_name_hint(spec.model_vars)} or pass one")
    if not settings.configured:
        raise GatewayError(
            f"provider {spec.name!r} is not configured; missing "
            + ", ".join(settings.missing()))

    from google.adk.models.lite_llm import LiteLlm

    class _ThinkingAwareLiteLlm(LiteLlm):
        """Forwards the thinking level, which ADK drops on the LiteLLM path."""

        async def generate_content_async(self, llm_request, stream=False):
            # ADK exposes LiteLLM's request arguments only as an instance field.
            # Keep that compatibility seam contained: request-specific thinking
            # arguments must not bleed into the next turn.  Serializing this
            # small mutation also prevents two concurrent calls on one model
            # instance from restoring each other's settings.
            lock = getattr(self, "_adkl_generation_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                object.__setattr__(self, "_adkl_generation_lock", lock)

            async with lock:
                prior = self._additional_args
                extra = _thinking_args(llm_request, spec, name)
                if extra:
                    merged = dict(prior or {})
                    for key, value in extra.items():
                        if key == "extra_body":
                            body = dict(merged.get("extra_body") or {})
                            body.update(value)
                            merged["extra_body"] = body
                        else:
                            merged[key] = value
                    self._additional_args = merged
                try:
                    async for response in super().generate_content_async(
                            llm_request, stream):
                        yield response
                finally:
                    self._additional_args = prior

    kwargs: dict[str, Any] = {
        "model": name if "/" in name else f"{spec.completion_prefix}/{name}",
        "api_key": settings.api_key,
    }
    if spec.uses_api_base:
        kwargs["api_base"] = settings.api_base
    return _ThinkingAwareLiteLlm(**kwargs)


def check_vectors(vector_file: Any, settings: GatewaySettings) -> list[str]:
    """Compare a loaded vector file against the configured embedder.

    Returns human-readable problems. Empty means the file and the model agree.

    This exists because the failure is silent: cosine similarity across two
    vector spaces returns plausible numbers rather than raising.
    gemini-embedding-001@768 and text-embedding-005@768 are the same width and
    measured 0.028 apart on the same text.
    """
    problems: list[str] = []

    p = settings.spec
    if vector_file.backend and vector_file.backend != p.backend:
        problems.append(
            f"vector file was built by the {vector_file.backend!r} backend but "
            f"the {p.backend!r} embedder is configured. Different backends are "
            f"different vector spaces even at the same width -- 'local' is a "
            f"384-dim ONNX space, and gemini-embedding-001@768 measured 0.028 "
            f"from text-embedding-005@768 on the same text. Rebuild with "
            f"`adkl embed`, or point at the matching file.")

    want = settings.embedding_dimensions or None
    got = vector_file.dimensions
    if want and got and want != got:
        problems.append(
            f"vector file is {got}-dimensional but "
            f"{_name_hint(p.embedding_dimensions_vars)}={want}.")

    if vector_file.task_type and vector_file.task_type != "RETRIEVAL_DOCUMENT":
        problems.append(
            f"vector file records task_type={vector_file.task_type!r}; document "
            f"embeddings should be RETRIEVAL_DOCUMENT. Ranking degrades "
            f"silently when query and document task types are mismatched.")

    return problems
