# Verify and run a Production runtime

This archive is byte-integrity-verified only in the internal sense when the bundled
offline verifier succeeds. The official release process requires pre-release staging
to reopen ACCEPTANCE_ROOT at the exact source commit and compare the rebuilt package
with the candidate bytes.

The downloaded verifier checks only the internal byte bindings and internal contracts
within the supplied four files, and reports their claimed and source-bound identities.
It does not reopen or access ACCEPTANCE_ROOT or private evidence. It does not prove
publisher origin or authenticity. It does not prove that staging was executed, does
not prove that private acceptance was actually reopened, and does not prove external
authorization. It does not re-prove real CUDA, metric alignment, Viewer QA or human
review.

Authenticity must come from a trusted release channel and an externally trusted digest
or signature, if one exists. This guide does not claim that any signature exists.

Production build, staging, report publication, and the repository's safe
extraction API are private-Linux-builder-only append-only mutations. Downloaded
archive and four-file verification remain read-only and cross-platform. On
Windows or macOS, verify the four downloaded files first, extract with the
platform tool into a new empty directory, then run the bundled `verify` command.

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
