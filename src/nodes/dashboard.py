import json
import logging
from datetime import date
from pathlib import Path

from src.config import REPORTS_PATH
from src.state import AgentState

logger = logging.getLogger(__name__)


async def dashboard_node(state: AgentState) -> dict:
    """Generate an HTML dashboard for the current month's energy data."""
    run_date = state.get("run_date", date.today().isoformat())

    if state.get("error"):
        return {}

    try:
        monthly_daily = state.get("monthly_daily", [])
        monthly_daily_production = state.get("monthly_daily_production", [])
        analysis = json.loads(state.get("analysis_json", "{}"))
        year_month = run_date[:7]

        html = _build_dashboard(
            year_month, monthly_daily, monthly_daily_production, analysis, run_date
        )
        _write_dashboard(year_month, html)
        return {}

    except Exception as exc:
        logger.error("Dashboard generation failed: %s", exc, exc_info=True)
        return {}


def _write_dashboard(year_month: str, html: str) -> None:
    reports_dir = Path(REPORTS_PATH)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"dashboard-{year_month}.html"
    path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s", path)

    # Keep a stable symlink so nginx can serve /monthly-dashboard/ without
    # knowing the current month's filename.
    latest = reports_dir / "latest.html"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(path.name)
    logger.info("Symlink latest.html -> %s", path.name)


