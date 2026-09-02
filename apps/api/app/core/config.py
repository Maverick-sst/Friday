from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings.

    Values are read from environment variables first, then from a local
    `.env` (apps/api/.env) or the repo root `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        extra="ignore",
    )

    # --- Application ---
    app_env: str = "development"
    log_level: str = "INFO"
    secret_key: str = "dev-only-secret-key-change-me-32b"
    web_origin: str = "http://localhost:5173"

    # --- Database ---
    database_url: str = "postgresql+psycopg://agent_commerce:agent_commerce@localhost:5439/agent_commerce"

    # --- Shopify ---
    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    shopify_scopes: str = "read_products,read_inventory,write_draft_orders,read_shop"
    shopify_redirect_uri: str = "http://localhost:8000/api/v1/onboarding/shopify/callback"
    shopify_api_version: str = "2026-07"

    # --- Razorpay ---
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # --- Buyer agent LLM (optional) ---
    agent_llm_base_url: str = ""
    agent_llm_api_key: str = ""
    agent_llm_model: str = ""
    agent_llm_fallback_models: str = ""  # comma-separated; else strategy chain is used

    # --- Commerce tuning ---
    quote_ttl_minutes: int = 10
    default_quote_ttl_seconds: int = 600

    # --- Strategy-team engine (PRD_3) ---
    redis_url: str = ""  # empty -> in-process fallback queue driver
    enable_legacy_routes: bool = True

    # Concurrency limits (PRD_3 §23.10) - config-driven, never hard-coded at call sites.
    max_concurrent_missions_global: int = 4
    max_concurrent_missions_per_merchant: int = 2
    max_agent_runs_per_mission: int = 25
    max_total_descendants_per_mission: int = 20
    max_sub_agent_depth: int = 1
    max_children_per_parent: int = 5
    max_tool_calls_per_run: int = 12
    max_llm_concurrency_global: int = 8

    # Timeouts (seconds) - every bounded unit of work has a wall-clock ceiling.
    agent_run_timeout_seconds: int = 300
    # Buyer runs stack live-web materialization (bounded page fetches + LLM offer
    # extraction) AND a gateway session of up to 12 LLM steps - observed ~245s
    # for materialization alone against adidas.co.in. 300s killed runs mid-flow
    # (mission 33beb1e4: TIMED_OUT at 300s, session orphaned mid-checkout).
    buyer_run_timeout_seconds: int = 720
    mission_timeout_seconds: int = 900
    baseline_timeout_seconds: int = 1200

    # Queue / worker tuning.
    worker_concurrency: int = 4
    job_lease_seconds: int = 90
    job_heartbeat_seconds: int = 30
    job_max_attempts: int = 3
    # Run a worker inside the API process (single-command demo). Set false in
    # deployments that scale workers as independent replicas.
    embedded_worker: bool = True

    # --- LLM (OpenAI-compatible; provider-agnostic behind app.llm.LLMProvider) ---
    strategy_llm_base_url: str = ""
    strategy_llm_api_key: str = ""
    strategy_llm_model: str = ""
    strategy_llm_fallback_models: str = ""  # comma-separated try-in-order chain

    # --- Tool plane ---
    composio_api_key: str = ""
    composio_enabled: bool = True  # false or missing key -> deterministic mock plane
    # Fleet PRD Phase B: route reddit/youtube/social searches to native Composio
    # toolkits (needs connected accounts) instead of source-scoped SERP queries.
    toolkit_native_social_enabled: bool = False

    # --- Browser Use Cloud (B6) ---
    # Managed stealth browser (residential proxies + CAPTCHA solving) for pages
    # that block static fetches. Task-based V4 REST API. Empty key = feature off.
    # Static httpx fetch stays the fast path; the browser only escalates when a
    # page yields no machine-readable offer (bot-wall / JS-only rendering).
    browser_use_api_key: str = ""
    browser_use_model: str = "gpt-5.6-luna"  # V4 recommended, cheapest
    browser_use_base_url: str = "https://api.browser-use.com/api/v4"
    browser_use_timeout_seconds: int = 180  # wall clock per page extract
    browser_use_max_pages_per_run: int = 3  # credit cap per materialization run

    # --- Memory plane ---
    mem0_api_key: str = ""  # missing -> local Postgres-backed memory adapter

    # --- Identity resolution & evidence relevance (FIX_PRD_1) ---
    # Identity gate: below this, research runs in degraded (domain-anchored,
    # honest) mode instead of pretending the merchant identity is known.
    identity_confidence_threshold: float = 0.80
    # Evidence gate: observations with entity_relevance below this never
    # become active evidence; None (unscored legacy outputs) are promoted.
    evidence_relevance_threshold: float = 0.60
    # Bounded identity pre-phase: tool calls + wall clock for the whole
    # resolution step (first-party fetch + verification + LLM synthesis).
    identity_tool_budget: int = 4
    identity_timeout_seconds: int = 120

    # --- Observability (OTel + Langfuse; OTEL_LANGFUSE_EXECUTION_PRD) ---
    # Master switch (PRD 21): tracing is best-effort and can never break the runtime.
    langfuse_enabled: bool = True
    # Cloud credentials (PRD 17); keys live in .env, never in code.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"  # EU | us. | jp. | hipaa.
    langfuse_tracing_environment: str = "demo"  # PRD 18: development|staging|production|demo
    # PRD 24 payload policy: full prompts/outputs into generation spans (dev/demo).
    trace_llm_payloads: bool = True
    # PRD 19 sampling for the local OTel fallback provider; 1.0 traces everything.
    langfuse_sample_rate: float = 1.0

    @property
    def shopify_scope_list(self) -> list[str]:
        return [s.strip() for s in self.shopify_scopes.split(",") if s.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.strategy_llm_api_key and self.strategy_llm_base_url)

    @property
    def llm_model_chain(self) -> list[str]:
        """Primary model first, then fallbacks (deduplicated, order kept)."""
        chain: list[str] = []
        for m in [self.strategy_llm_model, *self.strategy_llm_fallback_models.split(",")]:
            name = m.strip()
            if name and name not in chain:
                chain.append(name)
        return chain

    @property
    def agent_llm_model_chain(self) -> list[str]:
        """Tool-calling brain chain: AGENT_LLM_* first, then the strategy chain.

        Lets the demo run the LLM buyer brain off the same credentials/models as
        the strategy fleet when AGENT_LLM_* is not configured separately.
        """
        chain: list[str] = []
        for m in [
            self.agent_llm_model,
            *self.agent_llm_fallback_models.split(","),
            *self.llm_model_chain,
        ]:
            name = m.strip()
            if name and name not in chain:
                chain.append(name)
        return chain or ["gpt-4o-mini"]

    @property
    def composio_ready(self) -> bool:
        return self.composio_enabled and bool(self.composio_api_key)

    @property
    def browser_use_ready(self) -> bool:
        return bool(self.browser_use_api_key)

    @property
    def mem0_ready(self) -> bool:
        return bool(self.mem0_api_key)

    @property
    def langfuse_ready(self) -> bool:
        return self.langfuse_enabled and bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
