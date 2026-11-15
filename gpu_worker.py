"""Single-worker GPU adapter for FitCheck's local_http backend."""
import hmac
import io
import os
from pathlib import Path
import threading

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps

app = FastAPI(title="FitCheck GPU worker")
lock = threading.Lock()
pipeline = None
masker = None
model = os.getenv("FITCHECK_GPU_MODEL", "fashn")


def run_inference(person, garment, description, category):
    global pipeline, masker
    if model == "fashn":
        from fashn_vton import TryOnPipeline
        if pipeline is None:
            pipeline = TryOnPipeline(weights_dir=os.environ["FASHN_WEIGHTS"])
        return pipeline(person_image=person, garment_image=garment, category=category).images[0]
    if model == "catvton":
        from huggingface_hub import snapshot_download
        import torch
        from model.cloth_masker import AutoMasker
        from model.pipeline import CatVTONPipeline
        from diffusers.image_processor import VaeImageProcessor

        if pipeline is None:
            weights = Path(snapshot_download(repo_id="zhengchong/CatVTON"))
            pipeline = CatVTONPipeline(
                base_ckpt="booksforcharlie/stable-diffusion-inpainting",
                attn_ckpt=str(weights), attn_ckpt_version="mix", weight_dtype=torch.float16,
                device="cuda", use_tf32=False,
            )
            masker = AutoMasker(densepose_ckpt=str(weights / "DensePose"),
                                schp_ckpt=str(weights / "SCHP"), device="cuda")
        garment_type = {"tops":"upper", "bottoms":"lower", "one-pieces":"overall"}[category]
        image_mask = masker(person, garment_type)["mask"]
        processor = VaeImageProcessor(vae_scale_factor=8, do_normalize=False,
                                      do_binarize=True, do_convert_grayscale=True)
        return pipeline(image=person, condition_image=ImageOps.pad(garment, person.size, color="white"),
                        mask=processor.blur(image_mask,blur_factor=9), num_inference_steps=30,
                        guidance_scale=2.5, generator=torch.Generator("cuda").manual_seed(42))[0]
    if model == "idm":
        import tempfile
        from urllib.parse import urlsplit
        from gradio_client import Client, handle_file

        source = os.environ.get("IDM_LOCAL_GRADIO", "http://127.0.0.1:7861/")
        parsed = urlsplit(source)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("IDM must run on this GPU host")
        with tempfile.TemporaryDirectory() as work:
            person_path = str(Path(work) / "person.png")
            garment_path = str(Path(work) / "garment.png")
            person.save(person_path)
            garment.save(garment_path)
            client = Client(source, verbose=False, download_files=work, analytics_enabled=False)
            try:
                response = client.submit(
                    {"background":handle_file(person_path), "layers":[], "composite":None},
                    handle_file(garment_path), description, True, False, 30, 42, api_name="/tryon",
                ).result(timeout=150)
                with Image.open(response[0]) as generated:
                    return generated.convert("RGB")
            finally:
                client.close()
    raise ValueError("Unknown FITCHECK_GPU_MODEL")


def read_upload(upload):
    raw = upload.file.read(12 * 1024 * 1024 + 1)
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Upload too large")
    with Image.open(io.BytesIO(raw), formats=("PNG","JPEG","WEBP")) as image:
        if image.width * image.height > 5_000_000:
            raise ValueError("Upload resolution too large")
        return image.convert("RGB")


@app.post("/render")
def render(person: UploadFile = File(...), garment: UploadFile = File(...),
           description: str = Form("", max_length=160), category: str = Form("one-pieces"),
           authorization: str = Header("")):
    key = os.getenv("TRYON_HTTP_TOKEN","")
    if not key or not hmac.compare_digest(authorization.encode(), f"Bearer {key}".encode()):
        raise HTTPException(401,"Authentication required")
    if category not in {"tops", "bottoms", "one-pieces"}:
        raise HTTPException(422,"Unsupported garment category")
    try:
        original, item = read_upload(person), read_upload(garment)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422,"Use readable image files under 12 MB.")
    with lock:
        try:
            output = run_inference(original,item,description,category).convert("RGB")
            if output.size != original.size:
                output = output.resize(original.size,Image.Resampling.LANCZOS)
        except Exception:
            raise HTTPException(503,"The GPU model could not produce a preview.")
        buffer = io.BytesIO()
        output.save(buffer,"PNG")
        return Response(buffer.getvalue(),media_type="image/png",headers={"Cache-Control":"no-store"})
