"""Fetch a free clothing catalogue (DummyJSON and Fake Store API) into static/products/.

Writes garment images plus static/products/free_catalog.json, which /api/catalog merges in.
Run: .venv/bin/python fetch_catalog.py
"""
import json
import re
from pathlib import Path

import httpx

PRODUCTS = Path(__file__).resolve().parent / "static" / "products"
# DummyJSON category -> (garment category for the try-on model, label)
CATEGORIES = {"womens-dresses": "one-pieces", "tops": "tops", "mens-shirts": "tops"}
HEX = {"black": "#2b2b2b", "white": "#e8e8e8", "red": "#a54646", "blue": "#4b6898", "green": "#547655",
       "pink": "#ab6784", "yellow": "#d4b13a", "brown": "#7a5a43", "grey": "#808080", "gray": "#808080"}


# (regex on the title, garment category for try-on, shop category label); first match wins.
RULES = [
    (r"\bbackpack|\bbag\b|\bcap\b", None, None),  # not clothing
    (r"jacket|\bcoat|raincoat|windbreaker", "tops", "Jackets"),
    (r"t-?\s?shirt|\btshirt|\btee\b|short sleeve", "tops", "T-Shirts"),
    (r"\bshirt", "tops", "Shirts"),
    (r"hoodie|sweatshirt|sweater|cardigan|blouse|\btop\b", "tops", "Tops"),
    (r"corset|dress|frock|gown|suit", "one-pieces", "Dresses"),
    (r"jogger|\bpants?\b|jeans|trouser|legging|\bskirt\b", "bottoms", "Bottoms"),
]


def classify(title):
    t = title.lower()
    for pattern, garment, label in RULES:
        if re.search(pattern, t):
            return garment, label
    return None, None


def colour_of(title):
    return next((w.capitalize() for w in title.lower().replace("-", " ").split() if w in HEX), "Assorted")


def save_image(client, url, stem):
    suffix = Path(url.split("?")[0]).suffix.lower()
    filename = stem + (suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg")
    response = client.get(url)
    response.raise_for_status()
    (PRODUCTS / filename).write_bytes(response.content)
    return filename


def extra_sources(client):
    """Fake Store API: more free clothing, mostly plain product shots."""
    rows = []
    for cat in ("men's clothing", "women's clothing"):
        for p in client.get("https://fakestoreapi.com/products/category/" + cat).json():
            rows.append(("FS", p["id"], p["title"], p["price"], p["image"], "Fake Store"))
    items = []
    for prefix, pid, title, price, image, brand in rows:
        garment, label = classify(title)
        if not garment:
            continue
        try:
            filename = save_image(client, image, f"{prefix.lower()}-{pid}")
        except httpx.HTTPError:
            continue
        colour = colour_of(title)
        items.append({"sku_id": f"{prefix}-{pid}", "brand": brand, "name": title, "price": price,
                      "currency": "USD", "fit": "Regular", "category": "clothing", "category_label": label,
                      "garment_category": garment,
                      "colour": colour, "hex": HEX.get(colour.lower(), "#967087"), "image": filename})
    return items


def main():
    PRODUCTS.mkdir(parents=True, exist_ok=True)
    items = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for category in CATEGORIES:
            data = client.get(f"https://dummyjson.com/products/category/{category}", params={"limit": 100}).json()
            for p in data["products"]:
                url = p.get("thumbnail") or p["images"][0]
                garment, label = classify(p["title"])
                if not garment:
                    continue
                filename = f"dj-{p['id']}{Path(url.split('?')[0]).suffix.lower() or '.webp'}"
                (PRODUCTS / filename).write_bytes(client.get(url).content)
                colour = next((w.capitalize() for w in p["title"].lower().split() if w in HEX), "Assorted")
                items.append({"sku_id": f"DJ-{p['id']}", "brand": p.get("brand") or "DummyJSON",
                              "name": p["title"], "price": p["price"], "currency": "USD",
                              "fit": "Regular", "category": category, "category_label": label, "garment_category": garment,
                              "colour": colour, "hex": HEX.get(colour.lower(), "#967087"), "image": filename})
        items += extra_sources(client)
    (PRODUCTS / "free_catalog.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    print(f"Wrote {len(items)} products")


if __name__ == "__main__":
    main()
