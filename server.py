"""FitCheck Fit & Try-On API."""
import base64
import csv
from datetime import datetime, timezone
from functools import lru_cache
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
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont
import numpy as np

from tryon_model import ROOT, check_photo, read_image, remote_backend, run_model
from recommender import model, predict
from setup_models import ensure_models
from pydantic import BaseModel, Field

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
DAILY_CAP = int(os.getenv("TRYON_DAILY_CAP", "150"))
DAILY_LEDGER = LOG.with_name("tryon-daily.json")
GALLERY = ROOT / "static" / "gallery"
NO_STORE = {"Cache-Control": "no-store"}


CATEGORY_LABELS = {
    "saree": "Sarees", "lehenga": "Lehengas", "gowns": "Gowns", "kurta_men": "Men's Kurtas",
    "blouse": "Blouses", "dhoti_pants": "Dhoti Pants", "womens-dresses": "Dresses", "tops": "Tops",
    "mens-shirts": "Men's Shirts", "one-pieces": "Dresses & Sets", "bottoms": "Bottoms"}


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
                expired = [session for session, (_, until, _) in counts.items() if until <= now]
                for session in expired:
                    del counts[session]
            finally:
                lock.release()


@asynccontextmanager
async def lifespan(_app):
    model()
    try:
        ensure_models()
    except Exception as exc:  # photo checks will report 503 until the models exist
        print(f"WARNING: could not fetch photo-check models: {exc}")
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
    canvas.save(buffer, "PNG")
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


