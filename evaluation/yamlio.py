"""Safe YAML shared by capture, previews and evidence export.

The immutable Linux controller supplies hash-locked PyYAML. Local macOS use may
fall back to Ruby/Psych when PyYAML is absent; parsing never enables object
constructors. JSON stays dependency-free and is also a valid YAML document.
"""
import json
import subprocess


def load_all(text):
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        pass
    try:
        import yaml
    except ImportError:
        code='documents=YAML.parse_stream(STDIN.read).children.map { |d| stream=Psych::Nodes::Stream.new; stream.children << d; YAML.safe_load(stream.to_yaml, permitted_classes: [], permitted_symbols: [], aliases: true) }; puts JSON.generate(documents)'
        result=subprocess.run(['ruby','-ryaml','-rjson','-e',code],input=text,text=True,capture_output=True,timeout=30)
        if result.returncode:
            raise ValueError('safe YAML parser failed: '+result.stderr[-500:])
        return json.loads(result.stdout)
    try:
        return list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ValueError('invalid safe YAML: '+str(exc)) from exc


def load_mapping(text):
    documents=load_all(text)
    if len(documents)!=1 or not isinstance(documents[0],dict):
        raise ValueError('expected exactly one YAML mapping')
    return documents[0]


def dump_all(documents):
    try:
        import yaml
    except ImportError:
        return '\n---\n'.join(json.dumps(doc,indent=2) for doc in documents)+'\n'
    return yaml.safe_dump_all(documents,sort_keys=False)
