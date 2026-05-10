import json
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
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


@app.get("/api/sessions/{session_id}/pdf")
async def serve_pdf(session_id: str):
    pdf_path = SESSIONS_DIR / session_id / "document.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF nicht gefunden.")
    return FileResponse(pdf_path, media_type="application/pdf")


@app.get("/api/sessions/{session_id}/mobile-pdf")
async def serve_mobile_pdf(session_id: str):
    session_dir = SESSIONS_DIR / session_id
    original_path = session_dir / "document.pdf"
    mobile_path = session_dir / "mobile.pdf"

    if not original_path.exists():
        raise HTTPException(status_code=404, detail="Session nicht gefunden.")

    if not mobile_path.exists():
        try:
            extractor = PDFExtractor(str(original_path), str(session_dir))
            extractor.generate_mobile_pdf(str(mobile_path))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Konvertierung fehlgeschlagen: {e}")

    return FileResponse(
        mobile_path,
        media_type="application/pdf",
        filename="mobile.pdf",
        headers={"Content-Disposition": "attachment; filename=mobile.pdf"},
    )


@app.get("/api/images/{session_id}/{filename}")
async def serve_image(session_id: str, filename: str):
    img_path = SESSIONS_DIR / session_id / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    return FileResponse(img_path)


@app.post("/api/sessions/{session_id}/compose")
async def compose_pdf(session_id: str, request: Request):
    session_dir = SESSIONS_DIR / session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Session nicht gefunden.")

    body = await request.json()
    elements = body.get("elements", [])
    if not elements:
        raise HTTPException(status_code=400, detail="Keine Elemente übergeben.")

    output_path = session_dir / "composed.pdf"
    original_path = session_dir / "document.pdf"

    try:
        extractor = PDFExtractor(str(original_path), str(session_dir))
        extractor.generate_composed_pdf(elements, str(output_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF-Erstellung fehlgeschlagen: {e}")

    return FileResponse(
        str(output_path),
        media_type="application/pdf",
        filename="zusammengestellt.pdf",
        headers={"Content-Disposition": "attachment; filename=zusammengestellt.pdf"},
    )


def _load_results(session_id: str) -> dict:
    results_path = SESSIONS_DIR / session_id / "results.json"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Session nicht gefunden.")
    return json.loads(results_path.read_text(encoding="utf-8"))


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
