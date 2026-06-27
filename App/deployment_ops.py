import threading
from queue import Queue
from typing import Any, Optional, TypedDict

from App.models import Deployment, DeploymentStatus, ServiceName

_SERVICES: tuple[ServiceName, ...] = (
    "billing-api",
    "auth-service",
    "notifications",
    "frontend-web",
)

SPREAD = 5
_ANOMALY_HISTORY_SIZE = 5


class ServiceStats(TypedDict):
    deployments: Queue[Deployment]
    sum: float


class DeploymentOps:
    def __init__(self) -> None:
        self.deployments: Queue[Deployment] = Queue()
        self._deployments_by_id: dict[int, Deployment] = {}
        self.service_dict: dict[ServiceName, ServiceStats] = {
            service: {"deployments": Queue(), "sum": 0.0}
            for service in _SERVICES
        }
        self.anomaly_list: list[Deployment] = []
        self._status_cntr: dict[DeploymentStatus, int] = {
            "success": 0,
            "fail": 0,
            "rolled-back": 0,
        }
        self._lock = threading.RLock()

    def add_deployment(self, deployment: Deployment) -> None:
        with self._lock:
            if deployment.id in self._deployments_by_id:
                raise ValueError(f"Deployment with id {deployment.id} already exists")

            if self.is_anomaly(deployment):
                self.anomaly_list.append(deployment)

            self.deployments.put(deployment)
            self._deployments_by_id[deployment.id] = deployment

            service_stats = self.service_dict[deployment.service]
            service_stats["deployments"].put(deployment)
            service_stats["sum"] += deployment.duration
            self._status_cntr[deployment.status] += 1

    def is_anomaly(self, new_deployment: Deployment) -> bool:
        with self._lock:
            service = new_deployment.service
            temp_array = self._latest_service_deployments_unlocked(
                service,
                _ANOMALY_HISTORY_SIZE,
            )

            if len(temp_array) < _ANOMALY_HISTORY_SIZE:
                return False

            average_duration = sum(d.duration for d in temp_array) / len(temp_array)
            anomaly_duration = average_duration + 2 * SPREAD
            return new_deployment.duration > anomaly_duration

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

    def read_deployment(self, deployment_id: int) -> Deployment:
        with self._lock:
            try:
                return self._deployments_by_id[deployment_id]
            except KeyError:
                raise ValueError(f"Deployment with id {deployment_id} not found")

    def read_anomalies(self) -> list[Deployment]:
        with self._lock:
            return list(self.anomaly_list)

    def success_rate(self) -> float:
        with self._lock:
            total = sum(self._status_cntr.values())
            if total == 0:
                return 0.0
            return (self._status_cntr["success"] / total) * 100

    def p95_duration(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        with self._lock:
            for service in _SERVICES:
                temp_array = self._service_deployments_unlocked(service)

                if len(temp_array) < 5:
                    results.append(
                        {
                            "service": service,
                            "deployment": {"message": "not enough deployments"},
                        }
                    )
                    continue

                temp_array.sort(key=lambda deployment: deployment.duration)
                index = int(len(temp_array) * 0.95) - 1
                results.append(
                    {
                        "service": service,
                        "deployment": temp_array[index],
                    }
                )

        return results

    def _all_deployments(self) -> list[Deployment]:
        with self._lock:
            return self._all_deployments_unlocked()

    def _all_deployments_unlocked(self) -> list[Deployment]:
        items: list[Deployment] = []
        while not self.deployments.empty():
            items.append(self.deployments.get())
        for item in items:
            self.deployments.put(item)
        return items

    def _service_deployments_unlocked(self, service: ServiceName) -> list[Deployment]:
        queue = self.service_dict[service]["deployments"]
        items: list[Deployment] = []
        while not queue.empty():
            items.append(queue.get())
        for item in items:
            queue.put(item)
        return items

    def _latest_service_deployments_unlocked(
        self,
        service: ServiceName,
        count: int,
    ) -> list[Deployment]:
        items = self._service_deployments_unlocked(service)
        if len(items) < count:
            return items
        return items[-count:]