def _build_dashboard(
    year_month: str,
    monthly_daily: list[dict],
    monthly_daily_production: list[dict],
    analysis: dict,
    run_date: str,
) -> str:
    # --- Consumption aggregates ---
    total_kwh = sum(r["total_kwh"] or 0 for r in monthly_daily)
    total_cost = sum(r["total_cost"] or 0 for r in monthly_daily)
    days_with_data = len([r for r in monthly_daily if r.get("total_kwh") is not None])
    avg_daily_kwh = total_kwh / days_with_data if days_with_data else 0
    avg_price_month = (
        sum(r["avg_price"] or 0 for r in monthly_daily) / days_with_data
        if days_with_data else 0
    )

    # --- Production aggregates ---
    total_prod_kwh = sum(r["total_kwh"] or 0 for r in monthly_daily_production)
    total_profit = sum(r["total_profit"] or 0 for r in monthly_daily_production)
    days_with_prod = len([r for r in monthly_daily_production if r.get("total_kwh") is not None])
    avg_daily_prod = total_prod_kwh / days_with_prod if days_with_prod else 0
    has_production = days_with_prod > 0

    # Net consumption (consumption minus self-produced)
    net_kwh = total_kwh - total_prod_kwh

    # --- Build lookup maps by day for aligned JS arrays ---
    # Use all days present in either dataset
    all_days = sorted(
        set(r["day"] for r in monthly_daily) | set(r["day"] for r in monthly_daily_production)
    )
    cons_by_day = {r["day"]: r for r in monthly_daily}
    prod_by_day = {r["day"]: r for r in monthly_daily_production}

    def _safe(mapping, day, key, default=0):
        row = mapping.get(day)
        if row is None:
            return default
        v = row.get(key)
        return v if v is not None else default

    days_labels = json.dumps([d[8:] for d in all_days])  # DD
    kwh_js     = json.dumps([round(_safe(cons_by_day, d, "total_kwh"), 3) for d in all_days])
    cost_js    = json.dumps([round(_safe(cons_by_day, d, "total_cost"), 2) for d in all_days])
    price_js   = json.dumps([round(_safe(cons_by_day, d, "avg_price"), 4) for d in all_days])
    prod_js    = json.dumps([round(_safe(prod_by_day, d, "total_kwh"), 3) for d in all_days])
    profit_js  = json.dumps([round(_safe(prod_by_day, d, "total_profit"), 2) for d in all_days])

    # Today's analysis extras
    today_avg_price = analysis.get("today_avg_price")
    today_avg_price_str = (
        f"{float(today_avg_price):.4f}" if today_avg_price is not None else "N/A"
    )
    price_level = analysis.get("price_level", "N/A")
    price_percentile = analysis.get("price_percentile", "N/A")

    # Conditionally render production section
    prod_cards = ""
    if has_production:
        prod_cards = f"""
  <div class="card">
    <div class="label">Total production</div>
    <div class="value green">{total_prod_kwh:.1f}</div>
    <div class="sub">kWh this month</div>
  </div>
  <div class="card">
    <div class="label">Total profit</div>
    <div class="value green">{total_profit:.0f}</div>
    <div class="sub">SEK this month</div>
  </div>
  <div class="card">
    <div class="label">Avg daily production</div>
    <div class="value green">{avg_daily_prod:.2f}</div>
    <div class="sub">kWh / day</div>
  </div>
  <div class="card">
    <div class="label">Net consumption</div>
    <div class="value accent">{net_kwh:.1f}</div>
    <div class="sub">kWh (cons − prod)</div>
  </div>"""

    prod_charts = ""
    if has_production:
        prod_charts = """
  <div class="chart-box">
    <h2>Daily Production (kWh)</h2>
    <canvas id="cProduction" height="220"></canvas>
  </div>
  <div class="chart-box">
    <h2>Consumption vs Production</h2>
    <canvas id="cConsProd" height="220"></canvas>
  </div>"""

    prod_js_code = ""
    if has_production:
        prod_js_code = f"""
// Daily production bar chart
new Chart(document.getElementById('cProduction'), {{
  type: 'bar',
  data: {{
    labels: DAYS,
    datasets: [{{
      label: 'kWh produced',
      data: PROD,
      backgroundColor: 'rgba(52,211,153,0.75)',
      borderColor: '#34d399',
      borderWidth: 1,
      borderRadius: 3,
    }}],
  }},
  options: baseOpts('Daily Production'),
}});

// Consumption vs Production grouped bar
new Chart(document.getElementById('cConsProd'), {{
  type: 'bar',
  data: {{
    labels: DAYS,
    datasets: [
      {{
        label: 'Consumption kWh',
        data: KWH,
        backgroundColor: 'rgba(79,142,247,0.75)',
        borderColor: '#4f8ef7',
        borderWidth: 1,
        borderRadius: 3,
      }},
      {{
        label: 'Production kWh',
        data: PROD,
        backgroundColor: 'rgba(52,211,153,0.75)',
        borderColor: '#34d399',
        borderWidth: 1,
        borderRadius: 3,
      }},
    ],
  }},
  options: {{
    ...baseOpts('Consumption vs Production'),
    plugins: {{
      ...baseOpts('').plugins,
      legend: {{ display: true, labels: {{ color: '#94a3b8', boxWidth: 14, padding: 14 }} }},
    }},
  }},
}});
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Elis Energy Dashboard — {year_month}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2a2e3e;
    --accent: #4f8ef7;
    --green: #34d399;
    --yellow: #fbbf24;
    --red: #f87171;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --font: 'Segoe UI', system-ui, sans-serif;
  }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    min-height: 100vh;
    padding: 1.5rem;
  }}

  header {{
    display: flex;
    align-items: baseline;
    gap: 1rem;
    margin-bottom: 2rem;
  }}

  header h1 {{ font-size: 1.6rem; font-weight: 700; }}
  header span {{ color: var(--muted); font-size: 0.9rem; }}

  .section-label {{
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin: 1.5rem 0 0.6rem;
  }}

  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 1rem;
    margin-bottom: 0.5rem;
  }}

  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.1rem 1.3rem;
  }}

  .card .label {{
    font-size: 0.75rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.4rem;
  }}

  .card .value {{
    font-size: 1.7rem;
    font-weight: 700;
    line-height: 1;
  }}

  .card .sub {{
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 0.3rem;
  }}

  .accent {{ color: var(--accent); }}
  .green  {{ color: var(--green); }}
  .yellow {{ color: var(--yellow); }}
  .red    {{ color: var(--red); }}

  .charts {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin-top: 1.5rem;
    margin-bottom: 1rem;
  }}

  @media (max-width: 820px) {{
    .charts {{ grid-template-columns: 1fr; }}
  }}

  .chart-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.1rem 1.3rem 0.8rem;
  }}

  .chart-box h2 {{
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.9rem;
  }}

  canvas {{ display: block; width: 100% !important; }}

  footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.75rem;
    margin-top: 2rem;
  }}
</style>
</head>
<body>

<header>
  <h1>Elis Energy Dashboard</h1>
  <span>{year_month} &nbsp;·&nbsp; generated {run_date}</span>
</header>

<div class="section-label">Consumption</div>
<div class="cards">
  <div class="card">
    <div class="label">Total consumption</div>
    <div class="value accent">{total_kwh:.1f}</div>
    <div class="sub">kWh this month</div>
  </div>
  <div class="card">
    <div class="label">Total cost</div>
    <div class="value yellow">{total_cost:.0f}</div>
    <div class="sub">SEK this month</div>
  </div>
  <div class="card">
    <div class="label">Avg daily consumption</div>
    <div class="value accent">{avg_daily_kwh:.2f}</div>
    <div class="sub">kWh / day</div>
  </div>
  <div class="card">
    <div class="label">Avg price (month)</div>
    <div class="value yellow">{avg_price_month:.4f}</div>
    <div class="sub">SEK / kWh</div>
  </div>
  <div class="card">
    <div class="label">Today's avg price</div>
    <div class="value accent">{today_avg_price_str}</div>
    <div class="sub">{price_level} · p{price_percentile}</div>
  </div>
  <div class="card">
    <div class="label">Days with data</div>
    <div class="value">{days_with_data}</div>
    <div class="sub">of month so far</div>
  </div>
</div>
{f'<div class="section-label">Production</div><div class="cards">{prod_cards}</div>' if has_production else ''}

<div class="charts">
  <div class="chart-box">
    <h2>Daily Consumption (kWh)</h2>
    <canvas id="cConsumption" height="220"></canvas>
  </div>
  <div class="chart-box">
    <h2>Daily Cost vs Profit (SEK)</h2>
    <canvas id="cCost" height="220"></canvas>
  </div>
  <div class="chart-box">
    <h2>Avg Price per Day (SEK/kWh)</h2>
    <canvas id="cPrice" height="220"></canvas>
  </div>
  <div class="chart-box">
    <h2>Consumption vs Cost</h2>
    <canvas id="cDual" height="220"></canvas>
  </div>
  {prod_charts}
</div>

<footer>Generated by Elis Agent · {run_date}</footer>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script>
const DAYS   = {days_labels};
const KWH    = {kwh_js};
const COST   = {cost_js};
const PRICE  = {price_js};
const PROD   = {prod_js};
const PROFIT = {profit_js};

const gridColor = 'rgba(255,255,255,0.06)';
const tickColor = '#94a3b8';
const tooltipBg = '#1e2130';

Chart.defaults.color = tickColor;
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 11;

function baseOpts(title) {{
  return {{
    responsive: true,
    maintainAspectRatio: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: tooltipBg,
        borderColor: '#2a2e3e',
        borderWidth: 1,
        padding: 10,
        titleFont: {{ weight: '600' }},
      }},
    }},
    scales: {{
      x: {{ grid: {{ color: gridColor }}, ticks: {{ maxRotation: 0 }} }},
      y: {{ grid: {{ color: gridColor }}, beginAtZero: true }},
    }},
  }};
}}

// 1 — Daily consumption
new Chart(document.getElementById('cConsumption'), {{
  type: 'bar',
  data: {{
    labels: DAYS,
    datasets: [{{
      label: 'kWh',
      data: KWH,
      backgroundColor: 'rgba(79,142,247,0.75)',
      borderColor: '#4f8ef7',
      borderWidth: 1,
      borderRadius: 3,
    }}],
  }},
  options: baseOpts('Daily Consumption'),
}});

// 2 — Daily cost vs profit
new Chart(document.getElementById('cCost'), {{
  type: 'bar',
  data: {{
    labels: DAYS,
    datasets: [
      {{
        label: 'Cost SEK',
        data: COST,
        backgroundColor: 'rgba(251,191,36,0.75)',
        borderColor: '#fbbf24',
        borderWidth: 1,
        borderRadius: 3,
      }},
      {{
        label: 'Profit SEK',
        data: PROFIT,
        backgroundColor: 'rgba(52,211,153,0.75)',
        borderColor: '#34d399',
        borderWidth: 1,
        borderRadius: 3,
      }},
    ],
  }},
  options: {{
    ...baseOpts('Daily Cost vs Profit'),
    plugins: {{
      ...baseOpts('').plugins,
      legend: {{ display: true, labels: {{ color: '#94a3b8', boxWidth: 14, padding: 14 }} }},
    }},
  }},
}});

// 3 — Avg price line
new Chart(document.getElementById('cPrice'), {{
  type: 'line',
  data: {{
    labels: DAYS,
    datasets: [{{
      label: 'SEK/kWh',
      data: PRICE,
      borderColor: '#34d399',
      backgroundColor: 'rgba(52,211,153,0.12)',
      borderWidth: 2,
      pointRadius: 3,
      fill: true,
      tension: 0.3,
    }}],
  }},
  options: baseOpts('Avg Price'),
}});

// 4 — Dual axis: consumption + cost
new Chart(document.getElementById('cDual'), {{
  type: 'bar',
  data: {{
    labels: DAYS,
    datasets: [
      {{
        type: 'bar',
        label: 'kWh',
        data: KWH,
        backgroundColor: 'rgba(79,142,247,0.6)',
        borderColor: '#4f8ef7',
        borderWidth: 1,
        borderRadius: 3,
        yAxisID: 'yKwh',
      }},
      {{
        type: 'line',
        label: 'Cost SEK',
        data: COST,
        borderColor: '#fbbf24',
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.3,
        yAxisID: 'yCost',
      }},
    ],
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: true,
    plugins: {{
      legend: {{ display: true, labels: {{ color: tickColor, boxWidth: 14, padding: 14 }} }},
      tooltip: {{ backgroundColor: tooltipBg, borderColor: '#2a2e3e', borderWidth: 1, padding: 10 }},
    }},
    scales: {{
      x:     {{ grid: {{ color: gridColor }}, ticks: {{ maxRotation: 0 }} }},
      yKwh:  {{ grid: {{ color: gridColor }}, beginAtZero: true, position: 'left',  title: {{ display: true, text: 'kWh',  color: '#4f8ef7' }} }},
      yCost: {{ grid: {{ display: false }},   beginAtZero: true, position: 'right', title: {{ display: true, text: 'SEK',  color: '#fbbf24' }} }},
    }},
  }},
}});

{prod_js_code}
</script>
</body>
</html>
"""
