import pandas as pd
import numpy as np
import dermo_data
from dermo_data import INGREDIENT_DB

def test_load_and_score():
    print("Loading CSV...")
    df = pd.read_csv('master_product_database_v10_final.csv')
    print(f"Loaded {len(df)} rows.")
    
    # 1. Check required columns
    required_cols = ['title', 'ingredients']
    for col in required_cols:
        if col not in df.columns:
            print(f"ERROR: Missing column {col}")
            return

    # 2. Test scoring logic on first 5 rows
    print("\nTesting scoring logic on sample rows...")
    for idx, row in df.head(5).iterrows():
        ing_str = str(row['ingredients'])
        # Simple split by comma, cleaning whitespace
        ingredients = [x.strip() for x in ing_str.split(',') if x.strip()]
        
        scores = {}
        for st in ['oily', 'dry', 'sensitive', 'normal']:
            res = dermo_data.recommend_for_skin_type(ingredients, INGREDIENT_DB, st)
            scores[st] = res['risk_score']
            
        print(f"Product: {row['title'][:30]}...")
        print(f"  - Cleaned Ingredients count: {len(ingredients)}")
        print(f"  - Scores: {scores}")

if __name__ == "__main__":
    test_load_and_score()
