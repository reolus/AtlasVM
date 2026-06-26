from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLASVM_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AtlasVM"
    host: str = "0.0.0.0"
    port: int = 8443

    username: str = "admin"
    password: str = "changeme"

    libvirt_uri: str = "qemu:///system"

    default_storage_pool: str = "atlasvm-default"
    iso_pool: str = "atlasvm-iso"

    vm_disk_path: str = "/atlasvm-vmdata/vm-disks"
    iso_path: str = "/atlasvm-vmdata/iso"

    database_url: str = "sqlite:///./atlasvm.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
