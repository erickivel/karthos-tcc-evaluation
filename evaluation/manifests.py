"""Evaluation-owned Kubernetes objects and upstream demo overrides."""
from __future__ import annotations

from .artifact_wrapper import artifact_command
from .diagnostics.frontend_timeout import pinned_chart_values

OWNER_LABEL = "evaluation.karthos.dev/owner"
OWNER_VALUE = "karthos-evaluation"


def collector_self_metrics():
    """Explicit0.155/0.156 pull reader; endpoint stays inside the Pod."""
    return {'level':'detailed','readers':[{'pull':{'exporter':{'prometheus':{
        'host':'127.0.0.1','port':8888,'without_scope_info':True,
        'without_units':True,'without_type_suffix':True}}}}]}


def demo_values(demo: str, *, protocol_version: int = 2) -> dict:
    if demo == "boutique":
        return {
            "images": {"repository": "us-central1-docker.pkg.dev/online-boutique-ci/microservices-demo", "tag": "v0.10.6"},
            "loadGenerator": {"create": False},
            "frontend": {"externalService": False},
            "opentelemetryCollector": {"create": False},
            "googleCloudOperations": {"profiler": False, "tracing": False, "metrics": False},
        }
    if demo == "astronomy":
        return {
            "components": {"load-generator": {"enabled": False},
                           **({"frontend-proxy": pinned_chart_values()} if protocol_version>=4 else {})},
            "jaeger": {"enabled": False},
            "prometheus": {"enabled": False},
            "grafana": {"enabled": False},
            "opensearch": {"enabled": False},
            "opentelemetry-collector": {
                "ports": {"otlp": {"hostPort": 0}, "otlp-http": {"hostPort": 0}},
                "extraEnvs": [{"name": "KARTHOS_AGENT_HOST", "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}}}],
                "config": {
                    "exporters": {"otlp_grpc/karthos": {"endpoint": "${env:KARTHOS_AGENT_HOST}:4317", "tls": {"insecure": True}},
                                  "debug": {"verbosity": "basic"},
                                  "debug/traces": {"verbosity": "detailed", "use_internal_logger": False,
                                                   "sampling_thereafter": 1}},
                    "service": {"pipelines": {
                        "traces": {"exporters": ["otlp_grpc/karthos", "span_metrics", "debug/traces"]},
                        "metrics": {"exporters": ["debug"]},
                        "logs": {"exporters": ["debug"]},
                        "profiles": {"exporters": ["debug"]},
                    }, "telemetry": {"metrics": collector_self_metrics() if protocol_version>=4 else {"level": "none", "readers": []},
                                   "logs": {"processors": []}}},
                },
            },
        }
    raise ValueError(f"unsupported demo: {demo}")


def _job(name: str, namespace: str, image: str, command: str, env: dict[str, str], volume: dict | None = None) -> dict:
    container = {
        "name": name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["/bin/sh", "-c"],
        "args": [command],
        "env": [{"name": key, "value": value} for key, value in sorted(env.items())],
        "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1000m", "memory": "1Gi"}},
    }
    spec = {"restartPolicy": "Never", "containers": [container]}
    if volume:
        container["volumeMounts"] = [{"name": "script", "mountPath": "/scripts", "readOnly": True}]
        spec["volumes"] = [{"name": "script", "configMap": {"name": volume["name"]}}]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace, "labels": {OWNER_LABEL: OWNER_VALUE}},
        "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 3600,
                 "template": {"metadata": {"labels": {OWNER_LABEL: OWNER_VALUE},
                                          "annotations": {"sidecar.istio.io/inject": "false"}}, "spec": spec}},
    }


def _duration(duration_seconds: int, rate: float = 2) -> str:
    if duration_seconds <= 0 or rate <= 0:
        raise ValueError("duration_seconds and rate must be positive")
    return f"{duration_seconds}s"


def k6_job(demo: str, source: str, run_id: str, namespace: str, image: str, target: str,
           *, duration_seconds: int = 180, rate: int = 2, workload: str = "legacy",
           artifact_mode: bool = False, recovery_seconds: int = 1800) -> dict:
    name = f"eval-k6-{run_id}"
    command = (
        "echo __EVAL_K6_VERSION_BEGIN__; k6 version; echo __EVAL_K6_VERSION_END__; "
        "k6 run --quiet --log-format raw --log-output stdout --out json=/tmp/k6.ndjson /scripts/run.js; "
        "rc=$?; echo __EVAL_K6_BEGIN__; cat /tmp/k6.ndjson 2>/dev/null || true; "
        "echo __EVAL_K6_END__; exit $rc"
    )
    env = {
        "DEMO": demo, "SOURCE_MODE": source, "RUN_ID": run_id, "TARGET": target,
        "RATE": str(rate), "DURATION": _duration(duration_seconds, rate), "WORKLOAD": workload,
    }
    if artifact_mode:
        env.update(_artifact_env(recovery_seconds, duration_seconds))
        command = artifact_command('k6',
            'k6 run --quiet --log-format raw --log-output stdout '
            '--out "json=$EVAL_ARTIFACT_DIR/k6.ndjson" /scripts/run.js',
            'k6 version', recovery_seconds=recovery_seconds)
    return _job(name, namespace, image, command, env, {"name": "eval-k6-script"})


