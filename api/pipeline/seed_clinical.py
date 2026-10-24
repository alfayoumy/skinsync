"""
Seed script for dermatology clinical ingredients (comedogenicity, irritancy, tags, notes).
Populates the `clinical_ingredients` database table with authoritative records.
"""

import os
import sys
import json
import logging
from typing import List, Tuple, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection, DB_PATH

logger = logging.getLogger("skincare_pipeline.seed_clinical")

DEFAULT_CLINICAL_INGREDIENTS: List[Tuple[Any, ...]] = [
    ('Cocos Nucifera (Coconut) Oil', 'Coconut oil', 'oil', 4, 1, ['moisturizer', 'occlusive'],
     'Widely reported high comedogenic rating; tends to clog pores in acne-prone skin at leave-on concentrations.', [],
     'Effective emollient for dry skin; avoid in acne-prone/oily skin when used as primary leave-on oil.'),
    ('Theobroma Cacao (Cocoa) Seed Butter', 'Cocoa butter', 'butter', 4, 1, ['moisturizer'], '', [],
     'Rich occlusive; useful for very dry skin but can be pore-clogging in acne-prone skin.'),
    ('Isopropyl Myristate', 'Isopropyl myristate', 'emollient', 5, 2, ['texture', 'emollient'],
     'Classic comedogenicity tables flag IPM/IPMa as high risk, particularly in leave-on formulations.', [],
     'Often used to increase spreadability; avoid in acne-prone formulations or keep at low concentrations in rinse-off.'),
    ('Isopropyl Palmitate', 'Isopropyl palmitate', 'emollient', 4, 1, ['texture'], '', [], ''),
    ('Ethylhexyl Palmitate', 'Ethylhexyl palmitate', 'emollient', 4, 1, ['texture'], '', [], ''),
    ('Sodium Laureth Sulfate', 'SLES', 'surfactant', 0, 2, ['cleanser'], '', [],
     'Less irritating than SLS for many users, but still can be drying/irritating depending on formulation.'),
    ('Sodium Lauryl Sulfate', 'SLS', 'surfactant', 0, 5, ['cleanser', 'irritant'],
     'SLS is a well-documented irritant in patch/wash models. Avoid in sensitive-skin-targeted cleansers.', [],
     'Primarily an irritant; not a comedogen per se.'),
    ('Alcohol Denat.', 'Denatured alcohol', 'solvent', 0, 3, ['drying'],
     'Denatured alcohol can be drying and can exacerbate barrier dysfunction in sensitive/dry skin.', [],
     'Short-term texture benefits; avoid as primary active in dry/sensitive-skin leave-on products.'),
    ('Parfum', 'Fragrance (generic)', 'fragrance', 0, 4, ['fragrance'],
     'Fragrance is a leading cause of allergic contact dermatitis from cosmetics; many fragrance components are sensitizers.', [],
     'Use fragrance-free labeling for sensitive-skin products.'),
    ('Menthol', 'Menthol', 'sensitizer', 0, 5, ['cooling', 'sensitizer'], '', [], 'Common irritant and sensitizer in leave-on products.'),
    ('Melaleuca Alternifolia (Tea Tree) Leaf Oil', 'Tea tree oil', 'essential_oil', 1, 2, ['antimicrobial', 'acne_fighting'], '', [],
     'Has antimicrobial properties but can be irritating/sensitizing to some users; concentration-dependent.'),
    ('Niacinamide', 'Niacinamide (Vitamin B3)', 'active', 0, 1, ['brightening', 'acne_fighting', 'barrier_repair'],
     'Clinical testing shows good tolerability up to commonly used concentrations.', [], 'Generally well tolerated.'),
    ('Salicylic Acid', 'Salicylic acid (BHA)', 'exfoliant', 0, 2, ['acne_fighting', 'bha'],
     'Proven efficacy for acne; formulations may cause dryness/irritation depending on pH and vehicle.', [], ''),
    ('Glycolic Acid', 'Glycolic acid (AHA)', 'exfoliant', 0, 3, ['anti_aging', 'aha'], '', [],
     'Irritancy depends on concentration/pH; leave-on high-strength AHAs can be sensitizing.'),
    ('Retinol', 'Retinol (vitamin A derivative)', 'retinoid', 0, 4, ['anti_aging', 'acne_fighting'],
     'Topical retinoids increase skin turnover and can irritate; topical retinoids are generally advised against in pregnancy.',
     ['pregnancy'], 'Introduce slowly; use sunscreen due to photosensitivity.'),
    ('Ascorbic Acid', 'L-ascorbic acid (Vitamin C)', 'active', 0, 3, ['brightening', 'antioxidant'], '', [],
     'Potent antioxidant but acidic and can irritate sensitive skin; stability issues require appropriate formulation.'),
    ('Sodium Ascorbyl Phosphate', 'Sodium ascorbyl phosphate (SAP)', 'active', 0, 1, ['brightening', 'vitamin_c'],
     'SAP is a more stable, gentler vitamin C derivative often used for sensitive or acne-prone skin.', [],
     'Less irritating than L-ascorbic acid in many formulations.'),
    ('Sodium Hyaluronate', 'Sodium hyaluronate', 'humectant', 0, 0, ['hydration'], '', [], ''),
    ('Hyaluronic Acid', 'Hyaluronic acid', 'humectant', 0, 0, ['hydration'], '', [], ''),
    ('Glycerin', 'Glycerin', 'humectant', 0, 0, ['hydration'], '', [], ''),
    ('Ceramide NP', 'Ceramide NP', 'lipid', 0, 0, ['barrier_repair'], '', [], ''),
    ('Panthenol', 'Panthenol (pro-vitamin B5)', 'humectant', 0, 0, ['soothing'], '', [], ''),
    ('Allantoin', 'Allantoin', 'soother', 0, 0, ['soothing'], '', [], ''),
    ('Centella Asiatica Extract', 'Centella asiatica', 'extract', 0, 0, ['soothing'], '', [], ''),
    ('Aloe Barbadensis Leaf Juice', 'Aloe vera', 'extract', 0, 0, ['soothing'], '', [], ''),
    ('Squalane', 'Squalane (plant/synthetic)', 'oil', 1, 0, ['moisturizer'], '', [],
     'Generally low comedogenic risk and well tolerated in many skin types.'),
    ('Simmondsia Chinensis (Jojoba) Seed Oil', 'Jojoba oil', 'oil', 2, 0, ['moisturizer'], '', [], ''),
    ('Butyrospermum Parkii (Shea) Butter', 'Shea butter', 'butter', 2, 0, ['moisturizer'], '', [],
     'Useful for dry skin; may be heavier for oily/acne-prone skin depending on formula.'),
    ('Phenoxyethanol', 'Phenoxyethanol', 'preservative', 0, 1, ['preservative'], '', [], ''),
    ('Parabens', 'Parabens (methylparaben, propylparaben etc.)', 'preservative', 0, 2, ['preservative'], '', [], ''),
    ('Tocopherol', 'Vitamin E (tocopherol)', 'antioxidant', 2, 1, ['vitamin_e'], '', [], '')
]

