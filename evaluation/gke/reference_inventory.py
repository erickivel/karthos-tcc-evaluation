"""UID-bound immutable declarations and conservatively timed lifecycle references."""
def inventory_declarations(document,node_uids,member):
    # A sealed Pod object can attest its immutable volume declaration without
    # inventing an API acquisition time. Mutable lifecycle values stay separate.
    items=[pod for pod in document.get('items',[]) if pod.get('metadata',{}).get('uid') and pod.get('metadata',{}).get('resourceVersion') and node_uids.get(pod.get('spec',{}).get('nodeName'))]
    return {'items':items,'node_uids':node_uids,'immutable_spec_declaration':{'member':member,'scope':'Pod UID-bound immutable volume declarations only'}}


def lifecycle_snapshots(rows,cadences,node_uids):
    result=[]
    for row in rows:
        if row.get('event') or not row.get('uid') or not node_uids.get(row.get('node')):continue
        at=row.get('observed_at')
        candidates=[c for c in cadences if type(c.get('observed_at')) in (int,float) and type(c.get('iteration_seconds')) in (int,float) and c['iteration_seconds']>=0 and c['observed_at']-c['iteration_seconds']<=at<=c['observed_at']]
        if len(candidates)!=1:continue
        cadence=candidates[0];status={'phase':row.get('phase'),'containerStatuses':[],'initContainerStatuses':[]}
        for container in row.get('containers',[]):
            kind=container.get('status_kind')
            if kind in ('containerStatuses','initContainerStatuses'):status[kind].append({k:v for k,v in container.items() if k!='status_kind'})
        pod={'metadata':{'namespace':row.get('namespace'),'name':row.get('name'),'uid':row['uid']},'spec':{'nodeName':row.get('node')},'status':status}
        result.append({'items':[pod],'node_uids':node_uids,'acquisition_started_at':cadence['observed_at']-cadence['iteration_seconds'],'acquisition_ended_at':cadence['observed_at'],
                       'reference_timing':'conservative outer diagnostic iteration; not collector cache time','measurement_timestamp':None})
    return result


