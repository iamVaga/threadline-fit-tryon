"""FitCheck photo validation and model adapters."""
import base64
import io
import math
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import mediapipe as mp
import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent
POSE_MODEL = ROOT / "pose_landmarker_lite.task"
SEGMENT_MODEL = ROOT / "selfie_multiclass_256x256.tflite"
REPLICATE_VERSION = "d73e611d73581af0410fc0b5cc400e2cae959a92cb6c8b54d6ad102c52b4d5ab"
MAX_UPLOAD = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"


def read_image(stream):
    data = stream.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ValueError("Image too large")
    with Image.open(io.BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
        if source.width * source.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("Too many pixels")
        return ImageOps.exif_transpose(source).convert("RGB")


def mp_image(image):
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(image))


def detect_pose(image):
    """First pose's landmarks for a (reduced) copy of the image, or None."""
    reduced = image.copy()
    reduced.thumbnail((960, 1280))
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(POSE_MODEL)),
        num_poses=1, min_pose_detection_confidence=0.4)
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as detector:
        poses = detector.detect(mp_image(reduced)).pose_landmarks
    return poses[0] if poses else None


def _upright_score(pose):
    """How plausibly the person stands head-up: shoulders above hips above ankles, all visible."""
    if not pose:
        return -1.0
    mid = lambda a, b: (pose[a].y + pose[b].y) / 2
    ordered = pose[0].y < mid(11, 12) < mid(23, 24) < mid(27, 28)
    return sum(pose[i].visibility for i in (0, 11, 12, 23, 24, 27, 28)) / 7 * (1 if ordered else 0.2)


def _border_colour(image):
    small = image.resize((32, 32))
    px = np.asarray(small).reshape(-1, 3)
    edge = np.concatenate([np.asarray(small)[0], np.asarray(small)[-1],
                           np.asarray(small)[:, 0], np.asarray(small)[:, -1]])
    return tuple(int(v) for v in np.median(edge, axis=0)) if len(px) else (235, 235, 235)


def normalize_photo(image):
    """Straighten and frame a recognisable person photo. Returns (image, list of adjustments).

    Fixes sideways/upside-down photos, a tilted body, a wide frame, and a person touching the frame
    edge. If no person is found the image is returned unchanged so check_photo can explain why.
    """
    changes = []
    try:
        pose = detect_pose(image)
        # Wrong orientation: try the three other rotations and keep the most upright reading.
        if _upright_score(pose) < 0.45 or image.height < image.width * 0.9:
            best = (_upright_score(pose), 0, image, pose)
            for angle in (90, 270, 180):
                turned = image.rotate(angle, expand=True)
                candidate = detect_pose(turned)
                if _upright_score(candidate) > best[0] + 0.05:
                    best = (_upright_score(candidate), angle, turned, candidate)
            if best[1]:
                image, pose = best[2], best[3]
                changes.append("rotated")
        if not pose:
            return image, changes
        # Tilted body: rotate so the shoulder-to-hip axis is vertical.
        w, h = image.size
        mid = lambda a, b: ((pose[a].x + pose[b].x) / 2 * w, (pose[a].y + pose[b].y) / 2 * h)
        (sx, sy), (hx, hy) = mid(11, 12), mid(23, 24)
        tilt = math.degrees(math.atan2(hx - sx, hy - sy))
        if 2.5 < abs(tilt) <= 35:
            image = image.rotate(-tilt, expand=True, resample=Image.BICUBIC, fillcolor=_border_colour(image))
            pose = detect_pose(image) or pose
            changes.append("straightened")
        w, h = image.size
        # Wide frame: crop to a portrait window centred on the person.
        if w > h * 0.9:
            xs = [p.x for p in pose]
            cx = (min(xs) + max(xs)) / 2 * w
            crop_w = min(w, h * 0.75)
            if (max(xs) - min(xs)) * w <= crop_w * 0.95:
                x0 = int(min(max(0, cx - crop_w / 2), w - crop_w))
                image = image.crop((x0, 0, int(x0 + crop_w), h))
                pose = detect_pose(image) or pose
                changes.append("cropped")
        # Person touching the frame edge: add a margin so head and feet have room.
        w, h = image.size
        top = min(p.y for p in (pose[0], pose[2], pose[5])) * h
        bottom = max(pose[i].y for i in (29, 30, 31, 32)) * h
        body = max(bottom - top, 1)
        # The nose sits below the crown, so leave generous room above it.
        pad_top = max(0, int(body * 0.18 - top)) if pose[0].visibility >= 0.5 else 0
        pad_bottom = max(0, int(body * 0.06 - (h - bottom))) \
            if min(pose[i].visibility for i in (29, 30, 31, 32)) >= 0.5 else 0
        if pad_top or pad_bottom:
            canvas = Image.new("RGB", (w, h + pad_top + pad_bottom), _border_colour(image))
            canvas.paste(image, (0, pad_top))
            image = canvas
            changes.append("added margin")
    except Exception:
        return image, changes
    return image, changes


