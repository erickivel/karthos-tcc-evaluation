# No image build or package downloads are performed by offline qualification.
# Supply a reviewed linux/amd64 Python base with bash/git/curl/CA/native libs.
ARG BASE_IMAGE
FROM --platform=linux/amd64 ${BASE_IMAGE}
ARG BASE_IMAGE
COPY source/infrastructure/controller/verify_bundle.py /opt/verify_bundle.py
RUN python -c 'import os,re; assert re.fullmatch(r".+@sha256:[0-9a-f]{64}", os.environ["BASE_IMAGE"]), "immutable BASE_IMAGE required"'
# These local build inputs must be supplied and reviewed; absent inputs fail COPY.
COPY toolchain/ /opt/toolchain/
COPY source-manifest.json /opt/source-manifest.json
COPY product-manifest.json /opt/product-manifest.json
COPY product/ /opt/product/
COPY source/ /opt/evaluation/
COPY cache/ /opt/evaluation/cache/
COPY cache-manifest.json /opt/cache-manifest.json
# Hash arguments belong after the large copies so source changes reuse toolchain layers.
ARG SOURCE_MANIFEST_SHA256
ARG TOOLCHAIN_MANIFEST_SHA256
ARG PRODUCT_MANIFEST_SHA256
ARG CACHE_MANIFEST_SHA256
RUN python /opt/verify_bundle.py /opt/toolchain /opt/toolchain/manifest.json "$TOOLCHAIN_MANIFEST_SHA256" toolchain \
 && python /opt/verify_bundle.py /opt/evaluation /opt/source-manifest.json "$SOURCE_MANIFEST_SHA256" source \
 && python /opt/verify_bundle.py /opt/product /opt/product-manifest.json "$PRODUCT_MANIFEST_SHA256" product \
 && python /opt/verify_bundle.py /opt/evaluation/cache /opt/cache-manifest.json "$CACHE_MANIFEST_SHA256" cache \
 && pip install --no-cache-dir --no-index --find-links=/opt/toolchain/wheels --require-hashes -r /opt/toolchain/requirements.lock
ENV PATH="/opt/toolchain/bin:/opt/toolchain/go/bin:/opt/toolchain/google-cloud-sdk/bin:${PATH}" \
    CLOUDSDK_PYTHON=/usr/local/bin/python \
    CLOUDSDK_CONFIG=/data/gcloud \
    CLOUDSDK_CORE_DISABLE_PROMPTS=1 \
    GKE_EVAL_DATA_ROOT=/data \
    GKE_EVAL_PRODUCT_ROOT=/opt/product \
    GKE_EVAL_PRODUCT_MANIFEST=/opt/product-manifest.json \
    GKE_EVAL_SOURCE_MANIFEST=/opt/source-manifest.json \
    HOME=/data/home GOPATH=/data/go GOCACHE=/data/go-cache \
    KUBECONFIG=/data/kubeconfig PYTHONDONTWRITEBYTECODE=1
# Detect missing runtime dependencies without network or cloud authentication.
RUN bash --version && git --version && curl --version \
 && gke-gcloud-auth-plugin --version && python -c "import yaml; assert yaml.__version__ == '6.0.3'" \
 && kubectl version --client=true && helm version --short && istioctl version --remote=false \
 && terraform version && gcloud version && go version && task --version
WORKDIR /opt/evaluation
ENTRYPOINT ["python", "-m", "evaluation.gke"]
