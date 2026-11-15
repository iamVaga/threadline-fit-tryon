"""Train FitCheck on the synthetic PS-13 workbooks and export browser model data."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

import numpy as np
from openpyxl import load_workbook


KURTAS = "Women · Kurtas"
LETTERS = ("XS", "S", "M", "L", "XL", "XXL", "3XL")
NUMERIC = {
    "Women · Jeans": ("26", "28", "30", "32", "34", "36"),
    "Men · Jeans": ("28", "30", "32", "34", "36", "38", "40"),
}
ALIASES = {"SMALL": "S", "MEDIUM": "M", "LARGE": "L", "XLARGE": "XL",
           "2XL": "XXL", "XXXL": "3XL"}
MEASURE_COLUMNS = {
    "bust": "measured_bust_or_chest_cm",
    "waist": "measured_waist_cm",
    "hip": "measured_hip_cm",
}
FIT_CASES = {
    "FT-10022": "Returning shopper · Noor & Neel",
    "FT-10001": "Size exchange · Vastrika",
    "FT-10025": "Chart mismatch · Chandni Lane",
    "FT-10024": "New shopper · Sutra Nine",
    "FT-10026": "Rigid denim · Grey Fox",
}
ROOT = Path(__file__).resolve().parent


def read(file, tabs):
    with file.open("rb") as stream:
        book = load_workbook(stream, read_only=True, data_only=True)
        try:
            result = {}
            for tab in tabs:
                values = iter(book[tab].values)
                columns = next(values)
                result[tab] = [dict(zip(columns, row)) for row in values if row[0] is not None]
            return result
        finally:
            book.close()


def normalize(value):
    label = str(value or "").strip().upper().replace(" ", "").replace("-", "")
    if label.endswith(".0"):
        label = label[:-2]
    if label.endswith("W") and label[:-1].isdigit():
        label = label[:-1]
    return ALIASES.get(label, label)


def group(category):
    return category if category in NUMERIC else category.split(" · ")[0]


def scale(category):
    return NUMERIC.get(category, LETTERS)


def measure_basis(group_name):
    body = {"height": (162, 10), "weight": (60, 10)} if group_name.startswith("Women") else {
        "height": (175, 10), "weight": (72, 10),
    }
    if group_name == "Women · Jeans":
        return {"waist": (75, 5), "hip": (98, 5), **body}
    if group_name == "Men · Jeans":
        return {"waist": (82, 5), "hip": (98, 5), **body}
    if group_name == "Women":
        return {"bust": (90, 5), "hip": (98, 5), **body}
    return {"bust": (96, 5), "hip": (98, 5), **body}


def height_cm(value):
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+) ft (\d+) in", value.strip())
        if not match:
            return None
        feet, inches = map(int, match.groups())
        return (feet * 12 + inches) * 2.54
    return value


def shopper(row, category):
    row = row or {}
    usual = normalize(row.get("usual_size_bottom" if category in NUMERIC else "usual_size_top"))
    measurements = {
        key: row[column]
        for key, column in MEASURE_COLUMNS.items()
        if row.get("measurement_consent") and row.get(column) is not None
    }
    height = height_cm(row.get("self_reported_height_cm"))
    weight = row.get("self_reported_weight_kg")
    if height is not None and 125 <= height <= 220:
        measurements["height"] = height
    if weight is not None and 30 <= weight <= 200:
        measurements["weight"] = weight
    return {"usual": usual, "measurements": measurements}


def profile_features(profile, category):
    sizes = scale(category)
    usual = profile["usual"]
    features = {"usual": sizes.index(usual) - 2.5, "hasUsual": 1} if usual in sizes else {}
    for key, (center, step) in measure_basis(group(category)).items():
        value = profile["measurements"].get(key)
        if value is not None:
            features[key] = (value - center) / step
            features[f"has_{key}"] = 1
    return features


def product_features(item):
    category = item["category"]
    features = [f"category|{category}", f"brand|{category}|{item['brand']}"]
    if item["fit"] != "Regular":
        features.append(f"fit|{category}|{item['fit']}")
    if item["stretch"]:
        features.append(f"stretch|{category}")
    return features


def fit_group(rows, group_name, products, profiles):
    # Penalised least squares with a partially pooled customer effect. Each pass
    # learns the catalogue effects, then updates the customer effect from residuals.
    columns = {"intercept": 0}
    ratings = []
    for entry in rows:
        item = products[entry["sku"]]
        profile = shopper(profiles.get(entry["customer"]), item["category"])
        features = {"intercept": 1, **profile_features(profile, item["category"])}
        features.update(dict.fromkeys(product_features(item), 1))
        for key in features:
            columns.setdefault(key, len(columns))
        ratings.append(features)
    matrix = np.zeros((len(rows), len(columns)))
    for index, features in enumerate(ratings):
        for key, value in features.items():
            matrix[index, columns[key]] = value
    labels = np.array([scale(r["category"]).index(r["size"]) for r in rows])
    _, person = np.unique([r["customer"] for r in rows], return_inverse=True)
    count = np.bincount(person)
    weights = np.ones(len(columns)) * 12
    weights[0] = 0
    normal = matrix.T @ matrix + np.diag(weights)
    customer_effect = np.zeros(len(count))
    for _ in range(24):
        coefficients = np.linalg.solve(normal, matrix.T @ (labels - customer_effect[person]))
        residuals = labels - matrix @ coefficients
        customer_effect = np.bincount(person, weights=residuals) / (count + 1.5)
    return {
        "weights": {key: round(float(coefficients[index]), 7) for key, index in columns.items()},
        "measureBasis": measure_basis(group_name),
    }


def build_inputs(cases, products, profiles, history):
    for case in cases:
        sku = case["sku_id"]
        profile = shopper(profiles.get(case["customer_id"]), products[sku]["category"])
        yield {
            "id": case["test_id"], "sku": sku, **profile,
            "history": [{k: row[k] for k in ("sku", "size")}
                        for row in history[case["customer_id"]]
                        if group(row["category"]) == group(products[sku]["category"])],
        }


def prepare(args):
    folder = args.data
    source = read(folder / "PS-13_catalogue.xlsx", ["products"])
    products = {
        row["sku_id"]: {
            "brand": row["brand"], "category": row["category"], "name": row["product_name"],
            "colour": row["colour"], "price": row["mrp_inr"], "fit": row["fit_type"],
            "stretch": row["stretch_pct"],
            "sizes": [normalize(label) for label in row["sizes_available"].split(",")],
        }
        for row in source["products"]
    }
    training = read(folder / "PS-13_orders_and_returns.xlsx", ["orders", "customers"])
    profiles = {row["customer_id"]: row for row in training["customers"]}
    history, training_groups, returns = defaultdict(list), defaultdict(list), defaultdict(Counter)
    cutoff = datetime.fromisoformat(args.cutoff)
    for row in training["orders"]:
        if row["order_date"] >= cutoff:
            continue
        sku = row["sku_id"]
        category = products[sku]["category"]
        completed = row["return_requested_at"] is not None and row["return_requested_at"] < cutoff
        if not completed and row["order_date"] >= cutoff - timedelta(days=14):
            continue
        reason = row["return_reason"] if completed else None
        brand = products[sku]["brand"]
        returns[(category, brand)]["orders"] += 1
        if reason in {"Too small", "Too large"}:
            returns[(category, brand)][reason] += 1

        size = normalize(row["exchanged_to_size"] if completed else row["size_bought"])
        if size not in scale(category):
            continue
        entry = {"sku": sku, "size": size, "category": category, "customer": row["customer_id"]}
        history[entry["customer"]].append(entry)
        training_groups[group(category)].append(entry)

    model = {
        "products": products,
        "groups": {key: fit_group(rows, key, products, profiles) for key, rows in training_groups.items()},
        "returnDirection": {
            f"{category}|{brand}": round((counts["Too small"] - counts["Too large"]) / counts["orders"], 4)
            for (category, brand), counts in returns.items()
        },
    }
    test = read(folder / "PS-13_fit_test_set.xlsx", ["test_cases", "answers"])
    inputs = list(build_inputs(test["test_cases"], products, profiles, history))
    answers = {row["test_id"]: row for row in test["answers"]}
    model["examples"] = [{**entry, "title": FIT_CASES[entry["id"]]} for entry in inputs if entry["id"] in FIT_CASES]
    model_bytes = json.dumps(model, ensure_ascii=False, sort_keys=True).encode("utf-8")
    model["version"] = f"ps13-fit-{args.cutoff}-{sha256(model_bytes).hexdigest()[:10]}"
    args.model.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    rows = []
    for entry in inputs:
        answer = answers[entry["id"]]
        rows.append({
            **entry, "correct": normalize(answer["correct_size"]),
            "acceptable": [normalize(size) for size in answer["acceptable_sizes"].split(",")],
            "first": normalize(answer["size_customer_first_chose"]), "source": answer["label_source"],
        })
    args.evaluation.write_text(json.dumps(rows, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Fit model trained on {sum(map(len, training_groups.values())):,} mature kept/exchanged lines.")
    print(f"Exported {len(products):,} SKUs and prepared {len(rows):,} held-out cases.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="Directory containing the three supplied Excel workbooks.")
    parser.add_argument("--cutoff", default="2026-10-01", help="Exclusive training cutoff (ISO date).")
    parser.add_argument("--model", type=Path, default=ROOT / "static/fit/data.json")
    parser.add_argument("--evaluation", type=Path, default=ROOT / ".local/fit_cases.json")
    config = parser.parse_args()
    config.model.parent.mkdir(parents=True, exist_ok=True)
    config.evaluation.parent.mkdir(parents=True, exist_ok=True)
    prepare(config)
