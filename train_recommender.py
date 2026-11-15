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
from recommender import certainty_band, index, measurements_to_size, normalize, predict, product_adjustment, tree_score

START = datetime(2026, 1, 1)
JULY = datetime(2026, 7, 1)
AUGUST = datetime(2026, 8, 1)
SEPTEMBER = datetime(2026, 9, 1)
CALIBRATION = datetime(2026, 9, 16)
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
        "signup_date": row["signup_date"],
        "usual_top": normalize(row["usual_size_top"]), "usual_jeans": normalize(row["usual_size_bottom"]),
        "height_cm": height,
        "dimensions": {key: row[column] for key, column in DIMS.items()
                       if row["measurement_consent"] and row[column] is not None},
    }


def sample(product, profile):
    return profile["usual_jeans" if product["category"].endswith("Jeans") else "usual_top"]


def available_at(row):
    return max(row["order_date"], row["return_requested_at"] or row["order_date"])


def audit_training_rows(rows, cutoff=END):
    if cutoff > END or any(not START <= row["order_date"] <= available_at(row) < cutoff for row in rows):
        raise ValueError("Training data or outcome date is outside the allowed pre-October window")
    return {
        "count": len(rows),
        "latest_order": max((row["order_date"].isoformat() for row in rows), default=None),
        "latest_outcome_proxy": max((available_at(row).isoformat() for row in rows), default=None),
        "exclusive_cutoff": cutoff.isoformat(),
    }


def profile_as_of(customers, customer_id, cutoff):
    profile = customers.get(customer_id)
    if profile and (profile.get("signup_date") is None or profile["signup_date"] < cutoff):
        return profile
    return {"usual_top": "", "usual_jeans": "", "dimensions": {}}


def fit(orders, products, customers, charts, *, cutoff=END, use_exchanges=True, direction_offsets=False):
    audit_training_rows(orders, cutoff)
    outcomes = []
    stats = defaultdict(Counter)
    exchange_shifts = defaultdict(list)
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
            outcome, weight = (exchanged if use_exchanges else bought), .7
            exchange_shifts[c,row["brand"]].append(exchanged - bought)
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
            profile = profile_as_of(customers, customer, cutoff)
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
    if direction_offsets:
        # Blend the signed exchange correction into the latent offset. Thirty neutral
        # pseudo-observations shrink volatile estimates and leave unseen brands alone.
        for (category, brand), shifts in exchange_shifts.items():
            offsets[category][brand] += .5 * sum(shifts) / (len(shifts) + 30)

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
            "return_direction_gap_pp":round(100*(small-large)/count,2),
            "exchange_n":len(exchange_shifts[category,brand]),
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


def signals(customer, p, customers, histories, same, cutoff=END):
    category = p["category"]
    profile = profile_as_of(customers, customer, cutoff)
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


def export_boost(estimator, rows, classification=False):
    initial = float(estimator.init_.constant_[0,0]) if not classification else math.log(
        estimator.init_.class_prior_[1] / estimator.init_.class_prior_[0]
    )
    trees = []
    for estimator_row in estimator.estimators_:
        tree = estimator_row[0].tree_
        trees.append([tree.children_left.tolist(), tree.children_right.tolist(), tree.feature.tolist(),
                      tree.threshold.tolist(), tree.value[:,0,0].tolist()])
    result = {"intercept":initial,"rate":estimator.learning_rate,"trees":trees}
    check = estimator.decision_function if classification else estimator.predict
    for features, expected in zip(rows[:20], check(rows[:20])):
        if abs(tree_score(result,features)-expected) > 1e-6:
            raise RuntimeError("Exported booster disagrees with the fitted estimator")
    return result


