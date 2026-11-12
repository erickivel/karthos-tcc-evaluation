// Serve a declared local fixture through the pinned product's real API.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/erickivel/karthos/pkg/adapters/api"
	"github.com/erickivel/karthos/pkg/backend/persistence"
	"github.com/erickivel/karthos/pkg/core/entities"
	"github.com/erickivel/karthos/pkg/core/metrics"
	"github.com/erickivel/karthos/pkg/core/topology"
	"github.com/erickivel/karthos/pkg/sources"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	if err := fixture(ctx); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func fixture(ctx context.Context) error {
	start := time.Now().UTC().Add(-2 * time.Minute).Truncate(15 * time.Second)
	end := start.Add(time.Minute)
	graph := persistence.NewInMemoryGraphStore()
	store := persistence.NewInMemoryMetricStore(persistence.WithMetricQueryViews(sources.DefaultRegistry().QueryViews))
	node := func(kind entities.EntityKind, name, namespace string) topology.Node {
		id := entities.ComputeEntityID(kind, "thesis-local", namespace, name)
		return topology.Node{ID: id, Kind: kind, Labels: map[string]string{"name": name, "namespace": namespace}}
	}
	caller := node(entities.KindService, "frontend", "fixture")
	checkout := node(entities.KindService, "checkout", "fixture")
	unrelated := node(entities.KindService, "checkout", "other")
	deploy := node(entities.KindWorkload, "checkout", "fixture")
	rs := node(entities.KindWorkload, "checkout-rs", "fixture")
	var samples []metrics.MetricSample
	for index, name := range []string{"old", "new"} {
		at := start.Add(time.Duration(index) * time.Minute)
		pod := node(entities.KindPod, "checkout-"+name, "fixture")
		worker := node(entities.KindNode, "node-"+name, "")
		container := node(entities.KindContainer, "server", "fixture")
		container.ID = entities.ComputeEntityID(entities.KindContainer, "thesis-local", "fixture", "checkout-"+name, "server")
		container.Labels["k8s.pod.name"] = "checkout-" + name
		phase := float64(4)
		if index == 1 {
			phase = 2
		}
		samples = append(samples, metrics.MetricSample{EntityID: pod.ID, MetricName: "k8s.pod.status.phase", Value: phase, Unit: metrics.UnitCount, Timestamp: at, Labels: map[string]string{"source": metrics.SourceKubeCluster}})
		edges := []topology.Edge{
			topology.NewEdge(checkout.ID, deploy.ID, topology.RelExposes, at),
			topology.NewEdge(deploy.ID, rs.ID, topology.RelContains, at),
			topology.NewEdge(rs.ID, pod.ID, topology.RelContains, at),
			topology.NewEdge(pod.ID, container.ID, topology.RelContains, at),
			topology.NewEdge(pod.ID, worker.ID, topology.RelRunsOn, at),
		}
		if err := graph.PutStructuralSnapshot(ctx, topology.Snapshot{CreatedAt: at, Graph: topology.Graph{
			Timestamp: at, Nodes: []topology.Node{caller, checkout, unrelated, deploy, rs, pod, container, worker}, Edges: edges,
		}}); err != nil {
			return err
		}
		if _, err := graph.Materialize(ctx, at); err != nil {
			return err
		}
		if index == 1 {
			for i, value := range []float64{.25, .50} {
				t := end.Add(time.Duration(i-1) * 15 * time.Second)
				labels := map[string]string{"source": metrics.SourceKubelet}
				samples = append(samples,
					metrics.MetricSample{EntityID: container.ID, MetricName: "container.cpu.usage", Value: value, Unit: metrics.UnitCores, Timestamp: t, Labels: labels},
					metrics.MetricSample{EntityID: container.ID, MetricName: "container.memory.usage", Value: float64(i+1) * 1048576, Unit: metrics.UnitBytes, Timestamp: t, Labels: labels})
			}
		}
	}
	call := topology.NewEdge(caller.ID, checkout.ID, topology.RelCalls, start.Add(45*time.Second))
	call.Provenance = []string{metrics.SourceMeshIstio}
	if err := graph.PutObservedEdges(ctx, []topology.Edge{call}); err != nil {
		return err
	}
	for i := 0; i <= 4; i++ {
		at := start.Add(time.Duration(i) * 15 * time.Second)
		samples = append(samples, metrics.MetricSample{EntityID: caller.ID, MetricName: "servicegraph.request.count", Value: float64(i), Unit: metrics.UnitCount, Timestamp: at,
			Labels: map[string]string{"source": metrics.SourceMeshIstio, "edge_client": "frontend", "edge_client_id": string(caller.ID), "edge_server": "checkout", "edge_server_id": string(checkout.ID), "network.protocol.name": "grpc", "rpc.grpc.status_code": "13", "http.response.status_code": "200"}})
	}
	if err := store.Write(ctx, samples); err != nil {
		return err
	}
	server, err := api.NewServer(api.Config{ListenAddress: "127.0.0.1:0", GraphStore: graph, MetricStore: store}, nil)
	if err != nil {
		return err
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"endpoint": "http://" + server.Address(), "before": start, "after": end}); err != nil {
		return err
	}
	return server.Run(ctx)
}
