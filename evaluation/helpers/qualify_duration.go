// Runs against a dedicated, disposable VictoriaMetrics instance from the pinned product module.
package main

import (
	"context"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"time"

	"github.com/erickivel/karthos/pkg/adapters/storage/victoriametrics"
	"github.com/erickivel/karthos/pkg/backend/query"
	"github.com/erickivel/karthos/pkg/core/entities"
	"github.com/erickivel/karthos/pkg/core/metrics"
)

func main() {
	if err := qualify(os.Getenv("KARTHOS_TEST_VM_URL")); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func qualify(base string) error {
	if base == "" {
		return fmt.Errorf("disposable VictoriaMetrics URL required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	store := victoriametrics.NewVMMetricStore(base)
	defer store.Close()
	const metric = "servicegraph.request.duration"
	end := time.Now().UTC().Add(-5 * time.Minute).Truncate(15 * time.Second)
	entity := entities.EntityID(fmt.Sprintf("thesis-duration-%d", time.Now().UnixNano()))
	sources := []string{metrics.SourceMeshIstio, metrics.SourceTracesServicegraph}
	// Four seconds-based cohorts. The final minute contains durations 0.8,
	// 0.5, 0.7 seconds. The two older observations exercise the query boundary.
	cohorts := [][]float64{{}, {.2}, {.8}, {.8}, {.5, .7}}
	var samples []metrics.MetricSample
	for _, source := range sources {
		labels := map[string]string{"source": source, "edge_client_id": "frontend", "edge_server_id": "checkout"}
		var count, sum float64
		for index, durations := range cohorts {
			for _, duration := range durations {
				count++
				sum += duration
			}
			at := end.Add(time.Duration(index-4) * 30 * time.Second)
			samples = append(samples,
				metrics.MetricSample{EntityID: entity, MetricName: metric + "_sum", Value: sum, Unit: metrics.UnitSeconds, Labels: labels, Timestamp: at},
				metrics.MetricSample{EntityID: entity, MetricName: metric + "_count", Value: count, Unit: metrics.UnitCount, Labels: labels, Timestamp: at})
		}
	}
	if err := store.Write(ctx, samples); err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/internal/force_flush", nil)
	if err != nil {
		return err
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return err
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("flush status %d", response.StatusCode)
	}
	for _, source := range sources {
		rows, err := store.Query(ctx, query.MetricQuery{
			MetricName: metric, EntityIDs: []entities.EntityID{entity}, Source: source,
			From: end, To: end, Step: 15 * time.Second, Window: time.Minute, MaxSampleAge: 45 * time.Second,
			Aggregation: metrics.AggregationWindowMean,
		})
		if err != nil {
			return err
		}
		expected := (cohorts[3][0] + cohorts[4][0] + cohorts[4][1]) / 3
		if len(rows) != 1 || math.Abs(rows[0].Value-expected) > 1e-9 || rows[0].Labels["source"] != source {
			return fmt.Errorf("source %s expected one value %g at %s, got %+v", source, expected, end, rows)
		}
		fmt.Printf("PASS exact-duration %s samples=3 expected_seconds=%.9f observed_seconds=%.9f\n", source, expected, rows[0].Value)
	}
	return nil
}
