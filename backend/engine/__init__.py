"""AutomationAAE Evidence & Dynamic UI/UX Audit Engine Package."""

from backend.engine.css_intelligence import analyze_css
from backend.engine.interaction import run_interaction_suite
from backend.engine.runtime_diagnostics import RuntimeCollector
from backend.engine.coverage_gate import compute_acceptance_matrix, compute_advanced_regression
from backend.engine.spatial_relations import audit_spatial_relations

__all__ = [
    "analyze_css",
    "run_interaction_suite",
    "RuntimeCollector",
    "compute_acceptance_matrix",
    "compute_advanced_regression",
    "audit_spatial_relations",
]
