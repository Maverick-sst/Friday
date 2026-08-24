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
    database_url: str = (
        "postgresql+psycopg://agent_commerce:agent_commerce@localhost:5439/agent_commerce"
    )

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

    # --- Commerce tuning ---
    quote_ttl_minutes: int = 10
    default_quote_ttl_seconds: int = 600

    @property
    def shopify_scope_list(self) -> list[str]:
        return [s.strip() for s in self.shopify_scopes.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
