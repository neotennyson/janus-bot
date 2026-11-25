import unittest

from janus.models import Direction, Horizon, OptionType, Segment
from janus.normalize import from_json, from_text


class TestText(unittest.TestCase):
    def test_option_call(self):
        s = from_text("BUY NIFTY 24500 CE @ 120 SL 90 TGT 160")
        self.assertEqual(s.instrument.segment, Segment.OPTIONS)
        self.assertEqual(s.instrument.underlying, "NIFTY")
        self.assertEqual(s.instrument.strike, 24500.0)
        self.assertEqual(s.instrument.option_type, OptionType.CE)
        self.assertEqual(s.instrument.direction, Direction.LONG)
        self.assertEqual(s.entry_ref, 120.0)
        self.assertEqual(s.stop_loss, 90.0)
        self.assertEqual(s.targets, [160.0])

    def test_futures_short(self):
        s = from_text("SELL BANKNIFTY FUT @ 48000 SL 48200 TGT 47600")
        self.assertEqual(s.instrument.segment, Segment.FUTURES)
        self.assertEqual(s.instrument.direction, Direction.SHORT)
        self.assertEqual(s.entry_ref, 48000.0)
        self.assertEqual(s.targets, [47600.0])

    def test_commodity(self):
        s = from_text("GOLD BUY 72000 SL 71500 TGT 73000")
        self.assertEqual(s.instrument.segment, Segment.COMMODITY)
        self.assertEqual(s.instrument.exchange, "MCX")


class TestJson(unittest.TestCase):
    def test_structured(self):
        sig = from_json({
            "segment": "OPTIONS", "symbol": "NIFTY", "direction": "LONG",
            "kind": "ENTRY", "strike": 25000, "option_type": "PE",
            "entry": 150, "stop_loss": 120, "targets": [180, 220], "horizon": "INTRADAY",
        })
        self.assertEqual(sig.instrument.segment, Segment.OPTIONS)
        self.assertEqual(sig.horizon, Horizon.INTRADAY)
        self.assertEqual(sig.targets, [180.0, 220.0])


if __name__ == "__main__":
    unittest.main()
