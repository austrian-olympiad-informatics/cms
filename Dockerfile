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

FROM python:3.10 AS cms

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
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
    sed -i 's/collections.MutableMapping/collections.abc.MutableMapping/g' /usr/local/lib/python3.10/site-packages/tornado/httputil.py \
    && cp /cms/config/cms.conf.sample /usr/local/etc/cms.conf \
    && mkdir -p \
        /var/local/log/cms \
        /var/local/cache/cms \
        /var/local/lib/cms \
        /var/local/run/cms \
        /var/local/include/cms \
        /var/local/share/cms

COPY --from=isolate-builder /isolate/isolate /usr/local/bin/isolate
COPY --from=isolate-builder /isolate/default.cf /usr/local/etc/isolate
