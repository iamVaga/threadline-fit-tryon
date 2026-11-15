"""Render or score the PS-13 try-on grid. Photos and outputs stay in the private work directory."""
import argparse
import base64
import csv
import json
import math
import os
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

import httpx
from openpyxl import load_workbook

METRICS = ("realism_1to5", "garment_fidelity_1to5", "colour_fidelity_1to5",
           "body_preserved_yes_no", "simulation_label_shown_yes_no")
FIELDS = ("case_id", "status", "render_latency_sec", "cost_per_render_inr", *METRICS, "notes")


def grid(path):
    book = load_workbook(path)
    sheet = book["tryon_eval_grid"]
    keys = [cell.value for cell in sheet[1]]
    rows = [(i, dict(zip(keys, values))) for i,values in enumerate(sheet.values, 1)
            if i > 1 and values[0] and "example" not in str(values[0]).lower()]
    return book, sheet, keys, rows


def asset(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing file or path outside evaluation directory")
    return path


def load_log(log):
    if not log.exists():
        return {}
    with log.open(newline="", encoding="utf-8") as stream:
        return {r["case_id"]:r for r in csv.DictReader(stream)}


def save_log(log, records):
    temp = log.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records.values())
    temp.replace(log)


def render(args):
    _,_,_,cases = grid(args.workbook)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    root = args.manifest.parent
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = args.directory / "grid.csv"
    results = load_log(log)
    with httpx.Client(base_url=args.server, timeout=170, trust_env=False) as client:
        config = client.get("/api/tryon-config")
        config.raise_for_status()
        backend = config.json()["remote_backend"]
        if backend not in {"local_http","hf"}:
            raise SystemExit("Configure local_http or HF before rendering the grid.")
        for _,case in cases:
            case_id = case["case_id"]
            if not str(case_id).startswith("TO-") or not str(case_id)[3:].isdigit():
                raise ValueError("Invalid grid case ID")
            output = args.directory / f"{case_id}.png"
            if results.get(case_id,{}).get("status") == "rendered" and output.exists():
                continue
            entry = manifest.get(case_id, {})
            row = dict.fromkeys(FIELDS, "")
            row["case_id"] = case_id
            row["status"] = "not run"
            if entry.get("authorized_adult_photo") is not True or entry.get("sku_id") != case["sku_id"]:
                row["notes"] = "Missing consented adult photo or verified image for this exact SKU."
            else:
                session = str(uuid4())
                try:
                    person,garment = (asset(root,entry[key]) for key in ("person","garment"))
                    with person.open("rb") as photo, garment.open("rb") as item:
                        start = perf_counter()
                        response = client.post("/api/tryon", files={"photo":photo,"garment":item}, data={
                            "session":session,"age":"18","consent_photo":"true","consent_remote":backend,
                            "team_code":os.environ.get("TRYON_TEAM_CODE",""),
                            "desc":str(case["garment_pattern_or_print"]) + " " + str(case["garment_colour"]),
                            "garment_category":model_category(case["category"]),
                        })
                    row["render_latency_sec"] = round(perf_counter()-start,3)
                    result = response.json()
                    if not response.is_success or result.get("mock"):
                        raise ValueError(f"No real preview, HTTP {response.status_code}")
                    output.write_bytes(base64.b64decode(result["image"].split(",",1)[1], validate=True))
                    row["status"] = "rendered"
                    if result.get("cost_inr") is not None:
                        row["cost_per_render_inr"] = result["cost_inr"]
                except (OSError, KeyError, ValueError, httpx.HTTPError) as exc:
                    row["status"] = "failed"
                    row["notes"] = f"Render failed ({type(exc).__name__})."
                finally:
                    try:
                        client.post("/api/delete",data={"session":session}).raise_for_status()
                    except httpx.HTTPError:
                        row["notes"] += " Server deletion could not be confirmed."
            results[case_id] = row
            save_log(log,results)
    print({status:sum(r["status"]==status for r in results.values())
           for status in ("rendered","failed","not run")})


