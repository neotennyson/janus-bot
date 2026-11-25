import unittest

from janus import gate
from janus.config import Config
from janus.models import Direction, Horizon, Instrument, Kind, Segment, Signal

CFG = Config({
    "segments": ["FUTURES", "OPTIONS", "COMMODITY"],
    "gate": {"min_score": 0.55, "min_rr": 1.5, "max_sl_atr": 1.5, "min_adx": 20,
             "max_chase_pct": 0.003, "deep_discount_pct": 0.03},
})


def snap(price=100.0, sma50=95.0, sma200=90.0, adx=30.0, rsi=62.0, atr=3.0, macd_hist=1.0):
    return {"price": price, "sma20": 97.0, "sma50": sma50, "sma200": sma200,
            "ema20": 98.0, "rsi14": rsi, "atr14": atr, "adx14": adx,
            "macd_hist": macd_hist, "vol_rel": 1.4}


def make_signal(entry=100.0, sl=97.0, target=110.0, direction=Direction.LONG):
    return Signal(
        source="test", source_id="1", kind=Kind.ENTRY,
        instrument=Instrument(underlying="NIFTY", segment=Segment.FUTURES,
                              direction=direction, kind="FUT"),
        entry_low=entry, entry_high=entry, stop_loss=sl, targets=[target],
        horizon=Horizon.SWING,
    )


class TestGate(unittest.TestCase):
    def test_good_trade_passes(self):
        sig = make_signal()
        res = gate.evaluate(sig, snap(price=99.5), CFG)
        self.assertTrue(res.passed, res.reasons)
        self.assertEqual(res.entry_limit, 99.5)
        self.assertAlmostEqual(res.rr, 3.33, places=1)

    def test_low_rr_rejected(self):
        sig = make_signal(entry=100, sl=99, target=101)
        res = gate.evaluate(sig, snap(price=100), CFG)
        self.assertFalse(res.passed)
        self.assertIn("reward:risk", res.reasons[0])

    def test_chase_rejected(self):
        sig = make_signal()
        res = gate.evaluate(sig, snap(price=101.0), CFG)
        self.assertFalse(res.passed)
        self.assertIn("ran away", res.reasons[0])

    def test_wide_stop_rejected(self):
        sig = make_signal(entry=100, sl=92, target=120)  # 8 pts vs atr 3 = 2.7x
        res = gate.evaluate(sig, snap(price=100, adx=30), CFG)
        self.assertFalse(res.passed)
        self.assertIn("ATR", res.reasons[0])

    def test_chop_rejected(self):
        sig = make_signal()
        res = gate.evaluate(sig, snap(price=99.5, adx=12), CFG)
        self.assertFalse(res.passed)
        self.assertIn("ADX", res.reasons[0])


if __name__ == "__main__":
    unittest.main()
