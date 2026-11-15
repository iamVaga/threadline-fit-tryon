"""FitCheck size inference from the January–September model artifact."""
import json
from functools import lru_cache
from pathlib import Path

LETTERS = ("XS", "S", "M", "L", "XL", "XXL", "3XL")
JEANS = tuple(str(n) for n in range(24, 43, 2))
ALIASES = {"SMALL": "S", "MEDIUM": "M", "LARGE": "L", "X-LARGE": "XL",
           "2XL": "XXL", "XXXL": "3XL", "X-SMALL": "XS"}
FIT = {"Slim": .40, "Relaxed": -.60}
PRIMARY = {"Jeans": "waist", "Dresses": "bust", "Tops": "bust", "Kurtas": "bust",
           "T-shirts": "bust", "Shirts": "bust"}
SECONDARY = {"Kurtas": ("hip",), "Dresses": ("waist", "hip"), "Jeans": ("hip",)}


def labels(category):
    return JEANS if category.endswith("Jeans") else LETTERS


def normalize(value):
    value = str(value or "").strip().upper().removesuffix(".0")
    if value.endswith("W") and value[:-1].isdigit():
        value = value[:-1]
    return ALIASES.get(value, value)


def index(value, category):
    label = normalize(value)
    scale = labels(category)
    return scale.index(label) if label in scale else None


def product_adjustment(product):
    stretch = product.get("stretch_pct")
    return FIT.get(product["fit_type"], 0) - min(stretch or 0, 8) * .035


def measurements_to_size(product, dimensions):
    gender, garment = product["category"].split(" · ")
    reference = {"bust": 90 if gender == "Women" else 96,
                 "waist": 74 if gender == "Women" else 82,
                 "hip": 98 if gender == "Women" else 100}
    primary = PRIMARY[garment]
    if primary not in dimensions:
        return None
    if garment == "Jeans":
        waist = (dimensions["waist"] / 2.54 - 24) / 2
        hip = waist + max(0, (dimensions.get("hip", 0) - dimensions["waist"] - 29) / 12)
        return max(waist, hip)
    estimate = 2 + (dimensions[primary] - reference[primary]) / 5
    for key in SECONDARY.get(garment, ()):
        if key in dimensions:
            estimate = max(estimate, 2 + (dimensions[key] - reference[key]) / 5 - .5)
    return estimate


def certainty_band(history_count, has_anchor):
    return "2+ observations" if history_count >= 2 else "1 observation" if history_count else (
        "profile or inputs" if has_anchor else "category prior"
    )


@lru_cache(maxsize=1)
def model():
    path = Path(__file__).resolve().parent / "results" / "model.json"
    return json.loads(path.read_text(encoding="utf-8"))


def predict(state, product, profile=None, usual_size=None, usual_brand=None, preference=0,
            dimensions=None):
    category, brand = product["category"], product["brand"]
    scale = labels(category)
    offsets = state["offsets"].get(category, {})
    offset = offsets.get(brand, 0)
    profile = profile or {}
    anchor = index(usual_size or profile.get("usual"), category)
    measured = measurements_to_size(product, dimensions or {})
    if measured is None:
        measured = profile.get("measured")
    history = profile.get("history", [])
    # Same-category outcomes anchor the estimate. Cross-category outcomes have lower weight.
    sums = [(observation[0], observation[1]) for observation in history]
    if anchor is not None:
        sums.append((anchor - offsets.get(usual_brand, 0), .8))
    if measured is not None:
        sums.append((measured, 1.5))
    rigid = product.get("stretch_pct") == 0
    prior = state["priors"].get(category, scale.index("M") if scale == LETTERS else scale.index("32"))
    if rigid and category.endswith("Jeans"):
        prior = state.get("rigid_priors",{}).get(category,prior)
    if sums:
        latent = sum(x * w for x, w in sums) / sum(w for _, w in sums)
    else:
        latent = prior
    same_brand = profile.get("brands", {}).get(brand, [])
    if same_brand:
        latent = (latent + sum(same_brand)) / (1 + len(same_brand))
    target = latent + offset + product_adjustment(product) + preference
    options = [(index(label, category), label) for label in product["sizes_available"]]
    options = [(i, label) for i, label in options if i is not None]
    if not options:
        options = list(enumerate(scale))
    ordered_options = sorted(options)
    nearest = min(ordered_options, key=lambda item: abs(item[0] - target))
    margin = .02 if rigid and category.endswith("Jeans") else .18
    between = any(left[0] + margin < target < right[0] - margin
                  for left, right in zip(ordered_options, ordered_options[1:]))
    if rigid and category.endswith("Jeans") and between:
        nearest = next(item for item in ordered_options if item[0] > target)
    count = profile.get("count", 0)
    group = certainty_band(count, anchor is not None or measured is not None)
    calibration = state.get("calibration", {}).get(group, {})
    evidence = state.get("brand_index", {}).get(category, {}).get(brand, {})
    direction = evidence.get("direction", "Near true")
    reason = f"{brand} {direction.lower()} in {category.split(' · ')[1].lower()} based on {evidence.get('lines', 0):,} order lines."
    if count:
        reason += f" Your history contributes {count} prior fit outcomes."
    elif anchor is None and measured is None:
        reason = "Starting size from category history. Add your usual size and fit preference for a more specific recommendation."
    if rigid and category.endswith("Jeans"):
        reason += " The fabric has no stretch."
        if between:
            reason += " We chose the larger size because the estimate falls between sizes."
    return {"size": nearest[1], "confidence": calibration.get("accuracy"),
            "calibration_n": calibration.get("n", 0), "reason": reason, "history_count": count,
            "needs_input": not sums, "between_sizes": between, "direction": direction,
            "chart_unreliable": evidence.get("chart_unreliable", False), "sku_id": product["sku_id"],
            "category": category, "brand": brand}
