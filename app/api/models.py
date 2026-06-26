from pydantic import BaseModel, Field


class VMCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    memory_mb: int = Field(default=2048, ge=256)
    vcpus: int = Field(default=2, ge=1)
    disk_gb: int = Field(default=20, ge=1)
    storage_pool: str = "default"
    network: str = "default"
    iso_path: str | None = None
    os_variant: str = "generic"


class DeleteVMOptions(BaseModel):
    delete_disks: bool = False
