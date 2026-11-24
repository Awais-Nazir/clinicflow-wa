"""HTTP entry point for ClinicFlow WA."""

from fastapi import FastAPI

from clinicflow.observability import configure_error_reporting
from clinicflow.staff import router as staff_router
from clinicflow.webhooks import router as webhook_router

configure_error_reporting()

app = FastAPI(
    title="ClinicFlow WA",
    version="0.1.0",
)

app.include_router(webhook_router)
app.include_router(staff_router)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Report that the web process is running."""

    return {"status": "ok"}
