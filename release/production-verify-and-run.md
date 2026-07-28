# Verify and run a Production runtime

This archive is byte-integrity-verified when the bundled offline verifier succeeds.
Its receipt and public evidence bind the runtime bytes to an acceptance report that
was reopened by the pre-release staging step from ACCEPTANCE_ROOT at the exact
source commit. This downloaded verifier does not reopen that private root or
re-prove real CUDA, metric alignment, Viewer QA or human review.

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