def check_photo(image):
    width, height = image.size
    if min(width, height) < 480:
        return "Please retake the photo at a higher resolution (at least 480 pixels on the short side)."
    if height / width < 1.0:
        return "Please retake the photo in portrait orientation."

    reduced = image.copy()
    reduced.thumbnail((960, 1280))
    light = np.asarray(reduced.convert("L"))
    if np.percentile(light, 75) < 48 or np.percentile(light, 90) - np.percentile(light, 10) < 16:
        return "Please retake the photo with even light from the front and a clear background."
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(POSE_MODEL)),
        num_poses=2,
        min_pose_detection_confidence=0.4,
        output_segmentation_masks=True,
    )
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as detector:
        result = detector.detect(mp_image(reduced))
    poses = result.pose_landmarks
    if not poses:
        return "We couldn't find a person. Please retake the photo in good light with one person in the frame."
    if len(poses) > 1:
        return "Please retake the photo with just one person in the frame."

    pose = poses[0]
    required = (0, 2, 5, 11, 12, 23, 24, 29, 30, 31, 32)
    if any(
        not (0.02 < pose[index].x < 0.98 and 0.025 < pose[index].y < 0.975
             and pose[index].visibility >= 0.5 and pose[index].presence >= 0.5)
        for index in required
    ):
        return "Step back and retake the photo with your whole head and both feet visible."
    foreground = result.segmentation_masks[0].numpy_view().squeeze() > 0.55
    ys, _ = np.where(foreground)
    if not ys.size or ys.min() < foreground.shape[0] * .015 or ys.max() >= foreground.shape[0] * .985:
        return "Leave a little space above your head and below your feet, then retake."
    for heel, toe in ((29, 31), (30, 32)):
        evidence = []
        for index in (heel, toe):
            y = round(pose[index].y * (foreground.shape[0] - 1))
            x = round(pose[index].x * (foreground.shape[1] - 1))
            evidence.append(foreground[y-4:y+5, x-4:x+5].mean())
        if max(evidence) < .3:
            return "Please retake with both feet visible and uncovered."
    return None


def clothing_mask(segmenter, image):
    result = segmenter.segment(mp_image(image))
    # Google SelfieMulticlass: class 4 is clothing. This model's confidences peak below 0.95,
    # so a higher threshold finds no clothing at all.
    confidence = result.confidence_masks[4].numpy_view()
    mask = Image.fromarray(np.where(confidence.squeeze() >= 0.5, 255, 0).astype(np.uint8))
    return mask.filter(ImageFilter.MinFilter(5))


def remote_backend():
    if os.getenv("TRYON_REMOTE") != "1" or os.getenv("MOCK") == "1":
        return None
    backend = os.getenv("TRYON_BACKEND", "local_http")
    if backend == "gemini":
        return backend if os.getenv("ALLOW_GEMINI_PHOTOS") == "1" else None
    return backend if backend in {"local_http", "hf", "replicate"} else None


def person_crop_box(image):
    """Box around the person (3:4, with margin) when they are small in the frame, else None."""
    reduced = image.copy()
    reduced.thumbnail((960, 1280))
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(POSE_MODEL)),
        num_poses=1, min_pose_detection_confidence=0.4)
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as detector:
        poses = detector.detect(mp_image(reduced)).pose_landmarks
    if not poses:
        return None
    xs = [p.x for p in poses[0]]
    ys = [p.y for p in poses[0]]
    width, height = image.size
    left, right = max(0, min(xs)) * width, min(1, max(xs)) * width
    top, bottom = max(0, min(ys)) * height, min(1, max(ys)) * height
    if (bottom - top) > 0.75 * height:
        return None  # already fills the frame
    box_h = (bottom - top) * 1.35
    box_w = max((right - left) * 1.5, box_h * 0.75)
    box_h = max(box_h, box_w / 0.75)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    box_w, box_h = min(box_w, width), min(box_h, height)
    x0 = min(max(0, cx - box_w / 2), width - box_w)
    y0 = min(max(0, cy - box_h / 2), height - box_h)
    return (int(x0), int(y0), int(x0 + box_w), int(y0 + box_h))


