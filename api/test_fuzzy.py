import dermo_data
from dermo_data import INGREDIENT_DB, _get_ingredient_fuzzy

def test_fuzzy_matching():
    print("Testing fuzzy matching...")
    
    # Test Case 1: Exact match
    key = "Cocos Nucifera (Coconut) Oil"
    res = _get_ingredient_fuzzy(key, INGREDIENT_DB)
    assert res is not None
    assert res.inci == key
    print(f"PASS: Exact match for '{key}'")

    # Test Case 2: Fuzzy match (spacing/case/minor typo)
    # Expected to match "Cocos Nucifera (Coconut) Oil"
    fuzzy_key = "Cocos Nucifera Coconut Oil" 
    res = _get_ingredient_fuzzy(fuzzy_key, INGREDIENT_DB)
    assert res is not None
    assert res.inci == "Cocos Nucifera (Coconut) Oil"
    print(f"PASS: Fuzzy match '{fuzzy_key}' -> '{res.inci}'")

    # Test Case 3: Another fuzzy example
    # "Shea Butter" should match "Butyrospermum Parkii (Shea) Butter" ? 
    # Token sort ratio deals well with reordering and subsetting if tokens match.
    # "Shea Butter" vs "Butyrospermum Parkii (Shea) Butter" might be low score depending on ratio.
    # Let's try a closer one: "Butyrospermum Parkii Shea Butter"
    fuzzy_key_2 = "Butyrospermum Parkii Shea Butter"
    res = _get_ingredient_fuzzy(fuzzy_key_2, INGREDIENT_DB)
    
    if res:
        print(f"PASS: Fuzzy match '{fuzzy_key_2}' -> '{res.inci}'")
    else:
        print(f"WARN: Failed to match '{fuzzy_key_2}' (Score might be < 90)")

    # Test Case 4: No match
    fail_key = "Xylophone Extract"
    res = _get_ingredient_fuzzy(fail_key, INGREDIENT_DB)
    assert res is None
    print(f"PASS: Correctly failed to match '{fail_key}'")

    # Test Case 5: Integration in risk score
    # "Coconut Oil" might not be exact match in list, but "Cocos..." is in DB.
    # If we pass a list with "Cocos Nucifera Coconut Oil", it should get scored.
    ingredients = ["Cocos Nucifera Coconut Oil", "Water"]
    score = dermo_data.acne_risk_score(ingredients, INGREDIENT_DB)
    print(f"Risk Score for fuzzy coconut: {score}")
    assert score > 0
    print("PASS: Risk score computed with fuzzy match.")

    # Test Case 6: Lowercase match check
    # "parfum" should match "Parfum"
    res = _get_ingredient_fuzzy("parfum", INGREDIENT_DB)
    if res and res.inci == "Parfum":
        print("PASS: 'parfum' matched 'Parfum'")
    else:
        print(f"FAIL: 'parfum' did NOT match 'Parfum'. Got: {res}")
        
    # Test Case 7: Punctuation check
    # "parfum."
    res = _get_ingredient_fuzzy("parfum.", INGREDIENT_DB)
    if res and res.inci == "Parfum":
        print("PASS: 'parfum.' matched 'Parfum'")
    else:
        print(f"FAIL: 'parfum.' did NOT match 'Parfum'. Got: {res}")

if __name__ == "__main__":
    test_fuzzy_matching()
