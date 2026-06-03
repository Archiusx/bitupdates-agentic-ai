"""
BIT Updates v2 - Configuration Management
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_ENV: str = "development"
    APP_URL: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # MongoDB
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "bitupdates"

    # Gemini
    GEMINI_API_KEY: str = ""

    # Firebase
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_DATABASE_URL: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_PRIVATE_KEY_ID: str = ""
    FIREBASE_CLIENT_ID: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: str = ""

    # JWT
    SECRET_KEY: str = "change-me-in-production-use-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Notifications
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "BIT Updates <noreply@bitupdates.com>"

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE: str = ""

    SLACK_BOT_TOKEN: str = ""
    SLACK_WEBHOOK_URL: str = ""

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60

    # Google Drive
    GOOGLE_DRIVE_CREDENTIALS_JSON: str = ""
    GOOGLE_DRIVE_FOLDER_ID: str = ""

    # BigQuery
    BIGQUERY_PROJECT_ID: str = ""
    BIGQUERY_DATASET: str = "bitupdates_analytics"

    # Vector Search
    VECTOR_COLLECTION_NAME: str = "complaint_embeddings"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
