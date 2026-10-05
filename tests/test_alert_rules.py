"""The alert rules use the platform's own limits. A rule that alerts at a number the code does
not use is a second definition of the same limit, and the two drift apart.

promtool proves the rules fire, in CI. This file proves their numbers."""

import re
from pathlib import Path

from finplat.evaluation import PSI_SIGNIFICANT
from finplat.quality import FRESHNESS_HOURS

RULES = Path(__file__).parent.parent / "deploy" / "prometheus" / "rules.yml"


def expr(alert: str) -> str:
    match = re.search(rf"- alert: {alert}\n(?:\s+#.*\n)*\s+expr: (.+)\n", RULES.read_text(encoding="utf-8"))
    assert match, f"no rule {alert}"
    return match.group(1)


def test_the_drift_rule_alerts_at_the_significant_psi_and_skips_features_that_grow_by_design():
    rule = expr("FinplatDrift")
    assert rule.endswith(f">= {PSI_SIGNIFICANT}")
    assert 'by_design="false"' in rule


def test_the_no_batch_rule_uses_the_freshness_limit():
    assert expr("FinplatNoBatch").endswith(f"> {FRESHNESS_HOURS}")


def test_every_rule_reads_a_metric_the_api_publishes_or_the_scrape_itself():
    from finplat import metrics

    published = set(re.findall(r'Metric\("(finplat_[a-z_]+)"', Path(metrics.__file__).read_text(encoding="utf-8")))
    for alert in ("FinplatDrift", "FinplatNoBatch", "FinplatQualityGate", "FinplatApiDown"):
        names = set(re.findall(r"\b(finplat_[a-z_]+|up)\b", expr(alert))) - {"up"}
        assert names <= published, (alert, names - published)
