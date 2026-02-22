import asyncio
import base64
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
DEFAULT_MODEL = os.getenv("IMAGE_MODEL", os.getenv("SORA_MODEL", "gpt-image-1"))


@dataclass
class JobRecord:
    id: str
    batch_id: str
    sequence: int
    image_filename: str
    image_path: str
    prompt: str
    status: str = "queued"
    api_status: str | None = None
    revised_prompt: str | None = None
    output_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        output_exists = bool(self.output_path and Path(self.output_path).exists())
        file_url = f"/api/batches/{self.batch_id}/jobs/{self.id}/download" if output_exists else None
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "sequence": self.sequence,
            "image_filename": self.image_filename,
            "prompt": self.prompt,
            "status": self.status,
            "api_status": self.api_status,
            "revised_prompt": self.revised_prompt,
            "error": self.error,
            "download_url": file_url,
            "preview_url": file_url,
        }


@dataclass
class BatchRecord:
    id: str
    prompts: list[str]
    image_filenames: list[str]
    jobs: list[JobRecord] = field(default_factory=list)
    status: str = "queued"
    model: str = DEFAULT_MODEL
    size: str = "1024x1024"
    quality: str = "medium"
    output_format: str = "png"
    concurrency: int = 1
    error: str | None = None

    def counts(self) -> dict[str, int]:
        counts = {
            "total": len(self.jobs),
            "queued": 0,
            "submitting": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }
        for job in self.jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    def recalculate_status(self) -> None:
        counts = self.counts()
        if counts["total"] == 0:
            self.status = "queued"
            return
        if counts["failed"] and counts["completed"] + counts["failed"] == counts["total"]:
            self.status = "completed_with_errors"
            return
        if counts["completed"] == counts["total"]:
            self.status = "completed"
            return
        if counts["submitting"] or counts["processing"]:
            self.status = "running"
            return
        if counts["queued"] == counts["total"]:
            self.status = "queued"
            return
        self.status = "running"

    def to_dict(self) -> dict[str, Any]:
        self.recalculate_status()
        return {
            "id": self.id,
            "status": self.status,
            "model": self.model,
            "size": self.size,
            "quality": self.quality,
            "output_format": self.output_format,
            "concurrency": self.concurrency,
            "image_count": len(self.image_filenames),
            "prompt_count": len(self.prompts),
            "combination_count": len(self.jobs),
            "counts": self.counts(),
            "error": self.error,
            "jobs": [job.to_dict() for job in self.jobs],
        }


app = FastAPI(title="MSE Image Bulk Runner")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

state_lock = asyncio.Lock()
batches: dict[str, BatchRecord] = {}


def _openai_headers() -> dict[str, str]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return {"Authorization": f"Bearer {OPENAI_API_KEY}"}


