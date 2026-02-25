from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import httpx
import logging

from routes import render, upload, chat, downloads

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evtx_uploader")

# App setup
app = FastAPI(title="EVTX Uploader")

# Allow CORS (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# Healthcheck
@app.get("/health/splunk")
async def check_splunk_health():
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get("http://splunk:8088/services/collector/health", timeout=2.0)
            if resp.status_code == 200:
                return JSONResponse(content={"status": "ok"})
            else:
                return JSONResponse(content={"status": "starting"})
    except httpx.RequestError:
        return JSONResponse(content={"status": "starting"})

# Include Routers
app.include_router(render.router)
app.include_router(upload.router)
app.include_router(chat.router)
app.include_router(downloads.router)