from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from App.deployment_ops import DeploymentOps
from App.dummy_generator import DummyGenerator
from App.metric import MetricOps
from App.models import Deployment, DeploymentStatus, ServiceName, TerminalDeploymentStatus

STATIC_DIR = Path(__file__).parent / "static"
ROOT_DIR = Path(__file__).parent.parent
PROJECT_DOCS_HTML = ROOT_DIR / "index.html"
PROJECT_DOCS_CSS = ROOT_DIR / "site.css"


class ErrorDetail(BaseModel):
    error: str
    message: str
    code: str
    details: Optional[Any] = None


class ActionResponse(BaseModel):
    status: str
    message: str


class P95Message(BaseModel):
    message: str


class P95ServiceResult(BaseModel):
    service: ServiceName
    deployment: Deployment | P95Message


class SuccessRateResponse(BaseModel):
    status: Literal["success"]
    value: float


class GenerationStatusResponse(BaseModel):
    running: bool


class CompleteDeploymentRequest(BaseModel):
    status: TerminalDeploymentStatus
    timestamp: str


def _get_deployment_ops(request: Request) -> DeploymentOps:
    ops = getattr(request.app.state, "deployment_ops", None)
    if ops is None:
        raise _error(
            503,
            error="service_unavailable",
            message="Deployment store is not initialized",
            code="DEPLOYMENT_OPS_UNAVAILABLE",
        )
    return ops


def _get_metric_ops(request: Request) -> MetricOps:
    ops = getattr(request.app.state, "metric_ops", None)
    if ops is None:
        raise _error(
            503,
            error="service_unavailable",
            message="Metrics store is not initialized",
            code="METRIC_OPS_UNAVAILABLE",
        )
    return ops


def _get_dummy_generator(request: Request) -> DummyGenerator:
    generator = getattr(request.app.state, "dummy_generator", None)
    if generator is None:
        raise _error(
            503,
            error="service_unavailable",
            message="Deployment generator is not initialized",
            code="DUMMY_GENERATOR_UNAVAILABLE",
        )
    return generator


