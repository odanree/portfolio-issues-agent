from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Postgres + Redis
    database_url: str = "sqlite+aiosqlite:///./issues.db"
    redis_url: str = "redis://localhost:6379/3"

    # Anthropic
    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-4-6"

    # Beacon
    beacon_api_url: str = "https://beacon.danhle.net"
    beacon_jwt: str = ""

    # GitHub
    github_token: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # Scheduling — weekly by default. Set day_of_week to "" to disable the cron.
    audit_cron_day_of_week: str = "sun"
    audit_cron_hour: int = 9

    # Cap proposals per audit so a many-project portfolio doesn't flood Slack.
    # Findings are ranked by drift density (number of substantive change types)
    # and only the top N are surfaced. 0 = no cap.
    max_proposals_per_run: int = 5

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
