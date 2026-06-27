from typing import Literal

from pydantic import BaseModel, Field

ServiceName = Literal[
    "billing-api",
    "auth-service",
    "notifications",
    "frontend-web",
]

DeploymentStatus = Literal["success", "fail", "rolled-back"]


class Deployment(BaseModel):
    id: int = Field(..., ge=1, description="Unique deployment identifier")
    service: ServiceName
    status: DeploymentStatus
    duration: float = Field(..., ge=0, description="Deployment duration in seconds")
    timestamp: str = Field(..., description="ISO 8601 timestamp, e.g. 2026-06-27T14:30:00Z")
    commit_sha: str = Field(..., min_length=7, max_length=40, description="Git commit SHA")
