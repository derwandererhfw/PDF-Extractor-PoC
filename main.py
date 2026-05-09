import json
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from extractor import PDFExtractor

app = FastAPI(title="PDF Extractor")

BASE_DIR = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
STATIC_DIR = BASE_DIR / "static"

SESSIONS_DIR.mkdir(exist_ok=True)


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien erlaubt.")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir()

    pdf_path = session_dir / "document.pdf"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Datei ist leer.")

    pdf_path.write_bytes(content)

    try:
        extractor = PDFExtractor(str(pdf_path), str(session_dir))
        data = extractor.extract_all()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF konnte nicht verarbeitet werden: {e}")

    results_path = session_dir / "results.json"
    results_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    return {
        "session_id": session_id,
        "filename": file.filename,
        "stats": {
            "pages": len(data["text"]["pages"]),
            "images": len(data["images"]["images"]),
            "tables": len(data["tables"]["tables"]),
        },
    }


@app.get("/api/sessions/{session_id}/text")
async def get_text(session_id: str):
    return _load_results(session_id)["text"]


@app.get("/api/sessions/{session_id}/images")
async def get_images(session_id: str):
    return _load_results(session_id)["images"]


@app.get("/api/sessions/{session_id}/tables")
async def get_tables(session_id: str):
    return _load_results(session_id)["tables"]


@app.get("/api/images/{session_id}/{filename}")
async def serve_image(session_id: str, filename: str):
    img_path = SESSIONS_DIR / session_id / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    return FileResponse(img_path)


def _load_results(session_id: str) -> dict:
    results_path = SESSIONS_DIR / session_id / "results.json"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Session nicht gefunden.")
    return json.loads(results_path.read_text(encoding="utf-8"))


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
