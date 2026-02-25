from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("static/index.html", "r") as f:
        return f.read()

@router.get("/results/{session_id}", response_class=HTMLResponse)
async def serve_results(session_id: str):
    with open("static/results.html", "r") as f:
        return f.read()

@router.get("/chat", response_class=HTMLResponse)
async def serve_chat():
    with open("static/chat.html", "r") as f:
        return f.read()
