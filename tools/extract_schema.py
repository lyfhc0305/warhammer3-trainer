"""Development-only refresh of src/schema.json from the upstream RPFM schema (MIT).

    python tools/extract_schema.py path/to/schema_wh3.ron
    python tools/extract_schema.py --download

Only the tables already listed in schema.json are extracted.
"""
import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen

TARGET = Path(__file__).resolve().parents[1] / 'src/schema.json'
UPSTREAM = 'https://raw.githubusercontent.com/Frodo45127/rpfm-schemas/master/schema_wh3.ron'


def extract(text, tables):
    result = {}
    for table in sorted(tables):
        section = text.split('        "' + table + '": [', 1)[1].split('\n        ],', 1)[0]
        result[table] = {}
        for version, block in re.findall(r'version: (\d+),\s+fields: \[(.*?)\n                \],', section, re.S):
            fields = []
            for part in block.split('                    (')[1:]:
                name = re.search(r'name: "([^"]+)"', part)
                kind = re.search(r'field_type: (\w+)', part)
                default = re.search(r'default_value: Some\("([^"]*)"\)', part)
                fields.append({'name': name[1], 'type': kind[1], 'default': default[1] if default else None})
            result[table][version] = fields
    return result


def main(argv):
    if argv == ['--download']:
        with urlopen(UPSTREAM, timeout=60) as response:
            text = response.read().decode('utf-8')
    elif len(argv) == 1:
        text = Path(argv[0]).read_text(encoding='utf-8')
    else:
        raise SystemExit(__doc__)
    tables = json.loads(TARGET.read_text(encoding='utf-8'))
    result = extract(text, tables)
    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    for table, versions in result.items():
        print(table, {v: len(f) for v, f in versions.items()})


if __name__ == '__main__':
    main(sys.argv[1:])
