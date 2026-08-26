from app.damage_analysis import analyze_purchase, classify_damage


def test_cosmetic_damage_can_still_be_a_buy():
    result = analyze_purchase(
        condition="Used vehicle",
        tags=["Minor body damage"],
        damage_description="Bumper damage; small dent; paint scratches",
        make="Mercedes",
        purchase_price_aed=100000,
        market_value_aed=200000,
        import_cost_aed=10000,
    )
    assert result["purchase_recommendation"] == "KAUFEN"
    assert result["damage_risk_score"] < 45
    assert result["max_bid_aed"] > 100000


def test_structural_damage_is_avoided_even_with_margin():
    result = analyze_purchase(
        damage_description="Structural damage and chassis damage on front left",
        make="BMW",
        purchase_price_aed=50000,
        market_value_aed=250000,
    )
    assert result["purchase_recommendation"] == "MEIDEN"
    assert "Rahmen-/Chassisschaden" in result["damage_red_flags"]


def test_unknown_condition_is_never_auto_buy():
    result = analyze_purchase(
        purchase_price_aed=50000,
        market_value_aed=150000,
    )
    assert result["purchase_recommendation"] == "PRÜFEN"
    assert result["analysis_data_complete"] is False


def test_overbid_is_avoided_after_repair_and_profit_buffer():
    result = analyze_purchase(
        damage_description="Bumper damage and scratches",
        purchase_price_aed=90000,
        market_value_aed=100000,
        import_cost_aed=10000,
    )
    assert result["purchase_recommendation"] == "MEIDEN"
    assert result["max_bid_aed"] < 90000


def test_negated_structural_language_does_not_create_false_red_flag():
    result = classify_damage(
        damage_description="No chassis damage. No structural damage. Bumper damage only."
    )
    assert "Rahmen-/Chassisschaden" not in result["damage_red_flags"]
    assert result["damage_level"] == "niedrig"
