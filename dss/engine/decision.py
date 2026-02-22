"""Final decision engine — alert / reject based on score and vetoes."""

from __future__ import annotations

from datetime import datetime, timezone

from dss.config import DSSConfig
from dss.storage.repository import Repository


def make_decisions(
    setups: list[dict],
    cfg: DSSConfig,
) -> dict:
    """Make final alert/reject decisions for scored+vetoed setups.

    Returns:
        {
            "alerts": [...],
            "rejected": [...],
            "summary": str,
        }
    """
    alerts = []
    rejected = []
    repo = Repository()

    for setup in setups:
        direction = setup.get("direction", "LONG")
        total = setup.get("total_score", 0.0)
        vetoed = setup.get("vetoed", False)
        base_threshold = cfg.scoring.long_threshold if direction == "LONG" else abs(cfg.scoring.short_threshold)
        # Metals lack flow (±3) and options (±2) gates → scale threshold
        # to ~71% of crypto capacity (12.5/17.5)
        asset_name = setup.get("asset", "")
        asset_cfg = cfg.assets.get(asset_name)
        if asset_cfg and asset_cfg.asset_class == "metals":
            threshold = base_threshold * 0.71
        else:
            threshold = base_threshold

        # For shorts, score is negative; compare absolute values
        passes_threshold = False
        if direction == "LONG" and total >= threshold:
            passes_threshold = True
        elif direction == "SHORT" and total >= threshold:
            # Short scores are already signed positive from scorer for shorts
            passes_threshold = True

        if passes_threshold and not vetoed:
            setup["is_alert"] = True
            alerts.append(setup)
            try:
                repo.log_alert(setup)
            except Exception:
                pass
        else:
            setup["is_alert"] = False
            gap = threshold - total if direction == "LONG" else threshold - total
            rejected_info = {
                "asset": setup.get("asset", ""),
                "setup_type": setup.get("setup_type", ""),
                "direction": direction,
                "total_score": total,
                "score_gap": round(abs(gap), 2),
                "vetoed": vetoed,
                "veto_reasons": setup.get("veto_reasons", []),
                "failed_gate": _identify_weakest_gate(setup),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
            rejected.append(rejected_info)
            try:
                repo.log_rejected(rejected_info)
            except Exception:
                pass

    summary = _build_summary(alerts, rejected)
    repo.close()

    return {
        "alerts": alerts,
        "rejected": rejected,
        "summary": summary,
    }


def _identify_weakest_gate(setup: dict) -> str:
    """Identify which gate contributed least to the score."""
    gates = {
        "macro": abs(setup.get("macro_score", 0)),
        "flow": abs(setup.get("flow_score", 0)),
        "structure": abs(setup.get("structure_score", 0)),
        "phase": abs(setup.get("phase_score", 0)),
        "options": abs(setup.get("options_score", 0)),
        "mamis": abs(setup.get("mamis_score", 0)),
    }
    if setup.get("vetoed"):
        reasons = setup.get("veto_reasons", [])
        if any("MACRO" in r for r in reasons):
            return "macro"
        if any("FLOW" in r for r in reasons):
            return "flow"
        if any("STRUCTURE" in r for r in reasons):
            return "structure"
    return min(gates, key=gates.get)


def _build_summary(alerts: list, rejected: list) -> str:
    """Build a human-readable summary."""
    parts = []
    if alerts:
        for a in alerts:
            parts.append(
                f"ALERT: {a['asset']} {a['direction']} ({a['setup_type']}) "
                f"score={a['total_score']:.1f}"
            )
    if rejected:
        for r in rejected:
            reason = f"vetoed({', '.join(r['veto_reasons'][:2])})" if r["vetoed"] else f"gap={r['score_gap']:.1f}"
            parts.append(
                f"REJECTED: {r['asset']} {r['direction']} ({r['setup_type']}) "
                f"score={r['total_score']:.1f} — {reason}"
            )
    if not parts:
        parts.append("No setup candidates detected.")
    return "\n".join(parts)
