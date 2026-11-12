#!/bin/bash
set -euo pipefail
# Pinned COS supplies Docker; the public pinned SDK container supplies gcloud.
# No boot-time package installation or user credentials are required.
metadata() { curl --fail --silent --show-error -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
image="$(metadata controller-image)"
[[ "$image" =~ @sha256:[a-f0-9]{64}$ ]] || { echo 'immutable image required' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'reviewed Linux amd64 host required' >&2; exit 1; }
for tool in docker curl sha256sum; do command -v "$tool" >/dev/null || exit 1; done
mkdir -p /var/lib/google/karthos-evaluation/experiment /var/lib/google/karthos-evaluation/data/{tmp,home,gcloud}
chmod 700 /var/lib/google/karthos-evaluation
cd /var/lib/google/karthos-evaluation
sdk_image="$(metadata bootstrap-sdk-image)"
[[ "$sdk_image" =~ @sha256:[a-f0-9]{64}$ ]] || { echo 'immutable public SDK image required' >&2; exit 1; }
docker pull --platform linux/amd64 "$sdk_image"
gcloud() {
  docker run --rm --platform linux/amd64 --network host --read-only --tmpfs /tmp:rw,nosuid,nodev \
    -e CLOUDSDK_CONFIG=/tmp/gcloud --mount "type=bind,src=$PWD/experiment,dst=/workspace/experiment" \
    --workdir /workspace "$sdk_image" gcloud "$@"
}
# GCS object generations are required; gcloud uses attached VM metadata identity.
gcloud storage cp "$(metadata manifest-uri)#$(metadata manifest-generation)" experiment/manifest.json
printf '%s  experiment/manifest.json\n' "$(metadata manifest-sha256)" | sha256sum -c -
# Pulling the controller image is not permission to run an experiment.
registry="${image%%/*}"
# COS root is read-only; keep short-lived Docker auth on the writable boot disk.
export DOCKER_CONFIG="$PWD/data/tmp/docker-auth"
mkdir -p "$DOCKER_CONFIG"
chmod 700 "$DOCKER_CONFIG"
trap 'rm -f "$DOCKER_CONFIG/config.json"' EXIT
gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin "https://$registry"
docker pull --platform linux/amd64 "$image"
docker logout "$registry" >/dev/null
rm -f "$DOCKER_CONFIG/config.json"
sync
common=(--platform linux/amd64 --network host --read-only --tmpfs /tmp:rw,nosuid,nodev --mount "type=bind,src=$PWD/experiment,dst=/experiment,readonly" -v "$PWD/data:/data" -e GKE_EVAL_DATA_ROOT=/data -e TMPDIR=/data/tmp -e GKE_EVAL_PRODUCT_ROOT=/opt/product)
# Explicit ro experiment mount; never hide packaged /opt/product or reviewed code.
# --mount above is equivalent to /experiment:ro, without a writable host parent.
# Provisioning is dormant; the separate run command requests runtime.
[[ "$(metadata runtime-enabled)" == true ]] || { echo 'runtime is dormant'; exit 0; }
# Bootstrap code is supplied independently of the possibly corrupt image modules.
integrity="$(docker run --rm -i --platform linux/amd64 --network none --read-only --mount "type=bind,src=$PWD/experiment,dst=/experiment,readonly" -v "$PWD/data:/data" --entrypoint python "$image" - <<'NATIVE_INTEGRITY'
import hashlib,json
from pathlib import Path
manifest=json.loads(Path('/experiment/manifest.json').read_bytes())
name='infrastructure/controller/image_guard.py'
data=Path('/opt/evaluation',name).read_bytes()
if hashlib.sha256(data).hexdigest()!=manifest['implementation_hashes'][name]:raise ValueError('native guard module hash mismatch')
namespace={'__name__':'native_image_guard'}
exec(compile(data,name,'exec'),namespace)
namespace['verify_image'](Path('/'),manifest)
namespace['verify_executable_directory'](Path('/data/tmp'))
print('VERIFIED_CONTROLLER_IMAGE',flush=True)
NATIVE_INTEGRITY
)"
[[ "$integrity" == VERIFIED_CONTROLLER_IMAGE ]] || { echo 'native image integrity not verified' >&2; exit 1; }
echo 'Native controller image integrity verified; starting runtime.'
gcloud storage cp "$(metadata bootstrap-context-uri)#$(metadata bootstrap-context-generation)" experiment/bootstrap-context.json
common+=(-e GKE_EVAL_BOOTSTRAP_CONTEXT=/experiment/bootstrap-context.json)
# Runtime performs the same checks and owns watchdog/readiness/execution gates.
# Retain the named container and cidfile on stop/crash. Reboots never rerun silently.
# Status/recovery use docker inspect/logs and /data; replacement requires explicit inspected recovery.
[[ ! -e data/controller.cid ]] || { echo 'retained controller exists; inspect before explicit recovery' >&2; exit 1; }
exec docker run "${common[@]}" --name karthos-eval-controller --cidfile "$PWD/data/controller.cid" --label evaluation.karthos.dev/component=controller --label "evaluation.karthos.dev/manifest-sha256=$(metadata manifest-sha256)" "$image" runtime --manifest /experiment/manifest.json
