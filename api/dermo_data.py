# dermo_data.py

# A production-grade mapping of ingredients to their skin properties.
# This covers the top common ingredients found in Egyptian & Global skincare.

INGREDIENT_DB = {
    # --- ACNE TRIGGERS (Comedogenic 3-5) ---
    'cocos_nucifera_oil': {'type': 'oil', 'comedogenic': 4, 'irritant': 0, 'tags': ['moisturizer']}, # Coconut Oil
    'cocoa_butter': {'type': 'butter', 'comedogenic': 4, 'irritant': 0, 'tags': ['moisturizer']},
    'isopropyl_myristate': {'type': 'emollient', 'comedogenic': 5, 'irritant': 3, 'tags': ['texture']},
    'isopropyl_palmitate': {'type': 'emollient', 'comedogenic': 4, 'irritant': 1, 'tags': ['texture']},
    'ethylhexyl_palmitate': {'type': 'emollient', 'comedogenic': 4, 'irritant': 1, 'tags': ['texture']},
    'algae_extract': {'type': 'extract', 'comedogenic': 5, 'irritant': 2, 'tags': ['antioxidant']},
    'kelp': {'type': 'extract', 'comedogenic': 5, 'irritant': 0, 'tags': ['antioxidant']},
    'sodium_lauryl_sulfate': {'type': 'surfactant', 'comedogenic': 5, 'irritant': 5, 'tags': ['cleanser']}, # SLS (Very Bad)

    # --- SENSITIZERS (Bad for Sensitive Skin) ---
    'alcohol_denat': {'type': 'solvent', 'comedogenic': 0, 'irritant': 5, 'tags': ['drying']},
    'alcohol': {'type': 'solvent', 'comedogenic': 0, 'irritant': 5, 'tags': ['drying']},
    'parfum': {'type': 'fragrance', 'comedogenic': 0, 'irritant': 4, 'tags': ['fragrance']},
    'fragrance': {'type': 'fragrance', 'comedogenic': 0, 'irritant': 4, 'tags': ['fragrance']},
    'menthol': {'type': 'sensitizer', 'comedogenic': 0, 'irritant': 5, 'tags': ['cooling']},
    'peppermint_oil': {'type': 'essential_oil', 'comedogenic': 2, 'irritant': 4, 'tags': ['fragrance']},
    'lavender_oil': {'type': 'essential_oil', 'comedogenic': 0, 'irritant': 3, 'tags': ['fragrance']},
    'lemon_oil': {'type': 'essential_oil', 'comedogenic': 0, 'irritant': 4, 'tags': ['fragrance']},
    'limonene': {'type': 'fragrance_component', 'comedogenic': 0, 'irritant': 4, 'tags': ['fragrance']},
    'linalool': {'type': 'fragrance_component', 'comedogenic': 0, 'irritant': 4, 'tags': ['fragrance']},

    # --- HEROES (Actives) ---
    'niacinamide': {'type': 'active', 'comedogenic': 0, 'irritant': 1, 'tags': ['brightening', 'acne_fighting', 'barrier_repair']},
    'salicylic_acid': {'type': 'exfoliant', 'comedogenic': 0, 'irritant': 2, 'tags': ['acne_fighting', 'bha']},
    'glycolic_acid': {'type': 'exfoliant', 'comedogenic': 0, 'irritant': 3, 'tags': ['anti_aging', 'aha']},
    'retinol': {'type': 'active', 'comedogenic': 0, 'irritant': 4, 'tags': ['anti_aging', 'acne_fighting']},
    'l_ascorbic_acid': {'type': 'active', 'comedogenic': 0, 'irritant': 3, 'tags': ['brightening', 'vitamin_c']},
    'sodium_ascorbyl_phosphate': {'type': 'active', 'comedogenic': 0, 'irritant': 0, 'tags': ['brightening', 'acne_fighting']}, # Gentle Vit C

    # --- HYDRATORS & SOOTHERS ---
    'sodium_hyaluronate': {'type': 'humectant', 'comedogenic': 0, 'irritant': 0, 'tags': ['hydration']},
    'hyaluronic_acid': {'type': 'humectant', 'comedogenic': 0, 'irritant': 0, 'tags': ['hydration']},
    'glycerin': {'type': 'humectant', 'comedogenic': 0, 'irritant': 0, 'tags': ['hydration']},
    'ceramide_np': {'type': 'lipid', 'comedogenic': 0, 'irritant': 0, 'tags': ['barrier_repair']},
    'ceramide_ap': {'type': 'lipid', 'comedogenic': 0, 'irritant': 0, 'tags': ['barrier_repair']},
    'panthenol': {'type': 'humectant', 'comedogenic': 0, 'irritant': 0, 'tags': ['soothing']},
    'allantoin': {'type': 'humectant', 'comedogenic': 0, 'irritant': 0, 'tags': ['soothing']},
    'centella_asiatica': {'type': 'extract', 'comedogenic': 0, 'irritant': 0, 'tags': ['soothing']},
    'aloe_barbadensis': {'type': 'extract', 'comedogenic': 0, 'irritant': 0, 'tags': ['soothing']},
    'snail_secretion_filtrate': {'type': 'active', 'comedogenic': 0, 'irritant': 0, 'tags': ['repair']},

    # --- OILS (Good vs Bad) ---
    'squalane': {'type': 'oil', 'comedogenic': 1, 'irritant': 0, 'tags': ['moisturizer']}, # Safe oil
    'jojoba_oil': {'type': 'oil', 'comedogenic': 2, 'irritant': 0, 'tags': ['moisturizer']}, # Mostly safe
    'tea_tree_oil': {'type': 'essential_oil', 'comedogenic': 0, 'irritant': 2, 'tags': ['acne_fighting']},
    'shea_butter': {'type': 'butter', 'comedogenic': 2, 'irritant': 0, 'tags': ['moisturizer']}, # Safe for dry, risky for acne
    
    # --- PRESERVATIVES & STABILIZERS ---
    'phenoxyethanol': {'type': 'preservative', 'comedogenic': 0, 'irritant': 1, 'tags': []},
    'parabens': {'type': 'preservative', 'comedogenic': 0, 'irritant': 2, 'tags': []},
    'tocopherol': {'type': 'antioxidant', 'comedogenic': 2, 'irritant': 1, 'tags': ['vitamin_e']}
}