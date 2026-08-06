import json
from pathlib import Path

from pdf_engine import generate_report

BASE_DIR = Path(__file__).resolve().parent

json_file = BASE_DIR / "input" / "crif_response.json" 
output_file = BASE_DIR / "output" / "crif_report.pdf"

with open(json_file, "r", encoding="utf-8") as f:
    raw_json = json.load(f)

pdf_path = generate_report(
    raw_json=raw_json,
    output_path=output_file,
)

print("PDF Generated Successfully")
print(pdf_path)