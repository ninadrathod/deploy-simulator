import random
import secrets
import threading
import time
from datetime import datetime, timezone

from App.deployment_ops import DeploymentOps
from App.models import Deployment, ServiceName, TerminalDeploymentStatus

_SERVICES: list[ServiceName] = [
    "billing-api",
    "auth-service",
    "notifications",
    "frontend-web",
]
_SERVICE_WEIGHTS = [15, 10, 25, 50]

_TERMINAL_STATUSES: list[TerminalDeploymentStatus] = ["success", "fail", "rolled-back"]
_STATUS_WEIGHTS = [95, 3, 2]

_SERVICE_DURATION_RANGES: dict[ServiceName, tuple[float, float]] = {
    "auth-service": (20.0, 45.0),
    "notifications": (25.0, 55.0),
    "billing-api": (30.0, 70.0),
    "frontend-web": (90.0, 180.0),
}

_STATUS_DURATION_MULTIPLIERS: dict[TerminalDeploymentStatus, float] = {
    "success": 1.0,
    "rolled-back": 2.0,
    "fail": 5.0,
}

_GENERATION_INTERVAL_SECONDS = 10.0


class DummyGenerator:
    def __init__(self) -> None:
        self._latest_id = 0
        self._ops: DeploymentOps | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_completions: dict[int, tuple[TerminalDeploymentStatus, float]] = {}

    def create_dummy_deployment(self) -> dict:
        """Create a running deployment (duration 0). Used by tests and API introspection."""
        self._latest_id += 1
        service = random.choices(_SERVICES, weights=_SERVICE_WEIGHTS, k=1)[0]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commit_sha = secrets.token_hex(20)

        deployment = Deployment(
            id=self._latest_id,
            service=service,
            status="running",
            duration=0,
            timestamp=timestamp,
            commit_sha=commit_sha,
        )
        return deployment.model_dump()

    def start_deployments(self, ops: DeploymentOps) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Deployment generation is already running")

        self._ops = ops
        self._pending_completions.clear()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_generation_loop,
            daemon=True,
        )
        self._thread.start()

    def stop_deployments(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return

        self._stop_event.set()
        self._thread.join()
        self._thread = None
        self._ops = None
        self._pending_completions.clear()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_generation_loop(self) -> None:
        assert self._ops is not None

        while not self._stop_event.is_set():
            self._complete_eligible_deployments()
            self._start_running_deployment()
            if self._stop_event.wait(_GENERATION_INTERVAL_SECONDS):
                break

    def _start_running_deployment(self) -> None:
        assert self._ops is not None

        payload = self.create_dummy_deployment()
        deployment = Deployment(**payload)
        self._ops.add_deployment(deployment)

        final_status = random.choices(_TERMINAL_STATUSES, weights=_STATUS_WEIGHTS, k=1)[0]
        target_duration = self._generate_duration(deployment.service, final_status)
        self._pending_completions[deployment.id] = (final_status, target_duration)

    def _complete_eligible_deployments(self) -> None:
        assert self._ops is not None

        now = datetime.now(timezone.utc)
        for deployment in self._ops.read_running_deployments():
            pending = self._pending_completions.get(deployment.id)
            if pending is None:
                continue

            final_status, target_duration = pending
            started = datetime.fromisoformat(deployment.timestamp.replace("Z", "+00:00"))
            elapsed = (now - started).total_seconds()
            if elapsed < target_duration:
                continue

            timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            self._ops.complete_deployment(deployment.id, final_status, timestamp)
            del self._pending_completions[deployment.id]

    def _generate_duration(
        self,
        service: ServiceName,
        status: TerminalDeploymentStatus,
    ) -> float:
        low, high = _SERVICE_DURATION_RANGES[service]
        base = random.uniform(low, high)
        return round(base * _STATUS_DURATION_MULTIPLIERS[status], 2)
