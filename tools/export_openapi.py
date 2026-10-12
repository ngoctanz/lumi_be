import json
from pathlib import Path

from lumi_be.app import create_app

output = Path("docs/openapi.json")
output.parent.mkdir(exist_ok=True)
app = create_app()
output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
app.state.mongo_client.close()
print(output)
