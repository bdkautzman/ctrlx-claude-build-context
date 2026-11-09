"""Dev-only UI feedback notes: Alt+click an element of the page, type a note (never active in the snap).

run-local.sh sets APP_DEV_FEEDBACK to the kit's tools/ui-feedback/overlay.js; together with APP_DEV_NO_AUTH=1
(config.DEV_FEEDBACK_JS) the local dev web server then
  - adds <script src="/<snap>/__dev/feedback.js" defer> to every HTML page it serves,
  - serves that script and keeps the notes in <project>/.dev/feedback.json (git-ignored):
      GET    /<snap>/__dev/feedback          {"version": 1, "items": [...]}
      POST   /<snap>/__dev/feedback          one note from the overlay -> 201 + the stored item
      DELETE /<snap>/__dev/feedback/<id>     remove a note (e.g. made by mistake)
The skill /ui-feedback works through the "open" items and sets their status to "done", "question" or "wontfix" with
a "resolution". The file is read on every request, so it may be edited while the server runs.
"""

import datetime
import json
import os
import threading

from app import config

ROUTE = "/__dev/"                 # below config.URL_PREFIX
STATUSES = ("open", "done", "question", "wontfix")
MAX_ITEMS = 500
_TEXT_LIMITS = {"note": 4000, "selector": 2000, "path": 2000, "page": 500, "text": 300, "html": 500,
                "timestamp": 40, "colorScheme": 10}
_NUMBER_FIELDS = {"rect": ("x", "y", "width", "height"),
                  "viewport": ("width", "height", "dpr", "scrollX", "scrollY")}
_lock = threading.Lock()


def enabled():
    return bool(config.DEV_NO_AUTH and config.DEV_FEEDBACK_JS)


def script_tag():
    return '<script src="{}{}feedback.js" defer></script>'.format(config.URL_PREFIX, ROUTE)


def inject(html):
    """The page with the overlay's <script> before its last </body> (at the end without one)."""
    tag = script_tag().encode("utf-8")
    i = html.lower().rfind(b"</body>")
    return html[:i] + tag + b"\n" + html[i:] if i >= 0 else html + b"\n" + tag


def load():
    """The feedback document; ValueError when the file is not one (e.g. broken by a hand edit)."""
    try:
        with open(config.DEV_FEEDBACK_FILE, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {"version": 1, "items": []}
    except (ValueError, UnicodeError) as exc:
        raise ValueError("{} is not valid JSON: {}".format(config.DEV_FEEDBACK_FILE, exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise ValueError("{} needs {{\"items\": [...]}}".format(config.DEV_FEEDBACK_FILE))
    return doc


def _save(doc):
    os.makedirs(os.path.dirname(config.DEV_FEEDBACK_FILE), exist_ok=True)
    tmp = config.DEV_FEEDBACK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, config.DEV_FEEDBACK_FILE)


def _clean(body):
    """The fields we keep from the overlay's note (ValueError for a body that is not one)."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    item = {}
    for key, limit in _TEXT_LIMITS.items():
        value = body.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("{} must be a string".format(key))
        if len(value) > limit:
            raise ValueError("{} is longer than {} characters".format(key, limit))
        item[key] = value
    for key in ("note", "selector"):
        if not item.get(key, "").strip():
            raise ValueError("{} is missing".format(key))
    for key, fields in _NUMBER_FIELDS.items():
        value = body.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError("{} must be an object".format(key))
        item[key] = {f: value[f] for f in fields
                     if isinstance(value.get(f), (int, float)) and not isinstance(value.get(f), bool)}
    if "timestamp" not in item:
        item["timestamp"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return item


def add(body):
    """Append a note as an "open" item with the next id; returns the stored item."""
    item = _clean(body)
    with _lock:
        doc = load()
        if len(doc["items"]) >= MAX_ITEMS:
            raise ValueError("{} notes already - remove done ones from {}".format(MAX_ITEMS, config.DEV_FEEDBACK_FILE))
        ids = [i.get("id") for i in doc["items"] if isinstance(i, dict)]
        stored = {"id": max([i for i in ids if isinstance(i, int)], default=0) + 1, "status": "open"}
        stored.update(item)
        doc["items"].append(stored)
        _save(doc)
    return stored


def remove(item_id):
    """Delete the item with this id; False when there is none."""
    with _lock:
        doc = load()
        items = [i for i in doc["items"] if not (isinstance(i, dict) and i.get("id") == item_id)]
        if len(items) == len(doc["items"]):
            return False
        doc["items"] = items
        _save(doc)
    return True
