"""Threadline photo validation and model adapters."""
import base64
import io
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


def check_photo(image):
    width, height = image.size
    if min(width, height) < 480:
        return "Please retake the photo at a higher resolution (at least 480 pixels on the short side)."
    if height / width < 1.3:
        return "Please retake the photo in portrait orientation, with room above your head and below your feet."

    reduced = image.copy()
    reduced.thumbnail((960, 1280))
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
    # Nose, eyes, both heels and both toes. Leave a margin for the head above the eyes.
    landmark_missing = any(
        not (0.02 < pose[index].x < 0.98 and top < pose[index].y < 0.98
             and pose[index].visibility >= 0.5 and pose[index].presence >= 0.5)
        for index, top in ((0, 0.05), (2, 0.05), (5, 0.05), (29, 0.02), (30, 0.02), (31, 0.02), (32, 0.02))
    )
    if landmark_missing:
        return "Please step back and retake the photo with your head and both feet fully in the frame."

    # Pose landmarks can extrapolate a hidden foot. Also require foreground evidence
    # around at least one of the two landmarks per foot.
    foreground = result.segmentation_masks[0].numpy_view().squeeze()
    mask_height, mask_width = foreground.shape

    def foreground_at(index):
        point = pose[index]
        x, y = int(point.x * mask_width), int(point.y * mask_height)
        return foreground[y - 4:y + 5, x - 4:x + 5].mean()

    if any(max(foreground_at(heel), foreground_at(toe)) < 0.35
           for heel, toe in ((29, 31), (30, 32))):
        return "Please retake the photo with both feet visible and uncovered."
    return None


def clothing_mask(segmenter, image):
    result = segmenter.segment(mp_image(image))
    # Google SelfieMulticlass: class 4 is clothing.
    confidence = result.confidence_masks[4].numpy_view()
    mask = Image.fromarray(np.where(confidence.squeeze() >= 0.95, 255, 0).astype(np.uint8))
    return mask.filter(ImageFilter.MinFilter(7))


def remote_backend():
    if os.getenv("TRYON_REMOTE") != "1" or os.getenv("MOCK") == "1":
        return None
    backend = os.getenv("TRYON_BACKEND", "hf")
    return backend if backend in {"hf", "replicate"} else None


def run_model(person, garment, desc, session_folder):
    """Return the original with a reason whenever real inference is unavailable."""
    if os.getenv("MOCK") == "1":
        return person, "mock_mode"
    backend = remote_backend()
    token_name = {"hf": "HF_TOKEN", "replicate": "REPLICATE_API_TOKEN"}.get(backend)
    token = os.getenv(token_name, "") if token_name else ""
    if not token:
        return person, "model_unavailable"

    try:
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
                else:
                    generated = replicate_tryon(person_path, garment_path, desc, token)
            if generated.size != size:
                return person, "preservation_check"
            mask = ImageChops.multiply(original_mask, clothing_mask(segmenter, generated))
            if not mask.getbbox():
                return person, "preservation_check"
            return Image.composite(generated, padded, mask).crop(box), None
    except Exception:
        # Provider exceptions can include file paths, signed URLs or upstream input.
        return person, "model_unavailable"


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
