# ctrlX licensing (only when an app must enforce a license)

Upstream: `upstream-sdk-4.6.0/licensing.md`; API: `reference/rest-api/licensemanager.v1.10.0.openapi.json`.
Ready snippet: `templates/snippets/licensing.py` (unverified on a device - test once).

* **Mandatory for Bosch-signed partner apps** (App Guide 7.1): use the ctrlX license manager,
  no own license mechanism. In-house/OEM apps may skip it.
* Access: plug `licensing-service` (content, `target: $SNAP_DATA/licensing-service`) → Unix
  socket `$SNAP_DATA/licensing-service/licensing-service.sock`, HTTP without token:
  `GET /license-manager/api/v1/capabilities`, `POST /license-manager/api/v1/license`
  `{"name": "<SWL-…>", "version": "1.0"}` → `{id, isPermanent, …}`, `DELETE …/license/{id}`.
* Behaviour: acquire at start, re-check periodically (release + acquire, e.g. hourly), release
  on stop; store the id in `/tmp` (survives restarts, not reboots); show a clear warning/error
  in the UI when the license is missing; `tampered: true` = emergency mode → work normally.
* ctrlX COREvirtual has no real licenses: accept `SWL_XCR_ENGINEERING_4H` (4-hour engineering mode).
* Declare the license(s) in the manifest `licenses` array (`name`, `title`, `description`,
  `required`; `supportsTrial: true` enables the 10-day trial mode, schema 1.7).
* Customer apps that access the Data Layer may additionally need the device license
  "ctrlX OS License - Customer App" (`SWL-XC*-RUN-DLACCESSNRT**-NNNN`, example in
  package-assets.md) – clarify with your Bosch Rexroth contact for production devices.
