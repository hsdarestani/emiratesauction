from decimal import Decimal


def _d(value):
    return Decimal(str(value or 0))


def _clamp(value, low=0, high=100):
    return max(low, min(high, value))


def _clean_text(condition, tags, damage_description):
    text = " ".join(
        [condition or "", damage_description or ""] + [str(tag) for tag in (tags or [])]
    ).lower()
    # Avoid obvious false positives from reassuring inspection language.
    for phrase in (
        "no chassis damage", "no frame damage", "no structural damage",
        "no flood damage", "no water damage", "no fire damage",
        "airbags intact", "airbag intact", "engine working", "engine runs",
        "gearbox working", "transmission working", "no accident",
    ):
        text = text.replace(phrase, " ")
    return " ".join(text.split())


DAMAGE_RULES = (
    ("Rahmen-/Chassisschaden", 88, "critical", ("chassis damage", "frame damage", "structural damage", "structural issue", "unibody damage", "pillar damage")),
    ("Brand-/Feuerschaden", 96, "critical", ("fire damage", "burnt", "burned", "burn damage")),
    ("Wasser-/Flutschaden", 94, "critical", ("flood damage", "flooded", "water damage", "submerged")),
    ("Überschlag", 92, "critical", ("rollover", "rolled over", "roof crushed")),
    ("HV-Batterieschaden", 88, "critical", ("high voltage battery damage", "hv battery damage", "traction battery damage")),
    ("Motorschaden", 76, "major", ("engine damage", "engine fault", "engine issue", "engine problem", "engine seized", "engine not working")),
    ("Getriebeschaden", 72, "major", ("gearbox damage", "gearbox fault", "gearbox issue", "transmission damage", "transmission fault", "transmission issue")),
    ("Airbag-Schaden", 64, "major", ("airbag deployed", "airbags deployed", "airbag missing", "airbags missing")),
    ("Nicht fahrbereit", 60, "major", ("not running", "non runner", "non-running", "does not start", "not starting", "won't start", "will not start")),
    ("Elektrik-/Steuergeräteschaden", 55, "major", ("electrical fault", "electrical issue", "wiring damage", "ecu damage", "control unit damage")),
    ("Achs-/Fahrwerksschaden", 50, "major", ("suspension damage", "axle damage", "control arm damage", "steering damage")),
    ("Kühlungsschaden", 42, "major", ("radiator damage", "cooling system damage", "coolant leak")),
    ("Unterbodenschaden", 44, "major", ("undercarriage damage", "underbody damage")),
    ("Starker Unfallbereich", 38, "major", ("front damage", "rear damage", "side damage", "accident damage", "collision damage")),
    ("Karosserie-/Anbauteilschaden", 18, "cosmetic", ("bumper damage", "fender damage", "door damage", "hood damage", "bonnet damage", "trunk damage", "boot damage")),
    ("Kosmetischer Schaden", 12, "cosmetic", ("scratch", "scratches", "dent", "dents", "paint damage", "paintwork", "scuff")),
    ("Glas-/Leuchtenschaden", 16, "cosmetic", ("windshield damage", "windscreen damage", "glass damage", "headlight damage", "tail light damage", "mirror damage")),
)

FATAL_LABELS = {
    "Rahmen-/Chassisschaden", "Brand-/Feuerschaden", "Wasser-/Flutschaden",
    "Überschlag", "HV-Batterieschaden",
}

EXOTIC_MAKES = {"ferrari", "lamborghini", "mclaren", "bentley", "rolls royce", "rolls-royce", "aston martin", "maserati"}


def classify_damage(condition=None, tags=None, damage_description=None):
    text = _clean_text(condition, tags, damage_description)
    if not text:
        return {
            "damage_level": "unbekannt",
            "damage_risk_score": 50,
            "repairability_score": 50,
            "damage_red_flags": [],
            "damage_findings": [],
            "damage_data_available": False,
        }

    findings = []
    levels = []
    scores = []
    for label, score, level, patterns in DAMAGE_RULES:
        if any(pattern in text for pattern in patterns):
            findings.append(label)
            levels.append(level)
            scores.append(score)

    # Condition tags often contain useful but terse warnings that are not in our
    # phrase catalogue. Count them gently instead of treating every tag as severe.
    unmatched_tag_penalty = min(12, max(0, len(tags or []) - len(findings)) * 2)
    if scores:
        risk = max(scores) + min(18, max(0, len(scores) - 1) * 5) + unmatched_tag_penalty
    else:
        risk = 22 + unmatched_tag_penalty
    risk = int(_clamp(risk))

    if risk >= 85:
        level = "kritisch"
    elif risk >= 65:
        level = "hoch"
    elif risk >= 35:
        level = "mittel"
    else:
        level = "niedrig"

    red_flags = [label for label in findings if label in FATAL_LABELS]
    return {
        "damage_level": level,
        "damage_risk_score": risk,
        "repairability_score": 100 - risk,
        "damage_red_flags": red_flags,
        "damage_findings": findings,
        "damage_data_available": True,
    }


