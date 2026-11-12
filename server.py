"""FitCheck Fit & Try-On API."""
import base64
import csv
import hmac
import io
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

from tryon_model import ROOT, check_photo, read_image, remote_backend, run_model

TMP = Path(os.getenv("TMP_DIR", str(Path(tempfile.gettempdir()) / "fitcheck-sessions")))
LOG = Path(os.getenv("LOG_FILE", "render_log.csv"))
GARMENTS = ROOT / "static" / "garments"
GARMENTS.mkdir(exist_ok=True)
PRODUCTS = ROOT / "static" / "products"
PRODUCTS.mkdir(exist_ok=True)
LABEL = "Simulated preview — fit, colour and drape may differ from the actual product."
MAX_RENDERS = 3
PURGE_S = min(24, max(0.001, float(os.getenv("PURGE_HOURS", "24")))) * 3600
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
LOCKS = [threading.Lock() for _ in range(64)]
COUNTS = [{} for _ in LOCKS]
LOG_LOCK = threading.Lock()
NO_STORE = {"Cache-Control": "no-store"}


def lock_index(session):
    return hash(session) % len(LOCKS)


def canonical_session(value):
    try:
        session = str(UUID(value))
    except ValueError:
        return None
    return session if session == value else None


def purge_expired():
    now = time.time()
    for path in TMP.iterdir():
        if not canonical_session(path.name):
            continue
        lock = LOCKS[lock_index(path.name)]
        if lock.acquire(blocking=False):
            try:
                if path.is_dir() and now - path.stat().st_mtime >= PURGE_S:
                    shutil.rmtree(path)
            except OSError:
                pass  # Retry on the next pass.
            finally:
                lock.release()
    for lock, counts in zip(LOCKS, COUNTS):
        if lock.acquire(blocking=False):
            try:
                expired = [session for session, (_, until) in counts.items() if until <= now]
                for session in expired:
                    del counts[session]
            finally:
                lock.release()


