"""Reviewed survival item catalogue, filtered against the installed game at runtime."""
import json
from pathlib import Path

def script():
    return '// Item identifiers: Mojang Bedrock Samples v1.26.60.5, revision 15701fa9c13923f12662984c32c124327e40e3d3.\n// https://github.com/Mojang/bedrock-samples/tree/v1.26.60.5\nexport const lootCatalogue='+json.dumps(json.loads((Path(__file__).parent/'survival-items.json').read_text(encoding='utf-8'))['items'],separators=(',',':'))+';\n'
