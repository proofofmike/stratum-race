import json

from lib.pool_stats import PARSERS, parse_btcpowlab


def test_parse_btcpowlab_pool_summary():
    payload = {
        "economics": {"finder_bp": 8500, "community_bp": 1000, "lab_bp": 500},
        "pool": {
            "hashrate_1h_ths": 79.25,
            "hashrate_5m_ths": 82.5,
            "active_miners": 8,
            "active_workers": 9,
        },
    }

    assert parse_btcpowlab(payload) == {
        "hashrate_value": 79.25e12,
        "hashrate_formatted": "79.25 TH/s",
        "active_users": 8,
        "active_workers": 9,
        "pool_fee": 5.0,
        "miner_types": None,
    }


def test_btcpowlab_parser_is_registered():
    result = PARSERS["btcpowlab"](
        json.dumps({"economics": {"lab_bp": 500}, "pool": {"hashrate_5m_ths": 1}})
    )

    assert result["hashrate_value"] == 1e12
    assert result["pool_fee"] == 5.0
