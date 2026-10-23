# SkinSync Documentation

Welcome to the **SkinSync** engineering documentation repository.

## Documentation Index

1. **[Technical Design Document (TDD)](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/docs/technical_design_document.md)**
   - Complete technical specification of the production system.
   - **Workflow 1: Scraping and Ingestion Engine** (Shopify API harvesting, rate-limiting, exponential backoff, regex HTML extraction, Strategy A self-enrichment, SQLite WAL price tracking).
   - **Workflow 2: 4-Gate Decoupled INCI Matching Engine** (Gate 1 data quality, FastEmbed ONNX binary BLOB vectors, Gate 2 deterministic rule safeguards, candidate bypassing, Gate 3 active consistency, Gate 4 decoupled Jev queue worker, Strategy B Open Beauty Facts fallback).
   - **Workflow 3: Product Similarity & Recommendation Engine** (Concentration-decayed weighted Jaccard $\frac{1}{\sqrt{i+1}}$, active overlap, dermatological safety scoring, and 2-stage Jev 1.13 SystemOne reranking).

2. **[Production Architecture & Catalog Pipeline Plan](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/docs/enhancement_plan.md)**
   - Transition roadmap from static CSV files to automated relational database with daily synchronization.
   - Benchmark performance metrics (44 µs vector search, memory footprint, catalog coverage).
   - Audit findings and legacy data sanitization results.

---

## Configuration & Environment

All configurable thresholds, weights, and timeouts are centralized in [`repo/api/pipeline/config.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/config.py) and can be customized via [`repo/api/.env`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/.env).
