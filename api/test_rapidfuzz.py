from rapidfuzz import process, fuzz, utils

def test_match():
    choices = ["Parfum"]
    query = "parfum"
    
    # Test 1: No processor (Current implementation)
    res = process.extractOne(query, choices, scorer=fuzz.token_sort_ratio)
    print(f"No processor: {res}") 
    
    # Test 2: With default_process
    res2 = process.extractOne(query, choices, scorer=fuzz.token_sort_ratio, processor=utils.default_process)
    print(f"With default_process: {res2}")

if __name__ == "__main__":
    test_match()
