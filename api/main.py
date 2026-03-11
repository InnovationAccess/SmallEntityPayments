"""USPTO Data Platform – FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routers import ai_assistant, citations, mdm, query

app = FastAPI(
    title="USPTO Data Platform",
    description=(
        "A platform for exploring USPTO patent data with MDM-driven entity "
        "normalisation, a Boolean query builder, and a Gemini AI assistant."
    ),
    version="1.0.0",
)

# Register API routers
app.include_router(mdm.router)
app.include_router(query.router)
app.include_router(ai_assistant.router)
app.include_router(citations.router)

# Serve the frontend single-page application
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    return FileResponse("frontend/index.html")


@app.get("/health", tags=["Health"])
def health() -> dict:
    return {"status": "ok"}
