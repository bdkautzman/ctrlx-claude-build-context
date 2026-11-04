# reference/ - read-only material (never edit, never build from here)

| Folder | Content | Trust |
|---|---|---|
| `ctrlxdatalayer-api-3.6.0/` | Python source of the `ctrlxdatalayer` package 3.6.0, extracted from the SDK 4.6.0 Doxygen docs. **The authority for API names and signatures.** (Not importable as-is: needs the native lib.) | high |
| `sdk-4.6.0-samples-python/` | Official SDK 4.6.0 Python samples (core24) | high for API usage; NOT compliant with all validation rules (names, manifest `items`, restart-delay, proxy names) |
| `rest-api/` | OpenAPI specs of ctrlX OS services (package manager, identity manager, Data Layer REST, logbook, solutions, license manager) | high |
| `schemas/` | Official example files for manifest, fossinfo, validation descriptions (schemas themselves are in `tools/schemas/`) | high |
| `ctrlx-core-hardware/` | Hardware of the ctrlX CORE X2, X3, COREplus X3, X5, X7 in our own words, from the Bosch operating instructions (links, editions), and what we measured on our devices. PDFs local only, git-ignored | high for the manual figures; measured values are dated |
| `example-apps/` | Community/partner apps (see NOTES.md) | patterns only - all target older ctrlX OS (core20/core22) |
