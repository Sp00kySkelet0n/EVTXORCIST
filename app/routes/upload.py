import os
import uuid
import re
import json
import asyncio
import aiofiles
import logging
from zipfile import ZipFile
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse

from config import UPLOAD_DIR, OUTPUT_DIR
from utils import delete_later
from services.evtx_parser import parse_evtx_to_json
from services.chainsaw import run_chainsaw
from services.elasticsearch import push_to_elasticsearch
from services.splunk import push_to_splunk, push_chainsaw_to_splunk

logger = logging.getLogger("evtx_uploader")

router = APIRouter()

# In-memory dictionary to track async parsing/hunting progress by client ID
upload_progress = {}

@router.get("/progress/{client_id}")
async def get_progress(client_id: str):
    return upload_progress.get(client_id, {"status": "unknown"})

@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    client_id: str = Form("default-client"),
    case_name: str = Form("Untitled Case"),
    index: str = Form("evtx_index"),
    destination: str = Form("elasticsearch"),
    splunk_url: str = Form(None),
    splunk_token: str = Form(None),
    es_host: str = Form("elasticsearch"),
    es_port: int = Form(9200)
):
    saved_files = []
    json_files = []
    evtx_paths = []

    # Initialize tracking
    upload_progress[client_id] = {"status": "uploading", "completed": 0, "total": len(files)}

    case_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', case_name.strip())[:50]
    session_id = f"{case_slug}_{uuid.uuid4().hex[:8]}"
    session_folder = os.path.join(OUTPUT_DIR, session_id)
    os.makedirs(session_folder, exist_ok=True)

    # Process up to 8 EVTX files concurrently to avoid Out Of Memory (OOM) 
    # and to substantially speed up parsing and Splunk HTTP deliveries.
    sem = asyncio.Semaphore(8)

    async def process_single_file(file: UploadFile):
        filename = os.path.basename(file.filename)
        if not filename.lower().endswith(".evtx"):
            logger.info(f"Skipping non-EVTX file: {filename}")
            upload_progress[client_id]["completed"] += 1
            return None

        path = os.path.join(UPLOAD_DIR, filename)

        async with sem:
            async with aiofiles.open(path, "wb") as buffer:
                content = await file.read()
                await buffer.write(content)

            logger.info(f"Indexing: {filename} (index: {index})")

            try:
                # Pass function directly instead of lambda for cleaner ThreadPool performance
                json_records = await asyncio.get_event_loop().run_in_executor(
                    None,
                    parse_evtx_to_json,
                    path
                )

                json_filename = filename + ".json"
                json_path = os.path.join(session_folder, json_filename)
                async with aiofiles.open(json_path, "w") as jf:
                    await jf.write(json.dumps(json_records, indent=2))
                
                logger.info(f"Parsed {file.filename}, pushing to {destination}...")

                if destination == "elasticsearch":
                    await push_to_elasticsearch(json_records, es_host, es_port, index)
                elif destination == "splunk":
                    s_url = splunk_url or "http://splunk:8088/services/collector/event"
                    s_token = splunk_token or "11111111-1111-1111-1111-111111111111"
                    s_idx = index or "main"
                    await push_to_splunk(json_records, s_url, s_token, s_idx, source=case_name)

                upload_progress[client_id]["completed"] += 1
                logger.info(f"Pushed: {filename}")
                return {"filename": filename, "path": path, "json_path": json_path}

            except Exception as e:
                logger.exception(f"Error processing {filename}: {e}")
                upload_progress[client_id]["completed"] += 1
                return None

    # Update state to parsing
    upload_progress[client_id]["status"] = "parsing"

    # Gather and execute all individual file tasks concurrently
    tasks = [process_single_file(f) for f in files]
    results = await asyncio.gather(*tasks)

    for res in results:
        if res:
            saved_files.append(res["filename"])
            evtx_paths.append(res["path"])
            json_files.append(res["json_path"])

    if not saved_files:
        return JSONResponse(status_code=400, content={"error": "No .evtx files found in upload"})

    zip_name = f"{session_id}.zip"
    zip_path = os.path.join(OUTPUT_DIR, zip_name)

    with ZipFile(zip_path, "w") as zipf:
        for json_file in json_files:
            arcname = f"{session_id}/{os.path.basename(json_file)}"
            zipf.write(json_file, arcname=arcname)

    logger.info(f"ZIP created: {zip_path}")

    upload_progress[client_id]["status"] = "chainsaw"
    logger.info("Running Chainsaw analysis...")
    chainsaw_results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: run_chainsaw(UPLOAD_DIR)
    )
    logger.info(f"Chainsaw found {chainsaw_results['summary']['total']} detections")

    chainsaw_json_path = os.path.join(session_folder, "chainsaw_results.json")
    async with aiofiles.open(chainsaw_json_path, "w") as cf:
        await cf.write(json.dumps(chainsaw_results, indent=2))

    with ZipFile(zip_path, "a") as zipf:
        zipf.write(chainsaw_json_path, arcname=f"{session_id}/chainsaw_results.json")

    if destination == "splunk" and chainsaw_results.get("detections"):
        s_url = splunk_url or "http://splunk:8088/services/collector/event"
        s_token = splunk_token or "11111111-1111-1111-1111-111111111111"
        s_idx = index or "main"
        await push_chainsaw_to_splunk(chainsaw_results["detections"], s_url, s_token, s_idx, source=case_name)

    cleanup_paths = [zip_path, session_folder] + [os.path.join(UPLOAD_DIR, f) for f in saved_files]
    asyncio.create_task(delete_later(cleanup_paths))

    response_data = {
        "session_id": session_id,
        "case_name": case_name,
        "uploaded": saved_files,
        "index": index,
        "destination": destination,
        "zip_url": f"/download/{zip_name}",
        "chainsaw_url": f"/download/{session_id}/chainsaw_results.json",
        "detections": chainsaw_results.get("detections", []),
        "summary": chainsaw_results.get("summary", {})
    }

    results_json_path = os.path.join(session_folder, "results.json")
    async with aiofiles.open(results_json_path, "w") as rf:
        await rf.write(json.dumps(response_data, indent=2))

    # Mark fully complete
    upload_progress[client_id]["status"] = "complete"

    return JSONResponse(content=response_data)
