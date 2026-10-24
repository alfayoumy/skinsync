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


def match_is_verified(result):
    """A proposed match is not an identity approval, even if it contains INCI."""
    statuses = [str(result.get(key) or '').lower() for key in
                ('gate4_jev_status', 'gate4_gemini_status')]
    if any('rejected' in status for status in statuses):
        return False
    if any('approved' in status for status in statuses):
        return True
    if any(any(flag in status for flag in ('pending', 'ambiguous', 'bypassed', 'rate_limited')) for status in statuses):
        return False
    return result.get('gate4_cross_status') == 'cross_approved'


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
    def compute_ordered_formula_hash(tokens: List[str]) -> str:
        """
        Computes a deterministic SHA-256 fingerprint preserving concentration order.
        """
        normalized_str = ",".join(t.strip().lower() for t in tokens if t.strip())
        return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_unordered_formula_hash(tokens: List[str]) -> str:
        """
        Computes a deterministic SHA-256 fingerprint sorted by token name (order-independent).
        """
        normalized_str = ",".join(sorted(t.strip().lower() for t in tokens if t.strip()))
        return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_formula_hash(tokens: List[str]) -> str:
        """
        Default formulation fingerprint (ordered concentration representation).
        """
        return FormulationRepository.compute_ordered_formula_hash(tokens)

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
                   formula_version, formula_hash, formula_hash_unordered, formulation_published_at,
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
            if should_close:
                conn.commit()
            return evidence_id
        finally:
            if should_close:
                conn.close()

    def publish_formulation(self, candidate, approval_reason, force=False, audited_by="pipeline", conn=None):
        """Publish and audit atomically without committing a caller's transaction."""
        # Legacy CSV ingredients are untrusted even if their syntax looks valid.
        if (candidate.source_name == 'historical_store_scrape'
                or (candidate.validation_flags or {}).get('method') == 'historical_store_scrape'):
            logger.warning("Refusing untrusted historical INCI for canonical #%s", candidate.canonical_product_id)
            return False
        owned = conn is None
        if owned:
            conn = get_connection(self.db_path)
        # BEGIN keeps releasing our savepoint from committing caller-owned work.
        if not conn.in_transaction:
            conn.execute("BEGIN")
        conn.execute("SAVEPOINT formulation_publication")
        try:
            from pipeline.identity_trust import publication_block
            blocked = publication_block(conn, candidate)
            if blocked:
                self.record_candidate_evidence(candidate, decision='identity_blocked', discrepancy_notes=blocked,
                                               audited_by=audited_by, conn=conn)
                result = False
            else:
                result = self._publish_formulation(candidate, approval_reason, force, audited_by, conn)
            if result and 'formulation_provenance' in {r[1] for r in conn.execute('PRAGMA table_info(canonical_products)')}:
                conn.execute("UPDATE canonical_products SET formulation_provenance='approved_authoritative' WHERE id=?", (candidate.canonical_product_id,))
            conn.execute("RELEASE SAVEPOINT formulation_publication")
            if owned:
                conn.commit()
            return result
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT formulation_publication")
            conn.execute("RELEASE SAVEPOINT formulation_publication")
            if owned:
                conn.rollback()
            raise
        finally:
            if owned:
                conn.close()

    def _publish_formulation(
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
        - Only complete, valid candidates can publish, including forced corrections.
        - Conflicting ordered formulas require an explicit correction; automatic candidates
          preserve the existing formulation and record conflict evidence.
        - If the new formulation matches the existing formula_hash, it is recorded as corroborating evidence
          without incrementing the version.
        """
        if not candidate.parsed_tokens:
            logger.warning(f"Refusing to publish empty formulation for canonical product #{candidate.canonical_product_id}")
            return False

        if candidate.completeness != "full":
            logger.warning(
                f"Candidate for #{candidate.canonical_product_id} has completeness='{candidate.completeness}'. "
                f"Recording as audit evidence only; not publishing as authoritative formulation."
            )
            self.record_candidate_evidence(
                candidate=candidate,
                decision="candidate_evidence",
                discrepancy_notes=f"Candidate with completeness='{candidate.completeness}' rejected from authoritative publication.",
                audited_by=audited_by,
                conn=conn
            )
            return False

        if candidate.validation_flags.get("is_truncated"):
            logger.warning(
                f"Candidate for #{candidate.canonical_product_id} is flagged as truncated. "
                f"Recording as audit evidence only; not publishing as authoritative formulation."
            )
            self.record_candidate_evidence(
                candidate=candidate,
                decision="candidate_evidence",
                discrepancy_notes="Truncated formulation candidate rejected from authoritative publication.",
                audited_by=audited_by,
                conn=conn
            )
            return False

        # Re-validate source text at the publication boundary; callers cannot label
        # an obviously truncated or malformed candidate as a complete formulation.
        from pipeline.inci_tokenizer import tokenize_inci_string
        current = self.get_canonical_formulation(candidate.canonical_product_id, conn=conn)
        if current is None:
            return False
        validation = tokenize_inci_string(candidate.raw_ingredients, product_title=current.get("canonical_title"))
        flags = candidate.validation_flags
        invalid = (
            validation.is_truncated
            or '***' in candidate.raw_ingredients
            or candidate.raw_ingredients.count('(') != candidate.raw_ingredients.count(')')
            or candidate.raw_ingredients.count('[') != candidate.raw_ingredients.count(']')
            or any(phrase in candidate.raw_ingredients.lower() for phrase in (
                '<script', '<div', 'how to use', 'apply daily', 'deliberately hidden',
                'displayed in alphabetical order', 'please use our application'))
            or validation.completeness != "full"
            or candidate.source_name in {"whitelist_description", "whitelist_extracted", "cluster_propagation"}
            or not candidate.raw_ingredients.strip()
            or any(not isinstance(t, str) or not t.strip() for t in candidate.parsed_tokens)
            or flags.get("invalid_structure")
            or flags.get("identity_valid") is False
            or flags.get("identity_resolved") is False
        )
        if invalid:
            self.record_candidate_evidence(candidate, decision="candidate_evidence",
                discrepancy_notes="Publication validation failed: truncated, invalid, or unresolved identity.",
                audited_by=audited_by, conn=conn)
            return False

        should_close = False
        if conn is None:
            conn = get_connection(self.db_path)
            should_close = True

        new_ordered_hash = self.compute_ordered_formula_hash(candidate.parsed_tokens)
        new_unordered_hash = self.compute_unordered_formula_hash(candidate.parsed_tokens)
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

            has_versions = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='formulation_versions'").fetchone())
            if has_versions and existing.get("ingredients_raw"):
                conn.execute("""INSERT OR IGNORE INTO formulation_versions(canonical_product_id,formula_version,formula_hash,
                    raw_ingredients,parsed_tokens,source_name,source_url,approval_reason) VALUES (?,?,?,?,?,?,?,?)""",
                    (candidate.canonical_product_id,existing.get("formula_version") or 1,existing.get("formula_hash"),
                     existing["ingredients_raw"],json.dumps(existing.get("ingredients_parsed") or []),existing.get("ingredient_source"),
                     existing.get("ingredient_source_url"),existing.get("formulation_approval_reason")))

            curr_raw = existing.get("ingredients_raw")
            curr_src = existing.get("ingredient_source")
            curr_hash = existing.get("formula_hash")
            curr_version = existing.get("formula_version") or 1
            if not curr_hash and isinstance(existing.get("ingredients_parsed"), list):
                curr_hash = self.compute_ordered_formula_hash(existing["ingredients_parsed"])

            # Check if canonical formulation already exists
            if curr_raw and curr_raw.strip():
                # Same ordered formula fingerprint: corroborate without bumping version
                if curr_hash and curr_hash == new_ordered_hash:
                    logger.info(
                        f"Canonical product #{candidate.canonical_product_id} formulation confirmed by "
                        f"{candidate.source_name} (matching ordered formula hash {new_ordered_hash[:8]})."
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
                        f"Corroborated existing formulation (ordered hash {new_ordered_hash[:8]}). {approval_reason}",
                        flags_json,
                        audited_by
                    ))
                    if should_close:
                        conn.commit()
                    return True

                # Different formula: protect existing authoritative formulation unless force=True
                if not force:
                    logger.warning(
                        f"Conflicting formulation candidate from {candidate.source_name} for #{candidate.canonical_product_id}. "
                        f"Existing authoritative formulation ({curr_src or 'curated'}) preserved. "
                        f"Candidate hash {new_ordered_hash[:8]} vs existing {curr_hash[:8] if curr_hash else 'unknown'}."
                    )
                    self.record_candidate_evidence(
                        candidate=candidate,
                        decision="conflict_discrepancy",
                        discrepancy_notes=(
                            f"Conflicting candidate formulation detected from {candidate.source_name} "
                            f"(candidate hash: {new_ordered_hash[:8]}, existing hash: {curr_hash[:8] if curr_hash else 'unknown'}). "
                            f"Existing formulation preserved. Requires administrative review or force=True."
                        ),
                        audited_by=audited_by,
                        conn=conn
                    )
                    if should_close:
                        conn.commit()
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
                formula_hash_unordered = ?,
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
                new_ordered_hash,
                new_unordered_hash,
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

            if has_versions:
                conn.execute("""INSERT INTO formulation_versions(canonical_product_id,formula_version,formula_hash,raw_ingredients,
                    parsed_tokens,source_name,source_url,approval_reason) VALUES (?,?,?,?,?,?,?,?)""",
                    (candidate.canonical_product_id,new_version,new_ordered_hash,candidate.raw_ingredients,parsed_json,
                     candidate.source_name,candidate.evidence_url,approval_reason))
            if should_close:
                conn.commit()
            logger.info(
                f"Successfully published authoritative formulation v{new_version} for "
                f"canonical product #{candidate.canonical_product_id} from {candidate.source_name} "
                f"(hash: {new_ordered_hash[:8]})."
            )
            return True

        except Exception as e:
            if should_close:
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
