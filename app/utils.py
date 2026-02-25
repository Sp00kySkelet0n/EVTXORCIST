import os
import shutil
import asyncio
import logging

logger = logging.getLogger("evtx_uploader")

async def delete_later(paths, delay=300):
    """Delete files or directories after a delay."""
    await asyncio.sleep(delay)
    for path in paths:
        try:
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                logger.debug(f"Deleted {path}")
        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")
