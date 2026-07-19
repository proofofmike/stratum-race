"""Unit tests for ICMP RTT latency correction in race offsets and winners."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "collector"))

import str_race
from str_race import PoolState, Race, RaceTracker, one_way_seconds


class TestOneWaySeconds:
    def test_none_is_zero(self):
        assert one_way_seconds(None) == 0.0

    def test_negative_is_zero(self):
        assert one_way_seconds(-1.0) == 0.0

    def test_half_rtt_in_seconds(self):
        # 40ms RTT -> 20ms one-way -> 0.02s
        assert one_way_seconds(40.0) == pytest.approx(0.02)


class TestPoolStateRtt:
    def test_record_rtt_tracks_best(self):
        p = PoolState(name="a", host="a.example", port=3333, user="u")
        p.record_rtt(30.0)
        p.record_rtt(12.5)
        p.record_rtt(18.0)
        assert p.rtt_ms() == 12.5
        assert p.rtt_best_ms == 12.5
        assert p.rtt_samples_ms == [30.0, 12.5, 18.0]
        assert p.one_way_s() == pytest.approx(0.00625)

    def test_record_rtt_ignores_negative(self):
        p = PoolState(name="a", host="a.example", port=3333, user="u")
        p.record_rtt(-5.0)
        assert p.rtt_ms() is None
        assert p.rtt_samples_ms == []


class TestRaceRttOffsets:
    def _race(self) -> Race:
        return Race(
            index=1,
            prevhash="ab" * 32,
            first_pool="near",
            first_ts=1000.0,
            first_wall="t0",
            first_epoch=1.0,
            first_utc="u0",
            eligible_at_start={"near", "far"},
        )

    def test_raw_offsets_without_rtt(self):
        race = self._race()
        race.arrivals = {"near": 1000.0, "far": 1000.050}
        race.arrival_rtt_ms = {"near": None, "far": None}
        assert race.raw_arrival_offsets_ms()["near"] == 0.0
        assert race.raw_arrival_offsets_ms()["far"] == pytest.approx(50.0)
        # No RTT samples => corrected offsets match raw rebased to first arrival
        assert race.arrival_offsets_ms()["near"] == 0.0
        assert race.arrival_offsets_ms()["far"] == pytest.approx(50.0)
        assert race.corrected_winner() == "near"

    def test_rtt_correction_can_flip_winner(self):
        """Far pool arrives later on the wire but has larger RTT; after one-way
        subtraction it is the earlier estimated send."""
        race = self._race()
        # near: recv at t+0ms, RTT 2ms => one-way 1ms => send ~ t-1ms
        # far:  recv at t+10ms, RTT 40ms => one-way 20ms => send ~ t-10ms  (wins)
        race.arrivals = {"near": 1000.0, "far": 1000.010}
        race.arrival_rtt_ms = {"near": 2.0, "far": 40.0}
        race.first_pool = "near"

        offsets = race.arrival_offsets_ms()
        assert race.corrected_winner() == "far"
        assert offsets["far"] == 0.0
        assert offsets["near"] == pytest.approx(9.0)  # 1ms vs -10ms => 9ms behind

        raw = race.raw_arrival_offsets_ms()
        assert raw["near"] == 0.0
        assert raw["far"] == pytest.approx(10.0)

    def test_nonempty_offsets_use_same_rtt_snapshot(self):
        race = self._race()
        race.arrivals = {"near": 1000.0, "far": 1000.010}
        race.nonempty_arrivals = {"near": 1000.005, "far": 1000.020}
        race.arrival_rtt_ms = {"near": 2.0, "far": 40.0}
        assert race.nonempty_winner() == "far"
        offsets = race.nonempty_arrival_offsets_ms()
        assert offsets["far"] == 0.0
        assert offsets["near"] > 0.0


class TestRecomputeArrivalStats:
    def test_finalize_recounts_with_corrected_winner(self):
        """Late arrival can flip RTT-corrected winner; recompute fixes provisional wins."""
        pools = {
            "near": PoolState(name="near", host="n", port=1, user="u"),
            "mid": PoolState(name="mid", host="m", port=1, user="u"),
            "far": PoolState(name="far", host="f", port=1, user="u"),
        }
        tracker = RaceTracker()
        race = Race(
            index=1,
            prevhash="cd" * 32,
            first_pool="near",
            first_ts=1000.0,
            first_wall="t0",
            first_epoch=1.0,
            first_utc="u0",
            eligible_at_start={"near", "mid", "far"},
        )
        # Confirm with near+mid only: near is provisional corrected winner.
        race.arrivals = {"near": 1000.0, "mid": 1000.005}
        race.arrival_rtt_ms = {"near": 2.0, "mid": 4.0}
        race.confirmed = True
        race.closed = True
        tracker.all_races.append(race)

        tracker._count_arrival(race, "near", pools)
        tracker._count_arrival(race, "mid", pools)
        assert pools["near"].wins == 1
        assert pools["mid"].wins == 0
        assert pools["far"].wins == 0

        # Late far arrival with large RTT: estimated send is earliest overall.
        race.arrivals["far"] = 1000.010
        race.arrival_rtt_ms["far"] = 40.0
        tracker._count_arrival(race, "far", pools)
        # far is now corrected winner, but near still holds a provisional win.
        assert pools["far"].wins == 1
        assert pools["near"].wins == 1

        tracker.recompute_arrival_stats(pools)
        assert pools["far"].wins == 1
        assert pools["near"].wins == 0
        assert pools["mid"].wins == 0
        assert pools["far"].all_arrival_offsets[0] == 0.0
        assert pools["near"].all_arrival_offsets[0] == pytest.approx(9.0)


class TestPingParser:
    @pytest.mark.asyncio
    async def test_icmp_ping_parses_times(self, monkeypatch):
        class FakeProc:
            def __init__(self):
                self.returncode = 0

            async def communicate(self):
                out = (
                    b"PING h (1.2.3.4): 56 data bytes\n"
                    b"64 bytes from 1.2.3.4: icmp_seq=0 ttl=50 time=12.3 ms\n"
                    b"64 bytes from 1.2.3.4: icmp_seq=1 ttl=50 time=11.1 ms\n"
                )
                return out, b""

        async def fake_create(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(str_race.asyncio, "create_subprocess_exec", fake_create)
        samples = await str_race.icmp_ping_times_ms("example.com", count=2)
        assert samples == [12.3, 11.1]