def run_model(person, garment, desc, session_folder, category="one-pieces"):
    """Return the original with a reason whenever real inference is unavailable."""
    if os.getenv("MOCK") == "1":
        return person, "mock_mode"
    backend = remote_backend()
    token_name = {"local_http": "TRYON_HTTP_TOKEN", "hf": "HF_TOKEN",
                  "replicate": "REPLICATE_API_TOKEN", "gemini": "GEMINI_API_KEY"}.get(backend)
    token = os.getenv(token_name, "") if token_name else ""
    if not token:
        return person, "model_unavailable"
    try:
        box = person_crop_box(person)
    except Exception:
        box = None
    if not box:
        return _run_on(person, garment, desc, session_folder, backend, token, category)
    # Work on a close crop so the model sees the person at useful resolution, then paste it back.
    result, reason = _run_on(person.crop(box), garment, desc, session_folder, backend, token, category)
    if reason:
        return person, reason
    full = person.copy()
    full.paste(result, box[:2])
    return full, None


def _run_on(person, garment, desc, session_folder, backend, token, category):
    try:
        if backend == "gemini":
            # Gemini takes any aspect ratio, so send the photo as-is (no padding).
            padded = person.copy()
            padded.thumbnail((1280, 1280))
            size = padded.size
            box = (0, 0, size[0], size[1])
        else:
            size = (768, 1024)
            fitted = ImageOps.contain(person, size)
            x = (size[0] - fitted.width) // 2
            y = (size[1] - fitted.height) // 2
            box = (x, y, x + fitted.width, y + fitted.height)
            padded = Image.new("RGB", size, (235, 235, 235))
            padded.paste(fitted, (x, y))
        options = mp.tasks.vision.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(SEGMENT_MODEL)),
        )
        with mp.tasks.vision.ImageSegmenter.create_from_options(options) as segmenter:
            original_mask = clothing_mask(segmenter, padded)
            if not original_mask.getbbox():
                return person, "preservation_check"
            with TemporaryDirectory(prefix="work-", dir=session_folder) as work:
                person_path = Path(work) / "person.jpg"
                garment_path = Path(work) / "garment.jpg"
                padded.save(person_path, quality=95)
                garment.thumbnail((1024, 1024))
                garment.save(garment_path, quality=95)
                if backend == "hf":
                    generated = hf_tryon(person_path, garment_path, desc, token)
                elif backend == "gemini":
                    generated = gemini_tryon(person_path, garment_path, desc, token)
                    if abs(generated.width / generated.height - size[0] / size[1]) > 0.03:
                        return person, "preservation_check"
                    generated = generated.resize(size, Image.LANCZOS)
                elif backend == "local_http":
                    generated = local_http_tryon(person_path, garment_path, desc, token, category)
                else:
                    generated = replicate_tryon(person_path, garment_path, desc, token)
            if generated.size != size:
                return person, "preservation_check"
            mask = ImageChops.multiply(original_mask, clothing_mask(segmenter, generated))
            if not mask.getbbox():
                return person, "preservation_check"
            # Paste only the new kurta pixels onto the ORIGINAL full-resolution photo, so every
            # pixel outside the kurta is byte-for-byte the user's own.
            mask = mask.crop(box).resize(person.size, Image.NEAREST)
            generated = generated.crop(box).resize(person.size, Image.LANCZOS)
            return composite_preserving_source(person, generated, mask), None
    except httpx.HTTPStatusError as error:
        # Only the status code is surfaced; the body can echo request details.
        return person, "quota_exceeded" if error.response.status_code == 429 else "model_unavailable"
    except Exception:
        # Provider exceptions can include file paths, signed URLs or upstream input.
        return person, "model_unavailable"


def composite_preserving_source(person, generated, mask):
    """Only copy generated pixels at positive entries in the binary garment mask."""
    if person.size != generated.size or person.size != mask.size:
        raise ValueError("Composite inputs have different dimensions")
    return Image.composite(generated, person, mask.point(lambda value: 255 if value == 255 else 0))


def local_http_tryon(person, garment, desc, token, category):
    url = os.getenv("TRYON_HTTP_URL", "")
    parsed = httpx.URL(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.host in {"127.0.0.1", "localhost"}):
        raise ValueError("Use HTTPS for a remote model endpoint")
    return request_model(url, person, garment, desc, token, category)


