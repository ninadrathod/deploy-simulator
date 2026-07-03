import threading
from queue import Queue
from typing import Any, TypedDict

from App.models import Deployment, ServiceName, TerminalDeploymentStatus

_SERVICES: tuple[ServiceName, ...] = (
    "billing-api",
    "auth-service",
    "notifications",
    "frontend-web",
)

SPREAD = 5
_ANOMALY_HISTORY_SIZE = 5
_P95_MIN_DEPLOYMENTS = 5


class ServiceStats(TypedDict):
    deployments: Queue[Deployment]
    sum: float


class MetricOps:
    def __init__(self) -> None:
        self.service_dict: dict[ServiceName, ServiceStats] = {
            service: {"deployments": Queue(), "sum": 0.0}
            for service in _SERVICES
        }
        self.anomaly_list: list[Deployment] = []
        self._status_cntr: dict[TerminalDeploymentStatus, int] = {
            "success": 0,
            "fail": 0,
            "rolled-back": 0,
        }
        self._lock = threading.RLock()

    def record_completed(self, deployment: Deployment) -> None:
        with self._lock:
            if self._is_anomaly_unlocked(deployment):
                self.anomaly_list.append(deployment)

            service_stats = self.service_dict[deployment.service]
            service_stats["deployments"].put(deployment)
            service_stats["sum"] += deployment.duration
            self._status_cntr[deployment.status] += 1

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

                if len(temp_array) < _P95_MIN_DEPLOYMENTS:
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

    def _is_anomaly_unlocked(self, new_deployment: Deployment) -> bool:
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