def reserve_render():
    """Reserve a UTC-day slot before contacting a model, including failed calls."""
    today = datetime.now(timezone.utc).date().isoformat()
    with LOG_LOCK:
        try:
            state = json.loads(DAILY_LEDGER.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {}
        count = state["count"] if state.get("date") == today else 0
        if count >= DAILY_CAP:
            return False
        temporary = DAILY_LEDGER.with_suffix(".tmp")
        temporary.write_text(json.dumps({"date": today, "count": count + 1}), encoding="utf-8")
        temporary.replace(DAILY_LEDGER)
        return True


@app.get("/api/metrics")
def metrics():
    with LOG_LOCK:
        if not LOG.exists():
            rows = []
        else:
            with LOG.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
    successful = [r for r in rows if r["mock"] == "False"]
    times = [float(r["latency_s"]) for r in successful]
    costs = [float(r["cost_inr"]) for r in successful if r["cost_inr"]]
    hourly = float(os.getenv("TRYON_ESTIMATED_GPU_HOUR_INR") or 0)
    estimate = float(np.mean(times) * hourly / 3600) if not costs and hourly > 0 and times else None
    return {
        "attempts": len(rows), "renders": len(times),
        "p50_latency_s": float(np.percentile(times, 50)) if times else None,
        "p95_latency_s": float(np.percentile(times, 95)) if times else None,
        "latency_scope": "server inference pipeline",
        "measured_cost_count": len(costs),
        "average_cost_inr": sum(costs) / len(costs) if costs else None,
        "estimated_cost_inr": estimate,
        "estimated_gpu_hour_inr": hourly if estimate is not None else None,
        "cost_basis": ("Estimate assumes one GPU is billed for the entire server render time. "
                       "Actual billing, cold starts, networking and utilisation can differ."
                       if estimate is not None else "Provider billing unavailable for unmeasured requests."),
    }


@app.get("/api/gallery")
def gallery():
    manifest = GALLERY / "manifest.json"
    if not manifest.exists():
        return []
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    return [
        {"case_id": entry["case_id"], "image": "/gallery/" + entry["image"], "label": LABEL}
        for entry in entries if entry.get("publication_approved") is True
        and Path(entry["image"]).name == entry["image"]
        and Path(entry["image"]).suffix.lower() == ".png"
        and (GALLERY / entry["image"]).resolve().parent == GALLERY.resolve()
        and (GALLERY / entry["image"]).is_file()
    ]


@app.get("/gallery/{name}")
def gallery_image(name: str):
    if not any(entry["image"] == "/gallery/" + name for entry in gallery()):
        return error("Gallery image not found.", 404)
    return FileResponse(GALLERY / name, media_type="image/png", headers=NO_STORE)


@lru_cache(maxsize=48)
def prepared_garment(path, modified):
    with path.open("rb") as stream:
        image = read_image(stream)
        image.thumbnail((1024, 1024))
        return image


@app.get("/api/garments")
def garments():
    return sorted(
        path.name for path in GARMENTS.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


@app.get("/api/catalog")
def catalog():
    """Synthetic catalogue with optional approved garment-only photos per SKU."""
    manifest = PRODUCTS / "products.json"
    try:
        assets = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        assets = {}
    colours = {"Green": "#547655", "Cream": "#dfc998", "Red": "#a54646", "Mustard": "#bf8a31",
               "Teal": "#427e7a", "Black": "#50414f", "Blue": "#4b6898", "Lavender": "#7a64a0",
               "Sky blue": "#5687a6", "Pink": "#ab6784", "Maroon": "#7e3c52"}
    items = []
    for sku, p in model()["products"].items():
        if not (isinstance(assets.get(sku), str) and (PRODUCTS / assets[sku]).is_file()):
            continue  # no real photo: keep illustrations out of the shop (SKU lookup still works)
        if p["category"] != "Women · Kurtas":
            continue
        variant = {"sku": sku, "colour": p["colour"], "hex": colours.get(p["colour"], "#967087")}
        filename = assets.get(sku)
        if isinstance(filename, str) and Path(filename).name == filename and (PRODUCTS / filename).is_file():
            variant["image"] = "/products/" + filename
        items.append({"sku_id": sku, "brand": p["brand"], "name": p["product_name"],
                      "fit": p["fit_type"], "price": p["mrp_inr"], "currency": "INR",
                      "garment_category": "one-pieces", "variants": [variant]})
    free = []
    for name in ("free_catalog.json", "indo_catalog.json"):
        try:
            free += json.loads((PRODUCTS / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    for f in free:
        if Path(f["image"]).name != f["image"] or not (PRODUCTS / f["image"]).is_file():
            continue
        items.append({"sku_id": f["sku_id"], "brand": f["brand"], "name": f["name"], "fit": f["fit"],
                      "price": f["price"], "currency": f["currency"], "garment_category": f["garment_category"],
                      "category_label": CATEGORY_LABELS.get(f["category"]) or CATEGORY_LABELS[f["garment_category"]],
                      "free": True, "variants": [{"sku": f["sku_id"], "colour": f["colour"], "hex": f["hex"],
                                                  "image": "/products/" + f["image"]}]})
    return JSONResponse(items, headers=NO_STORE)


class RecommendationInput(BaseModel):
    test_id: str | None = None
    customer_id: str | None = None
    sku_id: str | None = None
    usual_size: str | None = None
    usual_brand: str | None = None
    fit_preference: float = Field(0, ge=-.5, le=.5)
    consent_measurements: bool = False
    bust: float | None = Field(None, ge=50, le=180)
    waist: float | None = Field(None, ge=45, le=180)
    hip: float | None = Field(None, ge=55, le=190)


@app.post("/api/recommend")
def recommend(request: RecommendationInput):
    started = time.perf_counter()
    response = recommendation_response(request)
    response.headers["Server-Timing"] = f"recommend;dur={(time.perf_counter()-started)*1000:.3f}"
    return response


def recommendation_response(request: RecommendationInput):
    state = model()
    has_override = bool(request.usual_size or request.usual_brand or request.consent_measurements or request.fit_preference)
    case = request.test_id
    if not case and not has_override:
        case = state["test_by_customer_sku"].get((request.customer_id,request.sku_id))
    base = state["predictions"].get(case)
    if request.test_id and not base:
        return error("That test case was not found.", 404)
    if base and not has_override:
        return Response(state["test_response"][case],media_type="application/json",headers=NO_STORE)
    sku = base["sku_id"] if base else request.sku_id
    product = state["products"].get(sku)
    if not product:
        return error("Choose a product or enter its SKU.", 404)
    customer = base["customer_id"] if base else request.customer_id
    profile = state["profiles"].get(customer, {}).get(product["category"])
    if customer and not base and customer not in state["profiles"]:
        return error("That synthetic customer was not found.", 404)
    dimensions = {key:getattr(request,key) for key in ("bust","waist","hip")
                  if request.consent_measurements and getattr(request,key) is not None}
    return JSONResponse(predict(state,product,profile,request.usual_size,request.usual_brand,
                                request.fit_preference,dimensions), headers=NO_STORE)


@app.get("/api/fit-results")
def fit_results():
    return FileResponse(ROOT / "results" / "metrics.json", media_type="application/json")


@app.get("/api/tryon-config")
def tryon_config():
    return JSONResponse({"remote_backend": remote_backend()}, headers=NO_STORE)


@app.post("/api/tryon")
def tryon(session: str = Form(...), age: int = Form(0), consent_photo: str = Form("false"),
          consent_remote: str = Form(""),
          desc: str = Form("kurta", max_length=160), garment_name: str = Form(""),
          sku_id: str = Form("", max_length=80), garment_category: str = Form("one-pieces"),
          photo: UploadFile = File(...), garment: UploadFile | None = File(None)):
    start = time.perf_counter()
    if consent_photo != "true":
        return error("Photo consent is required.", 403)
    if age < 18:
        return error("Photo try-on is for people aged 18 or over.", 403)
    backend = remote_backend()
    if backend:
        if consent_remote != backend:
            return error("Remote try-on consent is required.", 403)
    if not canonical_session(session):
        return error("Please reload the page to start a new session.", 422)
    if garment_category not in {"tops", "bottoms", "one-pieces"}:
        return error("Choose a supported garment category.", 422, rejected_before_render=True)

    stripe = lock_index(session)
    with LOCKS[stripe]:
        counts = COUNTS[stripe]
        count, until, deleted = counts.get(session, (0, 0, False))
        if deleted and time.time() < until:
            return error("This photo session was deleted. Start a new preview.", 410)
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
        except (RuntimeError, ValueError, OSError) as exc:
            print(f"Photo check failed: {exc!r}")
            return error("Photo checks are temporarily unavailable. Please try again later.",
                         503, rejected_before_render=True)
        if retake:
            return error(retake, 422, rejected_before_render=True)
        try:
            if garment and garment.filename:
                item = read_image(garment.file)
            elif sku_id:
                product = next((entry for entry in json.loads(catalog().body)
                                if entry.get("sku_id") == sku_id), None)
                if not product:
                    return error("That SKU has no try-on garment image.", 422, rejected_before_render=True)
                image_url = product["variants"][0].get("image")
                if not image_url:
                    return error("Upload a flat-lay or mannequin garment photo for this SKU.", 422, rejected_before_render=True)
                path = PRODUCTS / Path(image_url).name
                item = prepared_garment(path, path.stat().st_mtime_ns).copy()
            elif garment_name in garments():
                path = GARMENTS / garment_name
                item = prepared_garment(path, path.stat().st_mtime_ns).copy()
            else:
                return error("Please choose or upload a kurta image.", 422, rejected_before_render=True)
        except (OSError, ValueError, Image.DecompressionBombError):
            return error("Please upload a readable kurta JPG, PNG or WebP under 12 MB.",
                         422, rejected_before_render=True)

        try:
            if not reserve_render():
                return error("Today's preview limit has been reached. You can still browse examples.", 429)
        except (OSError, ValueError, KeyError):
            return error("Try-on is temporarily unavailable. You can still browse examples.", 503)
        counts[session] = (count + 1, time.time() + PURGE_S, False)
        folder = TMP / session
        folder.mkdir(exist_ok=True, mode=0o700)
        output, placeholder_reason = run_model(person, item, desc, folder, garment_category)
        mock = placeholder_reason is not None
        # Explicit mock mode does not contact a provider. Other attempts may have incurred a charge.
        cost = 0 if placeholder_reason == "mock_mode" else None
        if mock and placeholder_reason != "mock_mode":
            elapsed = round(time.perf_counter() - start, 3)
            log(elapsed, cost, mock)
            if not any(folder.iterdir()):
                folder.rmdir()
            return error("The model could not make a reliable preview. Try again or browse the sample gallery.",
                         503, placeholder_reason=placeholder_reason, latency_s=elapsed)
        preview = stamp(output, mock)
        temporary = folder / "result.tmp"
        temporary.write_bytes(preview)
        temporary.replace(folder / "result.png")
        os.utime(folder, None)
        elapsed = round(time.perf_counter() - start, 3)
        log(elapsed, cost, mock)
        return JSONResponse({
            "image": "data:image/png;base64," + base64.b64encode(preview).decode("ascii"),
            "mock": mock, "placeholder_reason": placeholder_reason, "label": LABEL,
            "latency_s": elapsed, "cost_inr": cost, "renders_used": count + 1,
        }, headers=NO_STORE)


@app.post("/api/delete")
def delete(session: str = Form(...)):
    if not canonical_session(session):
        return error("Invalid session.", 422)
    stripe = lock_index(session)
    with LOCKS[stripe]:
        try:
            shutil.rmtree(TMP / session)
        except FileNotFoundError:
            pass
        except OSError:
            return error("Server deletion failed. Please try again.", 503)
        COUNTS[stripe][session] = (0, time.time() + PURGE_S, True)
    return JSONResponse({"deleted": True}, headers=NO_STORE)


app.mount("/products", StaticFiles(directory=PRODUCTS), name="products")
app.mount("/garments", StaticFiles(directory=GARMENTS), name="garments")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
