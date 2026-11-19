"""Additional BRL budget, independent of historical cumulative USD ceilings."""
import math
from datetime import date
from decimal import Decimal, ROUND_FLOOR

PROFILE = 'boutique-complement-v1'
FULL_PROFILE = 'thesis-robustness-full-v1'
PROFILES = (PROFILE, FULL_PROFILE)


def limits(config):
    budget = config.get('complement_budget', {})
    if (budget.get('currency') != 'BRL' or budget.get('reference_brl') != 800
            or budget.get('ceiling_brl') != 1200):
        raise ValueError('complement requires BRL800 reference and BRL1200 additional ceiling')
    rate, margin, reserve = (budget.get(k) for k in ('brl_per_usd', 'fx_margin', 'cleanup_reserve_brl'))
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (rate, margin, reserve))
            or rate <= 0 or not .05 <= margin <= 1 or not 120 <= reserve < 1200):
        raise ValueError('dated exchange rate, at least 5% FX margin and BRL120 cleanup reserve required')
    try:
        date.fromisoformat(budget['fx_date'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('exchange rate date required') from exc
    if not str(budget.get('fx_source', '')).startswith('https://'):
        raise ValueError('exchange rate source required')
    prior = budget.get('prior_spending_brl', 0)
    if config.get('execution_profile') == FULL_PROFILE or 'prior_spending_brl' in budget:
        basis = budget.get('prior_spending_basis')
        if ('prior_spending_brl' not in budget or type(prior) not in (int, float)
                or not math.isfinite(prior) or not 0 <= prior < 1200-reserve
                or not isinstance(basis, str) or not basis.strip()):
            raise ValueError('explicit prior spending or conservative allowance and documented basis required; cleanup reserve protected')
    remaining = Decimal('1200') - Decimal(str(prior))
    effective = Decimal(str(rate)) * (1 + Decimal(str(margin)))
    usd = lambda value: float((Decimal(str(value))/effective).quantize(Decimal('.01'), rounding=ROUND_FLOOR))
    return dict(gross_limit_usd=usd(remaining), launch_ceiling_usd=usd(remaining-Decimal(str(reserve))),
                effective_brl_per_usd=float(effective), scope='additional BRL1200 envelope less documented prior spending; earlier study spending excluded',
                prior_spending_brl=prior, remaining_brl=float(remaining))


def validate(config):
    expected = limits(config)
    actual = config['limits']
    if any(actual.get(key) != expected[key] for key in ('gross_limit_usd', 'launch_ceiling_usd')):
        raise ValueError('USD limits must match conservative BRL conversion and cleanup reserve')
    seconds, cleanup = actual.get('infrastructure_seconds'), actual.get('cleanup_reserve_seconds')
    maximum_seconds = 86400 if config.get('execution_profile') == FULL_PROFILE else 64800
    if (type(seconds) is not int or type(cleanup) is not int
            or not 1800 <= cleanup < seconds <= maximum_seconds):
        raise ValueError('bounded infrastructure deadline and at least 30 minutes cleanup required')
    return expected
