import copy
import importlib
import importlib.util
import unittest
from evaluation.capture_watchdog import OWNER, restore_patch, run


def deployment(name):
    return {"metadata":{"name":name,"uid":name+"-uid","resourceVersion":"1","namespace":"karthos-eval-astronomy"},"spec":{"replicas":1,"template":{"metadata":{"labels":{"app":name}},"spec":{"containers":[{"name":name,"image":"app@sha256:abc"}]}}}}


class PacketCaptureTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("evaluation.diagnostics.packet_capture"), "packet capture helper is not implemented")
        return importlib.import_module("evaluation.diagnostics.packet_capture")

    def test_only_bounded_minimal_packet_permissions(self):
        m=self.module();original=deployment("frontend");plan=m.application_plan(original,"owner123",{"metadata":{"name":"dummy","uid":"d"},"data":{}})
        self.assertEqual(original,deployment("frontend"))
        spec=plan["after_workload"]["spec"]["template"]["spec"]
        side=spec["containers"][-1]
        self.assertEqual(side["securityContext"]["capabilities"],{"drop":["ALL"],"add":["NET_RAW"]})
        self.assertFalse(side["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(side["securityContext"]["runAsUser"],0)
        self.assertEqual(spec["volumes"][-1]["emptyDir"]["sizeLimit"],"32Mi")
        self.assertLess(50000*(512+16)+24,m.MAX_BYTES)
        self.assertEqual(side["image"],m.NETSHOOT_IMAGE)
        script=side["command"][-1]
        for token in ["600s","-s 512","-c 50000","--time-stamp-precision=nano","-U","-p","-i any","tcp and (port 8080 or port 15006)"]:
            self.assertIn(token,script)
        self.assertNotIn("NET_ADMIN",str(side))
        self.assertNotIn("-Z",script)

    def test_reject_other_workloads_existing_capture_and_scale(self):
        m=self.module();dummy={"metadata":{"name":"dummy","uid":"d"},"data":{}}
        for obj in [deployment("cart"),dict(deployment("frontend"),spec={**deployment("frontend")["spec"],"replicas":2})]:
            with self.assertRaises(ValueError):m.application_plan(obj,"owner",dummy)
        obj=deployment("frontend");obj["spec"]["template"]["spec"]["containers"].append({"name":"diag-tcpdump"})
        with self.assertRaises(ValueError):m.application_plan(obj,"owner",dummy)

    def test_guarded_restore_refuses_concurrent_change(self):
        m=self.module();p=m.application_plan(deployment("frontend"),"owner",{"metadata":{"name":"dummy","uid":"d"},"data":{}})
        self.assertTrue(restore_patch(p,"workload",p["after_workload"]))
        changed=copy.deepcopy(p["after_workload"]);changed["spec"]["template"]["spec"]["containers"][0]["image"]="new-image"
        with self.assertRaises(ValueError):restore_patch(p,"workload",changed)
        changed=copy.deepcopy(p["after_workload"]);changed["metadata"]["uid"]="replacement"
        with self.assertRaises(ValueError):restore_patch(p,"workload",changed)

    def test_watchdog_rbac_is_exact_and_recovers_after_deadline(self):
        m=self.module();dummy={"metadata":{"name":"dummy","uid":"d"},"data":{}}
        plans=[m.application_plan(deployment(n),"owner",dummy) for n in ("frontend","frontend-proxy")]
        snapshot={"name":"eval-packets-owner","owner":"owner","namespace":"karthos-eval-astronomy","ownership_marker_uid":"marker","cleanup_deadline_epoch":2000,"plans":plans}
        objects=m.watchdog_objects(snapshot,"python@sha256:abc",clock=1000)
        role=next(x for x in objects if x["kind"]=="Role")
        deployments=[x for x in role["rules"] if x["resources"]==["deployments"]][0]
        self.assertEqual(deployments["resourceNames"],["frontend","frontend-proxy"])
        self.assertEqual(deployments["verbs"],["get","patch"])
        self.assertNotIn("*",str(role))
        cm=next(x for x in objects if x["kind"]=="ConfigMap")
        self.assertIn("capture_watchdog.py",cm["data"])
        job=next(x for x in objects if x["kind"]=="Job")
        self.assertEqual(job["spec"]["activeDeadlineSeconds"],1300)
        self.assertEqual(job["spec"]["template"]["metadata"]["annotations"]["sidecar.istio.io/inject"],"false")


    def test_installation_uses_admitted_template_before_watchdog_snapshot(self):
        import tempfile,time,json
        from pathlib import Path
        from types import SimpleNamespace
        m=self.module()
        events=[];state={"deployment/"+n:deployment(n) for n in ("frontend","frontend-proxy")}
        state["configmap/karthos-evaluation-owner"]={"metadata":{"uid":"marker"}}
        def kube(args,**kwargs):
            events.append(args)
            if args[0]=="get":
                if args[1:3]==["namespace",m.NAMESPACE]:
                    return SimpleNamespace(stdout=json.dumps({"metadata":{"labels":{"evaluation.karthos.dev/owner":"karthos-evaluation"}}}))
                key=args[1]
                if key=="pods" and "-l" in args:
                    return SimpleNamespace(stdout=json.dumps({"items":[{"metadata":{"name":"watchdog-pod"}}]}))
                if key=="pods":
                    pods=[]
                    for n in ("frontend","frontend-proxy"):
                        work=state["deployment/"+n]
                        if OWNER in work["spec"]["template"].get("metadata",{}).get("annotations",{}):
                            pods.append({"metadata":{"name":n+"-pod","uid":n+"-pod-uid","annotations":{OWNER:work["spec"]["template"]["metadata"]["annotations"][OWNER]}},"status":{"phase":"Running"}})
                    return SimpleNamespace(stdout=json.dumps({"items":pods}))
                return SimpleNamespace(stdout=json.dumps(state[key]))
            if args[0]=="create":
                obj=json.loads(kwargs["input_text"]);obj["metadata"]["uid"]=obj["metadata"]["name"]+"-uid"
                state[obj["kind"].lower()+"/"+obj["metadata"]["name"]]=obj
            if args[0]=="patch":
                key=args[1];obj=copy.deepcopy(state[key]);patch=json.loads(args[args.index("-p")+1])
                obj["spec"]["template"]=next(x["value"] for x in patch if x["op"]=="replace")
                obj["spec"]["template"]["spec"]["containers"][-1]["imagePullPolicy"]="IfNotPresent"
                if "--dry-run=server" not in args:state[key]=obj
                return SimpleNamespace(stdout=json.dumps(obj))
            return SimpleNamespace(stdout="")
        with tempfile.TemporaryDirectory() as tmp:
            capture=m.PacketCapture(Path(tmp)/"packets",time.time()+900,"python@sha256:abc",kube=kube)
            capture.start()
            for plan in capture.snapshot["plans"]:
                self.assertEqual(plan["after_workload"]["spec"]["template"]["spec"]["containers"][-1]["imagePullPolicy"],"IfNotPresent")
            ready=next(i for i,a in enumerate(events) if a[0]=="wait")
            installs=[i for i,a in enumerate(events) if a[0]=="patch" and "--dry-run=server" not in a]
            self.assertTrue(installs and all(i>ready for i in installs))

    def test_conflicting_cleanup_retains_watchdog(self):
        import tempfile,time
        from pathlib import Path
        from unittest.mock import Mock
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            capture=m.PacketCapture(Path(tmp)/"packets",time.time()+60,"python@sha256:abc")
            capture.path.mkdir();capture.snapshot={"present":True}
            capture._restore=Mock(return_value={"restored":False,"resources":[]})
            capture._delete_owned=Mock()
            result=capture.stop()
            capture._delete_owned.assert_not_called()
            self.assertFalse(result["valid"])
            self.assertIn("guarded restoration refused; watchdog retained",result["errors"])


    def test_cleanup_deletes_watchdog_foreground_before_its_dependencies(self):
        import tempfile,time,json
        from pathlib import Path
        from types import SimpleNamespace
        m=self.module();calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            capture=m.PacketCapture(Path(tmp),time.time()+60,"python@sha256:abc")
            capture.created=[("configmap","guard"),("role","role"),("job","watchdog"),("serviceaccount","sa")]
            def kube(args,**kwargs):
                calls.append(args)
                return SimpleNamespace(stdout=json.dumps({"metadata":{"labels":{OWNER:capture.owner}},"data":{}}))
            capture.kube=kube;capture._delete_owned()
            deleted=[a for a in calls if a[0]=="delete"]
            self.assertEqual(deleted[0][1],"job/watchdog")
            self.assertTrue(all("--cascade=foreground" in a and "--wait=true" in a for a in deleted))


    def test_watchdog_wait_polls_for_pod_creation_before_explicit_ready(self):
        import tempfile,time,json
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        m=self.module();calls=[];polls=0
        def kube(args,**kwargs):
            nonlocal polls
            calls.append(args)
            if args[0]=="get":
                polls+=1
                return SimpleNamespace(stdout=json.dumps({"items":[] if polls==1 else [{"metadata":{"name":"watchdog-created-late"}}]}))
            return SimpleNamespace(stdout="")
        with tempfile.TemporaryDirectory() as tmp:
            capture=m.PacketCapture(Path(tmp),time.time()+300,"python@sha256:abc",kube=kube)
            with patch.object(m.time,"sleep"):
                capture._wait_watchdog()
            self.assertEqual(polls,2)
            wait=calls[-1]
            self.assertEqual(wait[0],"wait")
            self.assertIn("pod/watchdog-created-late",wait)
            self.assertNotIn("-l",wait)


    def test_unowned_namespace_is_rejected_before_any_mutation(self):
        import tempfile,time,json
        from pathlib import Path
        from types import SimpleNamespace
        m=self.module()
        for labels in ({},{"evaluation.karthos.dev/owner":"someone-else"}):
            calls=[]
            def kube(args,**kwargs):
                calls.append(args)
                return SimpleNamespace(stdout=json.dumps({"metadata":{"labels":labels}}))
            with tempfile.TemporaryDirectory() as tmp:
                capture=m.PacketCapture(Path(tmp)/"capture",time.time()+300,"python@sha256:abc",kube=kube)
                with self.assertRaisesRegex(ValueError,"namespace.*ownership"):
                    capture.start()
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][:3],["get","namespace",m.NAMESPACE])
            self.assertFalse(any(a[0] in ("create","patch","delete") for a in calls))


    def test_export_retries_are_bounded_and_preserve_process_failure_details(self):
        import tempfile,time,subprocess,json
        from pathlib import Path
        m=self.module();attempts=[]
        with tempfile.TemporaryDirectory() as tmp:
            capture=m.PacketCapture(Path(tmp),time.time()+300,"python@sha256:abc")
            def fail(name,pod,attempt):
                attempts.append(attempt)
                raise subprocess.CalledProcessError(1,["kubectl","exec"],output="partial stdout",stderr="actual transport stderr")
            capture._export_attempt=fail
            with self.assertRaisesRegex(RuntimeError,"three packet export attempts failed"):
                capture._export("frontend",{})
            self.assertEqual(attempts,[1,2,3])
            for n in attempts:
                row=json.loads((Path(tmp)/"frontend"/("export-attempt-"+str(n)+".json")).read_text())
                self.assertEqual(row["stderr"],"actual transport stderr")
                self.assertEqual(row["stdout"],"partial stdout")


    def test_packet_coverage_marks_count_cap_and_early_finish_incomplete(self):
        m=self.module()
        self.assertFalse(m.coverage_status("50000 packets captured\n",200,190)["valid"])
        self.assertFalse(m.coverage_status("10 packets captured\n",180,190)["valid"])
        self.assertTrue(m.coverage_status("10 packets captured\n",200,190)["valid"])
        self.assertFalse(m.coverage_status("no stats",200,190)["valid"])
        self.assertFalse(m.coverage_status("10 packets captured\n",200,None)["valid"])

if __name__ == "__main__":unittest.main()
