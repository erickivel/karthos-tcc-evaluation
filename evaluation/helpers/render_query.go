// Evaluation-only bridge to the deployed revision's production query compiler.
package main

import (
 "encoding/json"
 "fmt"
 "os"
 "time"

 "github.com/erickivel/karthos/pkg/backend/query"
 "github.com/erickivel/karthos/pkg/core/metrics"
 vm "github.com/erickivel/karthos/pkg/adapters/storage/victoriametrics"
)

func main() {
 var p map[string]string
 must(json.NewDecoder(os.Stdin).Decode(&p))
 q:=query.MetricQuery{MetricName:p["metric_name"],Source:p["source"]}
 var err error
 q.From,err=time.Parse(time.RFC3339Nano,p["from"]);must(err)
 q.To,err=time.Parse(time.RFC3339Nano,p["to"]);must(err)
 metric,ok:=metrics.CatalogByName()[q.MetricName];if !ok {panic("unknown metric")}
 a,err:=metrics.ResolveAggregation(metric,p["aggregation"],p["window"]);must(err)
 q.Aggregation,q.Window,q.Unit=a.Mode,a.Window,a.Unit
 resolved,err:=query.ResolveMetricQuery(q,q.To);must(err)
 must(json.NewEncoder(os.Stdout).Encode(map[string]any{
  "unit":a.Unit,"expression":vm.RenderQuery(resolved.MetricQuery),"evaluation_points":resolved.EvaluationPoints(),
  "from":resolved.From,"to":resolved.To,"step_seconds":resolved.Step.Seconds(),
  "max_sample_age_seconds":resolved.MaxSampleAge.Seconds(),"aggregation":a.Mode,"window_seconds":a.Window.Seconds(),
 }))
}
func must(err error){if err!=nil{fmt.Fprintln(os.Stderr,err);os.Exit(1)}}
