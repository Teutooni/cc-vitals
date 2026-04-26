import unittest

import tests  # noqa: F401
import pricing


class Lookup(unittest.TestCase):
    def test_opus_match(self):
        p = pricing.lookup('claude-opus-4-7')
        self.assertEqual(p['input'], 15.0)
        self.assertEqual(p['output'], 75.0)

    def test_sonnet_match(self):
        p = pricing.lookup('claude-sonnet-4-6')
        self.assertEqual(p['input'], 3.0)

    def test_haiku_match(self):
        p = pricing.lookup('claude-haiku-4-5-20251001')
        self.assertEqual(p['input'], 1.0)

    def test_case_insensitive(self):
        p = pricing.lookup('CLAUDE-OPUS-4-7')
        self.assertEqual(p['input'], 15.0)

    def test_unknown_falls_back_to_sonnet(self):
        p = pricing.lookup('claude-mythical-9')
        self.assertEqual(p, pricing._DEFAULT)

    def test_none_falls_back_to_default(self):
        self.assertEqual(pricing.lookup(None), pricing._DEFAULT)
        self.assertEqual(pricing.lookup(''), pricing._DEFAULT)


class AtRiskCost(unittest.TestCase):
    def test_zero_or_negative_is_zero(self):
        self.assertEqual(pricing.at_risk_cost(0, 'claude-sonnet-4'), 0.0)
        self.assertEqual(pricing.at_risk_cost(-100, 'claude-sonnet-4'), 0.0)

    def test_5m_tier_uses_5m_write_price(self):
        # Sonnet: input 3.0, write_5m 3.75, read 0.30 → delta = 6.45 / 1M
        risk = pricing.at_risk_cost(1_000_000, 'claude-sonnet-4', ttl='5m')
        self.assertAlmostEqual(risk, 6.45, places=4)

    def test_1h_tier_uses_1h_write_price(self):
        # Sonnet: input 3.0, write_1h 6.0, read 0.30 → delta = 8.70 / 1M
        risk = pricing.at_risk_cost(1_000_000, 'claude-sonnet-4', ttl='1h')
        self.assertAlmostEqual(risk, 8.70, places=4)

    def test_unknown_ttl_treated_as_5m(self):
        same = pricing.at_risk_cost(500_000, 'claude-opus-4', ttl='5m')
        unknown = pricing.at_risk_cost(500_000, 'claude-opus-4', ttl='invalid')
        self.assertEqual(same, unknown)


if __name__ == '__main__':
    unittest.main()
