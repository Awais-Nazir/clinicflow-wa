"""HTTP entry point for ClinicFlow WA."""

from fastapi import FastAPI

app = FastAPI(
    title="ClinicFlow WA",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Report that the web process is running."""

    return {"status": "ok"}
