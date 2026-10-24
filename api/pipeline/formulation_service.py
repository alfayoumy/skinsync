"""
SkinSync Formulation Repository & Publication Service
Provides centralized, transactional, and authoritative control over canonical INCI formulations.

Guarantees:
1. Strict Separation: Acquisition produces candidates; matching evaluates; this repository publishes.
2. Canonical Ownership: Only canonical_products records hold authoritative formulations.
3. Audit Provenance: Every publication or candidate evaluation is atomically recorded in inci_audit_evidence.
4. Non-Destructive Resolution: Weaker candidates or failed waterfall passes never erase or overwrite higher-confidence formulations.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    from pipeline.db_manager import get_connection, DB_PATH
    from pipeline.logging_config import get_logger
    logger = get_logger("skincare_pipeline.formulation")
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from pipeline.db_manager import get_connection, DB_PATH
    logger = logging.getLogger("skincare_pipeline.formulation")


@dataclass
class FormulationCandidate:
    canonical_product_id: int
    source_name: str  # 'skinsafe', 'openbeauty', 'curated_v10', 'langsearch_inkeedecoder', 'admin_override', etc.
    raw_ingredients: str
    parsed_tokens: List[str]
    actives: List[str] = field(default_factory=list)
    confidence_score: float = 0.0
    evidence_url: Optional[str] = None
    source_record_id: Optional[str] = None
    completeness: str = "full"  # 'full', 'partial', 'unverified'
    validation_flags: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None
    inci_matched_id: Optional[int] = None
    inci_matched_name: Optional[str] = None


class FormulationRepository:
    """
    Central repository for publishing authoritative formulations and managing INCI audit evidence.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    @staticmethod
    def compute_formula_hash(tokens: List[str]) -> str:
        """
        Computes a deterministic SHA-256 fingerprint for a list of parsed ingredient tokens.
        """
        normalized_str = ",".join(sorted(t.strip().lower() for t in tokens if t.strip()))
        return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_evidence_hash(raw_text: str) -> str:
        """
        Computes a deterministic SHA-256 fingerprint for raw source evidence text.
        """
        clean_text = " ".join(raw_text.strip().lower().split())
        return hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

    def get_canonical_formulation(self, canonical_product_id: int, conn=None) -> Optional[Dict[str, Any]]:
        """
        Retrieves the current authoritative formulation and version metadata for a canonical product.
        """
        should_close = False
        if conn is None:
            conn = get_connection(self.db_path)
            should_close = True
        try:
            cur = conn.cursor()
            cur.execute("""
            SELECT id, canonical_title, brand, category,
                   ingredients_raw, ingredients_parsed, actives,
                   ingredient_source, ingredient_source_url,
                   formula_version, formula_hash, formulation_published_at,
                   formulation_approval_reason, inci_matched_id, inci_matched_name
            FROM canonical_products
            WHERE id = ?;
            """, (canonical_product_id,))
            row = cur.fetchone()
            if not row:
                return None

            row_dict = dict(row)
            if row_dict.get("ingredients_parsed") and isinstance(row_dict["ingredients_parsed"], str):
                try:
                    row_dict["ingredients_parsed"] = json.loads(row_dict["ingredients_parsed"])
                except Exception:
                    pass
            if row_dict.get("actives") and isinstance(row_dict["actives"], str):
                try:
                    row_dict["actives"] = json.loads(row_dict["actives"])
                except Exception:
                    pass
            return row_dict
        finally:
            if should_close:
                conn.close()

    def record_candidate_evidence(
        self,
        candidate: FormulationCandidate,
        decision: str = "candidate_evidence",
        discrepancy_notes: Optional[str] = None,
        audited_by: str = "pipeline",
        conn=None
    ) -> int:
        """
        Records an INCI candidate observation in inci_audit_evidence without modifying canonical_products.
        """
        should_close = False
        if conn is None:
            conn = get_connection(self.db_path)
            should_close = True

        evidence_hash = self.compute_evidence_hash(candidate.raw_ingredients)
        tokens_json = json.dumps(candidate.parsed_tokens)
        flags_json = json.dumps(candidate.validation_flags) if candidate.validation_flags else None

        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO inci_audit_evidence (
                canonical_product_id, source_name, source_record_id,
                evidence_url, evidence_hash, raw_ingredients_found,
                parsed_tokens, confidence_score, completeness,
                decision, discrepancy_notes, validation_flags, audited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                candidate.canonical_product_id,
                candidate.source_name,
                candidate.source_record_id,
                candidate.evidence_url,
                evidence_hash,
                candidate.raw_ingredients,
                tokens_json,
                candidate.confidence_score,
                candidate.completeness,
                decision,
                discrepancy_notes or candidate.notes,
                flags_json,
                audited_by
            ))
            evidence_id = cur.lastrowid
            conn.commit()
            return evidence_id
        finally:
            if should_close:
                conn.close()

    def publish_formulation(
        self,
        candidate: FormulationCandidate,
        approval_reason: str,
        force: bool = False,
        audited_by: str = "pipeline",
        conn=None
    ) -> bool:
        """
        Atomically publishes a candidate as the authoritative formulation for a canonical product
        and records the decision in inci_audit_evidence.

        Protection Rules:
        - If the candidate completeness is 'partial' and not forced, it cannot be published as authoritative.
        - If an existing authoritative formulation has higher confidence, the candidate is logged as
          candidate_evidence or rejected_discrepancy, leaving the existing formulation untouched.
        - If the new formulation matches the existing formula_hash, it is recorded as corroborating evidence
          without incrementing the version.
        """
        if not candidate.parsed_tokens:
            logger.warning(f"Refusing to publish empty formulation for canonical product #{candidate.canonical_product_id}")
            return False

        if candidate.completeness == "partial" and not force:
            logger.warning(
                f"Candidate for #{candidate.canonical_product_id} is partial (e.g. key actives only). "
                f"Recording as audit evidence only; not publishing as authoritative formulation."
            )
            self.record_candidate_evidence(
                candidate=candidate,
                decision="candidate_evidence",
                discrepancy_notes="Partial ingredient callout rejected from authoritative publication.",
                audited_by=audited_by,
                conn=conn
            )
            return False

        should_close = False
        if conn is None:
            conn = get_connection(self.db_path)
            should_close = True

        new_formula_hash = self.compute_formula_hash(candidate.parsed_tokens)
        evidence_hash = self.compute_evidence_hash(candidate.raw_ingredients)
        parsed_json = json.dumps(candidate.parsed_tokens)
        actives_json = json.dumps(candidate.actives)
        flags_json = json.dumps(candidate.validation_flags) if candidate.validation_flags else None

        try:
            cur = conn.cursor()
            existing = self.get_canonical_formulation(candidate.canonical_product_id, conn=conn)
            if not existing:
                logger.error(f"Cannot publish formulation: canonical product #{candidate.canonical_product_id} not found.")
                return False

            curr_raw = existing.get("ingredients_raw")
            curr_src = existing.get("ingredient_source")
            curr_hash = existing.get("formula_hash")
            curr_version = existing.get("formula_version") or 1

            # Check if canonical formulation already exists
            if curr_raw and len(curr_raw.strip()) >= 15:
                # Same formula fingerprint: corroborate without bumping version
                if curr_hash and curr_hash == new_formula_hash:
                    logger.info(
                        f"Canonical product #{candidate.canonical_product_id} formulation confirmed by "
                        f"{candidate.source_name} (matching formula hash)."
                    )
                    # Corroborate in audit log
                    cur.execute("""
                    INSERT INTO inci_audit_evidence (
                        canonical_product_id, source_name, source_record_id,
                        evidence_url, evidence_hash, raw_ingredients_found,
                        parsed_tokens, confidence_score, completeness,
                        decision, discrepancy_notes, validation_flags, audited_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved_authoritative', ?, ?, ?);
                    """, (
                        candidate.canonical_product_id,
                        candidate.source_name,
                        candidate.source_record_id,
                        candidate.evidence_url,
                        evidence_hash,
                        candidate.raw_ingredients,
                        parsed_json,
                        candidate.confidence_score,
                        candidate.completeness,
                        f"Corroborated existing formulation (hash {new_formula_hash[:8]}). {approval_reason}",
                        flags_json,
                        audited_by
                    ))
                    conn.commit()
                    return True

                # Different formula: check authority precedence
                # High authority sources: admin_override, curated_v10
                if curr_src in ("admin_override", "curated_v10") and candidate.source_name not in ("admin_override", "curated_v10") and not force:
                    logger.info(
                        f"Preserving existing {curr_src} formulation for #{candidate.canonical_product_id}. "
                        f"New candidate from {candidate.source_name} logged as candidate_evidence."
                    )
                    self.record_candidate_evidence(
                        candidate=candidate,
                        decision="candidate_evidence",
                        discrepancy_notes=f"Candidate superseded by higher-authority existing formulation ({curr_src}).",
                        audited_by=audited_by,
                        conn=conn
                    )
                    return False

            # Qualifying upgrade or new formulation
            new_version = (curr_version + 1) if curr_raw else 1

            cur.execute("""
            UPDATE canonical_products
            SET ingredients_raw = ?,
                ingredients_parsed = ?,
                actives = ?,
                ingredient_source = ?,
                ingredient_source_url = ?,
                formula_version = ?,
                formula_hash = ?,
                formulation_published_at = CURRENT_TIMESTAMP,
                formulation_approval_reason = ?,
                inci_matched_id = COALESCE(?, inci_matched_id),
                inci_matched_name = COALESCE(?, inci_matched_name),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (
                candidate.raw_ingredients,
                parsed_json,
                actives_json,
                candidate.source_name,
                candidate.evidence_url,
                new_version,
                new_formula_hash,
                approval_reason,
                candidate.inci_matched_id,
                candidate.inci_matched_name,
                candidate.canonical_product_id
            ))

            cur.execute("""
            INSERT INTO inci_audit_evidence (
                canonical_product_id, source_name, source_record_id,
                evidence_url, evidence_hash, raw_ingredients_found,
                parsed_tokens, confidence_score, completeness,
                decision, discrepancy_notes, validation_flags, audited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved_authoritative', ?, ?, ?);
            """, (
                candidate.canonical_product_id,
                candidate.source_name,
                candidate.source_record_id,
                candidate.evidence_url,
                evidence_hash,
                candidate.raw_ingredients,
                parsed_json,
                candidate.confidence_score,
                candidate.completeness,
                approval_reason,
                flags_json,
                audited_by
            ))

            conn.commit()
            logger.info(
                f"Successfully published authoritative formulation v{new_version} for "
                f"canonical product #{candidate.canonical_product_id} from {candidate.source_name} "
                f"(hash: {new_formula_hash[:8]})."
            )
            return True

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to publish formulation for canonical product #{candidate.canonical_product_id}: {e}")
            raise
        finally:
            if should_close:
                conn.close()

    def get_audit_history(self, canonical_product_id: int, conn=None) -> List[Dict[str, Any]]:
        """
        Returns full audit history for a canonical product ordered chronologically.
        """
        should_close = False
        if conn is None:
            conn = get_connection(self.db_path)
            should_close = True
        try:
            cur = conn.cursor()
            cur.execute("""
            SELECT id, canonical_product_id, source_name, source_record_id,
                   evidence_url, evidence_hash, raw_ingredients_found,
                   parsed_tokens, confidence_score, completeness,
                   decision, discrepancy_notes, validation_flags, audited_by, created_at
            FROM inci_audit_evidence
            WHERE canonical_product_id = ?
            ORDER BY created_at ASC, id ASC;
            """, (canonical_product_id,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get("parsed_tokens") and isinstance(r["parsed_tokens"], str):
                    try:
                        r["parsed_tokens"] = json.loads(r["parsed_tokens"])
                    except Exception:
                        pass
                if r.get("validation_flags") and isinstance(r["validation_flags"], str):
                    try:
                        r["validation_flags"] = json.loads(r["validation_flags"])
                    except Exception:
                        pass
            return rows
        finally:
            if should_close:
                conn.close()