def seed_clinical_ingredients(conn=None, db_path: str = DB_PATH, ingredients: Optional[List[Tuple[Any, ...]]] = None) -> int:
    """
    Seeds clinical dermatology ingredients into the clinical_ingredients table.
    Returns the number of clinical ingredients seeded.
    """
    target_ingredients = ingredients if ingredients is not None else DEFAULT_CLINICAL_INGREDIENTS
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    count = 0
    try:
        cur = conn.cursor()
        for inci, c_name, f_type, comedo, irrit, tags, evid, contras, notes in target_ingredients:
            cur.execute("""
            INSERT INTO clinical_ingredients (
                inci, common_name, functional_type, comedogenicity, irritancy,
                tags, evidence, contraindications, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(inci) DO UPDATE SET
                common_name = excluded.common_name,
                functional_type = excluded.functional_type,
                comedogenicity = excluded.comedogenicity,
                irritancy = excluded.irritancy,
                tags = excluded.tags,
                evidence = excluded.evidence,
                contraindications = excluded.contraindications,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP;
            """, (inci.strip(), c_name.strip() if c_name else None,
                  f_type.strip() if f_type else "active",
                  int(comedo), int(irrit),
                  json.dumps(tags or []), evid.strip() if evid else None,
                  json.dumps(contras or []), notes.strip() if notes else None))
            count += 1

        conn.commit()
        logger.info(f"Successfully seeded {count} clinical ingredients into database.")
        return count
    except Exception as e:
        logger.error(f"Failed to seed clinical ingredients: {e}")
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = seed_clinical_ingredients()
    print(f"Seeded {n} clinical ingredients into database.")
