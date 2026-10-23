"""
Benchmark evaluation for cosmetic formulation matching and Cross-Encoder verification.
Tests both positive pairs (equivalent formulations with volume/titling variations)
and negative pairs (same brand, different functional line or active).
"""

import sys
import os
import json
import logging
from typing import List, Dict

# Ensure api directory is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.inci_matcher import score_cross_encoder, clean_product_title

BENCHMARK_PAIRS = [
    # --- POSITIVE PAIRS (Equivalent formulations / size variations) ---
    {"p1": "CeraVe Moisturizing Cream 50ml", "p2": "CeraVe Moisturising Cream", "label": 1, "reason": "Exact formulation, UK/US spelling and volume variation"},
    {"p1": "La Roche-Posay Cicaplast Baume B5+ 40ml", "p2": "La Roche Posay Cicaplast Baume B5+", "label": 1, "reason": "Exact formulation, brand punctuation and volume"},
    {"p1": "Bioderma Sensibio H2O Micellar Water 500ml", "p2": "Bioderma Sensibio H2O - Micellar Cleansing Water", "label": 1, "reason": "Exact micellar formulation, hyphen/volume variation"},
    {"p1": "CeraVe Foaming Cleanser 236ml", "p2": "CeraVe Foaming Facial Cleanser For Normal to Oily Skin", "label": 1, "reason": "Exact foaming cleanser formulation"},
    {"p1": "CeraVe Hydrating Cleanser 473ml", "p2": "CeraVe Hydrating Facial Cleanser for Normal to Dry Skin", "label": 1, "reason": "Exact hydrating cleanser formulation"},
    {"p1": "The Ordinary Niacinamide 10% + Zinc 1% 30ml", "p2": "The Ordinary Niacinamide 10% + Zinc 1% 60ml", "label": 1, "reason": "Identical active percentage and formula, volume variant"},
    {"p1": "La Roche-Posay Anthelios UVMune 400 Invisible Fluid SPF50+ 50ml", "p2": "La Roche Posay Anthelios UVMune 400 Invisible Fluid SPF 50+", "label": 1, "reason": "Exact sunscreen fluid"},
    {"p1": "CeraVe Daily Moisturizing Lotion 236ml", "p2": "CeraVe Daily Moisturising Lotion for Dry to Very Dry Skin", "label": 1, "reason": "Exact lotion vehicle"},
    {"p1": "Bioderma Sebium Gel Moussant Purifying Cleansing Foaming Gel 200ml", "p2": "Bioderma Sebium Purifying Cleansing Foaming Gel", "label": 1, "reason": "Exact Sebium cleanser formulation"},
    {"p1": "La Roche-Posay Effaclar Duo+ 40ml", "p2": "La Roche Posay Effaclar Duo (+) Anti-Imperfections", "label": 1, "reason": "Exact treatment formulation"},

    # --- NEGATIVE PAIRS (Critical hard negatives: same brand, different actives / lines) ---
    {"p1": "Bioderma Sensibio H2O Micellar Water", "p2": "Bioderma Sebium H2O Micellar Water", "label": 0, "reason": "Hard negative: Sensibio (sensitive) vs Sebium (acne/zinc)"},
    {"p1": "CeraVe Hydrating Cleanser", "p2": "CeraVe Foaming Cleanser", "label": 0, "reason": "Hard negative: Non-foaming lotion cleanser vs Foaming gel cleanser"},
    {"p1": "CeraVe Hydrating Cleanser", "p2": "CeraVe SA Smoothing Cleanser", "label": 0, "reason": "Hard negative: Basic hydrating vs Salicylic Acid active"},
    {"p1": "CeraVe AM Facial Moisturizing Lotion SPF 25", "p2": "CeraVe PM Facial Moisturizing Lotion", "label": 0, "reason": "Hard negative: Chemical Sunscreen filters vs Night Barrier lotion"},
    {"p1": "La Roche-Posay Cicaplast Baume B5", "p2": "La Roche-Posay Cicaplast Levres Moisturising Lip Balm", "label": 0, "reason": "Hard negative: Face balm vs Lip balm vehicle"},
    {"p1": "La Roche-Posay Anthelios Invisible Fluid SPF 50+", "p2": "La Roche-Posay Anthelios Oil Control Gel-Cream SPF 50+", "label": 0, "reason": "Hard negative: Liquid fluid vs Matte gel-cream"},
    {"p1": "The Ordinary Hyaluronic Acid 2% + B5", "p2": "The Ordinary Niacinamide 10% + Zinc 1%", "label": 0, "reason": "Hard negative: Hydrating serum vs Sebum control active"},
    {"p1": "The Ordinary Glycolic Acid 7% Toning Solution", "p2": "The Ordinary Salicylic Acid 2% Solution", "label": 0, "reason": "Hard negative: AHA 7% exfoliant vs BHA 2% pore treatment"},
    {"p1": "Bioderma Sebium Hydra Moisturizing Cream", "p2": "Bioderma Sebium Pore Refiner", "label": 0, "reason": "Hard negative: Rich repair cream vs Matte pore primer"},
    {"p1": "CeraVe Blemish Control Cleanser", "p2": "CeraVe Blemish Control Gel", "label": 0, "reason": "Hard negative: Rinse-off wash vs Leave-on targeted acid gel"}
]

def run_benchmark():
    print("=" * 70)
    print("RUNNING BENCHMARK EVALUATION: LOCAL ONNX CROSS-ENCODER")
    print("=" * 70)

    tp, fp, tn, fn = 0, 0, 0, 0
    results = []
    threshold = 0.70

    for pair in BENCHMARK_PAIRS:
        p1 = pair["p1"]
        p2 = pair["p2"]
        expected = pair["label"]

        score = score_cross_encoder(p1, p2)
        predicted = 1 if score >= threshold else 0

        is_correct = (predicted == expected)
        if expected == 1 and predicted == 1:
            tp += 1
        elif expected == 0 and predicted == 1:
            fp += 1
        elif expected == 0 and predicted == 0:
            tn += 1
        elif expected == 1 and predicted == 0:
            fn += 1

        results.append({
            "p1": p1,
            "p2": p2,
            "expected": expected,
            "predicted": predicted,
            "score": score,
            "correct": is_correct,
            "reason": pair["reason"]
        })

        status = "PASS" if is_correct else "FAIL"
        print(f"[{status}] Score: {score:.4f} | Exp: {expected} Pred: {predicted} | '{p1[:30]}' vs '{p2[:30]}'")

    total = len(BENCHMARK_PAIRS)
    accuracy = (tp + tn) / total if total > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    print("-" * 70)
    print(f"RESULTS SUMMARY (Threshold = {threshold}):")
    print(f"Total Evaluated: {total}")
    print(f"True Positives:  {tp}/{tp+fn}")
    print(f"True Negatives:  {tn}/{tn+fp}")
    print(f"False Positives: {fp}")
    print(f"False Negatives: {fn}")
    print(f"Accuracy:        {accuracy * 100:.1f}%")
    print(f"Precision:       {precision * 100:.1f}%")
    print(f"Recall:          {recall * 100:.1f}%")
    print(f"F1-Score:        {f1 * 100:.1f}%")
    print("=" * 70)

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "details": results
    }

if __name__ == "__main__":
    run_benchmark()
