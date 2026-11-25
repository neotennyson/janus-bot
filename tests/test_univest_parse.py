import unittest

from janus.models import Direction, OptionType, Segment
from janus.sources.notifications import parse_notification
from janus.univest_parse import signals_from_payload


class TestPayload(unittest.TestCase):
    def test_nested_option_idea(self):
        payload = {"data": {"ideas": [{
            "id": 123, "symbol": "NIFTY", "segment": "OPTIONS", "strike": 25000,
            "option_type": "CE", "entry_price": 120, "stop_loss": 90,
            "targets": [160, 200], "direction": "BUY", "expiry": "2026-09-25",
        }]}}
        sigs = signals_from_payload(payload)
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual(s.instrument.underlying, "NIFTY")
        self.assertEqual(s.instrument.segment, Segment.OPTIONS)
        self.assertEqual(s.instrument.option_type, OptionType.CE)
        self.assertEqual(s.instrument.strike, 25000.0)
        self.assertEqual(s.entry_ref, 120.0)
        self.assertEqual(s.targets, [160.0, 200.0])
        self.assertEqual(s.instrument.direction, Direction.LONG)

    def test_commodity_futures(self):
        payload = {"results": [{"tradingsymbol": "GOLD", "entry": 72000, "sl": 71500,
                                 "target": "73000", "side": "SELL"}]}
        sigs = signals_from_payload(payload)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].instrument.segment, Segment.COMMODITY)
        self.assertEqual(sigs[0].instrument.exchange, "MCX")
        self.assertEqual(sigs[0].instrument.direction, Direction.SHORT)


class TestNotification(unittest.TestCase):
    def test_bare_trigger(self):
        kind, sig = parse_notification("New trade posted! Open the app for details.")
        self.assertEqual(kind, "trigger")
        self.assertIsNone(sig)

    def test_rich_notification(self):
        kind, sig = parse_notification("NIFTY 25000 CE @ 120 SL 90 TGT 160")
        self.assertEqual(kind, "signal")
        self.assertIsNotNone(sig)


if __name__ == "__main__":
    unittest.main()
