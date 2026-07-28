# Verify and run a Production runtime

This archive is byte-integrity-verified when the bundled offline verifier
succeeds. The receipt and public evidence bind the runtime bytes to a private
acceptance report that was reopened by the pre-release `stage-production-assets`
step from `ACCEPTANCE_ROOT`+`VERSION` on the exact HEAD. The download-side
verifier proves the downloaded bytes match that already-notarized receipt and
internal contract, and does not itself reopen real CUDA/metric/viewer/human
checks.

```powershell
python make.py verify
python make.py serve
```

`verify` delegates exactly to the bundled
`scripts/verify_production_release.py . --json`. Run it before installing
dependencies or starting the server. The runtime runner does not accept a
private scene import override.

Open the Studio URL printed by the second command. The accepted scene manifest
is mounted at `/web/data/recon/recon_manifest.json`; Studio and Viewer must
display Production status and the same content-addressed scene identity.

Do not add files to the extracted tree before verification. Re-download the
archive if verification fails.