def _error(
    status_code: int,
    *,
    error: str,
    message: str,
    code: str,
    details: Any = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorDetail(
            error=error,
            message=message,
            code=code,
            details=details,
        ).model_dump(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    metric_ops = MetricOps()
    app.state.metric_ops = metric_ops
    app.state.deployment_ops = DeploymentOps(metric_ops)
    app.state.dummy_generator = DummyGenerator()
    yield


app = FastAPI(title="Deploy Simulator", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/project")
def serve_project_docs() -> FileResponse:
    return FileResponse(PROJECT_DOCS_HTML)


@app.get("/site.css")
def serve_project_docs_css() -> FileResponse:
    return FileResponse(PROJECT_DOCS_CSS, media_type="text/css")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=ErrorDetail(
            error="validation_error",
            message="Invalid request parameters",
            code="VALIDATION_ERROR",
            details=exc.errors(),
        ).model_dump(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    _request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        content = exc.detail
    else:
        content = ErrorDetail(
            error="http_error",
            message=str(exc.detail),
            code=f"HTTP_{exc.status_code}",
        ).model_dump()
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=ErrorDetail(
            error="internal_error",
            message="An unexpected error occurred",
            code="INTERNAL_ERROR",
            details=str(exc),
        ).model_dump(),
    )


@app.get("/deployments", response_model=List[Deployment])
def list_deployments(
    request: Request,
    service: Optional[ServiceName] = Query(None),
    status: Optional[DeploymentStatus] = Query(None),
) -> List[Deployment]:
    ops = _get_deployment_ops(request)
    return ops.read_deployments(service=service, status=status)


@app.get("/deployments/status", response_model=GenerationStatusResponse)
def get_generation_status(request: Request) -> GenerationStatusResponse:
    generator = _get_dummy_generator(request)
    return GenerationStatusResponse(running=generator.is_running())


@app.get("/p95", response_model=List[P95ServiceResult])
def get_p95_durations(request: Request) -> List[P95ServiceResult]:
    metric_ops = _get_metric_ops(request)
    try:
        return metric_ops.p95_duration()
    except Exception as exc:
        raise _error(
            500,
            error="internal_error",
            message="Failed to calculate p95 durations",
            code="P95_CALCULATION_FAILED",
            details=str(exc),
        )


@app.get("/anomalies", response_model=List[Deployment])
def get_anomalies(request: Request) -> List[Deployment]:
    metric_ops = _get_metric_ops(request)
    try:
        return metric_ops.read_anomalies()
    except Exception as exc:
        raise _error(
            500,
            error="internal_error",
            message="Failed to load anomalies",
            code="ANOMALIES_LOAD_FAILED",
            details=str(exc),
        )


@app.get("/success-rate", response_model=SuccessRateResponse)
def get_success_rate(request: Request) -> SuccessRateResponse:
    metric_ops = _get_metric_ops(request)
    try:
        return SuccessRateResponse(status="success", value=metric_ops.success_rate())
    except Exception as exc:
        raise _error(
            500,
            error="internal_error",
            message="Failed to calculate success rate",
            code="SUCCESS_RATE_FAILED",
            details=str(exc),
        )


@app.get("/deployments/latest", response_model=List[Deployment])
def list_latest_completed_deployments(request: Request) -> List[Deployment]:
    ops = _get_deployment_ops(request)
    return ops.read_latest_completed_deployments()


@app.get("/deployments/running", response_model=List[Deployment])
def list_running_deployments(
    request: Request,
    service: Optional[ServiceName] = Query(None),
) -> List[Deployment]:
    ops = _get_deployment_ops(request)
    return ops.read_running_deployments(service=service)


@app.get("/deployments/{deployment_id}", response_model=Deployment)
def get_deployment(
    request: Request,
    deployment_id: Annotated[int, ApiPath(ge=1, description="Deployment id, must be >= 1")],
) -> Deployment:
    ops = _get_deployment_ops(request)
    try:
        return ops.read_deployment(deployment_id)
    except ValueError:
        raise _error(
            404,
            error="not_found",
            message=f"Deployment with id {deployment_id} not found",
            code="DEPLOYMENT_NOT_FOUND",
            details={"deployment_id": deployment_id},
        )


@app.patch("/deployments/{deployment_id}", response_model=Deployment)
def complete_deployment(
    request: Request,
    deployment_id: Annotated[int, ApiPath(ge=1, description="Deployment id, must be >= 1")],
    body: CompleteDeploymentRequest,
) -> Deployment:
    ops = _get_deployment_ops(request)
    try:
        return ops.complete_deployment(
            deployment_id,
            body.status,
            body.timestamp,
        )
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise _error(
                404,
                error="not_found",
                message=message,
                code="RUNNING_DEPLOYMENT_NOT_FOUND",
                details={"deployment_id": deployment_id},
            )
        raise _error(
            422,
            error="validation_error",
            message=message,
            code="DEPLOYMENT_COMPLETION_FAILED",
            details={"deployment_id": deployment_id},
        )


@app.post("/deployments/start", response_model=ActionResponse)
def start_deployments(request: Request) -> ActionResponse:
    ops = _get_deployment_ops(request)
    generator = _get_dummy_generator(request)

    if generator.is_running():
        raise _error(
            409,
            error="conflict",
            message="Deployment generation is already running",
            code="GENERATOR_ALREADY_RUNNING",
        )

    try:
        generator.start_deployments(ops)
    except RuntimeError:
        raise _error(
            409,
            error="conflict",
            message="Deployment generation is already running",
            code="GENERATOR_ALREADY_RUNNING",
        )

    return ActionResponse(
        status="started",
        message="Dummy deployment generation started (one deployment every 10 seconds)",
    )


@app.post("/deployments/stop", response_model=ActionResponse)
def stop_deployments(request: Request) -> ActionResponse:
    generator = _get_dummy_generator(request)

    if not generator.is_running():
        return ActionResponse(
            status="idle",
            message="Deployment generation was not running",
        )

    generator.stop_deployments()
    return ActionResponse(
        status="stopped",
        message="Dummy deployment generation stopped",
    )
