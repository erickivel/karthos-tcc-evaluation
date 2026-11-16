"""Bounded analysis projection; original report bytes remain authoritative."""
import hashlib
import ijson
from ijson.common import ObjectBuilder

OMITTED_SUFFIX='.matching_references'
OMITTED={'traffic.report.availability.observations','selected_accuracy.item.checks'}

class HashedReader:
 def __init__(self,stream):self.stream=stream;self.digest=hashlib.sha256();self.size=0
 def read(self,size=-1):
  data=self.stream.read(size);self.digest.update(data);self.size+=len(data);return data

def read_analysis_report(stream,expected_sha256,*,open_member=None,input_members=None):
 reader=HashedReader(stream);builder=ObjectBuilder();skipped=None
 for prefix,event,value in ijson.parse(reader,use_float=True):
  if skipped is not None:
   if prefix==skipped or prefix.startswith(skipped+'.'):continue
   skipped=None
  if event=='map_key':
   key=prefix+'.'+value if prefix else value
   if key in OMITTED or key.endswith(OMITTED_SUFFIX):skipped=key;continue
  builder.event(event,value)
 if reader.digest.hexdigest()!=expected_sha256:raise ValueError('report member checksum differs')
 result=builder.value
 if 'compact_report' in result:
  from .compact_report import expand_report
  result=expand_report(result,open_member,projection=True,input_members=input_members)
 return result,reader.size
