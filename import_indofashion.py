"""Import a slice of the Indo Fashion dataset into the catalogue.

1. Download https://www.kaggle.com/datasets/validmodel/indo-fashion-dataset (needs a Kaggle login)
   and unzip it somewhere, e.g. ~/Downloads/indo-fashion.
2. Run: .venv/bin/python import_indofashion.py ~/Downloads/indo-fashion [--per-class 40]

It reads the *_data.json annotation files (JSON lines or a JSON list), copies a resized JPEG of up to
--per-class items per garment class into static/products/, and writes static/products/indo_catalog.json,
which /api/catalog merges in. The dataset is published for research: check its licence before public use.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

PRODUCTS = Path(__file__).resolve().parent / "static" / "products"
# class label -> try-on garment category. Unlisted classes (dupatta, petticoat, mojari...) are skipped.
CLASSES = {
    "saree": "one-pieces", "lehenga": "one-pieces", "gown": "one-pieces", "gowns": "one-pieces",
    "women kurta": "one-pieces", "kurta women": "one-pieces", "kurta_women": "one-pieces",
    "men kurta": "one-pieces", "kurta men": "one-pieces", "kurta_men": "one-pieces", "sherwani": "one-pieces",
    "blouse": "tops", "nehru jacket": "tops", "nehru_jacket": "tops",
    "leggings": "bottoms", "palazzo": "bottoms", "dhoti pants": "bottoms", "dhoti_pants": "bottoms",
}


def records(path):
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def find_image(root, row):
    for key in ("image_path", "image", "path", "filename"):
        value = row.get(key)
        if isinstance(value, str) and value:
            for candidate in (root / value, root / "images" / value, root / "images" / Path(value).name):
                if candidate.is_file():
                    return candidate
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--per-class", type=int, default=40)
    args = parser.parse_args()
    root = args.folder.expanduser().resolve()
    files = sorted(root.rglob("*_data.json"))
    if not files:
        raise SystemExit(f"No *_data.json annotation files under {root}. Is this the unzipped dataset?")
    PRODUCTS.mkdir(parents=True, exist_ok=True)
    taken, items = {}, []
    for file in files:
        for row in records(file):
            label = str(row.get("class_label") or row.get("class") or "").strip().lower()
            garment = CLASSES.get(label)
            if not garment or taken.get(label, 0) >= args.per_class:
                continue
            source = find_image(root, row)
            if not source:
                continue
            try:
                with Image.open(source) as image:
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image.thumbnail((1024, 1024))
                    stem = "in-" + hashlib.sha1(str(source.relative_to(root)).encode()).hexdigest()[:10]
                    image.save(PRODUCTS / f"{stem}.jpg", quality=90)
            except OSError:
                continue
            taken[label] = taken.get(label, 0) + 1
            title = re.sub(r"\s+", " ", str(row.get("product_title") or label.title())).strip()[:120]
            items.append({"sku_id": stem.upper(), "brand": str(row.get("brand") or "Indo Fashion")[:60],
                          "name": title, "price": None, "currency": None, "fit": "Regular",
                          "category": label, "garment_category": garment, "colour": "Assorted",
                          "hex": "#967087", "image": f"{stem}.jpg"})
    (PRODUCTS / "indo_catalog.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    print(f"Wrote {len(items)} products:", ", ".join(f"{k} {v}" for k, v in sorted(taken.items())))


if __name__ == "__main__":
    main()
