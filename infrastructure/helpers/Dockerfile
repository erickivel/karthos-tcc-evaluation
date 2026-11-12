# Prerequisite: reviewed immutable Linux base containing Python 3, stress-ng,
# fio and iperf3. No mutable OS packages are resolved while building this layer.
# Record tool --version output and the final image digest in the review package.
ARG HELPER_BASE_IMAGE
FROM ${HELPER_BASE_IMAGE}
ARG HELPER_BASE_IMAGE
RUN python3 -c 'import os,re,shutil; assert re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}",os.environ["HELPER_BASE_IMAGE"]), "immutable helper base required"; assert all(shutil.which(x) for x in ("stress-ng","fio","iperf3")), "reviewed tool versions required in helper base"'
WORKDIR /opt/evaluation
COPY evaluation/__init__.py /opt/evaluation/evaluation/__init__.py
COPY evaluation/references /opt/evaluation/evaluation/references
COPY infrastructure/helpers/reference_sampler.py /opt/evaluation/infrastructure/helpers/reference_sampler.py
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3", "/opt/evaluation/infrastructure/helpers/reference_sampler.py"]
