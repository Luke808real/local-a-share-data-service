import importlib.util
from pathlib import Path
import pytest

PATH = Path(__file__).resolve().parents[1] / "tools/certify_r3_incremental_publication_v01.py"
spec = importlib.util.spec_from_file_location("cert", PATH)
cert = importlib.util.module_from_spec(spec); spec.loader.exec_module(cert)

def row(code="sz.000001", day="2026-08-18", status="0"):
    return {"symbol":"000001.SZ", "trade_date":"2026-08-18", "requested_code":"sz.000001",
            "error_code":"0", "rows":[{"code":code,"date":day,"tradestatus":status}]}

@pytest.mark.parametrize("value", [row(code="sz.000002"), row(day="2026-08-19"),
                                     {**row(), "error_code":"1"}, {**row(), "rows":[]}, row(status="1")])
def test_provider_results_never_become_suspension_without_exact_evidence(value):
    classified = cert.classify([value])
    with pytest.raises(RuntimeError): cert.validate(classified)

def test_exact_suspension_is_accepted():
    classified = cert.classify([row()])
    cert.validate(classified)
