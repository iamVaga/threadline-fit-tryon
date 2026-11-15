"""Run a consented PS-13 workbook through the local FitCheck HTTP API."""
import argparse
import csv
import math
import os
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from openpyxl import load_workbook

LATENCY = "render_latency_sec"
COST = "cost_per_render_inr"
HUMAN = {"realism", "realism_1to5", "garment_fidelity", "garment_fidelity_1to5",
         "colour_fidelity", "colour_fidelity_1to5", "color_fidelity", "color_fidelity_1to5",
         "body_preserved", "body_preserved_yes_no", "simulation_label_shown_yes_no", "notes"}


def normalize(value):
    name = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return name.removeprefix("fill_in_").removesuffix("_fill_in")


def layout(sheet, args):
    photo_names = {normalize(args.photo_column)} if args.photo_column else {
        "photo", "photo_file", "photo_filename", "photo_path", "person_photo", "person_photo_path"
    }
    garment_names = {normalize(args.garment_column)} if args.garment_column else {
        "garment", "garment_name", "garment_file", "garment_image", "garment_filename",
        "garment_path", "kurta_image", "kurta_file"
    }
    for row in sheet.iter_rows():
        columns = {normalize(cell.value): cell.column for cell in row if cell.value is not None}
        if LATENCY not in columns or COST not in columns:
            continue
        if args.assets_csv:
            if "case_id" not in columns:
                raise ValueError("The evaluation grid needs a case_id column.")
            return row[0].row, columns, None, None
        photos = photo_names & columns.keys()
        garments = garment_names & columns.keys()
        if len(photos) != 1 or len(garments) != 1:
            raise ValueError("Set --photo-column and --garment-column to the workbook headers.")
        return row[0].row, columns, columns[photos.pop()], columns[garments.pop()]
    raise ValueError(f"Could not find a header row with {LATENCY} and {COST}.")


def local_image(directory, filename):
    root = directory.resolve()
    path = (root / str(filename)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("An image is missing or lies outside its input directory.")
    return path


def costs_by_row(path):
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8-sig") as source:
        costs = {}
        for record in csv.DictReader(source):
            row, cost = int(record["worksheet_row"]), float(record[COST])
            if row in costs or not math.isfinite(cost) or cost < 0:
                raise ValueError("Costs must be finite, nonnegative and unique per worksheet row.")
            costs[row] = cost
        return costs


def assets_by_case(path):
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"case_id", "photo", "garment"}
        if not required.issubset(reader.fieldnames or set()):
            raise ValueError("Use case_id, photo and garment columns in the asset CSV.")
        assets = {}
        for row in reader:
            case, photo, garment = ((row.get(key) or "").strip() for key in ("case_id", "photo", "garment"))
            if not all((case, photo, garment)) or case in assets:
                raise ValueError("Asset rows need a unique case ID and both filenames.")
            assets[case] = (photo, garment)
        if not assets:
            raise ValueError("Add at least one evaluation case to the asset CSV.")
        return assets


def evaluate(args):
    if args.workbook.resolve() == args.output.resolve() or args.output.exists():
        raise ValueError("Choose a new output workbook path.")
    costs = costs_by_row(args.measured_cost_csv)
    book = load_workbook(args.workbook)
    sheet = book["tryon_eval_grid"]
    header, columns, photo_column, garment_column = layout(sheet, args)
    if args.assets_csv:
        assets = assets_by_case(args.assets_csv)
        targets = {}
        for row in range(header + 1, sheet.max_row + 1):
            case = str(sheet.cell(row, columns["case_id"]).value or "")
            if case in assets and "example" not in case.lower():
                targets[row] = assets.pop(case)
        if assets:
            raise ValueError("Unknown or example case IDs in the asset CSV: " + ", ".join(assets))
    else:
        targets = {row: (sheet.cell(row, photo_column).value, sheet.cell(row, garment_column).value)
                   for row in range(header + 1, sheet.max_row + 1)}
        targets = {row: pair for row, pair in targets.items() if any(pair)}
    failures = 0
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=200, trust_env=False) as client:
        config = client.get("/api/tryon-config")
        config.raise_for_status()
        backend = config.json()["remote_backend"]
        for row, (photo, garment) in targets.items():
            for name in ({LATENCY, COST} | HUMAN) & columns.keys():
                sheet.cell(row, columns[name]).value = None
            session = str(uuid4())
            try:
                person = local_image(args.photos, photo)
                item = local_image(args.garments, garment)
                with person.open("rb") as person_file, item.open("rb") as garment_file:
                    started = time.perf_counter()
                    response = client.post("/api/tryon", data={
                        "session": session, "age": 18, "consent_photo": "true",
                        "consent_remote": backend or "",
                    }, files={"photo": person_file, "garment": garment_file})
                if not response.is_success:
                    raise ValueError(f"HTTP {response.status_code}: {response.json().get('error', 'request failed')}")
                payload = response.json()
                elapsed = time.perf_counter() - started
                if payload["mock"]:
                    raise ValueError(f"placeholder ({payload.get('placeholder_reason', 'unknown')})")
            except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
                failures += 1
                print(f"Row {row}: {exc}", file=sys.stderr)
                if "notes" in columns:
                    sheet.cell(row, columns["notes"]).value = f"Render failed: {exc}"
                if row in costs:
                    sheet.cell(row, columns[COST]).value = costs[row]
                try:
                    client.post("/api/delete", data={"session": session}).raise_for_status()
                except httpx.HTTPError:
                    print(f"Row {row}: server cleanup could not be confirmed.", file=sys.stderr)
                continue
            sheet.cell(row, columns[LATENCY]).value = round(elapsed, 3)
            sheet.cell(row, columns[COST]).value = costs.get(row, payload["cost_inr"])
            print(f"Row {row}: preview saved in server TMP_DIR/{session}/result.png")
    book.save(args.output)
    print(f"Saved workbook. {len(targets) - failures}/{len(targets)} selected cases rendered successfully.")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path("PS-13_fit_test_set.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("PS-13_fit_test_set_rendered.xlsx"))
    parser.add_argument("--photos", type=Path, default=Path("photos"))
    parser.add_argument("--garments", type=Path, default=Path("static/garments"))
    parser.add_argument("--photo-column")
    parser.add_argument("--garment-column")
    parser.add_argument("--assets-csv", type=Path, help="Private case_id,photo,garment mapping for the supplied grid.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--measured-cost-csv", type=Path)
    parser.add_argument("--confirm-consented-adults", action="store_true", required=True,
                        help="Confirm everyone in the workbook is 18+ and explicitly consented to this evaluation and its provider.")
    args = parser.parse_args()
    try:
        return evaluate(args)
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        parser.exit(2, f"Evaluation setup failed: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
