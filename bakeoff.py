"""Run one model across the same approved 4×3 inputs, then view the real comparisons."""
import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
from PIL import Image, ImageOps
from tempfile import TemporaryDirectory
from time import perf_counter, time

import mediapipe as mp
import numpy as np

from server import stamp
from tryon_model import SEGMENT_MODEL, clothing_mask, composite_preserving_source, request_model

FIELDS = ("model", "person_id", "garment_id", "status", "latency_s",
          "changed_outside_mask_raw", "changed_outside_mask_composite", "garment_print_fidelity_1to5")


def bakeoff(manifest, model, directory):
    if model not in {"fashn", "catvton", "idm"}:
        raise ValueError("Choose one of fashn, catvton, idm")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    people, garments = data["people"], data["garments"]
    if len(people) != 3 or len(garments) != 4 or not all(p["authorized_adult_photo"] is True for p in people):
        raise ValueError("Provide three approved adult photos and four garment photos.")
    directory.mkdir(exist_ok=True, parents=True)
    for old in directory.glob("*.png"):
        if time() - old.stat().st_mtime >= 86400:
            old.unlink()
    rows = []
    url = os.environ.get("BAKEOFF_MODEL_URL", "http://127.0.0.1:7860/render")
    if not url.startswith("http://127.0.0.1:"):
        raise ValueError("Bake-off endpoints must run on this notebook host")
    def read(relative):
        path = (manifest.parent / relative).resolve()
        if not path.is_relative_to(manifest.parent.resolve()):
            raise ValueError("Image path escapes the private manifest directory")
        with Image.open(path) as image:
            return ImageOps.exif_transpose(image).convert("RGB")
    options = mp.tasks.vision.ImageSegmenterOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(SEGMENT_MODEL)),
    )
    with mp.tasks.vision.ImageSegmenter.create_from_options(options) as segmenter, TemporaryDirectory() as work:
        person_path, item_path = Path(work)/"photo.png", Path(work)/"garment.png"
        for person_index, p in enumerate(people, 1):
            original = read(p["file"])
            network_input = ImageOps.pad(original, (768,1024), color="#ebebeb")
            contained = ImageOps.contain(original,(768,1024))
            x,y=(768-contained.width)//2,(1024-contained.height)//2
            box=(x,y,x+contained.width,y+contained.height)
            network_input.save(person_path)
            for item_index,item in enumerate(garments, 1):
                row = dict.fromkeys(FIELDS,"")
                row.update(model=model,person_id=person_index,garment_id=item_index,status="failed")
                try:
                    garment = read(item["file"])
                    garment.thumbnail((1024,1024))
                    garment.save(item_path)
                    start = perf_counter()
                    generated = request_model(url,person_path,item_path,item.get("description","kurta"),os.environ["TRYON_HTTP_TOKEN"])
                    row["latency_s"] = round(perf_counter()-start,3)
                    mask = np.asarray(clothing_mask(segmenter,network_input)) == 255
                    mask &= np.asarray(clothing_mask(segmenter,generated)) == 255
                    if not mask.any():
                        raise ValueError("Empty clothing intersection")
                    full_mask = Image.fromarray(mask.astype(np.uint8)*255).crop(box).resize(original.size,Image.Resampling.NEAREST)
                    aligned = generated.crop(box).resize(original.size,Image.Resampling.LANCZOS)
                    output = composite_preserving_source(original,aligned,full_mask)
                    outside = np.asarray(full_mask) == 0
                    row["changed_outside_mask_raw"] = int(np.count_nonzero(np.any(np.asarray(original)!=np.asarray(aligned),axis=2)&outside))
                    row["changed_outside_mask_composite"] = int(np.count_nonzero(np.any(np.asarray(original)!=np.asarray(output),axis=2)&outside))
                    if row["changed_outside_mask_composite"]:
                        raise ValueError("Outside-mask verification failed")
                    (directory/f"{model}-{person_index}-{item_index}.png").write_bytes(stamp(output,False))
                    row["status"] = "rendered"
                except (OSError, ValueError, KeyError):
                    pass
                except Exception:
                    # Record the failure without printing credentials or uploaded image paths.
                    pass
                rows.append(row)
                with (directory/f"{model}.csv").open("w",encoding="utf-8",newline="") as stream:
                    writer=csv.DictWriter(stream,fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
    print(dict(Counter(r["status"] for r in rows)))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model",choices=["fashn","catvton","idm"])
    parser.add_argument("--manifest",type=Path,default=Path("photos/bakeoff.json"))
    parser.add_argument("--out",type=Path,default=Path("renders/bakeoff"))
    args=parser.parse_args()
    bakeoff(args.manifest,args.model,args.out)
