# Verify and run a Production runtime

This archive is accepted only when the bundled offline verifier succeeds. The
receipt and public evidence bind the runtime bytes to a private acceptance
report, but they do not replace the omitted raw capture, control-point or
operator evidence.

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
