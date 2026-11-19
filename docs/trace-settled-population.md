# Settled trace population accounting

The versioned trace-settled-population-v1 check complements the original fixed workload-window diagnostic. It changes no API query timestamps, workloads, tolerances, collection intervals or product code.

Required evidence: valid independent recorder and generator/observer/paired checkout identity join; recorded stable quiet endpoints with fresh samples; exact source/storage endpoint coverage at VictoriaMetrics millisecond resolution (native nanosecond records retained); complete edge/namespace/source/outcome partitions; reset-free cumulative request/failure counters and server histogram sums/counts; capture lifetime covering counter brackets and no extra or crossing checkout calls; explicit cumulative temporality and known units. Missing or ambiguous evidence stays insufficient.

The result establishes controlled aggregate population accounting and weighted server-duration agreement across paired spans, raw connector output and storage. It cannot exclude compensating loss/duplication or identify individual stored requests. Proxy timing remains separate.

The settled_trace_population report retains the original fixed-window population proof. Summary families preserve fixed_window_traffic_count, fixed_window_traffic_error and fixed_window_traffic_duration alongside settled primary comparisons. Exact API/storage parity stays separate.

accuracy-evidence-v3.4.0 verifies input archives and members, records implementation hashes and requires a new output directory. Original archives and verdicts remain unchanged. No cloud execution is part of this recovery.
