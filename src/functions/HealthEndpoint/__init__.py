"""Health check endpoint — replaces the FastAPI / Uvicorn health server."""
import json
from datetime import datetime, timezone

import azure.functions as func


def main(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "news-aggregator-azure",
            "version": "1.0.0",
        }),
        mimetype="application/json",
        status_code=200,
    )
