"""Client-only rendered manifest previews, never an apply or cluster query."""
from __future__ import annotations
import json,subprocess
from pathlib import Path
from .models import read,write,sha
from .render import transform
from ..manifests import demo_values

def previews(root,destination,baseline,config,lock,run_id,*,executor=subprocess.run,chart_snapshot=None,product_snapshot=None):
    from .. import yamlio
    root=Path(root);destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    from types import SimpleNamespace
    from .setup import deployment_lock,plan_command
    image_lock=deployment_lock(SimpleNamespace(manifest={'image_lock':lock},config=config))
    status={'state':'complete','kind':'offline client rendering; not deployment evidence','commands':[],'errors':[],'unresolved_images':[],'objects':{}}
    inputs=[]
    for demo in ('boutique','astronomy'):
        values=destination/(demo+'-values.json');write(values,demo_values(demo,protocol_version=4))
        chart=root/'cache'/('boutique/helm-chart' if demo=='boutique' else 'opentelemetry-demo')
        inputs.append((demo,chart,values,'demo','karthos-eval-'+demo))
    for cell in ['r1-boutique-trace','r1-astronomy-trace','r1-boutique-mesh','r1-astronomy-mesh']:
        historical=Path(baseline)/'measured'/cell
        chart=Path(chart_snapshot) if chart_snapshot else root/'cache/karthos-chart'
        values=historical/'karthos-values.yaml'
        if product_snapshot is not None:
            values=destination/(cell+'-generated-values.yaml')
            command=plan_command(historical/'profile.json',values,destination/(cell+'-resolved-plan.json'),chart,thesis=True)
            status['commands'].append(command)
            try:
                result=executor(command,cwd=product_snapshot,capture_output=True,text=True,timeout=120)
                if result.returncode:
                    raise ValueError(result.stderr[-1500:])
            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                status['errors'].append(cell+' planning: '+str(exc))
                continue
        if values.exists():inputs.append(('karthos-'+cell,chart,values,'karthos','karthos-system'))
    for label,chart,values,mode,namespace in inputs:
        command=['helm','template','karthos' if mode=='karthos' else 'eval-'+label,str(chart),'-n',namespace,'-f',str(values)]
        status['commands'].append(command)
        try:
            result=executor(command,capture_output=True,text=True,timeout=90)
            if result.returncode:raise ValueError(result.stderr[-1500:])
            objects=yamlio.load_all(result.stdout);rendered=[]
            for obj in objects:
                if not obj:continue
                try:rendered+=transform([obj],image_lock,run_id=run_id,mode=mode)
                except ValueError as exc:
                    status['unresolved_images'].append({'preview':label,'kind':obj.get('kind'),'name':obj.get('metadata',{}).get('name'),'reason':str(exc)})
                    obj.setdefault('metadata',{}).setdefault('annotations',{})['evaluation.karthos.dev/preview-only']='unresolved immutable image; NEVER APPLY';rendered.append(obj)
            write(destination/(label+'.yaml'),yamlio.dump_all(rendered));status['objects'][label]=len(rendered)
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:status['errors'].append(label+': '+str(exc))
    status['state']='pending' if status['errors'] or status['unresolved_images'] else 'complete'
    status['prior_profile_limitation']='Karthos previews use recorded local discovery inputs solely to show chart shape; fresh GKE discovery is required at runtime.'
    write(destination/'status.json',status);return status
