from pydantic import Field, SecretStr, field_validator, model_validator
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
    image_upload_max_bytes: int = Field(default=5242880, ge=262144, le=10485760)
    image_asset_limit: int = Field(default=50, ge=1, le=500)
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: SecretStr | None = None
    cloudinary_folder: str = Field(default="lumi", pattern=r"^[A-Za-z0-9_/-]{1,80}$")

    @field_validator("cloudinary_cloud_name", "cloudinary_api_key", mode="before")
    @classmethod
    def empty_cloudinary_value(cls, value):
        return value or None

    @field_validator("cloudinary_api_secret", mode="before")
    @classmethod
    def empty_cloudinary_secret(cls, value):
        return value or None

    @model_validator(mode="after")
    def validate_settings(self):
        if not self.mongodb_uri.get_secret_value().startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("Configure a MongoDB connection URI")
        if "*" in self.cors_origins:
            raise ValueError("Set explicit companion origins")
        credentials = (
            self.cloudinary_cloud_name,
            self.cloudinary_api_key,
            self.cloudinary_api_secret,
        )
        if any(credentials) and not all(credentials):
            raise ValueError("Configure all Cloudinary credentials or leave all three empty")
        return self

    @property
    def cloudinary_enabled(self):
        return bool(self.cloudinary_cloud_name and self.cloudinary_api_key and self.cloudinary_api_secret)