def request_model(url, person, garment, desc, token, category="one-pieces"):
    with person.open("rb") as photo, garment.open("rb") as item:
        with httpx.stream("POST", url, headers={"Authorization": f"Bearer {token}"},
                          data={"description": desc, "category": category}, files={"person": photo, "garment": item},
                          timeout=90) as response:
            response.raise_for_status()
            buffer = io.BytesIO()
            for chunk in response.iter_bytes():
                if buffer.tell() + len(chunk) > MAX_UPLOAD:
                    raise ValueError("Model image exceeded size limit")
                buffer.write(chunk)
            buffer.seek(0)
            image = read_image(buffer)
    return image


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
_gemini_model = None


def gemini_model(token):
    """GEMINI_MODEL if set, else the first image-capable model this key can use."""
    global _gemini_model
    if os.getenv("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    if _gemini_model:
        return _gemini_model
    response = httpx.get(f"{GEMINI_URL}/models", params={"pageSize": 200}, headers={"x-goog-api-key": token}, timeout=15)
    response.raise_for_status()
    names = [m["name"].split("/", 1)[1] for m in response.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", []) and "image" in m["name"]
             and not any(bad in m["name"] for bad in ("imagen", "vision", "embedding"))]
    if not names:
        raise RuntimeError("No Gemini image model is available for this key")
    # Prefer stable names over previews/experiments.
    names.sort(key=lambda n: ("preview" in n or "exp" in n, n))
    _gemini_model = names[0]
    return _gemini_model


def gemini_tryon(person, garment, desc, token):
    def part(path):
        return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(path.read_bytes()).decode("ascii")}}

    prompt = (
        "Image 1 is a photo of a person. Image 2 is a product photo of a kurta (" + desc + "). "
        "Edit image 1 so the person is wearing exactly the kurta from image 2: same colour, print, neckline, "
        "sleeve length and hem length as the product photo, fitted naturally to the person's body and pose. "
        "Do not change anything else: keep the person's face, hair, skin tone, body shape, pose, hands, "
        "legs, footwear, background, framing and lighting identical to image 1. Output only the edited image."
    )
    body = {"contents": [{"parts": [{"text": prompt}, part(person), part(garment)]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    response = httpx.post(f"{GEMINI_URL}/models/{gemini_model(token)}:generateContent",
                          headers={"x-goog-api-key": token}, json=body, timeout=150)
    response.raise_for_status()
    for candidate in response.json().get("candidates", []):
        for item in candidate.get("content", {}).get("parts", []):
            data = (item.get("inlineData") or item.get("inline_data") or {}).get("data")
            if data:
                return read_image(io.BytesIO(base64.b64decode(data)))
    raise RuntimeError("Gemini returned no image")


def hf_tryon(person, garment, desc, token):
    from gradio_client import Client, handle_file

    client = Client(
        os.getenv("HF_MODEL_SPACE", "yisol/IDM-VTON"),
        token=token,
        verbose=False,
        analytics_enabled=False,
        download_files=person.parent / "output",
        httpx_kwargs={"timeout": 120},
        max_workers=1,
    )
    try:
        job = client.submit(
            dict={"background": handle_file(str(person)), "layers": [], "composite": None},
            garm_img=handle_file(str(garment)),
            garment_des=desc,
            is_checked=True,
            is_checked_crop=False,
            denoise_steps=30,
            seed=42,
            api_name="/tryon",
        )
        try:
            output, _ = job.result(timeout=150)
        except TimeoutError:
            job.cancel()
            raise
        with Path(output).open("rb") as stream:
            return read_image(stream)
    finally:
        client.close()


def replicate_tryon(person, garment, desc, token):
    # Data URLs keep the files in the prediction input rather than creating separate uploads.
    def data_uri(path):
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    with httpx.Client(
        base_url="https://api.replicate.com/v1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    ) as client:
        response = client.post("predictions", headers={"Cancel-After": "120s"}, json={
            "version": REPLICATE_VERSION,
            "input": {
                "human_img": data_uri(person),
                "garm_img": data_uri(garment),
                "garment_des": desc,
                "category": "upper_body",
                "crop": False,
                "steps": 30,
                "seed": 42,
            },
        })
        response.raise_for_status()
        prediction = response.json()
        prediction_id = prediction["id"]
        deadline = time.monotonic() + 125
        try:
            while prediction["status"] in {"starting", "processing"} and time.monotonic() < deadline:
                time.sleep(2)
                response = client.get(f"predictions/{prediction_id}")
                response.raise_for_status()
                prediction = response.json()
            if prediction["status"] != "succeeded":
                raise RuntimeError("Replicate did not complete the prediction")
            output = httpx.get(prediction["output"], timeout=15)
            output.raise_for_status()
            return read_image(io.BytesIO(output.content))
        finally:
            if prediction["status"] in {"starting", "processing"}:
                client.post(f"predictions/{prediction_id}/cancel")