def experiment(orders, products, customers, charts):
    """Select improvements on early September, before opening the October workbook."""
    audit_training_rows(orders)
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor

    audits = {}

    def fit_until(date, use_exchanges=True, direction_offsets=False):
        subset = [r for r in orders if available_at(r) < date]
        name = f"{date.date()} exchanges={use_exchanges} directions={direction_offsets}"
        audits[name] = audit_training_rows(subset,date)
        return fit(subset,products,customers,charts,cutoff=date,use_exchanges=use_exchanges,
                   direction_offsets=direction_offsets)

    def temporal_rows(start, end):
        return [r for r in orders if start <= r["order_date"] < end]

    validation = [r for r in temporal_rows(SEPTEMBER,CALIBRATION) if kept(r)]
    audits["selection_validation"] = audit_training_rows(validation)
    decisions = []

    def validate(name, candidate, baseline=None, keep=True):
        state,history,same = candidate
        wins, exchange_wins = [], []
        for row in validation:
            product = products[row["sku_id"]]
            profile = signals(row["customer_id"],product,customers,history,same,SEPTEMBER)
            result = predict(state,product,profile)
            correct = result["size"] == kept(row)
            wins.append(correct)
            if row["returned"]:
                exchange_wins.append(correct)
        entry = {"variant":name,"validation_accepted_pct":round(100*mean(wins),3),
                 "validation_exchange_pct":round(100*mean(exchange_wins),3),
                 "n":len(wins),"n_exchange":len(exchange_wins)}
        entry["selected_at_step"] = keep if baseline is None else entry["validation_accepted_pct"] > baseline
        decisions.append(entry)
        return entry

    def attach(snapshot, boosts):
        return (snapshot[0] | boosts, snapshot[1], snapshot[2])

    first = fit_until(SEPTEMBER,use_exchanges=False)
    validate("first_choice_label_control",first,keep=False)
    existing = fit_until(SEPTEMBER)
    selection = validate("a_exchange_labels",existing)
    current = {}
    selected = "a_exchange_labels"
    direction = fit_until(SEPTEMBER,direction_offsets=True)
    entry = validate("b_exchange_direction",direction,selection["validation_accepted_pct"])
    use_direction = entry["selected_at_step"]
    chosen = existing
    if use_direction:
        chosen, selection, selected = direction, entry, entry["variant"]

    july_state,july_history,july_same = fit_until(JULY,direction_offsets=use_direction)
    ordinal_rows = [r for r in temporal_rows(JULY,AUGUST) if kept(r) and available_at(r) < AUGUST]
    audits["ordinal_training"] = audit_training_rows(ordinal_rows,AUGUST)
    x,y = [], []
    for row in ordinal_rows:
        product = products[row["sku_id"]]
        label = index(kept(row),product["category"])
        if label is None:
            continue
        profile = signals(row["customer_id"],product,customers,july_history,july_same,JULY)
        baseline = predict(july_state,product,profile,with_features=True)
        x.append(baseline["features"])
        y.append(label - baseline["target"])
    ordinal = GradientBoostingRegressor(n_estimators=64,max_depth=3,min_samples_leaf=40,
                                        learning_rate=.065,loss="huber",random_state=13)
    ordinal.fit(x,y)
    ordinal_boost = {"ordinal_boost":export_boost(ordinal,x)}
    entry = validate("c_ordinal_boost",attach(chosen,ordinal_boost),selection["validation_accepted_pct"])
    if entry["selected_at_step"]:
        current.update(ordinal_boost)
        selection,selected = entry,entry["variant"]

    august_state,august_history,august_same = attach(fit_until(AUGUST,direction_offsets=use_direction),current)
    acceptance_rows = [r for r in temporal_rows(AUGUST,SEPTEMBER) if available_at(r) < SEPTEMBER]
    audits["acceptance_training"] = audit_training_rows(acceptance_rows,SEPTEMBER)
    x,y = [], []
    for row in acceptance_rows:
        product = products[row["sku_id"]]
        category = product["category"]
        accepted = index(kept(row),category)
        rejected = index(row["size_bought"],category) if row["returned"] and row["return_reason"] in SIZE_REASONS else None
        labels = [(accepted,1),(rejected,0)] if accepted != rejected else [(accepted,1)]
        profile = signals(row["customer_id"],product,customers,august_history,august_same,AUGUST)
        result = predict(august_state,product,profile,with_features=True)
        for size,success in labels:
            if size is not None:
                x.append([*result["features"],size,size-result["target"]])
                y.append(success)
    acceptance = GradientBoostingClassifier(n_estimators=64,max_depth=3,min_samples_leaf=40,
                                            learning_rate=.065,random_state=13)
    acceptance.fit(x,y)
    acceptance_boost = {"acceptance_boost":export_boost(acceptance,x,classification=True)}
    entry = validate("d_expected_acceptance",attach(chosen,current|acceptance_boost),selection["validation_accepted_pct"])
    before_acceptance = current.copy()
    if entry["selected_at_step"]:
        current.update(acceptance_boost)
        selected = entry["variant"]

    calibration = [r for r in temporal_rows(CALIBRATION,END) if kept(r)]
    audits["calibration"] = audit_training_rows(calibration)

    def confidence(snapshot):
        state,history,same = snapshot
        bins = defaultdict(list)
        for row in calibration:
            p = products[row["sku_id"]]
            profile = signals(row["customer_id"],p,customers,history,same,CALIBRATION)
            result = predict(state,p,profile)
            band = certainty_band(profile["count"],bool(profile["usual"]) or profile["measured"] is not None)
            bins[band].append(result["size"] == kept(row))
        return {key:{"n":len(values),"accuracy":round(mean(values),4),
                     "outcome":"exact match to observed kept/exchanged size"} for key,values in bins.items()}

    a = fit_until(END)
    b = fit_until(END,direction_offsets=True)
    base = b if use_direction else a
    candidates = {
        "first_choice_label_control":fit_until(END,use_exchanges=False),
        "a_exchange_labels":a, "b_exchange_direction":b,
        "c_ordinal_boost":attach(base,ordinal_boost),
        "d_expected_acceptance":attach(base,before_acceptance|acceptance_boost),
    }
    incumbent_calibration = fit_until(CALIBRATION)
    selected_calibration = fit_until(CALIBRATION,direction_offsets=True) if use_direction else incumbent_calibration
    incumbent = attach(a,{"calibration":confidence(incumbent_calibration)})
    selected_model = attach(base,current | {"calibration":confidence(attach(selected_calibration,current))})
    return incumbent, selected_model, candidates, decisions, selected, audits