def estimate_repair_cost(market_value_aed, purchase_price_aed, damage, make=None, manual_repair_aed=0):
    manual = _d(manual_repair_aed)
    if manual > 0:
        return manual, manual, "manuell"
    if not damage["damage_data_available"]:
        return Decimal(0), Decimal(0), "nicht_sicher_bestimmbar"

    market = _d(market_value_aed)
    purchase = _d(purchase_price_aed)
    base = market if market > 0 else purchase * Decimal("1.35")
    if base <= 0:
        return Decimal(0), Decimal(0), "nicht_sicher_bestimmbar"

    ratios = {
        "niedrig": (Decimal("0.02"), Decimal("0.07"), Decimal("2000"), Decimal("6000")),
        "mittel": (Decimal("0.07"), Decimal("0.17"), Decimal("7000"), Decimal("18000")),
        "hoch": (Decimal("0.16"), Decimal("0.32"), Decimal("16000"), Decimal("38000")),
        "kritisch": (Decimal("0.30"), Decimal("0.58"), Decimal("32000"), Decimal("80000")),
    }
    low_ratio, high_ratio, low_floor, high_floor = ratios[damage["damage_level"]]
    make_factor = Decimal("1.30") if (make or "").lower().strip() in EXOTIC_MAKES else Decimal("1")
    low = max(low_floor, base * low_ratio) * make_factor
    high = max(high_floor, base * high_ratio) * make_factor
    return low.quantize(Decimal("1")), high.quantize(Decimal("1")), "heuristisch"


def analyze_purchase(
    *, condition=None, tags=None, damage_description=None, make=None,
    purchase_price_aed=0, market_value_aed=0, import_cost_aed=0,
    manual_repair_aed=0, eur_aed_rate=4.30,
):
    damage = classify_damage(condition, tags, damage_description)
    purchase = _d(purchase_price_aed)
    market = _d(market_value_aed)
    import_cost = _d(import_cost_aed)
    repair_low, repair_high, repair_source = estimate_repair_cost(
        market, purchase, damage, make, manual_repair_aed
    )
    repair_mid = (repair_low + repair_high) / 2 if repair_high else Decimal(0)

    net = market - purchase - import_cost - repair_mid if market > 0 and purchase > 0 else Decimal(0)
    margin = (net / market * 100) if market > 0 else Decimal(0)
    target_profit = max(market * Decimal("0.15"), Decimal("15000")) if market > 0 else Decimal(0)
    max_bid = max(Decimal(0), market - import_cost - repair_high - target_profit) if market > 0 else Decimal(0)

    data_complete = bool(damage["damage_data_available"] and market > 0 and purchase > 0 and repair_source != "nicht_sicher_bestimmbar")
    fatal = bool(damage["damage_red_flags"])

    if fatal:
        recommendation = "MEIDEN"
        reason = f"Kritischer Schaden erkannt: {damage['damage_red_flags'][0]}."
    elif damage["damage_risk_score"] >= 75:
        recommendation = "MEIDEN"
        reason = "Das technische und wirtschaftliche Reparaturrisiko ist zu hoch."
    elif not data_complete:
        recommendation = "PRÜFEN"
        reason = "Marktvergleich oder Zustandsdaten reichen noch nicht für eine belastbare Kaufentscheidung."
    elif max_bid > 0 and purchase > max_bid:
        recommendation = "MEIDEN"
        reason = "Das aktuelle Gebot liegt über dem wirtschaftlich sinnvollen Maximalgebot."
    elif net <= 0 or margin < Decimal("5"):
        recommendation = "MEIDEN"
        reason = "Nach Reparatur und Nebenkosten bleibt keine ausreichende Marge."
    elif damage["damage_risk_score"] <= 45 and purchase <= max_bid and margin >= Decimal("12") and net >= Decimal("15000"):
        recommendation = "KAUFEN"
        reason = "Der Schaden wirkt wirtschaftlich reparierbar und das Gebot liegt unter dem kalkulierten Maximalgebot."
    else:
        recommendation = "PRÜFEN"
        reason = "Grundsätzlich interessant, aber Marge oder Reparaturrisiko sollten vor dem Kauf nochmals geprüft werden."

    rate = _d(eur_aed_rate) or Decimal("4.30")
    result = dict(damage)
    result.update({
        "purchase_recommendation": recommendation,
        "purchase_reason": reason,
        "analysis_data_complete": data_complete,
        "repair_estimate_source": repair_source,
        "repair_estimate_low_aed": float(repair_low),
        "repair_estimate_high_aed": float(repair_high),
        "repair_estimate_low_eur": float(repair_low / rate) if repair_low else 0.0,
        "repair_estimate_high_eur": float(repair_high / rate) if repair_high else 0.0,
        "estimated_net_profit_aed": float(net),
        "estimated_margin_percent": round(float(margin), 1),
        "max_bid_aed": float(max_bid),
    })
    return result