def _guess_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def _create_image_edit(job: JobRecord, batch: BatchRecord) -> tuple[bytes, str | None]:
    image_path = Path(job.image_path)
    file_bytes = image_path.read_bytes()
    files = {
        "image": (
            job.image_filename,
            file_bytes,
            _guess_content_type(job.image_filename),
        )
    }
    data = {
        "model": batch.model,
        "prompt": job.prompt,
        "size": batch.size,
        "quality": batch.quality,
        "output_format": batch.output_format,
        "n": "1",
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{OPENAI_BASE_URL}/images/edits",
            headers=_openai_headers(),
            data=data,
            files=files,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Image edit failed ({response.status_code}): {response.text[:500]}")

        payload = response.json()
        data_items = payload.get("data") or []
        if not data_items:
            raise RuntimeError("Image edit response did not include image data.")

        image_item = data_items[0]
        b64_json = image_item.get("b64_json")
        if not b64_json:
            raise RuntimeError("Image edit response did not include b64_json output.")
        try:
            image_bytes = base64.b64decode(b64_json)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to decode returned image data.") from exc

        return image_bytes, image_item.get("revised_prompt")


async def _update_job(batch_id: str, job_id: str, **changes: Any) -> None:
    async with state_lock:
        batch = batches.get(batch_id)
        if not batch:
            return
        job = next((j for j in batch.jobs if j.id == job_id), None)
        if not job:
            return
        for key, value in changes.items():
            setattr(job, key, value)
        batch.recalculate_status()


async def _run_job(batch: BatchRecord, job: JobRecord, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        try:
            await _update_job(batch.id, job.id, status="submitting", api_status="submitting", error=None)
            await _update_job(batch.id, job.id, status="processing", api_status="processing")

            image_bytes, revised_prompt = await _create_image_edit(job, batch)

            output_dir = OUTPUTS_DIR / batch.id
            output_dir.mkdir(parents=True, exist_ok=True)
            extension = "jpg" if batch.output_format == "jpeg" else batch.output_format
            output_path = output_dir / f"{job.id}.{extension}"
            output_path.write_bytes(image_bytes)

            await _update_job(
                batch.id,
                job.id,
                status="completed",
                api_status="completed",
                revised_prompt=revised_prompt,
                output_path=str(output_path),
            )
        except Exception as exc:  # noqa: BLE001
            await _update_job(
                batch.id,
                job.id,
                status="failed",
                api_status="failed",
                error=str(exc),
            )


async def _process_batch(batch_id: str) -> None:
    async with state_lock:
        batch = batches.get(batch_id)
    if not batch:
        return

    semaphore = asyncio.Semaphore(max(1, batch.concurrency))
    async with state_lock:
        batch.status = "running"

    tasks = [asyncio.create_task(_run_job(batch, job, semaphore)) for job in batch.jobs]
    await asyncio.gather(*tasks)

    async with state_lock:
        final_batch = batches.get(batch_id)
        if final_batch:
            final_batch.recalculate_status()


def _sanitize_prompts(prompts_text: str) -> list[str]:
    return [line.strip() for line in prompts_text.splitlines() if line.strip()]


def _validate_base_name(base_name: str) -> str:
    cleaned = base_name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Base name is required.")
    if any(ch in cleaned for ch in r'<>:"/\|?*'):
        raise HTTPException(
            status_code=400,
            detail="Base name contains invalid filename characters (<>:\"/\\|?*).",
        )
    if cleaned.endswith(" ") or cleaned.endswith("."):
        raise HTTPException(status_code=400, detail="Base name cannot end with a space or period.")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _open_folder_picker() -> str:
    # Native dialog for local desktop use (works when the server runs on the same machine).
    try:
        from tkinter import Tk, filedialog
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Tkinter is not available in this Python environment.") from exc

    root = Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        selected = filedialog.askdirectory(title="Select folder for PNG renaming")
    finally:
        root.destroy()
    return selected or ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "default_model": DEFAULT_MODEL},
    )


@app.post("/api/batches")
async def create_batch(
    prompts_text: str = Form(...),
    images: list[UploadFile] = File(...),
    model: str = Form(DEFAULT_MODEL),
    size: str = Form("1024x1024"),
    quality: str = Form("medium"),
    output_format: str = Form("png"),
    concurrency: int = Form(1),
) -> dict[str, Any]:
    prompts = _sanitize_prompts(prompts_text)
    if not prompts:
        raise HTTPException(status_code=400, detail="Provide at least one prompt (one per line).")
    if not images:
        raise HTTPException(status_code=400, detail="Upload at least one image.")
    if concurrency < 1 or concurrency > 10:
        raise HTTPException(status_code=400, detail="concurrency must be between 1 and 10.")
    if quality not in {"auto", "low", "medium", "high"}:
        raise HTTPException(status_code=400, detail="quality must be auto, low, medium, or high.")
    if output_format not in {"png", "webp", "jpeg"}:
        raise HTTPException(status_code=400, detail="output_format must be png, webp, or jpeg.")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured on the server.")

    batch_id = uuid.uuid4().hex[:12]
    upload_dir = UPLOADS_DIR / batch_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_images: list[tuple[str, Path]] = []
    for idx, upload in enumerate(images, start=1):
        if not upload.filename:
            raise HTTPException(status_code=400, detail=f"Image {idx} is missing a filename.")
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"Image {idx} is empty.")
        safe_name = Path(upload.filename).name
        file_path = upload_dir / f"{idx:03d}_{safe_name}"
        file_path.write_bytes(content)
        saved_images.append((safe_name, file_path))

    jobs: list[JobRecord] = []
    seq = 1
    for image_filename, image_path in saved_images:
        for prompt in prompts:
            jobs.append(
                JobRecord(
                    id=uuid.uuid4().hex[:10],
                    batch_id=batch_id,
                    sequence=seq,
                    image_filename=image_filename,
                    image_path=str(image_path),
                    prompt=prompt,
                )
            )
            seq += 1

    batch = BatchRecord(
        id=batch_id,
        prompts=prompts,
        image_filenames=[name for name, _ in saved_images],
        jobs=jobs,
        model=model.strip() or DEFAULT_MODEL,
        size=size.strip() or "1024x1024",
        quality=quality,
        output_format=output_format,
        concurrency=concurrency,
    )

    async with state_lock:
        batches[batch_id] = batch

    asyncio.create_task(_process_batch(batch_id))
    return {"batch_id": batch_id, "batch": batch.to_dict()}


