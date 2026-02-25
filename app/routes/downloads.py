import os
import json
import aiofiles
from fastapi import APIRouter
from fastapi.responses import JSONResponse, FileResponse
from config import OUTPUT_DIR

router = APIRouter()

@router.get("/api/results/{session_id}")
async def get_results_api(session_id: str):
    results_path = os.path.join(OUTPUT_DIR, session_id, "results.json")
    if os.path.exists(results_path):
        async with aiofiles.open(results_path, "r") as f:
            content = await f.read()
            return JSONResponse(content=json.loads(content))
    return JSONResponse(status_code=404, content={"error": "Results not found"})

@router.get("/download/{zip_name}")
async def download_zip(zip_name: str):
    zip_path = os.path.join(OUTPUT_DIR, zip_name)
    if os.path.exists(zip_path):
        return FileResponse(zip_path, filename=zip_name)
    return JSONResponse(status_code=404, content={"error": "Not found"})

@router.get("/download/{session_id}/{filename}")
async def download_session_file(session_id: str, filename: str):
    file_path = os.path.join(OUTPUT_DIR, session_id, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename)
    return JSONResponse(status_code=404, content={"error": "Not found"})
