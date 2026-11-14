"""Distinguish capture hop from producer for identical forwarded kubelet points."""
import hashlib
import json
from collections import defaultdict


def _key(observed):
    if observed.get('source')!='kubelet' or not str(observed.get('metric','')).startswith(('k8s.pod.volume.','k8s.volume.')) or not observed.get('raw_point',{}).get('timeUnixNano'):return None
    identity={k:v for k,v in observed.items() if k not in ('collector_uid','value')}
    identity['raw_point']={k:v for k,v in observed['raw_point'].items() if k not in ('asInt','asDouble')}
    return json.dumps(identity,sort_keys=True,separators=(',',':'))


def _value(observed):
    point=observed.get('raw_point',{})
    return observed.get('value'),point.get('asInt'),point.get('asDouble')


def source_index(observations,producer_keys):
    result=defaultdict(list)
    for index,observed in enumerate(observations):
        key=_key(observed)
        if key is not None and (observed.get('collector_uid'),observed.get('node')) in producer_keys:result[key].append((index,observed))
    return result


def attribute(observed,index):
    key=_key(observed);matches=index.get(key,[]) if key is not None else []
    result={'state':'insufficient','reason':'no unique exact Agent counterpart for forwarded volume point','capture_collector_uid':observed.get('collector_uid')}
    producers={row['collector_uid'] for _,row in matches}
    if len(producers)!=1:return result
    if any(_value(row)!=_value(observed) for _,row in matches):return dict(result,state='fail',reason='Agent/Gateway volume point value disagreement; no value selected')
    producer=next(iter(producers));indices=[i for i,_ in matches]
    provenance={'source_observation_indices':indices,'semantic_identity_sha256':hashlib.sha256(key.encode()).hexdigest(),'rule':'exact full raw datapoint identity, with value preservation checked separately'}
    mapped=dict(observed,collector_uid=producer,capture_collector_uid=observed['collector_uid'],producer_attribution=provenance)
    return dict(result,state='matched',reason=None,observed=mapped,source_observation_indices=indices)
