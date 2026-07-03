import threading
from datetime import datetime
from queue import Queue
from typing import Optional

from App.metric import MetricOps
from App.models import Deployment, DeploymentStatus, ServiceName, TerminalDeploymentStatus


def _parse_iso_timestamp(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


class DeploymentOps:
    def __init__(self, metric_ops: MetricOps) -> None:
        self._metric_ops = metric_ops
        self.deployments: Queue[Deployment] = Queue()
        self._deployments_by_id: dict[int, Deployment] = {}
        self._running_deployments: dict[int, Deployment] = {}
        self._lock = threading.RLock()

    def add_deployment(self, deployment: Deployment) -> None:
        with self._lock:
            if deployment.id in self._deployments_by_id:
                raise ValueError(f"Deployment with id {deployment.id} already exists")

            if deployment.status == "running":
                if deployment.duration != 0:
                    raise ValueError("Running deployments must have duration 0")
                self._running_deployments[deployment.id] = deployment
                self._deployments_by_id[deployment.id] = deployment
                return

            raise ValueError(
                "Only running deployments can be added directly; "
                "use complete_deployment to finalize a running deployment"
            )

    def complete_deployment(
        self,
        deployment_id: int,
        status: TerminalDeploymentStatus,
        timestamp: str,
    ) -> Deployment:
        with self._lock:
            try:
                running = self._running_deployments[deployment_id]
            except KeyError:
                raise ValueError(
                    f"Running deployment with id {deployment_id} not found"
                )

            running_ts = _parse_iso_timestamp(running.timestamp)
            final_ts = _parse_iso_timestamp(timestamp)
            duration = (final_ts - running_ts).total_seconds()
            if duration < 0:
                raise ValueError("Completion timestamp must be after the running timestamp")

            completed = running.model_copy(
                update={
                    "status": status,
                    "timestamp": timestamp,
                    "duration": duration,
                }
            )

            del self._running_deployments[deployment_id]
            self._deployments_by_id[deployment_id] = completed
            self.deployments.put(completed)

        self._metric_ops.record_completed(completed)
        return completed

    def read_deployments(
        self,
        service: Optional[ServiceName] = None,
        status: Optional[DeploymentStatus] = None,
    ) -> list[Deployment]:
        with self._lock:
            results = self._all_deployments_unlocked()
        if service is not None:
            results = [d for d in results if d.service == service]
        if status is not None:
            results = [d for d in results if d.status == status]
        return results

    def read_latest_completed_deployments(self, limit: int = 10) -> list[Deployment]:
        with self._lock:
            completed = self._completed_deployments_unlocked()
        count = min(limit, len(completed))
        if count == 0:
            return []
        return completed[-count:]

    def read_running_deployments(
        self,
        service: Optional[ServiceName] = None,
    ) -> list[Deployment]:
        with self._lock:
            results = list(self._running_deployments.values())
        if service is not None:
            results = [d for d in results if d.service == service]
        return results

    def read_deployment(self, deployment_id: int) -> Deployment:
        with self._lock:
            try:
                return self._deployments_by_id[deployment_id]
            except KeyError:
                raise ValueError(f"Deployment with id {deployment_id} not found")

    def _completed_deployments_unlocked(self) -> list[Deployment]:
        items: list[Deployment] = []
        while not self.deployments.empty():
            items.append(self.deployments.get())
        for item in items:
            self.deployments.put(item)
        return items

    def _all_deployments_unlocked(self) -> list[Deployment]:
        return self._completed_deployments_unlocked() + list(
            self._running_deployments.values()
        )
