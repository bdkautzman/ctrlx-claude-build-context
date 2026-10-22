"""Save/load commands called by the ctrlX Solutions app (app data / configurations).

Registered in the package manifest under commands.activeConfiguration.save/load.
Spec: docs/ctrlx/upstream-sdk-4.6.0/persistdata.md (section 5), summary in
docs/ctrlx/app-data-settings.md.

Request body (POST, JSON):  {"configurationPath": "...", "id": "282xy5", "phase": "load"}
Success: 204 without body. Failure: 4xx/5xx with a Problem JSON body.
Unknown phases MUST be answered with 204 (forward compatibility).
Long-running work (> 1 min) would need the asynchronous 201+Location protocol - this
template is synchronous because reading one JSON file is fast.
"""

import json
import logging

from app.settings_store import LOAD_QUERY_TIMEOUT_S, SettingsError

log = logging.getLogger("appdata")


def problem(status, title, detail="", op_id=""):
    body = {"type": "about:blank", "title": title, "status": status, "severity": "Error"}
    if detail:
        body["detail"] = detail
        # Shown to the user in the Solutions UI; convention: operation id in brackets
        body["dynamicDescription"] = "{} [{}]".format(detail, op_id) if op_id else detail
    return status, body


def _parse(body_bytes):
    try:
        request = json.loads(body_bytes.decode("utf-8") or "{}")
    except (ValueError, UnicodeError):
        return None
    return request if isinstance(request, dict) else None


def handle_save(body_bytes, store):
    """Returns (status, body_or_None)."""
    request = _parse(body_bytes)
    if request is None:
        return problem(400, "Invalid request body")
    phase, op_id = request.get("phase", ""), request.get("id", "")
    if phase != "save":
        return 204, None
    try:
        store.save_to_disk()
    except (SettingsError, OSError) as exc:
        log.error("Saving settings failed: %s [%s]", exc, op_id)
        return problem(500, "Saving settings failed", str(exc), op_id)
    log.info("Settings saved to app data [%s]", op_id)
    return 204, None


def handle_load(body_bytes, store):
    """Returns (status, body_or_None)."""
    request = _parse(body_bytes)
    if request is None:
        return problem(400, "Invalid request body")
    phase, op_id = request.get("phase", ""), request.get("id", "")
    log.info("Load phase '%s' [%s]", phase, op_id)

    if phase == "query":
        # Can we load in the current system state? Answer 409 + mainDiagnosisCode 080F0E00 if a
        # specific device state (e.g. Setup) were required. This app can always load.
        # Pause polling already now (copyOnLoad copies files between query and prepare), but only
        # briefly: if another app refuses in this phase, Solutions cancels without sending abort.
        store.begin_load_operation(LOAD_QUERY_TIMEOUT_S)
        return 204, None
    if phase in ("prepare", "load"):
        store.begin_load_operation()
    if phase == "prepare":
        return 204, None
    if phase == "load":
        # copyOnLoad=true: Solutions already copied our directory into the active configuration.
        try:
            store.load_from_disk(create_if_missing=False)
        except SettingsError as exc:
            log.error("Loading settings failed: %s [%s]", exc, op_id)
            return problem(400, "Invalid app settings in configuration", "; ".join(exc.errors), op_id)
        return 204, None
    if phase in ("activate", "abort"):
        store.end_load_operation()
        return 204, None
    # "validate" and any future phase
    return 204, None
