import json
import httpx
import logging

logger = logging.getLogger("evtx_uploader")

async def push_to_elasticsearch(records: list[dict], host: str, port: int, index: str):
    es_url = f"http://{host}:{port}/_bulk"
    bulk_data = ""
    for record in records:
        action = {"index": {"_index": index}}
        bulk_data += json.dumps(action) + "\n"
        bulk_data += json.dumps(record) + "\n"
    
    if bulk_data:
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    es_url, 
                    content=bulk_data, 
                    headers={"Content-Type": "application/x-ndjson"},
                    timeout=30.0
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to push to Elasticsearch: {e}")
