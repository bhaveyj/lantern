import pytest

from scripts.audit_dependencies import findings, python_packages


def test_audit_reports_vulnerable_exact_versions_and_incomplete_results(tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("example==1.2.3\n")
    packages = python_packages(lock)
    assert findings(packages, [{"vulns": [{"id": "GHSA-example"}]}]) == ["example==1.2.3: GHSA-example"]
    with pytest.raises(ValueError, match="Incomplete"):
        findings(packages, [])
    assert findings([{"commit": "abc123"}], [{"vulns": [{"id": "OSV-test"}]}]) == ["BoringSSL@abc123: OSV-test"]
    lock.write_text("example>=1\n")
    with pytest.raises(ValueError, match="exact"):
        python_packages(lock)
