"""Score the five public API questions on the pinned product's seeded fixture."""
import argparse
import json
from pathlib import Path

from ..consumers.api_only import APIConsumer, thesis_answers
from ..gke.models import sha, write
from ..gke.thesis import openapi_projection


def evaluate(client, before, after):
    document = thesis_answers(client,before,after,namespace='fixture',
                              service='checkout',container='server',source='mesh.istio')
    expected = {
        'interval-callers':['frontend'],
        'current-replicas-and-placement':[{'pod':'checkout-new','node':'node-new'}],
        'retained-replicas-and-health':[
            [{'pod':'checkout-old','node':'node-old','health':'failed'}],
            [{'pod':'checkout-new','node':'node-new','health':'healthy'}],
        ],
        'container-resource-history':{'container.cpu.usage':[.25,.5],'container.memory.usage':[1048576,2097152]},
        'request-error-derivation':[['mesh.istio','istio.request-errors','1','servicegraph.request.count']],
    }
    answers = document['answers']
    def value(key):
        answer = answers.get(key,{})
        return answer.get('value') if answer.get('state') == 'observed' else None
    old,new = value('before_replicas'),value('after_replicas')
    callers = value('interval-callers')
    resources = value('container-resource-history')
    errors = value('request-error-derivation')
    observed = {
        'interval-callers':sorted({row['caller'] for row in callers}) if callers is not None else None,
        'current-replicas-and-placement':[{k:row[k] for k in ('pod','node')} for row in new] if new is not None else None,
        'retained-replicas-and-health':[old,new] if old is not None and new is not None and document['snapshot_changed'] else None,
        'container-resource-history':{sample['metric_name']:[
            row['value'] for row in sorted(response['samples'],key=lambda s:s['timestamp'])
        ] for response in resources for sample in response['samples'][:1]} if resources is not None else None,
        'request-error-derivation':[list(x) for x in sorted({
            (row['labels']['source'],row['derivation']['id'],str(row['derivation']['version']),row['derivation']['input_metric'])
            for row in errors['samples'] if row.get('derivation')
        })] if errors is not None else None,
    }
    return {'openapi_validated':document['openapi_validated'],'response_evidence':document,'answers':{
        key:{'expected':ref,'observed':observed[key],
             'state':'insufficient' if observed[key] is None or not document['openapi_validated']
                     else 'correct' if observed[key]==ref else 'incorrect'}
        for key,ref in expected.items()
    }}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openapi',type=Path,required=True)
    parser.add_argument('--handshake',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    endpoint=json.loads(args.handshake.read_text())
    client=APIConsumer(endpoint['endpoint'],contract=openapi_projection(args.openapi,sha(args.openapi)),page_size=3)
    report=evaluate(client,endpoint['before'],endpoint['after'])
    write(args.out,report)
    return 0 if all(row['state']=='correct' for row in report['answers'].values()) else 2


if __name__=='__main__':
    raise SystemExit(main())
