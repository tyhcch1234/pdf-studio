"""PDF Studio - local web app.

Tabs:
  1. PDF -> Word  (text kept editable, images preserved, formulas as Word equations)
  2. Merge PDFs   (done in the browser with pdf-lib, no upload)
  3. A3 -> A4     (cut in half, done in the browser with pdf-lib, no upload)

Run:  python3 server.py  ->  http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import shutil
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import ConvertOptions, capabilities, convert_pdf

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
JOBS = os.path.join(BASE, "jobs")
os.makedirs(JOBS, exist_ok=True)

app = FastAPI(title="PDF Studio", version="1.0")

# The page can be hosted on GitHub Pages while this service runs elsewhere.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
EXECUTOR = ThreadPoolExecutor(max_workers=1)  # OCR models are RAM hungry
JOBS_DB: dict[str, dict] = {}


def _job(job_id: str) -> dict:
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/capabilities")
def api_capabilities():
    return capabilities()


@app.post("/api/convert")
async def api_convert(
    files: list[UploadFile] = File(...),
    ocr_mode: str = Form("auto"),
    formula_mode: str = Form("auto"),
    keep_images: bool = Form(True),
    dpi: int = Form(220),
):
    if not files:
        raise HTTPException(400, "no files")
    job_id = uuid.uuid4().hex[:12]
    workdir = os.path.join(JOBS, job_id)
    srcdir = os.path.join(workdir, "src")
    outdir = os.path.join(workdir, "out")
    os.makedirs(srcdir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)

    entries = []
    for idx, upload in enumerate(files):
        name = os.path.basename(upload.filename or f"file{idx}.pdf")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        dest = os.path.join(srcdir, f"{idx:02d}_{name}")
        with open(dest, "wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        entries.append(
            {
                "id": idx,
                "name": name,
                "src": dest,
                "status": "queued",
                "progress": 0,
                "message": "排隊中",
                "result": None,
            }
        )

    job = {
        "id": job_id,
        "state": "queued",
        "created": time.time(),
        "options": {
            "ocr_mode": ocr_mode,
            "formula_mode": formula_mode,
            "keep_images": keep_images,
            "dpi": dpi,
        },
        "files": entries,
        "error": None,
    }
    JOBS_DB[job_id] = job
    EXECUTOR.submit(_run_job, job_id, outdir)
    return {"job_id": job_id, "files": [e["name"] for e in entries]}


def _run_job(job_id: str, outdir: str) -> None:
    job = JOBS_DB[job_id]
    job["state"] = "running"
    opts = ConvertOptions(
        ocr=job["options"]["ocr_mode"],
        formulas=job["options"]["formula_mode"],
        images=bool(job["options"]["keep_images"]),
        dpi=int(job["options"]["dpi"]),
    )
    made: list[tuple[str, str]] = []
    try:
        for entry in job["files"]:
            entry["status"] = "running"
            entry["message"] = "開始轉換"
            stem = os.path.splitext(entry["name"])[0]
            out_name = f"{stem}.docx"
            out_path = os.path.join(outdir, out_name)

            def progress(done: int, total: int, msg: str, _e=entry) -> None:
                _e["progress"] = int(done / max(1, total) * 100)
                _e["message"] = msg

            try:
                stats = convert_pdf(
                    entry["src"], out_path, opts, progress=progress, title=stem
                )
                entry["status"] = "done"
                entry["progress"] = 100
                entry["message"] = "完成"
                entry["result"] = {
                    "download": f"/api/jobs/{job_id}/download/{out_name}",
                    "filename": out_name,
                    "stats": stats,
                }
                made.append((out_path, out_name))
            except Exception as exc:  # keep other files going
                entry["status"] = "error"
                entry["message"] = f"{type(exc).__name__}: {exc}"
                job.setdefault("errors", []).append(traceback.format_exc())

        if len(made) > 1:
            zip_path = os.path.join(outdir, "converted-word.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for path, name in made:
                    zf.write(path, name)
            job["bundle"] = {
                "download": f"/api/jobs/{job_id}/download/converted-word.zip",
                "filename": "converted-word.zip",
            }
        job["state"] = "done" if made else "error"
        if not made:
            job["error"] = "所有檔案轉換失敗"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["traceback"] = traceback.format_exc()


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = _job(job_id)
    payload = {
        "id": job["id"],
        "state": job["state"],
        "error": job.get("error"),
        "files": [
            {
                "name": e["name"],
                "status": e["status"],
                "progress": e["progress"],
                "message": e["message"],
                "result": e["result"],
            }
            for e in job["files"]
        ],
        "bundle": job.get("bundle"),
    }
    return JSONResponse(payload)


@app.get("/api/jobs/{job_id}/download/{filename}")
def api_download(job_id: str, filename: str):
    _job(job_id)
    safe = os.path.basename(filename)
    path = os.path.join(JOBS, job_id, "out", safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "file not found")
    return FileResponse(
        path,
        filename=safe,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if safe.endswith(".docx")
        else "application/zip",
    )


@app.get("/api/health")
def api_health():
    return {"ok": True, "jobs": len(JOBS_DB)}


def _sweep_old_jobs(max_age_h: int = 24) -> None:
    """Remove working directories left over from earlier runs."""
    now = time.time()
    for name in os.listdir(JOBS):
        path = os.path.join(JOBS, name)
        try:
            if os.path.isdir(path) and now - os.path.getmtime(path) > max_age_h * 3600:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


_sweep_old_jobs()

app.mount("/", StaticFiles(directory=WEB, html=True), name="web")

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"PDF Studio -> http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