@asynccontextmanager
async def lifespan(_app):
    TMP.mkdir(parents=True, exist_ok=True, mode=0o700)
    stop = threading.Event()

    def purge():
        while not stop.is_set():
            purge_expired()
            stop.wait(min(60, PURGE_S))

    thread = threading.Thread(target=purge, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


app = FastAPI(title="FitCheck Fit & Try-On", lifespan=lifespan)


def label_font(size):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/System/Library/Fonts/Supplemental/Arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def stamp(image, placeholder):
    image.thumbnail((1600, 2300))
    font_size = max(17, round(image.width / 28))
    font = label_font(font_size)
    line_height = font_size + 6
    lines = ["PLACEHOLDER — original photo"] if placeholder else []
    line = ""
    for word in LABEL.split():
        candidate = f"{line} {word}".strip()
        if line and font.getlength(candidate) > image.width - 24:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    canvas = Image.new("RGB", (image.width, image.height + 20 + line_height * len(lines)))
    canvas.paste(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    for number, text in enumerate(lines):
        draw.text((12, image.height + 10 + number * line_height), text, fill="white", font=font)
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", quality=94)
    return buffer.getvalue()


def log(secs, cost, mock):
    with LOG_LOCK:
        new = not LOG.exists() or LOG.stat().st_size == 0
        with LOG.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if new:
                writer.writerow(["latency_s", "cost_inr", "mock"])
            writer.writerow([secs, cost, mock])


def error(message, status, **fields):
    return JSONResponse({"error": message, **fields}, status, headers=NO_STORE)


@app.get("/api/garments")
def garments():
    return sorted(
        path.name for path in GARMENTS.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


@app.get("/api/catalog")
def catalog():
    """Real SKUs from static/products/products.json. Variants without an image file are skipped."""
    manifest = PRODUCTS / "products.json"
    if not manifest.is_file():
        return JSONResponse([], headers=NO_STORE)
    try:
        items = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse([], headers=NO_STORE)
    out = []
    for item in items:
        variants = [
            {**v, "image": "/products/" + v["image"]}
            for v in item.get("variants", [])
            if v.get("image") and (PRODUCTS / v["image"]).resolve().parent == PRODUCTS.resolve()
            and (PRODUCTS / v["image"]).is_file()
        ]
        if variants:
            out.append({**item, "variants": variants})
    return JSONResponse(out, headers=NO_STORE)


@app.get("/api/tryon-config")
def tryon_config():
    return JSONResponse({"remote_backend": remote_backend()}, headers=NO_STORE)


@app.post("/api/tryon")
def tryon(session: str = Form(...), age: int = Form(0), consent_photo: str = Form("false"),
          consent_remote: str = Form(""), team_code: str = Form("", max_length=256),
          desc: str = Form("kurta", max_length=160), garment_name: str = Form(""),
          photo: UploadFile = File(...), garment: UploadFile | None = File(None)):
    if consent_photo != "true":
        return error("Photo consent is required.", 403)
    if age < 18:
        return error("Photo try-on is for people aged 18 or over.", 403)
    backend = remote_backend()
    if backend:
        required_code = os.getenv("TRYON_TEAM_CODE", "")
        if not required_code:
            return error("Team try-on is temporarily unavailable.", 503)
        if consent_remote != backend or not hmac.compare_digest(team_code.encode(), required_code.encode()):
            return error("Remote try-on requires team consent and a valid team code.", 403)
    if not canonical_session(session):
        return error("Please reload the page to start a new session.", 422)

    stripe = lock_index(session)
    with LOCKS[stripe]:
        counts = COUNTS[stripe]
        count, until = counts.get(session, (0, 0))
        if time.time() >= until:
            count = 0
        if count >= MAX_RENDERS:
            return error("You have used all 3 previews for this session.", 429)
        try:
            person = read_image(photo.file)
        except (OSError, ValueError, Image.DecompressionBombError):
            return error("Please retake the photo and upload a JPG, PNG or WebP under 12 MB.",
                         422, rejected_before_render=True)
        try:
            retake = check_photo(person)
        except (RuntimeError, ValueError, OSError):
            return error("Photo checks are temporarily unavailable. Please try again later.",
                         503, rejected_before_render=True)
        if retake:
            return error(retake, 422, rejected_before_render=True)
        try:
            if garment and garment.filename:
                item = read_image(garment.file)
            elif garment_name in garments():
                with (GARMENTS / garment_name).open("rb") as stream:
                    item = read_image(stream)
            else:
                return error("Please choose or upload a kurta image.", 422, rejected_before_render=True)
        except (OSError, ValueError, Image.DecompressionBombError):
            return error("Please upload a readable kurta JPG, PNG or WebP under 12 MB.",
                         422, rejected_before_render=True)

        folder = TMP / session
        folder.mkdir(exist_ok=True, mode=0o700)
        start = time.perf_counter()
        output, placeholder_reason = run_model(person, item, desc, folder)
        elapsed = round(time.perf_counter() - start, 3)
        mock = placeholder_reason is not None
        # Explicit mock mode does not contact a provider. Other attempts may have incurred a charge.
        cost = 0 if placeholder_reason == "mock_mode" else None
        preview = stamp(output, mock)
        temporary = folder / "result.tmp"
        temporary.write_bytes(preview)
        temporary.replace(folder / "result.jpg")
        os.utime(folder, None)
        counts[session] = (count + 1, time.time() + PURGE_S)
        log(elapsed, cost, mock)
        return JSONResponse({
            "image": "data:image/jpeg;base64," + base64.b64encode(preview).decode("ascii"),
            "mock": mock, "placeholder_reason": placeholder_reason, "label": LABEL,
            "latency_s": elapsed, "cost_inr": cost, "renders_used": count + 1,
        }, headers=NO_STORE)


@app.post("/api/delete")
def delete(session: str = Form(...)):
    if not canonical_session(session):
        return error("Invalid session.", 422)
    with LOCKS[lock_index(session)]:
        try:
            shutil.rmtree(TMP / session)
        except FileNotFoundError:
            pass
        except OSError:
            return error("Server deletion failed. Please try again.", 503)
    return JSONResponse({"deleted": True}, headers=NO_STORE)


app.mount("/products", StaticFiles(directory=PRODUCTS), name="products")
app.mount("/garments", StaticFiles(directory=GARMENTS), name="garments")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
