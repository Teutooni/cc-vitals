import unittest

import tests  # noqa: F401
import pricing


class Lookup(unittest.TestCase):
    def test_opus_legacy_match(self):
        # Opus 4 / 4.1 stay on the high tier.
        p = pricing.lookup('claude-opus-4-1')
        self.assertEqual(p['input'], 15.0)
        self.assertEqual(p['output'], 75.0)

    def test_opus_4_5_plus_match(self):
        # Opus 4.5 / 4.6 / 4.7 dropped to a new lower tier.
        for mid in ('claude-opus-4-5', 'claude-opus-4-6', 'claude-opus-4-7'):
            p = pricing.lookup(mid)
            self.assertEqual(p['input'], 5.0, mid)
            self.assertEqual(p['output'], 25.0, mid)

    def test_sonnet_match(self):
        p = pricing.lookup('claude-sonnet-4-6')
        self.assertEqual(p['input'], 3.0)

    def test_haiku_match(self):
        p = pricing.lookup('claude-haiku-4-5-20251001')
        self.assertEqual(p['input'], 1.0)

    def test_case_insensitive(self):
        p = pricing.lookup('CLAUDE-OPUS-4-7')
        self.assertEqual(p['input'], 5.0)

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
        # Sonnet: write_5m 3.75, read 0.30 → delta = 3.45 / 1M
        risk = pricing.at_risk_cost(1_000_000, 'claude-sonnet-4', ttl='5m')
        self.assertAlmostEqual(risk, 3.45, places=4)

    def test_1h_tier_uses_1h_write_price(self):
        # Sonnet: write_1h 6.0, read 0.30 → delta = 5.70 / 1M
        risk = pricing.at_risk_cost(1_000_000, 'claude-sonnet-4', ttl='1h')
        self.assertAlmostEqual(risk, 5.70, places=4)

    def test_opus_legacy_1h_matches_anthropic_billing(self):
        # Regression for #6: input price must NOT enter the formula.
        # Opus 4 / 4.1: write_1h 30.0, read 1.5 → delta = 28.5 / 1M
        risk = pricing.at_risk_cost(1_000_000, 'claude-opus-4-1', ttl='1h')
        self.assertAlmostEqual(risk, 28.5, places=4)

    def test_opus_4_7_1h_matches_anthropic_billing(self):
        # Opus 4.7 (new tier): write_1h 10.0, read 0.50 → delta = 9.5 / 1M
        risk = pricing.at_risk_cost(1_000_000, 'claude-opus-4-7', ttl='1h')
        self.assertAlmostEqual(risk, 9.5, places=4)

    def test_unknown_ttl_treated_as_5m(self):
        same = pricing.at_risk_cost(500_000, 'claude-opus-4', ttl='5m')
        unknown = pricing.at_risk_cost(500_000, 'claude-opus-4', ttl='invalid')
        self.assertEqual(same, unknown)


if __name__ == '__main__':
    unittest.main()
