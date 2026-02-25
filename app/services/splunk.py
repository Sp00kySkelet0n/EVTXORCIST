import json
import httpx
import logging

logger = logging.getLogger("evtx_uploader")

async def push_to_splunk(records: list[dict], url: str, token: str, index: str, source: str = "evtxorcist"):
    # Prepare HEC batch
    batch_data = ""
    for record in records:
        payload = {
            "index": index,
            "sourcetype": "_json",
            "source": source,
            "event": record
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
                logger.error(f"Failed to push to Splunk HEC: {e}")

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
