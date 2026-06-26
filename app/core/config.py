from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Phase One VM Manager"
    app_host: str = "0.0.0.0"
    app_port: int = 8443
    app_username: str = "admin"
    app_password: str = "change-this-password"
    database_url: str = "sqlite:///./phase1_vm_manager.db"
    libvirt_uri: str = "qemu:///system"
    default_storage_pool: str = "default"
    default_network: str = "default"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
