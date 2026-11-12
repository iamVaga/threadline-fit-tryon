"""Run a consented PS-13 workbook through the local FitCheck HTTP API."""
import argparse
import csv
import math
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from openpyxl import load_workbook

LATENCY = "render_latency_sec"
COST = "cost_per_render_inr"
HUMAN = {"realism", "garment_fidelity", "colour_fidelity", "color_fidelity", "body_preserved", "notes"}


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


def evaluate(args):
    if args.workbook.resolve() == args.output.resolve() or args.output.exists():
        raise ValueError("Choose a new output workbook path.")
    costs = costs_by_row(args.measured_cost_csv)
    book = load_workbook(args.workbook)
    sheet = book["tryon_eval_grid"]
    header, columns, photo_column, garment_column = layout(sheet, args)
    failures = 0
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=200, trust_env=False) as client:
        config = client.get("/api/tryon-config")
        config.raise_for_status()
        backend = config.json()["remote_backend"]
        team_code = os.getenv("TRYON_TEAM_CODE", "")
        if backend and not team_code:
            raise ValueError("Set TRYON_TEAM_CODE to the local server's team access code.")
        for row in range(header + 1, sheet.max_row + 1):
            photo = sheet.cell(row, photo_column).value
            garment = sheet.cell(row, garment_column).value
            if photo is None and garment is None:
                continue
            for name in ({LATENCY, COST} | HUMAN) & columns.keys():
                sheet.cell(row, columns[name]).value = None
            session = str(uuid4())
            try:
                person = local_image(args.photos, photo)
                item = local_image(args.garments, garment)
                with person.open("rb") as person_file, item.open("rb") as garment_file:
                    response = client.post("/api/tryon", data={
                        "session": session, "age": 18, "consent_photo": "true",
                        "consent_remote": backend or "", "team_code": team_code,
                    }, files={"photo": person_file, "garment": garment_file})
                if not response.is_success:
                    raise ValueError(f"request returned HTTP {response.status_code}")
                payload = response.json()
                if payload["mock"]:
                    raise ValueError("server returned a placeholder")
            except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
                failures += 1
                print(f"Row {row}: {exc}", file=sys.stderr)
                try:
                    client.post("/api/delete", data={"session": session}).raise_for_status()
                except httpx.HTTPError:
                    print(f"Row {row}: server cleanup could not be confirmed.", file=sys.stderr)
                continue
            sheet.cell(row, columns[LATENCY]).value = payload["latency_s"]
            sheet.cell(row, columns[COST]).value = costs.get(row, payload["cost_inr"])
            print(f"Row {row}: preview saved in server TMP_DIR/{session}/result.jpg")
    book.save(args.output)
    print(f"Saved workbook. {failures} cases could not be rendered.")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path("PS-13_fit_test_set.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("PS-13_fit_test_set_rendered.xlsx"))
    parser.add_argument("--photos", type=Path, default=Path("photos"))
    parser.add_argument("--garments", type=Path, default=Path("static/garments"))
    parser.add_argument("--photo-column")
    parser.add_argument("--garment-column")
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
