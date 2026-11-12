package main
import (
 "encoding/json"
 "os"
 "sort"
 "time"
 "github.com/erickivel/karthos/pkg/collector/spec"
)
func main(){
 p:=spec.CollectionPlan{Gateway:spec.GatewayRequirements{ServiceGraph:&spec.ServiceGraph{LatencyHistogramBuckets:[]string{"100ms","250ms","500ms","1s","5s"}}}}
 before:=append([]string{},p.Gateway.ServiceGraph.LatencyHistogramBuckets...);p.Normalize();after:=p.Gateway.ServiceGraph.LatencyHistogramBuckets
 bounds:=make([]float64,len(after));for i,s:=range after{d,_:=time.ParseDuration(s);bounds[i]=d.Seconds()};i:=sort.SearchFloat64s(bounds,0.8)
 json.NewEncoder(os.Stdout).Encode(map[string]any{"before":before,"after_normalize":after,"increasing_duration_bounds":sort.Float64sAreSorted(bounds),"example_duration_seconds":0.8,"selected_upper_bound_seconds":bounds[i],"correct_upper_bound_seconds":1,"impact":"Histogram bucket assignment; sum/count means are not changed by ordering alone"})
}