def score(rows):
    def ratio(values):
        known = [x for x in values if x is not None]
        return {"n":len(known),"pct":round(100*mean(known),3) if known else None}
    return {"n":len(rows), "exact":ratio([r["exact"] for r in rows]),
            "within":ratio([r["within"] for r in rows]), "usual":ratio([r["usual"] for r in rows]),
            "model_on_usual_subset":ratio([r["within"] for r in rows if r["usual"] is not None]),
            "first":ratio([r["first"] for r in rows])}


def error_breakdown(rows):
    def counts(group):
        histogram = Counter(r["distance_to_acceptable"] for r in group)
        misses = [abs(distance) for distance in histogram.elements() if distance]
        return {"n":len(group),"misses":len(misses),
                "undersized":sum(n for size,n in histogram.items() if size<0),
                "oversized":sum(n for size,n in histogram.items() if size>0),
                "mean_steps_when_wrong":round(mean(misses),3) if misses else None,
                "signed_size_steps":dict(sorted(histogram.items()))}

    result = {}
    for key,subset in {
        "kept_after_exchange":[r for r in rows if r["source"]=="Kept after exchange"],
        "no_history":[r for r in rows if r["history"]=="0"],
    }.items():
        result[key] = {"overall":counts(subset)}
        for field in ("brand","category"):
            groups = defaultdict(list)
            for row in subset:
                groups[row[field]].append(row)
            result[key]["by_"+field] = {name:counts(group) for name,group in groups.items()}
    return result


