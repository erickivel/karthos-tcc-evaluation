"""Operator cleanup honors the watchdog's immutable first UID across paths."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote
from evaluation.gke import provision as p,teardown as t
ROOT=Path(__file__).resolve().parents[2]


class CrossPathUIDTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.name='projects/research-project/zones/us-central1-a/disks/pvc-late'
        self.uid='A';self.values={};self.writes=[];self.commands=[];self.race_uid=None
        self.labels={'evaluation-owner':p.OWNER,'evaluation-run':'run-1'}
        self.ctx=SimpleNamespace(run_id='run-1',work=Path(self.tmp.name),config={'gcp':{'project_id':'research-project','zone':'us-central1-a','region':'us-central1'}},
             state={'owner_id':p.OWNER,'run_id':'run-1','started_at':time.time(),'deadline':time.time()+3600,
                    'resources':[],'terraform':{'foundation':{'evidence_bucket':'evidence'}},'lease':{'token':'a'*32},
                    'creation_intents':[{'kind':'instance','name':'unrelated-active'}], 'pending_resources':[{'name':'untouched'}]})
        self.events=[];self.ctx.event=lambda phase,**kw:self.events.append((phase,json.loads(json.dumps(self.ctx.state))))
        self.ctx.command=self.command
        for target in ['evaluation.gke.provision.assert_project_lease','evaluation.gke.teardown.assert_project_lease']:
            token=patch(target);token.start();self.addCleanup(token.stop)
        spec=importlib.util.spec_from_file_location('watchdog_cross_uid',ROOT/'infrastructure/watchdog/main.py')
        self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod)

    def command(self,args,**kw):
        self.commands.append(args);value={};code=0;stderr=''
        if args[:4]==['gcloud','compute','disks','list']:
            value=[{'id':self.uid,'selfLink':'https://www.googleapis.com/compute/v1/'+self.name,'labels':self.labels}]
        elif args[:4]==['gcloud','compute','disks','describe']:
            value={'id':self.uid,'labels':self.labels,'users':[]}
        elif args[:4]==['gcloud','storage','objects','describe']:
            key=args[4].split('evidence/')[1]
            if key in self.values:value={'generation':'7'}
            else:code=1;stderr='NOT_FOUND'
        elif args[:3]==['gcloud','storage','cat']:
            key=args[3].split('evidence/')[1].split('#')[0];value=self.values[key]
        elif args[:3]==['gcloud','storage','cp']:
            self.assertEqual(args[-1],'--if-generation-match=0')
            key=args[4].split('evidence/')[1];data=json.loads(Path(args[3]).read_text())
            self.writes.append(('operator',data))
            if self.race_uid is not None:
                self.values[key]={**data,'uid':self.race_uid};self.race_uid=None
                code=1;stderr='412 conditionNotMet'
            elif key in self.values:code=1;stderr='412 conditionNotMet'
            else:self.values[key]=data
        else:self.fail('unexpected command '+str(args))
        return SimpleNamespace(stdout=json.dumps(value),returncode=code,stderr=stderr)

    def watchdog_observe(self):
        fixture=self
        class Session:
            def get(self,url,params=None,**kwargs):
                key=unquote(url.split('/o/')[1]);present=key in fixture.values
                value=fixture.values.get(key) if params else {'generation':'7'}
                return SimpleNamespace(status_code=200 if present else 404,json=lambda:value,raise_for_status=lambda:None)
            def post(self,url,params=None,data=None,**kwargs):
                value=json.loads(data);fixture.writes.append(('watchdog',value));fixture.values[params['name']]=value
                return SimpleNamespace(status_code=200,raise_for_status=lambda:None)
        cloud=self.mod.GCPCloud(Session());cloud.verify_lease=lambda _:True
        cloud.get=lambda _:{'uid':self.uid,'owner_id':p.OWNER,'run_id':'run-1'}
        cloud.list_owned_disks=lambda _:[{'kind':'disk','name':self.name,'uid':self.uid}]
        record={'project_id':'research-project','owner_id':p.OWNER,'run_id':'run-1','lease':self.ctx.state['lease']}
        with patch.dict(os.environ,{'INVENTORY_BUCKET':'evidence'}):
            return self.mod.discover_disks(record,cloud,[],dry_run=False)

    def test_watchdog_only_uid_A_operator_replacement_B_never_rebinds(self):
        self.watchdog_observe();self.uid='B'
        with self.assertRaisesRegex(ValueError,'first-observed creation UID was replaced'):
            t.refresh_cleanup_disks(self.ctx)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(next(iter(self.values.values()))['uid'],'A')
        self.assertFalse(any('delete' in args for args in self.commands))
        self.assertEqual(self.ctx.state['creation_intents'],[{'kind':'instance','name':'unrelated-active'}])
        self.assertEqual(self.ctx.state['pending_resources'],[{'name':'untouched'}])

    def test_operator_teardown_cannot_delete_watchdog_only_replacement(self):
        self.watchdog_observe();self.uid='B'
        self.ctx.state['resources']=[{'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','uid':'cluster'}]
        self.ctx.state['creation_intents']=[];self.ctx.state['pending_resources']=[]
        with self.assertRaisesRegex(ValueError,'first-observed creation UID was replaced'):
            t.teardown(self.ctx)
        self.assertFalse(any('delete' in args or '-destroy' in args for args in self.commands))
        self.assertEqual(len(self.writes),1)

    def test_watchdog_same_uid_is_accepted_without_new_write(self):
        self.watchdog_observe()
        resources=t.refresh_cleanup_disks(self.ctx)
        self.assertEqual(resources[0]['uid'],'A');self.assertIn('binding',resources[0])
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.ctx.state['creation_intents'],[{'kind':'instance','name':'unrelated-active'}])

    def test_operator_first_binding_uses_same_intent_as_watchdog(self):
        resources=t.refresh_cleanup_disks(self.ctx)
        self.assertEqual(resources[0]['uid'],'A')
        self.assertEqual(self.writes[0][0],'operator')
        self.watchdog_observe()
        self.assertEqual(len(self.values),1);self.assertEqual(len(self.writes),1)

    def test_absent_binding_CAS_loser_reads_winner_and_refuses_other_uid(self):
        self.race_uid='B'
        with self.assertRaisesRegex(ValueError,'first-observed creation UID was replaced'):
            t.refresh_cleanup_disks(self.ctx)
        self.assertEqual(len(self.writes),1);self.assertEqual(next(iter(self.values.values()))['uid'],'B')
        self.assertFalse(any('delete' in args for args in self.commands))

    def test_absent_binding_CAS_same_uid_winner_is_accepted(self):
        self.race_uid='A'
        resources=t.refresh_cleanup_disks(self.ctx)
        self.assertEqual(resources[0]['uid'],'A');self.assertEqual(len(self.writes),1)
