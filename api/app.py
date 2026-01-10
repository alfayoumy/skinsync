from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pickle
import pandas as pd
import numpy as np
import os

app = FastAPI(title="Egyptian Skincare AI (Production)", version="3.0")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. LOAD RESOURCES
print("Loading Production Engine...")

# Load the pickle for the Matrix & Logic
with open('skincare_engine_v2.pkl', 'rb') as f:
    model_data = pickle.load(f)

# Load the V4 Database (The one with "safety_warnings" and "score_sensitive")
# We use this INSTEAD of the dataframe inside the pickle
df = pd.read_csv('master_product_database_v4_production.csv')
# RESTORE KEYS from the model (Cluster ID is critical for the logic)
df['cluster_id'] = model_data['df']['cluster_id']

cosine_sim = model_data['cosine_sim']

# FIX: Truncate dataframe to match the model's cosine matrix size to prevent IndexError
# The V4 CSV appears to have more rows than the trained model, causing crashes.
df = df.iloc[:cosine_sim.shape[0]]

print("Engine Live.")

# 2. INPUT MODEL
class ProductRequest(BaseModel):
    product_title: str
    user_skin_type: str = "normal" # oily, dry, sensitive, normal

# 3. RECOMMENDATION LOGIC
def get_recommendations(title: str, skin_type: str):
    matches = df[df['title'].str.lower() == title.lower()]
    if matches.empty: return None
    
    target = matches.iloc[0]
    target_idx = target.name
    target_cat = target['std_category']
    target_cluster = target['cluster_id']
    
    # FILTER
    if target_cat not in ["Other", "Set", "Tool", "Accessory"]:
        candidate_indices = df[df['std_category'] == target_cat].index
    else:
        candidate_indices = df[df['cluster_id'] == target_cluster].index

    # SCORE
    results = []
    all_scores = cosine_sim[target_idx]
    
    for i in candidate_indices:
        if i == target_idx: continue
        
        sim_score = all_scores[i] * 100
        
        # Get Skin Score
        if skin_type == 'oily': skin_score = df.iloc[i]['score_oily']
        elif skin_type == 'dry': skin_score = df.iloc[i]['score_dry']
        elif skin_type == 'sensitive': skin_score = df.iloc[i]['score_sensitive']
        else: skin_score = 0
            
        final_score = sim_score + (skin_score * 2)
        
        # Safety Warnings (Only show if relevant)
        warnings = df.iloc[i]['safety_warnings'] if pd.notna(df.iloc[i]['safety_warnings']) else None
        
        results.append({
            "index": i,
            "final_score": final_score,
            "match_percent": sim_score,
            "skin_bonus": float(skin_score),
            "warnings": warnings
        })

    # SORT
    results = sorted(results, key=lambda x: x['final_score'], reverse=True)[:5]
    
    # Helper functions for data cleaning
    def clean_float(val):
        if pd.isna(val) or np.isnan(val) or np.isinf(val):
            return 0.0
        return float(val)

    def clean_str(val):
        if pd.isna(val):
            return ""
        return str(val)

    output = []
    for r in results:
        row = df.iloc[r['index']]
        output.append({
            "title": clean_str(row['title']),
            "brand": clean_str(row['brand']),
            "price": clean_float(row['price_low']),
            "match_score": round(clean_float(r['final_score']), 1),
            "warnings": clean_str(r['warnings']) if r['warnings'] else None,
            "url": clean_str(row['url'])
        })
        
    return output

@app.post("/recommend")
def recommend(request: ProductRequest):
    data = get_recommendations(request.product_title, request.user_skin_type)
    if not data: raise HTTPException(status_code=404, detail="Product not found")
    # Sanitize user_skin too just in case? No, it comes from request.
    return {"user_skin": request.user_skin_type, "results": data}

@app.get("/search")
def search_products(query: str):
    """
    Returns a list of top 5 product titles matching the search query.
    Case-insensitive.
    """
    if not query or len(query) < 2:
        return []
        
    # Search for the text inside the title
    # na=False handles any potential empty titles safely
    matches = df[df['title'].str.contains(query, case=False, na=False)]
    
    # Return top 5 titles as a simple list
    return matches['title'].head(5).tolist()
    
@app.get("/")
def health_check():
    return {"status": "active", "products": len(df)}