def _artifact_env(recovery_seconds: int, duration_seconds: int) -> dict:
    if not isinstance(recovery_seconds, int) or recovery_seconds <= 0:
        raise ValueError('recovery_seconds must be a positive integer')
    return {"EVAL_ARTIFACT_MODE": "1", "EVAL_ARTIFACT_DIR": "/tmp/eval-artifacts",
            "EVAL_RECOVERY_SECONDS": str(recovery_seconds),
            "EVAL_WORKLOAD_MAX_SECONDS": str(duration_seconds + 60)}


def _locust_artifact_command(arguments: str, duration_seconds: int, recovery_seconds: int, *, executable="locust") -> str:
    command = (f'{executable} {arguments} --headless --host="$LOCUST_HOST" '
               f'--run-time {_duration(duration_seconds)} --stop-timeout 20 '
               '--csv="$EVAL_ARTIFACT_DIR/locust" --csv-full-history '
               '--html="$EVAL_ARTIFACT_DIR/locust-report.html" --only-summary')
    return artifact_command('locust', command, 'locust --version', recovery_seconds=recovery_seconds)


def _locust_command(arguments: str, duration_seconds: int) -> str:
    exports = ""
    for part in ("stats", "failures", "exceptions", "stats_history"):
        exports += (f"echo __EVAL_LOCUST_{part}_BEGIN__; "
                    f"cat /tmp/locust_{part}.csv 2>/dev/null || true; "
                    f"echo __EVAL_LOCUST_{part}_END__; ")
    return (
        "echo __EVAL_LOCUST_VERSION_BEGIN__; locust --version; echo __EVAL_LOCUST_VERSION_END__; "
        f'locust {arguments} --headless --host="$LOCUST_HOST" '
        f"--run-time {_duration(duration_seconds)} --stop-timeout 20 "
        "--csv=/tmp/locust --csv-full-history --html=/tmp/locust.html --only-summary; "
        "rc=$?; " + exports +
        "echo __EVAL_LOCUST_html_BASE64_BEGIN__; base64 /tmp/locust.html 2>/dev/null || true; "
        "echo __EVAL_LOCUST_html_BASE64_END__; exit $rc"
    )


def _locust_env(demo: str, target: str) -> dict:
    env = {
        "LOCUST_HOST": target,
        "LOCUST_BROWSER_TRAFFIC_ENABLED": "false",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_METRICS_EXPORTER": "none",
        "PYTHONUNBUFFERED": "1",
    }
    if demo == "boutique":
        env["FRONTEND_ADDR"] = target.replace("http://", "")
    elif demo == "astronomy":
        # Prevent the Kubernetes AGENT_PORT Service variable from corrupting
        # the upstream native generator's numeric endpoint port.
        env["AGENT_PORT"] = "8010"
        env["AGENT_ENDPOINT"] = "agent"
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://otel-collector:4317"
    else:
        raise ValueError(f"unsupported demo: {demo}")
    return env


def locust_job(demo: str, run_id: str, namespace: str, image: str, target: str,
               *, duration_seconds: int = 180, artifact_mode: bool = False,
               recovery_seconds: int = 1800, native_ledger: bool = False) -> dict:
    """Native demo tasks and their original image working directory."""
    env = _locust_env(demo, target)
    env['RUN_ID'] = run_id
    env['DURATION'] = _duration(duration_seconds)
    command = _locust_command("--users 5 --spawn-rate 1", duration_seconds)
    if artifact_mode:
        env.update(_artifact_env(recovery_seconds, duration_seconds))
        command = _locust_artifact_command("--users 5 --spawn-rate 1", duration_seconds, recovery_seconds)
    volume = None
    if native_ledger:
        if not artifact_mode:
            raise ValueError('native ledger requires verified artifact mode')
        command = _locust_artifact_command("--users 5 --spawn-rate 1", duration_seconds,
                    recovery_seconds, executable="python3 /scripts/native_locust.py")
        volume = {"name": "eval-matched-locust-script"}
    return _job(f"eval-locust-{run_id}", namespace, image, command, env, volume)


def matched_locust_job(demo: str, source: str, run_id: str, namespace: str,
                       image: str, target: str, *, duration_seconds: int = 180,
                       rate: int = 2, workload: str = "legacy", artifact_mode: bool = False,
                       recovery_seconds: int = 1800) -> dict:
    """Single process, closed population; target rate is iterations per second."""
    env = _locust_env(demo, target)
    env.update({"DEMO": demo, "SOURCE_MODE": source, "RUN_ID": run_id,
                "WORKLOAD": workload, "RATE": str(rate),
                "DURATION": _duration(duration_seconds, rate), "PYTHONPATH": "/scripts"})
    arguments = "-f /scripts/matched_locust.py --users 10 --spawn-rate 10"
    command = _locust_command(arguments, duration_seconds)
    if artifact_mode:
        env.update(_artifact_env(recovery_seconds, duration_seconds))
        command = _locust_artifact_command(arguments, duration_seconds, recovery_seconds)
    return _job(f"eval-matched-locust-{run_id}", namespace, image, command,
                env, {"name": "eval-matched-locust-script"})
