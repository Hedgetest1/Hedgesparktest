from fastapi import APIRouter
from fastapi.responses import FileResponse
import os

router = APIRouter()

@router.get("/tracker.js")
def tracker():

    path="/opt/wishspark/tracker/spark-tracker.js"

    return FileResponse(
        path,
        media_type="application/javascript",
        filename="tracker.js"
    )
