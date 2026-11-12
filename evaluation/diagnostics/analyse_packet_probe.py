"""Offline packet diagnostics using the exact Linux decoder, never host timestamp rendering."""
from __future__ import annotations
import argparse,csv,hashlib,json,statistics,struct,subprocess
from collections import Counter,defaultdict
from decimal import Decimal
from pathlib import Path
from evaluation import runner as r
from .packet_capture import NETSHOOT_IMAGE

FIELDS=('frame.number','frame.time_epoch','frame.cap_len','frame.len','sll.ifindex','ip.src','ip.dst',
        'tcp.srcport','tcp.dstport','tcp.stream','tcp.seq_raw','tcp.ack_raw','tcp.len','tcp.flags.fin',
        'tcp.flags.reset','http.request.method','http.request.uri','http.response.code','http.connection')


def summarize_node_closes(rows,pod_ip):
    streams=defaultdict(list);truncated=0
    for row in rows:
        if row.get('frame.cap_len') and int(row['frame.cap_len'])<int(row['frame.len']):truncated+=1
        source=(row.get('ip.src'),row.get('tcp.srcport'))
        destination=(row.get('ip.dst'),row.get('tcp.dstport'))
        if ((source==(pod_ip,'8080') and destination[0]=='127.0.0.6')
            or (destination==(pod_ip,'8080') and source[0]=='127.0.0.6')):
            streams[row['tcp.stream']].append(row)
    closes=[];resets=0
    for stream,events in streams.items():
        last_payload=None;first=None
        for row in events:
            at=int(Decimal(row['frame.time_epoch'])*1_000_000_000)
            if int(row.get('tcp.len') or '0')>0:last_payload=at
            reset=row.get('tcp.flags.reset') in ('1','True');fin=row.get('tcp.flags.fin') in ('1','True')
            resets+=int(reset)
            if first is None and (fin or reset):
                first=dict(stream=stream,frame=row.get('frame.number'),close_time_unix_ns=at,
                           source=[row['ip.src'],row['tcp.srcport']],destination=[row['ip.dst'],row['tcp.dstport']],
                           initiator='application_endpoint' if row['tcp.srcport']=='8080' else 'local_proxy_endpoint',
                           flag='RST' if reset else 'FIN',last_payload_time_unix_ns=last_payload,
                           idle_nanoseconds=None if last_payload is None else at-last_payload)
                closes.append(first)
    idles=[x['idle_nanoseconds']/1e9 for x in closes if x['initiator']=='application_endpoint' and x['idle_nanoseconds'] is not None]
    return dict(local_application_streams=len(streams),closes=closes,reset_frames=resets,truncated_frames=truncated,
                application_first_close_idle_seconds=dict(count=len(idles),minimum=min(idles),median=statistics.median(idles),maximum=max(idles)) if idles else None,
                semantics='First observed FIN/RST direction on same-pod proxy127.0.0.6 to application8080 connections. Idle age is since last observed payload, not a claimed error cause. Header truncation is intentional; it cannot establish payload completeness or network loss.')


def analyse(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if output.exists() or output==source or source in output.parents or output in source.parents:
        raise ValueError('analysis output must be new and outside source evidence')
    status=json.loads((source/'packets/status.json').read_text())
    if not status.get('valid'):raise ValueError('packet evidence is not valid; do not infer complete coverage')
    output.mkdir(parents=True)
    report=dict(source=str(source),decoder_image=NETSHOOT_IMAGE,decoder_scope='Linux userspace decoder; no capture/network capabilities',roles={})
    command=['docker','run','--rm','--network','none','--cap-drop','ALL']
    report['decoder_version']=subprocess.check_output(command+[NETSHOOT_IMAGE,'tshark','--version'],text=True,stderr=subprocess.STDOUT)
    for name in ('frontend','frontend-proxy'):
        folder=source/'packets'/name
        attempts=[json.loads(p.read_text()) for p in folder.glob('export-attempt-*.json')]
        complete=[x['attempt'] for x in attempts if x.get('valid')]
        if len(complete)!=1:raise ValueError('missing or ambiguous successful packet export')
        relative=f'raw-attempt-{complete[0]}/traffic.pcap';pcap=folder/relative
        digest=hashlib.sha256(pcap.read_bytes()).hexdigest()
        if status['artifacts'][name]['files'][relative]['sha256']!=digest:raise ValueError('packet hash differs from closed export')
        header=pcap.read_bytes()[:40]
        if header[:4]!=bytes.fromhex('4d3cb2a1'):raise ValueError('expected little-endian nanosecond pcap')
        sec,fraction,captured,original=struct.unpack('<IIII',header[24:40])
        args=command+['--mount',f'type=bind,source={pcap},target=/input.pcap,readonly',NETSHOOT_IMAGE,
                      'tshark','-n','-r','/input.pcap','-T','fields','-E','header=y','-E','separator=/t','-E','quote=d','-E','occurrence=f']
        for field in FIELDS:args+=['-e',field]
        decoded=output/(name+'.tsv')
        with decoded.open('w') as handle:
            process=subprocess.run(args,stdout=handle,stderr=subprocess.PIPE,text=True)
        (output/(name+'-decoder.stderr')).write_text(process.stderr)
        if process.returncode:raise RuntimeError('Linux packet decoder failed; see retained stderr')
        with decoded.open() as handle:rows=list(csv.DictReader(handle,delimiter='\t'))
        if not rows or int(Decimal(rows[0]['frame.time_epoch'])*1_000_000_000)!=sec*1_000_000_000+fraction:
            raise ValueError('decoder timestamp disagrees with binary pcap header')
        pod=json.loads((folder/'pod-before.json').read_text());pod_ip=pod['status']['podIP']
        role=dict(pcap=str(pcap),sha256=digest,frames=len(rows),first_binary_timestamp_unix_ns=sec*1_000_000_000+fraction,
                  pod_ip=pod_ip,pod_uid=pod['metadata']['uid'],node_closes=summarize_node_closes(rows,pod_ip),
                  http_response_header_frames=dict(Counter(x['http.response.code'] for x in rows if x.get('http.response.code'))))
        report['roles'][name]=role
    report['interpretation']='No error cause is inferred from normal idle closes. Compare failing and passing TCP streams and correlated request IDs before attributing a UC failure. Initial macOS tcpdump rendering lost timestamp precision; use this exact-image analysis.'
    r.write(output/'analysis.json',report);r.checksums(output)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();report=analyse(args.source,args.output)
    print(json.dumps({name:value['node_closes']['application_first_close_idle_seconds'] for name,value in report['roles'].items()}))
if __name__=='__main__':main()
