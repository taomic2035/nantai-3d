# Production CUDA image inputs

This directory defines the repository-owned `linux/amd64` CUDA 11.8 image.
It is a fail-closed runtime candidate, not evidence that a real GPU training
run has succeeded.

The Dockerfile accepts only:

- two digest-pinned NVIDIA CUDA Ubuntu 22.04 bases;
- Ubuntu packages resolved from snapshot `20260701T000000Z`;
- CPython 3.11.9 and the four primary ML artifacts bound by SHA-256;
- a complete hash-locked Python dependency graph;
- the allowlisted `pipeline/` package from the exact source commit.

The image build must pass `SOURCE_COMMIT` as exactly 40 lowercase hexadecimal
characters. It compiles gsplat 1.4.0 from the locked sdist for CUDA
architectures `7.5;8.0;8.6;8.9;9.0+PTX`.

## Refresh procedure

1. Select a new Ubuntu snapshot and resolve every requested apt package from
   the official `main`, `universe`, `multiverse` and `restricted` indices.
2. Review `requirements.in`. Use `uv 0.8.13` to resolve CPython 3.11 for
   `x86_64-manylinux_2_31` with `--generate-hashes`.
3. PyTorch's cu118 wheel is 2.2 GiB. To avoid downloading it merely for
   metadata, range-read its official wheel `METADATA`, resolve with the same
   `2.1.2` dependency metadata, then replace only the Torch and Torchvision
   requirement rows with their official cu118 URLs and published SHA-256
   values. Remove the PyPI CUDA 12 helper packages, which are not dependencies
   of the self-contained cu118 wheel. Do not guess this substitution.
4. Recheck the CPython, PyPI, PyTorch and Docker Hub identities against their
   authoritative registries.
5. Recompute the byte length and SHA-256 of all three auxiliary locks, then
   regenerate canonical `runtime-lock.json` with
   `pipeline.production_cuda_runtime_lock`.
6. Update the same hashes in the Dockerfile and run the repository contract
   tests before requesting a Linux BuildKit run.

Local Windows testing proves only the static contract. The GitHub workflow
must build and probe the resulting digest with networking disabled before a
detached image receipt can be accepted. A fresh external NVIDIA host must
still pass the existing GPU clearance and non-mock training gate.