def model_category(category):
    if category.endswith("Jeans"):
        return "bottoms"
    if category.endswith(("Dresses","Kurtas")):
        return "one-pieces"
    return "tops"


def evaluation(args):
    book,sheet,keys,cases = grid(args.workbook)
    log = load_log(args.directory / "grid.csv")
    scored = []
    for i,case in cases:
        record = log.get(case["case_id"],{})
        status = record.get("status","not run")
        timing = ["render_latency_sec","cost_per_render_inr"]
        for field in timing:
            value = record.get(field)
            numeric = float(value) if value else None
            if numeric is not None and (not math.isfinite(numeric) or numeric < 0):
                raise ValueError(f"Invalid latency/cost for {case['case_id']}")
            sheet.cell(i,keys.index(field)+1,numeric)
        for field in METRICS:
            value = record.get(field, "").strip()
            if not value or status != "rendered":
                continue
            if field.endswith("_1to5"):
                value = int(value)
                if not 1 <= value <= 5:
                    raise ValueError(f"Score outside 1–5 for {case['case_id']}")
            elif value.lower() not in {"yes","no"}:
                raise ValueError(f"Expected Yes or No for {case['case_id']}")
            else:
                value = value.capitalize()
            case[field] = value
            sheet.cell(i,keys.index(field)+1,value)
        sheet.cell(i,keys.index("notes")+1,f"{status}. {record.get('notes','')}".strip())
        if status == "rendered" and all(isinstance(case.get(k), int) for k in METRICS[:3]) and all(
            case.get(k) in {"Yes","No"} for k in METRICS[3:]
        ):
            scored.append(case)
    overall = {key:mean(r[key] for r in scored) for key in METRICS[:3]} if scored else {}
    breakdown = {}
    for dimension in ("body_type","skin_tone_band"):
        groups = {}
        for label in sorted({case[dimension] for _,case in cases}):
            subset = [r for r in scored if r[dimension]==label]
            averages = {key:mean(r[key] for r in subset) for key in METRICS[:3]} if subset else {}
            groups[label] = {"n":len(subset),"means":averages,
                "more_than_half_below": [key for key,v in averages.items() if overall[key]-v > .5],
                "body_preservation_failures": sum(r["body_preserved_yes_no"]=="No" for r in subset),
                "status": "measured" if subset else "not run"}
        breakdown[dimension] = groups
    acceptable = sum(all(r[key]>=3 for key in METRICS[:3])
                     and all(r[k]=="Yes" for k in METRICS[3:]) for r in scored)
    report = {"grid_cases":len(cases),"scored":len(scored),"acceptable":acceptable,
              "rendered":sum(log.get(r["case_id"],{}).get("status")=="rendered" for _,r in cases),
              "failed":sum(log.get(r["case_id"],{}).get("status")=="failed" for _,r in cases),
              "overall":overall,"by_group":breakdown,
              "worst_cases":[r["case_id"] for r in sorted(scored,key=lambda r:sum(r[k] for k in METRICS[:3]))[:5]]}
    output = args.directory / "scored.xlsx"
    if output.resolve()==args.workbook.resolve():
        raise ValueError("Choose an output directory separate from the source workbook")
    args.directory.mkdir(parents=True,exist_ok=True)
    book.save(output)
    (args.directory / "fairness.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["render","report"])
    parser.add_argument("--workbook",type=Path,default=Path("data/PS-13_fit_test_set.xlsx"))
    parser.add_argument("--directory",type=Path,default=Path("renders"))
    parser.add_argument("--manifest",type=Path,default=Path("photos/grid.json"))
    parser.add_argument("--server",default="http://127.0.0.1:8000")
    args=parser.parse_args()
    if args.action=="render":
        render(args)
    else:
        evaluation(args)


if __name__=="__main__":
    main()
