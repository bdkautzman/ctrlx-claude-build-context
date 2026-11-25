# Fake `ctrlxdatalayer` for offline tests

A tiny in-memory stand-in for the real `ctrlxdatalayer` Python package (API 3.6.0, see
`reference/ctrlxdatalayer-api-3.6.0/`). It only implements what `app/datalayer_service.py`
uses, so the provider glue can be tested on a PC without `libcomm_datalayer.so`.

It is NOT a simulator of the Data Layer. If you use more of the real API in the app,
extend the fake with the SAME names/signatures as in `reference/ctrlxdatalayer-api-3.6.0/`.
Tests put this folder on `sys.path` only when the real package cannot be imported.
