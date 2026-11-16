"""Temporary controller-only IAP SSH ingress with identity checks and cleanup proof."""
import json,uuid
from .cloud_identity import sanitized_error


def recover(operator, probe, receipt=None):
    instance=operator.instance()
    networks={nic['network'] for nic in instance.get('networkInterfaces',[])}
    accounts={sa['email'] for sa in instance.get('serviceAccounts',[])}
    if len(networks)!=1 or len(accounts)!=1:raise ValueError('exactly one controller network and identity required')
    network=next(iter(networks));account=next(iter(accounts))
    peers=json.loads(operator.call('compute','instances','list','--format=json'))
    targets=[vm for vm in peers if any(nic.get('network')==network for nic in vm.get('networkInterfaces',[]))
             and any(sa.get('email')==account for sa in vm.get('serviceAccounts',[]))]
    if len(targets)!=1 or str(targets[0].get('id'))!=operator.args.uid:
        raise ValueError('controller service account target is shared or unverified in network')
    name='karthos-iap-recovery-'+uuid.uuid4().hex[:20]
    description=json.dumps({'evaluation-owner':operator.args.owner_id,'evaluation-run':operator.args.run_id,
                            'controller_uid':operator.args.uid,'recovery':name},sort_keys=True)
    result={'firewall':name,'project':operator.args.project,'description':description,
            'temporary_removal_confirmed':False}
    def save():
        if receipt:
            receipt.parent.mkdir(parents=True,exist_ok=True)
            receipt.write_text(json.dumps(result,indent=2)+'\n')
    if receipt and receipt.exists():raise ValueError('recovery receipt already exists')
    save()
    try:
        operator.instance()  # Recheck UID immediately before mutation.
        operator.call('compute','firewall-rules','create',name,'--network='+network,
                      '--direction=INGRESS','--allow=tcp:22','--source-ranges=35.235.240.0/20',
                      '--target-service-accounts='+account,'--description='+description,'--format=json','--quiet')
        result['observation']=probe(operator)
    except Exception as exc:
        result['observation_error']=sanitized_error(str(exc))
    finally:
        try:
            rules=json.loads(operator.call('compute','firewall-rules','list','--filter=name='+name,'--format=json'))
            exact=[r for r in rules if r.get('name')==name]
            if exact:
                actual=exact[0]
                if len(exact)!=1 or actual.get('description')!=description:
                    raise ValueError('temporary firewall ownership changed; refusing deletion')
                checked=json.loads(operator.call('compute','firewall-rules','describe',name,'--format=json'))
                if checked.get('id')!=actual.get('id') or checked.get('description')!=description:
                    raise ValueError('temporary firewall UID changed; refusing deletion')
                operator.call('compute','firewall-rules','delete',name,'--quiet')
            remaining=json.loads(operator.call('compute','firewall-rules','list','--filter=name='+name,'--format=json'))
            result['temporary_removal_confirmed']=not any(r.get('name')==name for r in remaining)
            if not result['temporary_removal_confirmed']:result['cleanup_error']='temporary firewall still present'
        except Exception as exc:
            result['cleanup_error']=sanitized_error(str(exc))
        save()
    return result
