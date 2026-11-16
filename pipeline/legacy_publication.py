import json

from pipeline.storage.strict_json import load_json_rejecting_duplicates

LEGACY_REASON_CODES = {
    "source_policy_excluded": "not_collected_this_run",
    "news_translation_incomplete": "input_dataset_unhealthy",
}
LEGACY_RISK_DOCUMENT_PATHS = {"latest/risk.json", "latest/dashboard.json"}
LEGACY_RISK_HISTORY_PATHS = {
    "history/risk/daily.json",
    "history/risk/30d.json",
    "history/risk/90d.json",
}


def legacy_publication_bytes(relative: str, content: bytes) -> bytes | None:
    if relative in LEGACY_RISK_HISTORY_PATHS:
        points = load_json_rejecting_duplicates(content)
        if not isinstance(points, list) or any(not isinstance(point, dict) for point in points):
            raise ValueError("Legacy risk history must be an array of objects")
        if any(point.get("evidence_state") != "complete" for point in points):
            return None
        return content
    if relative == "latest/crypto.json":
        document = load_json_rejecting_duplicates(content)
        if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
            raise ValueError("Legacy crypto publication has no payload")
        assets = document["payload"].get("assets", [])
        if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
            raise ValueError("Legacy crypto publication has invalid assets")
        if any(not isinstance(asset.get("updated_at"), str) for asset in assets):
            return None
        return content
    if not (relative.startswith("latest/") or relative.startswith("metadata/")):
        return content
    document = load_json_rejecting_duplicates(content)
    if not isinstance(document, dict):
        raise ValueError("Legacy publication document must be an object")
    changed = _map_legacy_reasons(document)
    if relative in LEGACY_RISK_DOCUMENT_PATHS:
        risk = _legacy_risk_payload(document, relative)
        if _risk_is_unrepresentable(risk):
            return None
    if not changed:
        return content
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _legacy_risk_payload(document: dict, relative: str) -> object:
    if relative == "latest/risk.json":
        return document.get("payload")
    payload = document.get("payload")
    return payload.get("risk") if isinstance(payload, dict) else None


def _map_legacy_reasons(value: object) -> bool:
    if isinstance(value, list):
        changed = False
        for item in value:
            changed = _map_legacy_reasons(item) or changed
        return changed
    if not isinstance(value, dict):
        return False
    changed = False
    reason = value.get("reason")
    if isinstance(reason, dict):
        mapped = LEGACY_REASON_CODES.get(reason.get("code"))
        if mapped is not None:
            reason["code"] = mapped
            changed = True
    for nested in value.values():
        changed = _map_legacy_reasons(nested) or changed
    return changed


def _risk_is_unrepresentable(risk: object) -> bool:
    if not isinstance(risk, dict):
        raise ValueError("Legacy risk publication has no risk object")
    if risk.get("evidence_state") != "complete":
        return True
    dimensions = risk.get("dimensions", [])
    if not isinstance(dimensions, list) or any(not isinstance(item, dict) for item in dimensions):
        raise ValueError("Legacy risk publication has invalid dimensions")
    return any(item.get("trend") not in {"rising", "falling", "flat"} for item in dimensions)
