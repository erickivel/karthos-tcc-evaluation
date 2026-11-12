// Exercise the pinned topology ingest and source-filtered observation store.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/erickivel/karthos/pkg/backend/ingest"
	"github.com/erickivel/karthos/pkg/backend/persistence"
	"github.com/erickivel/karthos/pkg/core/metrics"
	"github.com/erickivel/karthos/pkg/core/schema"
)

type observation struct {
	Counter  int       `json:"counter"`
	LastSeen time.Time `json:"last_seen"`
	Scraped  time.Time `json:"scrape_at"`
}

func main() {
	rows, err := observe()
	if err == nil {
		err = json.NewEncoder(os.Stdout).Encode(rows)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func observe() ([]observation, error) {
	ctx := context.Background()
	store := persistence.NewInMemoryGraphStore()
	builder := ingest.NewMetricCallsTopologyBuilder("thesis-fixture", nil)
	buffer := ingest.NewBufferedTopologyIngestor(map[schema.SignalType]ingest.TopologyBuilder{schema.SignalMetric: builder}, store, nil, time.Hour)
	defer buffer.Close()
	const source = metrics.SourceMeshIstio
	start := time.Now().UTC().Truncate(time.Second)
	var observations []observation
	for index, count := range []int{0, 2, 2, 4} {
		at := start.Add(time.Duration(index) * 15 * time.Second)
		raw := fmt.Sprintf(`{"scopeMetrics":[{"metrics":[{"name":"istio_requests_total","sum":{"aggregationTemporality":"AGGREGATION_TEMPORALITY_CUMULATIVE","isMonotonic":true,"dataPoints":[{"attributes":[{"key":"client","value":{"stringValue":"frontend"}},{"key":"server","value":{"stringValue":"checkout"}},{"key":"client_namespace","value":{"stringValue":"app"}},{"key":"server_namespace","value":{"stringValue":"app"}},{"key":"karthos.source","value":{"stringValue":"mesh.istio"}}],"timeUnixNano":"%d","asInt":"%d"}]}}]}]}`, at.UnixNano(), count)
		envelope, err := schema.NewRawEnvelope("otlp/grpc", schema.SignalMetric, map[string]string{}, json.RawMessage(raw), at)
		if err != nil {
			return nil, err
		}
		if err := buffer.Ingest(ctx, envelope); err != nil {
			return nil, err
		}
		buffer.Flush()
		rows, err := store.ObservedEdgesInWindow(ctx, start, at, source)
		if err != nil {
			return nil, err
		}
		if index == 0 && len(rows) == 0 {
			continue
		}
		if len(rows) == 0 {
			return nil, fmt.Errorf("selected-source observation absent at %s", at)
		}
		lastSeen := rows[0].LastSeen
		for _, row := range rows {
			if row.ID != rows[0].ID {
				return nil, fmt.Errorf("unexpected second edge identity at %s", at)
			}
			if row.LastSeen.After(lastSeen) {
				lastSeen = row.LastSeen
			}
		}
		other, err := store.ObservedEdgesInWindow(ctx, start, at, metrics.SourceTracesServicegraph)
		if err != nil || len(other) != 0 {
			return nil, fmt.Errorf("unexpected history for the noncontributing source: %v %v", other, err)
		}
		observations = append(observations, observation{Counter: count, LastSeen: lastSeen, Scraped: at})
	}
	return observations, nil
}
