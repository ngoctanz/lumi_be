from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUMI_", env_file=".env", extra="ignore")
    mongodb_uri: SecretStr = SecretStr("mongodb://127.0.0.1:27017/?retryWrites=true")
    mongodb_database: str = Field(default="lumi", pattern=r"^[A-Za-z0-9_-]{1,63}$")
    mongodb_pool_size: int = Field(default=10, ge=2, le=50)
    environment: str = "development"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "https://localhost"])
    cdn_hosts: list[str] = Field(default_factory=list)
    registration_enabled: bool = True
    session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)
    pairing_ttl_seconds: int = Field(default=300, ge=30, le=600)
    request_max_bytes: int = Field(default=16384, ge=4096, le=65536)

    @model_validator(mode="after")
    def validate_settings(self):
        if not self.mongodb_uri.get_secret_value().startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("Configure a MongoDB connection URI")
        if "*" in self.cors_origins:
            raise ValueError("Set explicit companion origins")
        return self
