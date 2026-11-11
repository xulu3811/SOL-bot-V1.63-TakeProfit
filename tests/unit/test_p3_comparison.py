import unittest
from decimal import Decimal
from solbot.mathematics.comparison import synthetic_study,SCHEMES

class ComparisonTests(unittest.TestCase):
    def test_repeatable_four_models_and_no_universal_winner(self):
        first=synthetic_study()
        self.assertEqual(first,synthetic_study())
        for case in first:
            self.assertEqual(set(case['models']),set(SCHEMES))
            for model in case['models'].values():
                stops=[Decimal(row['stop']) for row in model['trajectory']]
                self.assertEqual(stops,sorted(stops))
        case=next(c for c in first if c['name']=='false_exhaustion')
        self.assertLess(Decimal(case['models']['APTS']['net_pnl']),Decimal(case['models']['FIXED_1_0']['net_pnl']))
