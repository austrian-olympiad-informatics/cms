FROM debian:bullseye as isolate-builder

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libcap-dev \
    && rm -rf \
        /tmp/* \
        /var/{cache,log}/* \
        /var/lib/apt/lists/*

COPY isolate /isolate

RUN make -C /isolate isolate


FROM debian:bullseye as rootfs-builder

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        debootstrap \
    && rm -rf \
        /tmp/* \
        /var/{cache,log}/* \
        /var/lib/apt/lists/*

RUN \
    debootstrap --variant=minbase --include=build-essential bullseye /box-rootfs \
    && mkdir -p \
        /box-rootfs/box \
        /box-rootfs/fifo0 \
        /box-rootfs/fifo1 \
        /box-rootfs/fifo2 \
        /box-rootfs/fifo3 \
        /box-rootfs/fifo4

FROM python:3.9-bullseye AS cmsbase

RUN \
    apt-get update \
    # Use pinned versions so that we get updates with build caching
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        cppreference-doc-en-html \
        libcap2 \
    && rm -rf \
        /tmp/* \
        /var/{cache,log}/* \
        /var/lib/apt/lists/*

COPY requirements.txt /
RUN pip3 install --no-cache-dir -r /requirements.txt

COPY . /cms

RUN pip3 install --no-cache-dir -e /cms

RUN \
    cp /cms/config/cms.conf.sample /usr/local/etc/cms.conf \
    && mkdir -p \
        /var/local/log/cms \
        /var/local/cache/cms \
        /var/local/lib/cms \
        /var/local/run/cms \
        /var/local/include/cms \
        /var/local/share/cms

FROM cmsbase AS cmsworker

COPY --from=rootfs-builder /box-rootfs/ /box-rootfs
COPY --from=isolate-builder /isolate/isolate /usr/local/bin/isolate
COPY --from=isolate-builder /isolate/default.cf /usr/local/etc/isolate
