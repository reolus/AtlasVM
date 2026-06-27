from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_prefix='ATLASVM_',
        extra='ignore',
        case_sensitive=False,
    )

    app_name: str = 'AtlasVM'
    host: str = '0.0.0.0'
    port: int = 8443

    username: str = 'admin'
    password: str = 'change-this-password'

    database_url: str = 'sqlite:///./atlasvm.db'
    libvirt_uri: str = 'qemu:///system'

    default_storage_pool: str = 'atlasvm-default'
    iso_pool: str = 'atlasvm-iso'
    default_network: str = 'default'

    vm_disk_path: str = '/atlasvm-vmdata/vm-disks'
    iso_path: str = '/atlasvm-vmdata/iso'
    template_path: str = '/atlasvm-vmdata/templates'
    backup_path: str = '/atlasvm-vmdata/backups'

    console_bind_host: str = '0.0.0.0'
    console_public_host: str = ''
    console_port_base: int = 6080
    console_port_max: int = 6099


@lru_cache
def get_settings() -> Settings:
    return Settings()
