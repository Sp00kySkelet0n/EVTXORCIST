import json
import httpx
import logging

import asyncio

logger = logging.getLogger("evtx_uploader")

async def _send_batch(client: httpx.AsyncClient, batch_str: str, url: str, token: str):
    """Helper function to send a single stringified JSON payload batch to Splunk HEC."""
    if not batch_str:
        return
    try:
        resp = await client.post(
            url,
            content=batch_str,
            headers={"Authorization": f"Splunk {token}"},
            timeout=45.0
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to push batch to Splunk HEC: {e}")

async def push_to_splunk(records: list[dict], url: str, token: str, index: str, source: str = "evtxorcist"):
    """Push raw EVTX records to Splunk HEC in asynchronous batches of 5000 to maximize throughput."""
    BATCH_SIZE = 5000
    
    # Break records apart into batches
    batches = []
    current_batch = ""
    count = 0

    for record in records:
        payload = {
            "index": index,
            "sourcetype": "_json",
            "source": source,
            "event": record
        }
        current_batch += json.dumps(payload) + "\n"
        count += 1
        
        if count >= BATCH_SIZE:
            batches.append(current_batch)
            current_batch = ""
            count = 0
            
    if current_batch:
        batches.append(current_batch)

    # Fire off all batches simultaneously using HTTPX connection pooling
    async with httpx.AsyncClient(verify=False, limits=httpx.Limits(max_connections=20)) as client:
        tasks = [_send_batch(client, batch, url, token) for batch in batches]
        await asyncio.gather(*tasks)

async def push_chainsaw_to_splunk(detections: list[dict], url: str, token: str, index: str, source: str = "evtxorcist"):
    """Push Chainsaw detection results to Splunk with sourcetype 'chainsaw'."""
    batch_data = ""
    for det in detections:
        payload = {
            "index": index,
            "sourcetype": "chainsaw",
            "source": source,
            "event": det
        }
        batch_data += json.dumps(payload) + "\n"
    
    if batch_data:
        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.post(
                    url,
                    content=batch_data,
                    headers={"Authorization": f"Splunk {token}"},
                    timeout=30.0
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to push Chainsaw to Splunk HEC: {e}")
