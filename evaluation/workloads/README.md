# Matched workload contract

The official `thesis-completion-v2` protocol dispatches k6 for all of its traffic windows. The Locust adapter descriptions below document earlier pilot and qualification evidence; they are not active launch options. The Python helper image used for reference capture remains required independently of the traffic generator.

Both adapters consume `workload.json`: ordered HTTP requests, payload templates,
expected status and outcome predicates. Redirects are disabled, so a Boutique
cart add is one recorded HTTP 302 rather than a hidden additional cart GET.
A fresh `eval-{sha256(run_id UTF-8)[:12]}-{iteration}` identity is assigned per
journey. Hashing bounds the email local part and excludes run-ID punctuation such
as colons, which otherwise makes Boutique reject checkout before reaching gRPC.
Python and k6 use the same lowercase hexadecimal prefix. The complete original
run ID and request IDs remain in headers and ledger evidence. Boutique
receives it through the actual `shop_session-id` cookie; Astronomy uses the same
identity for cart read (`sessionId` query), cart add and checkout (`userId` JSON).
Currency-sensitive Astronomy GET/checkout routes explicitly set USD.

`legacy`: 180 seconds, target 2 iterations/second. Its 50-iteration schedule
preserves the original error-branch precedence: 360 iterations yield 29 success
checkout attempts and 7 unknown-product attempts. `checkout-heavy`: 300 seconds,
target 2 iterations/second; each 10 iterations contain 2 browse, 2 cart, 5 success
checkout and 1 expired-card checkout journeys. All checkout journeys add one real
product first. Expired cards have a valid number, month and CVV but year 2020.

The matching guarantee concerns the sequence assigned to each global iteration,
payload and outcome definition. It does not equate load models: k6 uses an open
constant arrival rate with 10 preallocated/50 maximum VUs; Locust runs one process
with 10 closed users and `constant_throughput(2/10)` per user. Slow journeys reduce
Locust's achieved iteration rate. The shared Locust counter covers all users;
distributed workers would need a different global sequencing protocol.

Each adapter emits `EVAL_WINDOW {json}` at test start and finish. The initial
record has `ended_at=null`; the final record retains `started_at` and adds the
actual end after graceful drain. `intended_duration_seconds` defines the scheduled
window; `target_iterations_per_second` and `expected_iterations` make the intended
iteration budget explicit (e.g. 30 seconds at 1/s means 30). Actual per-route request
counts derive from the shared schedule and observed globally assigned iterations.
`EVAL_ATTEMPT {json}` is flushed before each network call with request/run/journey
IDs, route, generator and start time. An attempt without a terminal ledger record
is incomplete evidence, including process interruption.

Every attempted request finishing in the adapter emits `EVAL_REQUEST {json}` to
stdout. Fields include stable request and journey IDs, run ID, generator, route,
RFC3339 start/end, wall duration milliseconds, status (0 on transport failure),
exception, expected status, outcome correctness, attempted and completed.
`completed` means an HTTP response was received, including expected error status;
`outcome_ok` additionally validates the declared status and business outcome.
Killed/aborted process requests may lack terminal ledger records: reconcile them
against k6 granular metrics and independent application access evidence. Request
headers carry `X-Evaluation-Run` and `X-Evaluation-Request-ID` for reconciliation.

Expected failures are successful outcome checks but remain HTTP failures in
application evidence. Boutique expired checkout must be HTTP 500 with the payment
expiration and order-completion error text. Astronomy must be HTTP 422 with JSON
`code=PAYMENT_FAILED`; generic 500 is rejected. Success requires Boutique's order
completion template, or Astronomy's nonempty orderId and product item quantity 1.
The Astronomy cart-add response must also contain that product and quantity.

Jobs print actual runtime tool versions. k6 retains granular NDJSON plus JSON
summary; native and matched Locust retain all CSV files and base64 HTML report.
Native Locust uses the demo image's original default locustfile/working directory;
matched Locust explicitly loads `/scripts/matched_locust.py` from a ConfigMap.
ConfigMap keys for each matched generator: `run.js`, `matched_locust.py`,
`common.py`, `workload.json`.

## Verified upstream references

Verified against cached Boutique v0.10.6 source and Astronomy app version 3.1.0
(the chart's `appVersion`). Runtime versions are captured because the actual
Locust version is owned by each demo image, rather than assumed from a generic tag.

- [Boutique v0.10.6 handlers](https://github.com/GoogleCloudPlatform/microservices-demo/blob/v0.10.6/src/frontend/handlers.go): cart add 302, successful order HTML, failed checkout 500.
- [Boutique validation](https://github.com/GoogleCloudPlatform/microservices-demo/blob/v0.10.6/src/frontend/validator/validator.go): expired nonzero year passes request schema validation.
- [Boutique payment](https://github.com/GoogleCloudPlatform/microservices-demo/blob/v0.10.6/src/paymentservice/charge.js): rejects expired card after receiving checkout call.
- [Boutique session middleware](https://github.com/GoogleCloudPlatform/microservices-demo/blob/v0.10.6/src/frontend/middleware.go): cart identity is the shop_session-id cookie, rather than email.
- [Astronomy 3.1.0 checkout](https://github.com/open-telemetry/opentelemetry-demo/blob/3.1.0/src/frontend/pages/api/checkout.ts): payment failure classification 422/PAYMENT_FAILED, unrelated failure 500, success returns flattened order.
- [Astronomy 3.1.0 cart](https://github.com/open-telemetry/opentelemetry-demo/blob/3.1.0/src/frontend/pages/api/cart.ts): GET sessionId and currencyCode, POST userId and item, returns actual cart.
- [Astronomy 3.1.0 payment](https://github.com/open-telemetry/opentelemetry-demo/blob/3.1.0/src/payment/charge.js): valid card details checked before expiration.
- [Locust constant_throughput](https://docs.locust.io/en/2.38.1/api.html#locust.wait_time.constant_throughput): tasks per second per user; a task can contain several HTTP requests. Actual deployed version must be checked from job output.
- [k6 constant arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/): iteration starts independent of response time, subject to available VUs; dropped iterations retained and checked.

## Reproduce adapter parity

Run `python3 -m unittest evaluation.tests.test_workloads` for the shared contract
regressions. Run `python3 evaluation/tests/check_workload_parity.py` from the harness
root for 800 journey fixtures across both demos and workloads, including long
colon-containing run IDs, distinct windows and UTF-8 identifiers. This command
runs Node inside Docker and checks the actual k6 adapter through k6 API shims;
it performs no application requests and does not test k6 runtime scheduling.

The bounded identity uses the documented [k6 sha256(input, outputEncoding)
API](https://grafana.com/docs/k6/latest/javascript-api/k6-crypto/sha256/), confirmed
against the exact [k6 v1.8.2 crypto implementation](https://github.com/grafana/k6/blob/v1.8.2/internal/js/modules/k6/crypto/crypto.go).
