def maturity(controls):
    total = sum(c["weight"] for c in controls)
    verified = sum(c["weight"] for c in controls if c["status"] in {"VERIFIED", "PRODUCTION_PROVEN"})
    blockers = [
        c["id"] for c in controls if c["critical"] and c["status"] in {"FAILED", "EXPIRED", "NOT_EVIDENCED"}
    ]
    score = (verified / total * 100) if total else 0
    return score, blockers


def test_critical_failure_blocks_certification():
    score, blockers = maturity(
        [
            {"id": "SEC-1", "weight": 99, "critical": False, "status": "VERIFIED"},
            {"id": "TENANT-ISO", "weight": 1, "critical": True, "status": "FAILED"},
        ]
    )
    assert score == 99
    assert blockers == ["TENANT-ISO"]
