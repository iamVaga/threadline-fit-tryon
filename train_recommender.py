"""Rebuild FitCheck's model and evidence using only January–September training rows."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import re
from statistics import NormalDist, mean, median, quantiles

from openpyxl import load_workbook
from recommender import certainty_band, index, labels, measurements_to_size, normalize, predict, product_adjustment

START = datetime(2026, 1, 1)
CALIBRATION = datetime(2026, 8, 1)
END = datetime(2026, 10, 1)
SIZE_REASONS = {"Too small", "Too large", "Didn't like the fit"}
DIMS = {"bust": "measured_bust_or_chest_cm", "waist": "measured_waist_cm", "hip": "measured_hip_cm"}
SCENARIOS = dict(zip(("T1","T2","T3","T4","T5"), ("FT-10022","FT-10001","FT-10025","FT-10024","FT-10026")))


def sheet(book, name):
    data = book[name].values
    header = next(data)
    return [dict(zip(header, row)) for row in data if row[0] is not None]


def clean_profile(row):
    height = row["self_reported_height_cm"]
    if isinstance(height, str):
        parts = re.findall(r"\d+", height)
        height = (int(parts[0]) * 12 + int(parts[1])) * 2.54 if len(parts) == 2 else None
    return {
        "usual_top": normalize(row["usual_size_top"]), "usual_jeans": normalize(row["usual_size_bottom"]),
        "height_cm": height,
        "dimensions": {key: row[column] for key, column in DIMS.items()
                       if row["measurement_consent"] and row[column] is not None},
    }


def sample(product, profile):
    return profile["usual_jeans" if product["category"].endswith("Jeans") else "usual_top"]


def fit(orders, products, customers, charts):
    outcomes = []
    stats = defaultdict(Counter)
    for row in orders:
        p = products[row["sku_id"]]
        c = row["category"]
        bought = index(row["size_bought"], c)
        exchanged = index(row["exchanged_to_size"], c)
        reason = row["return_reason"]
        fit_stats = stats[c, row["brand"]]
        fit_stats["lines"] += 1
        if row["returned"] and reason in SIZE_REASONS:
            fit_stats["size_returns"] += 1
            fit_stats[reason] += 1
        if bought is None:
            continue
        if exchanged is not None:
            outcome, weight = exchanged, .7
        elif not row["returned"]:
            outcome, weight = bought, 1.
        elif reason in {"Too small", "Too large"}:
            outcome, weight = bought + (1 if reason == "Too small" else -1), .18
        else:
            continue
        outcomes.append((row, p, outcome - product_adjustment(p), weight))

    offsets = defaultdict(dict)
    category_sizes = defaultdict(list)
    histories = defaultdict(list)
    for row,p,value,weight in outcomes:
        histories[row["customer_id"], row["category"]].append((row,p,value,weight))
        if weight >= .7:
            category_sizes[row["category"]].append(value)
    priors = {key:median(values) for key,values in category_sizes.items()}
    sizes = {}
    for _ in range(14):
        for (customer, cat), observations in histories.items():
            profile = customers[customer]
            product = observations[0][1]
            known = index(sample(product,profile),cat)
            measured = measurements_to_size(product,profile["dimensions"])
            values = [(size - offsets[cat].get(row["brand"],0), weight) for row,_,size,weight in observations]
            if known is not None:
                values.append((known, .7))
            if measured is not None:
                values.append((measured, 1.3))
            sizes[customer,cat] = sum(value * weight for value,weight in values) / sum(w for _,w in values)
        residual = defaultdict(lambda: [0.,0.])
        for row,_,size,weight in outcomes:
            bucket = residual[row["category"], row["brand"]]
            bucket[0] += (size - sizes[row["customer_id"],row["category"]]) * weight
            bucket[1] += weight
        for (category, brand),(total,weight) in residual.items():
            offsets[category][brand] = total / (weight + 6)

    gaps = defaultdict(list)
    for row,product,_,weight in outcomes:
        if weight != 1:
            continue
        dimensions = customers[row["customer_id"]]["dimensions"]
        dim = "waist" if row["category"].endswith("Jeans") else "bust"
        if dim not in dimensions:
            continue
        options = charts.get((row["category"], row["brand"], dim), [])
        if not options:
            continue
        chart_prediction = min(options, key=lambda item: abs(item[1] - dimensions[dim]))[0]
        gaps[row["category"], row["brand"]].append(index(row["size_bought"], row["category"]) - chart_prediction)

    brand_index = defaultdict(dict)
    for (category,brand),values in stats.items():
        count = values["lines"]
        small,large = values["Too small"],values["Too large"]
        gap_values = gaps[category,brand]
        gap = mean(gap_values) if gap_values else None
        learned_offset = offsets[category].get(brand,0)
        direction = "Runs small" if learned_offset >= .15 else "Runs large" if learned_offset <= -.15 else "Near true"
        contradictory = gap is not None and abs(gap) >= .2 and abs(small-large)/count >= .12 and gap * (small-large) < 0
        unreliable = bool(len(gap_values) >= 15 and (abs(gap) >= .55 or contradictory))
        brand_index[category][brand] = {
            "brand":brand,"lines":count,"ret": round(100*values["size_returns"]/count,1),
            "ts":round(100*small/count,1),"tl":round(100*large/count,1),"direction":direction,
            "gap":round(gap,2) if gap is not None else None, "chart_n":len(gap_values),
            "chart_unreliable":unreliable,"b":round(learned_offset,4),
            "msg":f"{brand} recorded {values['size_returns']:,} size-related returns across {count:,} {category.lower()} order lines, including {small:,} too-small and {large:,} too-large returns."
            + (f" Across {len(gap_values)} consented kept orders, shoppers kept a size {abs(gap):.2f} steps {'above' if gap > 0 else 'below'} the published chart prediction on average. Please review the chart." if unreliable else ""),
        }
    by_customer = defaultdict(list)
    same = defaultdict(lambda:defaultdict(list))
    rigid_latent = defaultdict(list)
    for row,p,value,weight in outcomes:
        c,b,customer = row["category"],row["brand"],row["customer_id"]
        reduced = value - offsets[c].get(b,0)
        by_customer[customer].append((c,reduced,weight))
        if weight >= .7:
            same[customer,c][b].append(reduced)
            if c.endswith("Jeans") and p["stretch_pct"] == 0:
                rigid_latent[c].append(reduced)
    # With no customer anchor, the 65th percentile encodes a higher cost for rigid denim fitting too small.
    rigid_priors = {cat:quantiles(values,n=20)[12] for cat,values in rigid_latent.items() if len(values)>20}
    state = {"offsets":offsets,"priors":priors,"rigid_priors":rigid_priors,"brand_index":brand_index}
    return state,by_customer,same


def signals(customer, p, customers, histories, same):
    category = p["category"]
    profile = customers.get(customer, {"usual_top":"","usual_jeans":"","dimensions":{}})
    observations = histories.get(customer, ())
    cross = category.endswith("Jeans")
    earned = [(round(value,4),weight if c==category else round(weight*.32,4))
              for c,value,weight in observations if c.endswith("Jeans")==cross]
    count = sum(weight >= .7 and c.endswith("Jeans")==cross for c,_,weight in observations)
    measured = measurements_to_size(p,profile["dimensions"])
    return {"usual":sample(p,profile),"measured":None if measured is None else round(measured,3),
            "count":count,"history":earned,"brands":same.get((customer,category), {})}


def kept(row):
    return normalize(row["exchanged_to_size"] if row["returned"] else row["size_bought"])


def score(rows):
    def ratio(values):
        known = [x for x in values if x is not None]
        return {"n":len(known),"pct":round(100*mean(known),3) if known else None}
    return {"n":len(rows), "exact":ratio([r["exact"] for r in rows]),
            "within":ratio([r["within"] for r in rows]), "usual":ratio([r["usual"] for r in rows]),
            "model_on_usual_subset":ratio([r["within"] for r in rows if r["usual"] is not None]),
            "first":ratio([r["first"] for r in rows])}


def business(sessions, scored, orders):
    clean = [r for r in sessions if not (r["time_on_pdp_sec"] < 2 and r["images_viewed"] == 0)]
    kurta = [r for r in clean if r["category"] == "Women · Kurtas"]
    baseline, lift = .039, .10
    p1 = baseline * (1+lift)
    pbar = (baseline+p1)/2
    z = NormalDist().inv_cdf
    n = math.ceil((z(.975)*math.sqrt(2*pbar*(1-pbar)) +
                   z(.80)*math.sqrt(baseline*(1-baseline)+p1*(1-p1)))**2/(p1-baseline)**2)
    traffic = len(kurta) * 7 / 30
    relevant = [r for r in scored if r["category"] == "Women · Kurtas" and r["usual"] is not None]
    usual_errors = sum(not r["usual"] for r in relevant)
    model_errors = sum(not r["within"] for r in relevant)
    difference = (usual_errors - model_errors)/usual_errors if usual_errors else 0
    returns = sum(r["category"] == "Women · Kurtas" and r["returned"] and r["return_reason"] in SIZE_REASONS for r in orders)
    scenarios=[]
    for adoption in (.30,.50,.80):
        avoided=round(returns*25*difference*adoption)
        scenarios.append({"adoption":adoption,"avoided":avoided,"refunds":round(.6*avoided),"logistics_inr":avoided*117})
    guest = sum(not r["customer_id"] for r in kurta)
    return {"ab":{"baseline_assumed":baseline,"relative_lift":lift,"per_arm":n,"weeks":math.ceil(2*n/traffic),
                  "kurta_sessions":len(kurta),"kurta_guest_sessions":guest,"kurta_purchases":sum(bool(r["purchased"]) for r in kurta),
                  "bots_removed":len(sessions)-len(clean),"duration_basis":"September sample, both arms, all eligible sessions, no clustering inflation"},
            "returns":{"heldout_pairs":len(relevant),"usual_wrong":usual_errors,"model_wrong":model_errors,
                       "relative_error_change":difference,"training_period_kurta_size_returns":returns,
                       "scale":25,"scenarios":scenarios,"status":"projection"}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,default=Path("data"))
    parser.add_argument("--out",type=Path,default=Path("results"))
    args=parser.parse_args()
    def book(stem):
        return load_workbook(args.data / f"PS-13_{stem}.xlsx",read_only=True,data_only=True)
    with_books = [book("catalogue"),book("orders_and_returns"),book("fit_test_set")]
    catalogue, orderbook, testbook = with_books
    products = {r["sku_id"]:{key:r[key] for key in ("sku_id","brand","category","product_name","colour","fit_type","fabric","stretch_pct","sizes_available","mrp_inr")}
                for r in sheet(catalogue,"products")}
    for p in products.values():
        p["sizes_available"] = [normalize(v) for v in p["sizes_available"].split(",")]
    charts = defaultdict(list)
    for r in sheet(catalogue,"size_charts"):
        for dim in ("bust","hip","waist"):
            lo,hi=r[f"body_{dim}_cm_from"],r[f"body_{dim}_cm_to"]
            i=index(r["size_label"],r["category"])
            if lo is not None and hi is not None and i is not None:
                charts[r["category"],r["brand"],dim].append((i,(lo+hi)/2))
    raw = sheet(orderbook,"customers")
    customers = {r["customer_id"]:clean_profile(r) for r in raw}
    volume_orders = [r for r in sheet(orderbook,"orders") if START <= r["order_date"] < END]
    orders = [r for r in volume_orders if r["return_requested_at"] is None or r["return_requested_at"] < END]
    earlier = [r for r in orders if r["order_date"] < CALIBRATION
               and (r["return_requested_at"] is None or r["return_requested_at"] < CALIBRATION)]
    warm,hist,same = fit(earlier,products,customers,charts)
    bins = defaultdict(list)
    for r in orders:
        if r["order_date"] < CALIBRATION or not kept(r):
            continue
        p=products[r["sku_id"]]
        profile=signals(r["customer_id"],p,customers,hist,same)
        result=predict(warm,p,profile)
        band=certainty_band(profile["count"],bool(profile["usual"]) or profile["measured"] is not None)
        bins[band].append(result["size"]==kept(r))
    state,hist,same=fit(orders,products,customers,charts)
    state["calibration"]={key:{"n":len(values),"accuracy":round(mean(values),4),
                               "outcome":"exact match to observed kept/exchanged size"} for key,values in bins.items()}
    cases=sheet(testbook,"test_cases")
    answers={r["test_id"]:r for r in sheet(testbook,"answers")}
    predictions = {}
    scored=[]
    scenarios={}
    for r in cases:
        case,customer,sku=r["test_id"],r["customer_id"],r["sku_id"]
        p=products[sku]
        profile=signals(customer,p,customers,hist,same)
        prediction=predict(state,p,profile)
        predictions[case]={**prediction,"customer_id":customer}
        answer=answers[case]
        acceptable={normalize(x) for x in answer["acceptable_sizes"].split(",")}
        usual=profile["usual"]
        history_count=profile["count"]
        score_row={"test_id":case,"category":p["category"],"source":answer["label_source"],
            "history":"0" if history_count==0 else "1" if history_count==1 else "2–3" if history_count<4 else "4+",
            "exact":prediction["size"]==normalize(answer["correct_size"]),"within":prediction["size"] in acceptable,
            "usual":usual in acceptable if usual else None,"first":normalize(answer["size_customer_first_chose"]) in acceptable}
        scored.append(score_row)
        for key,target in SCENARIOS.items():
            if case==target:
                scenarios[key]={**prediction,"test_id":case,"acceptable":sorted(acceptable),"within":score_row["within"]}
    report={"overall":score(scored),"training_rows":len(orders),"test_rows":len(cases),
            "normalized_spellings":sum(normalize(r["size_bought"])!=str(r["size_bought"]) for r in orders),
            "text_heights_parsed":sum(isinstance(r["self_reported_height_cm"],str) and customers[r["customer_id"]]["height_cm"] is not None for r in raw),
            "features_excluded":["height","weight"],"reliability":state["calibration"],"scenarios":scenarios,
            "brands":state["brand_index"],"business":business(sheet(orderbook,"pdp_sessions"),scored,volume_orders)}
    for field in ("source","category","history"):
        report["by_"+field]={k:score([r for r in scored if r[field]==k]) for k in sorted({r[field] for r in scored})}
    report["worst_categories"]=sorted(report["by_category"],key=lambda key:report["by_category"][key]["within"]["pct"])[:3]
    report["failure_examples"] = []
    for row in scored:
        if row["within"] or len(report["failure_examples"]) == 5 or row["source"] != "Kept after exchange":
            continue
        case = row["test_id"]
        result = predictions[case]
        report["failure_examples"].append({"test_id":case,"category":row["category"],"history":row["history"],
            "prediction":result["size"],"acceptable_sizes":answers[case]["acceptable_sizes"],
            "reason":result["reason"].replace("Your history","Shopper history")})
    args.out.mkdir(exist_ok=True,parents=True)
    state["products"]=products
    state["predictions"]=predictions
    examples = {p["category"]:p for p in products.values()}
    # Compact synthetic fit signals only. Keep raw measurements, rows and labels local.
    state["profiles"]={}
    for customer in customers:
        signal_by_category = {}
        for cat,p in examples.items():
            info = signals(customer,p,customers,hist,same)
            items = info["history"]
            if items:
                weight = sum(w for _,w in items)
                info["history"] = [[round(sum(x*w for x,w in items)/weight,5),round(weight,5)]]
            if any(info.values()) or info["measured"] is not None:
                signal_by_category[cat] = info
        if signal_by_category:
            state["profiles"][customer]=signal_by_category
    for name,value in (("model",state),("metrics",report)):
        (args.out/f"{name}.json").write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
    print(json.dumps({"overall":report["overall"],"scenarios":scenarios,"business":report["business"]},ensure_ascii=False,indent=2))
    for handle in with_books:
        handle.close()


if __name__=="__main__":
    main()
