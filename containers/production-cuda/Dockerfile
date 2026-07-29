# syntax=docker/dockerfile:1.7

# linux/amd64 manifest:
# sha256:60eda04ab6790aa76d73bf0df245b361eabc6d8f7b6f6cf9846c70f399b9a1eb
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04@sha256:94fd755736cb58979173d491504f0b573247b1745250249415b07fefc738e41f AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    SOURCE_DATE_EPOCH=1712056281

COPY containers/production-cuda/apt-build.lock /tmp/apt-build.lock
RUN printf '%s  %s\n' \
      '3106fbaa1ec08942c0b2e234843c06f44cd08575d2299925fc03daa4c91bf3b1' \
      '/tmp/apt-build.lock' | sha256sum -c - \
    && rm -f /etc/apt/sources.list.d/*.list \
    && printf '%s\n' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy main universe multiverse restricted' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy-updates main universe multiverse restricted' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy-security main universe multiverse restricted' \
      > /etc/apt/sources.list \
    && apt-get update \
    && xargs --no-run-if-empty apt-get install -y --no-install-recommends \
      < /tmp/apt-build.lock \
    && while IFS= read -r binding; do \
      package="${binding%%=*}"; expected="${binding#*=}"; \
      actual="$(dpkg-query -W -f='${Version}' "${package}")"; \
      test "${actual}" = "${expected}"; \
    done < /tmp/apt-build.lock \
    && rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:9b1e896523fc510691126c864406d9360a3d1e986acbda59cda57b5abda45b87 \
    https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tar.xz \
    /tmp/artifacts/Python-3.11.9.tar.xz
ADD --checksum=sha256:8aa81a785e0daf3ed60d0b9930a56c0f337280e6989351d1f1b74e21cf190160 \
    https://files.pythonhosted.org/packages/58/a9/f2a40a8fbe6f53ed49934cbfe44045d4cb131ae514a2b113458bcaf567b0/gsplat-1.4.0.tar.gz \
    /tmp/artifacts/gsplat-1.4.0.tar.gz
ADD --checksum=sha256:ee6d3d360a1e363ad2f1703b602da5a8987485bff812d0ae8aa4a6e672b994c4 \
    https://files.pythonhosted.org/packages/ee/86/93a22f779f3810be8ddbaae9073c7adfed97d4703a2953e66d36bdb4037a/nerfstudio-1.1.5-py3-none-any.whl \
    /tmp/artifacts/nerfstudio-1.1.5-py3-none-any.whl
ADD --checksum=sha256:bb0b899b3830c02fdf3dbde48ea59611833f366fef836e5c32cf8145134b7d3d \
    https://files.pythonhosted.org/packages/2c/ba/a4bc465d36f6aafbff89da1bf67bcc6a97475b1d2300a74a778dcb293cef/pyliblzfse-0.4.1.tar.gz \
    /tmp/artifacts/pyliblzfse-0.4.1.tar.gz
ADD --checksum=sha256:051833f6174e672eb313ee1c70dbcaf97e558dc46237215407933d28f40bca85 \
    https://download-r2.pytorch.org/whl/cu118/torch-2.1.2%2Bcu118-cp311-cp311-linux_x86_64.whl \
    /tmp/artifacts/torch-2.1.2+cu118-cp311-cp311-linux_x86_64.whl
ADD --checksum=sha256:9a784073e801c04066a5e4453306010b67bacfbff12bd57e5d65c1a638584a89 \
    https://download-r2.pytorch.org/whl/cu118/torchvision-0.16.2%2Bcu118-cp311-cp311-linux_x86_64.whl \
    /tmp/artifacts/torchvision-0.16.2+cu118-cp311-cp311-linux_x86_64.whl

RUN mkdir -p /tmp/cpython \
    && tar --extract --xz --strip-components=1 \
      --file /tmp/artifacts/Python-3.11.9.tar.xz \
      --directory /tmp/cpython \
    && cd /tmp/cpython \
    && ./configure \
      --prefix=/opt/python \
      --enable-shared \
      --with-ensurepip=install \
    && make -j"$(nproc)" \
    && make altinstall \
    && LD_LIBRARY_PATH=/opt/python/lib /opt/python/bin/python3.11 -c \
      'import platform; assert platform.python_version() == "3.11.9"' \
    && rm -rf /tmp/cpython

ENV LD_LIBRARY_PATH=/opt/python/lib \
    PATH=/opt/python/bin:/usr/local/bin:/usr/bin:/bin \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TORCH_CUDA_ARCH_LIST="7.5;8.0;8.6;8.9;9.0+PTX" \
    MAX_JOBS=2

COPY containers/production-cuda/requirements.lock /tmp/requirements.lock
RUN printf '%s  %s\n' \
      '63f8a156e43296f386c85af0f9829bedfb7c67a2fde514deb768b5962d336c6f' \
      '/tmp/requirements.lock' | sha256sum -c - \
    && awk ' \
      /^(gsplat|nerfstudio|pyliblzfse|torch|torchvision)(==| @)/ { skip=1; next } \
      skip && /^[A-Za-z0-9][A-Za-z0-9._-]*(==| @)/ { skip=0 } \
      !skip { print } \
    ' /tmp/requirements.lock > /tmp/base-requirements.lock \
    && ! grep -Eq '^(gsplat|nerfstudio|pyliblzfse|torch|torchvision)(==| @)' \
      /tmp/base-requirements.lock \
    && /opt/python/bin/python3.11 -m pip install \
      --no-deps \
      --no-index \
      /tmp/artifacts/torch-2.1.2+cu118-cp311-cp311-linux_x86_64.whl \
      /tmp/artifacts/torchvision-0.16.2+cu118-cp311-cp311-linux_x86_64.whl \
    && /opt/python/bin/python3.11 -m pip install --require-hashes \
      --no-deps --only-binary=:all: \
      --requirement /tmp/base-requirements.lock \
    && /opt/python/bin/python3.11 -m pip install \
      --no-deps \
      --no-index \
      /tmp/artifacts/nerfstudio-1.1.5-py3-none-any.whl \
    && /opt/python/bin/python3.11 -m pip install --no-build-isolation \
      --no-deps --no-index \
      /tmp/artifacts/pyliblzfse-0.4.1.tar.gz \
    && /opt/python/bin/python3.11 -m pip install --no-build-isolation \
      --no-deps --no-index \
      /tmp/artifacts/gsplat-1.4.0.tar.gz \
    && /opt/python/bin/python3.11 -m pip check \
    && /opt/python/bin/python3.11 -c \
      'import importlib.metadata as m, torch, torchvision; assert m.version("nerfstudio") == "1.1.5"; assert m.version("gsplat") == "1.4.0"; assert torch.__version__ == "2.1.2+cu118"; assert torch.version.cuda == "11.8"; assert torchvision.__version__ == "0.16.2+cu118"' \
    && rm -rf /tmp/artifacts /tmp/base-requirements.lock \
      /tmp/requirements.lock

COPY pipeline /opt/nantai/repo/pipeline
COPY containers/production-cuda/runtime-lock.json /opt/nantai/runtime-lock.json
RUN PYTHONPATH=/opt/nantai/repo /opt/python/bin/python3.11 -c \
      'from pathlib import Path; from pipeline.production_cuda_runtime_lock import load_production_cuda_runtime_lock_bytes; lock=load_production_cuda_runtime_lock_bytes(Path("/opt/nantai/runtime-lock.json").read_bytes()); assert lock.content_sha256 == "c84760e12c405d5b20b84d8fffae7ac8bb0fb54c6259756d588be5ec31424c1d"'

# linux/amd64 manifest:
# sha256:7c8e5ee1e8531f0d19f8d7e78c5b7efa9c455697fa96e62f083f361d6842e452
FROM nvidia/cuda:11.8.0-runtime-ubuntu22.04@sha256:eaaccb3528ceca110601131434ab467e41d694a41e8c9bf280fb27ac18fcb29b AS runtime

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

COPY containers/production-cuda/apt-runtime.lock /tmp/apt-runtime.lock
RUN printf '%s  %s\n' \
      '53537754e39148dceb0cd7e9bf622b3acc2d1ba765fa87d95b479cec38c0bf1c' \
      '/tmp/apt-runtime.lock' | sha256sum -c - \
    && rm -f /etc/apt/sources.list.d/*.list \
    && printf '%s\n' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy main universe multiverse restricted' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy-updates main universe multiverse restricted' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy-security main universe multiverse restricted' \
      > /etc/apt/sources.list \
    && apt-get update \
    && xargs --no-run-if-empty apt-get install -y --no-install-recommends \
      < /tmp/apt-runtime.lock \
    && while IFS= read -r binding; do \
      package="${binding%%=*}"; expected="${binding#*=}"; \
      actual="$(dpkg-query -W -f='${Version}' "${package}")"; \
      test "${actual}" = "${expected}"; \
    done < /tmp/apt-runtime.lock \
    && rm -rf /var/lib/apt/lists/* /tmp/apt-runtime.lock

COPY --from=builder /opt/python /opt/python
COPY --from=builder /opt/nantai/repo /opt/nantai/repo
COPY --from=builder /opt/nantai/runtime-lock.json /opt/nantai/runtime-lock.json
RUN rm -f /opt/python/bin/python /usr/local/bin/python \
    && install -m 0755 /opt/python/bin/python3.11 /opt/python/bin/python \
    && install -m 0755 /opt/python/bin/python3.11 /usr/local/bin/python \
    && chmod 0444 /opt/nantai/runtime-lock.json \
    && test ! -L /opt/python/bin/python \
    && test ! -L /opt/python/bin/ns-train \
    && test ! -L /opt/python/bin/ns-export

ARG SOURCE_COMMIT
RUN [[ "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]]

LABEL org.opencontainers.image.source="https://github.com/taomic2035/nantai-3d" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.version="1.0.0rc2" \
      org.opencontainers.image.licenses="MIT" \
      io.nantai.base.builder="nvidia/cuda:11.8.0-devel-ubuntu22.04@sha256:94fd755736cb58979173d491504f0b573247b1745250249415b07fefc738e41f" \
      io.nantai.base.runtime="nvidia/cuda:11.8.0-runtime-ubuntu22.04@sha256:eaaccb3528ceca110601131434ab467e41d694a41e8c9bf280fb27ac18fcb29b" \
      io.nantai.runtime-lock.content-sha256="c84760e12c405d5b20b84d8fffae7ac8bb0fb54c6259756d588be5ec31424c1d"

ENV PYTHONHOME=/opt/python \
    PYTHONPATH=/opt/nantai/repo \
    PATH=/opt/python/bin:/usr/local/bin:/usr/bin:/bin \
    LD_LIBRARY_PATH=/opt/python/lib \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
CMD ["/bin/bash"]
