import json
from evtx import PyEvtxParser

def parse_evtx_to_json(path: str) -> list[dict]:
    """Parse a single EVTX file into a list of JSON dictionaries."""
    parser = PyEvtxParser(path)
    records = []
    for record in parser.records_json():
        try:
            records.append(json.loads(record['data']))
        except json.JSONDecodeError:
            pass
    return records