@app.get("/api/batches/{batch_id}")
async def get_batch(batch_id: str) -> dict[str, Any]:
    async with state_lock:
        batch = batches.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found.")
        return batch.to_dict()


@app.get("/api/batches/{batch_id}/jobs/{job_id}/download")
async def download_job_output(batch_id: str, job_id: str) -> FileResponse:
    async with state_lock:
        batch = batches.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found.")
        job = next((j for j in batch.jobs if j.id == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        if not job.output_path:
            raise HTTPException(status_code=404, detail="Job output not ready.")
        output_path = Path(job.output_path)
        output_format = batch.output_format

    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file is missing.")

    ext = "jpg" if output_format == "jpeg" else output_format
    filename = f"{batch_id}_{job.sequence:03d}_{Path(job.image_filename).stem}.{ext}"
    media_type = f"image/{output_format}"
    return FileResponse(path=output_path, media_type=media_type, filename=filename)


@app.post("/api/rename-pngs")
async def rename_pngs(
    folder_path: str = Form(...),
    base_name: str = Form(...),
) -> dict[str, Any]:
    target_dir = Path(folder_path.strip()).expanduser()
    validated_base = _validate_base_name(base_name)

    if not target_dir.exists():
        raise HTTPException(status_code=400, detail="Folder does not exist.")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Provided path is not a folder.")

    png_files = sorted(
        [p for p in target_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"],
        key=lambda p: p.name.lower(),
    )
    if not png_files:
        raise HTTPException(status_code=400, detail="No .png files found in the selected folder.")

    planned_names = [f"{validated_base}_{idx}.png" for idx in range(1, len(png_files) + 1)]

    current_set = {p.name for p in png_files}
    for new_name in planned_names:
        conflict_path = target_dir / new_name
        if conflict_path.exists() and new_name not in current_set:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot rename because target file already exists: {new_name}",
            )

    # Two-phase rename avoids collisions (e.g. a.png -> b.png while b.png also exists in batch).
    temp_paths: list[Path] = []
    for source in png_files:
        temp_name = f".__tmp_rename_{uuid.uuid4().hex}.png"
        temp_path = target_dir / temp_name
        source.rename(temp_path)
        temp_paths.append(temp_path)

    renamed: list[dict[str, str]] = []
    try:
        for idx, temp_path in enumerate(temp_paths, start=1):
            new_name = f"{validated_base}_{idx}.png"
            final_path = target_dir / new_name
            original_name = png_files[idx - 1].name
            temp_path.rename(final_path)
            renamed.append({"old_name": original_name, "new_name": new_name})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Rename failed: {exc}") from exc

    return {
        "folder_path": str(target_dir),
        "base_name": validated_base,
        "renamed_count": len(renamed),
        "files": renamed,
    }


@app.post("/api/select-folder")
async def select_folder() -> dict[str, str]:
    try:
        folder = await asyncio.to_thread(_open_folder_picker)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not folder:
        raise HTTPException(status_code=400, detail="No folder selected.")

    return {"folder_path": folder}