def audit_charts(brand_index):
    brands = brand_index["Women · Kurtas"]
    return {
        "flagged": sorted(brand for brand,row in brands.items() if row["chart_unreliable"]),
        "not_flagged": sorted(({"brand":brand,"gap":row["gap"],"chart_n":row["chart_n"],
                                "return_direction_gap_pp":row["return_direction_gap_pp"]}
                               for brand,row in brands.items() if not row["chart_unreliable"]),
                              key=lambda r:-abs(r["gap"] or 0)),
        "rule": {"min_consented_kept_orders":15,"absolute_gap_steps":.55,
                 "conflicting_gap_steps":.20,"conflicting_return_imbalance_pp":12},
    }


def business(sessions, scored, orders):
    clean = [r for r in sessions if not (r["time_on_pdp_sec"] < 2 and r["images_viewed"] == 0)]
    kurta = [r for r in clean if r["category"] == "Women · Kurtas"]
    baseline, lift = .039, .10
    p1 = baseline * (1+lift)
    pbar = (baseline+p1)/2
    z = NormalDist().inv_cdf
    n = math.ceil((z(.975)*math.sqrt(2*pbar*(1-pbar)) +
                   z(.80)*math.sqrt(baseline*(1-baseline)+p1*(1-p1)))**2/(p1-baseline)**2)
    company_monthly = 25 * len(kurta)
    traffic = company_monthly * 7 / 30
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
    return {"ab":{"baseline_assumed":baseline,"relative_lift":lift,"per_arm":n,"weeks":max(4,math.ceil(2*n/traffic)),
                  "company_monthly_kurta_sessions":company_monthly,"traffic_scale":25,"minimum_weeks":4,
                  "kurta_sessions":len(kurta),"kurta_guest_sessions":guest,"kurta_purchases":sum(bool(r["purchased"]) for r in kurta),
                  "bots_removed":len(sessions)-len(clean),
                  "duration_basis":"Assumes company kurta traffic is 25× the bot-filtered September sample. Uses both arms and all eligible sessions, before clustering inflation."},
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
    catalogue, orderbook = book("catalogue"), book("orders_and_returns")
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
    raw_orders = sheet(orderbook,"orders")
    volume_orders = [r for r in raw_orders if START <= r["order_date"] < END]
    orders = [r for r in volume_orders if available_at(r) < END]
    incumbent, challenger, candidates, comparisons, selected, audits = experiment(orders,products,customers,charts)
    # The October cases and answers are opened only after selection and calibration.
    testbook = book("fit_test_set")
    cases=sheet(testbook,"test_cases")
    answers={r["test_id"]:r for r in sheet(testbook,"answers")}
    if any(r["order_date"] < END for r in cases):
        raise ValueError("Evaluation workbook contains a pre-October test case")
    predictions, scored = evaluate(cases, answers, products, customers, incumbent)
    _, challenger_rows = evaluate(cases, answers, products, customers, challenger)
    exchange = "Kept after exchange"
    incumbent_acc = score(scored)["within"]["pct"]
    incumbent_exchange = score([r for r in scored if r["source"] == exchange])["within"]["pct"]
    challenger_acc = score(challenger_rows)["within"]["pct"]
    challenger_exchange = score([r for r in challenger_rows if r["source"] == exchange])["within"]["pct"]
    # October reports a regression check. The training command always emits the
    # incumbent artifact and never changes the serving variant from holdout scores.
    passes_holdout = challenger_acc > incumbent_acc and challenger_exchange >= incumbent_exchange
    state,hist,same = incumbent
    for entry in comparisons:
        _, variant_rows = evaluate(cases, answers, products, customers, candidates[entry["variant"]])
        entry["october"] = score(variant_rows)
        entry["october_exchange"] = score([r for r in variant_rows if r["source"]=="Kept after exchange"])
    scenarios={}
    for scenario,case in SCENARIOS.items():
        result = predictions[case]
        acceptable = [normalize(x) for x in answers[case]["acceptable_sizes"].split(",")]
        scenarios[scenario] = {k:v for k,v in result.items() if k != "customer_id"} | {
            "test_id":case,"acceptable":acceptable,"within":result["size"] in acceptable,
        }
    report={"overall":score(scored),"training_rows":len(orders),"test_rows":len(cases),
            "normalized_spellings":sum(normalize(r["size_bought"])!=str(r["size_bought"]) for r in orders),
            "text_heights_parsed":sum(isinstance(r["self_reported_height_cm"],str) and customers[r["customer_id"]]["height_cm"] is not None for r in raw),
            "features_excluded":["height","weight"],"reliability":state["calibration"],"scenarios":scenarios,
            "brands":state["brand_index"],"business":business(sheet(orderbook,"pdp_sessions"),scored,volume_orders)}
    report["experiments"] = {"selected":selected,"served":"a_exchange_labels",
        "challenger_passed_holdout_check":passes_holdout,"comparison":comparisons,
        "release_rule":"Report whether the frozen challenger improved October overall without regressing on exchange cases. The generator emits the incumbent independently of the October scores.",
        "challenger_holdout":{"within":challenger_acc,"exchange_within":challenger_exchange},
        "incumbent_holdout":{"within":incumbent_acc,"exchange_within":incumbent_exchange},
        "validation_target":"Observed acceptable size (one known kept or exchanged-to size per historical row)",
        "selection_period":"2026-09-01 through 2026-09-15",
        "confidence_period":"2026-09-16 through 2026-09-30",
        "october_used_for_scoring_and_release_regression_only":True}
    report["audit"] = {"stages":audits,"outcome_proxy":"return_requested_at; exchange completion date is unavailable",
        "october_order_rows_excluded":sum(r["order_date"] >= END for r in raw_orders),
        "late_outcomes_excluded":len(volume_orders)-len(orders),
        "profiles_with_october_or_later_signup_excluded":sum(p["signup_date"] is not None and p["signup_date"]>=END for p in customers.values()),
        "profile_limit":"Profiles are the Jan–Sep workbook snapshot; measurement and usual-size capture dates are not supplied.",
        "october_rows_used_for_training":0}
    report["error_analysis"] = error_breakdown(scored)
    report["chart_audit"] = audit_charts(state["brand_index"])
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
    for handle in (catalogue,orderbook,testbook):
        handle.close()


def evaluate(cases, answers, products, customers, snapshot):
    state,hist,same = snapshot
    predictions, scored = {}, []
    for r in cases:
        case,customer,sku = r["test_id"],r["customer_id"],r["sku_id"]
        product = products[sku]
        category = product["category"]
        profile = signals(customer,product,customers,hist,same)
        prediction = predict(state,product,profile)
        predictions[case] = prediction | {"customer_id":customer}
        answer = answers[case]
        acceptable = {normalize(x) for x in answer["acceptable_sizes"].split(",")}
        predicted = index(prediction["size"],category)
        distance = min((predicted - index(size,category) for size in acceptable),key=lambda n:(abs(n),n))
        usual, count = profile["usual"], profile["count"]
        scored.append({"test_id":case,"brand":product["brand"],"category":category,"source":answer["label_source"],
            "history":"0" if count==0 else "1" if count==1 else "2–3" if count<4 else "4+",
            "exact":prediction["size"]==normalize(answer["correct_size"]),"within":distance==0,
            "usual":usual in acceptable if usual else None,"first":normalize(answer["size_customer_first_chose"]) in acceptable,
            "distance_to_acceptable":distance})
    return predictions, scored


if __name__=="__main__":
    main()
