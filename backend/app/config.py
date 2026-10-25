from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "Flower Shop Krasnoyarsk"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://flower:flower_dev_password@localhost:5432/flower"
    bot_token: str = ""
    admin_telegram_ids: str = ""
    admin_api_key: str = "replace-with-long-random-secret"
    frontend_origin: str = "http://localhost:5173"
    pickup_address: str = "Красноярск, проспект Мира, 5"
    delivery_fee_rub: int = 350
    min_order_rub: int = 0
    payments_enabled: bool = False
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_return_url: str = "https://example.invalid/payment-result"
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    @property
    def admin_ids(self):
        return {int(v.strip()) for v in self.admin_telegram_ids.split(",") if v.strip().isdigit()}
settings = Settings()
