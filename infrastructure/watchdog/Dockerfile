# Build from the prepared bundle; dependencies install from its hash-locked wheels.
ARG BASE_IMAGE
FROM --platform=linux/amd64 ${BASE_IMAGE}
ARG BASE_IMAGE
ARG WATCHDOG_SOURCE_SHA256
COPY toolchain/wheels/ /opt/wheels/
COPY toolchain/requirements.lock /opt/requirements.lock
RUN python -c 'import os,re; assert re.fullmatch(r".+@sha256:[0-9a-f]{64}",os.environ["BASE_IMAGE"]), "immutable BASE_IMAGE required"' \
 && pip install --no-cache-dir --no-index --find-links=/opt/wheels --require-hashes -r /opt/requirements.lock \
 && rm -rf /opt/wheels
COPY source/infrastructure/watchdog/main.py /opt/watchdog/main.py
RUN python -c 'import hashlib,os; assert hashlib.sha256(open("/opt/watchdog/main.py","rb").read()).hexdigest()==os.environ["WATCHDOG_SOURCE_SHA256"], "watchdog source hash mismatch"'
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/watchdog
CMD ["functions-framework", "--target=handler", "--source=/opt/watchdog/main.py", "--host=0.0.0.0", "--port=8080"]
