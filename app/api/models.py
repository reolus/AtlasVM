from pydantic import BaseModel, Field


class VMCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    memory_mb: int = Field(default=2048, ge=256)
    vcpus: int = Field(default=2, ge=1)
    disk_gb: int = Field(default=20, ge=1)
    storage_pool: str = 'atlasvm-default'
    network: str = 'default'
    iso_path: str | None = None
    os_variant: str = 'generic'
    description: str | None = None
    start_after_create: bool = False
    autostart: bool = False
    firmware: str = 'bios'


class DeleteVMOptions(BaseModel):
    delete_disks: bool = False


class SnapshotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None


class VMEdit(BaseModel):
    memory_mb: int = Field(default=2048, ge=256)
    vcpus: int = Field(default=2, ge=1)
    description: str | None = None


class VMBackupRequest(BaseModel):
    compress: bool = True
    require_shutdown: bool = True


class VMCloneRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)
    storage_pool: str | None = None
