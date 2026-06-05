from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    whisper_model: str = "small"
    api_key: str = ""
    log_level: str = "info"
    browser_headless: bool = True


settings = Settings()
