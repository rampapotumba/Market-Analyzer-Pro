"""Central bank policy-rate collector.

Data sources (all free, no authentication required unless noted):
  FED   — FRED API (requires FRED_KEY)
  ECB   — ECB Data Warehouse REST API (public)
  BOJ   — BOJ API (public)
  BOE   — Bank of England API (public)
  RBA   — RBA CSV (public)
  BOC   — Bank of Canada Valet API (public)
  SNB   — SNB Data Portal (public)
  RBNZ  — RBNZ API (public)

Each bank's collector method is independently fault-tolerant; if one fails the
others still run.  Rates are stored in the `central_bank_rates` table via
`upsert_central_bank_rate` in crud.py.
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx

from src.cache import cache
from src.collectors.base import BaseCollector, CollectorResult
from src.config import settings
from src.database.engine import async_session_factory
from src.database.models import CentralBankRate

logger = logging.getLogger(__name__)

# Cache TTL for central bank rates (6 hours)
_CACHE_TTL = 6 * 3600

# Bank metadata: (name, currency, description)
_BANKS = {
    "FED": ("USD", "Federal Reserve"),
    "ECB": ("EUR", "European Central Bank"),
    "BOJ": ("JPY", "Bank of Japan"),
    "BOE": ("GBP", "Bank of England"),
    "RBA": ("AUD", "Reserve Bank of Australia"),
    "BOC": ("CAD", "Bank of Canada"),
    "SNB": ("CHF", "Swiss National Bank"),
    "RBNZ": ("NZD", "Reserve Bank of New Zealand"),
}


class CentralBankCollector(BaseCollector):
    """Collects and persists current policy rates from major central banks."""

    def __init__(self) -> None:
        super().__init__("CentralBankCollector")
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        return self._client

    # ── Public interface ──────────────────────────────────────────────────────

    async def collect(self) -> CollectorResult:
        fetchers = [
            self._fetch_fed,
            self._fetch_ecb,
            self._fetch_boj,
            self._fetch_boe,
            self._fetch_rba,
            self._fetch_boc,
            self._fetch_snb,
            self._fetch_rbnz,
        ]
        rates: list[dict] = []
        errors: list[str] = []

        for fetcher in fetchers:
            try:
                result = await fetcher()
                if result:
                    rates.append(result)
            except Exception as exc:
                errors.append(f"{fetcher.__name__}: {exc}")
                logger.warning("CentralBankCollector: %s failed: %s", fetcher.__name__, exc)

        if rates:
            await self._persist(rates)

        if self._client and not self._client.is_closed:
            await self._client.aclose()

        success = len(rates) > 0
        return CollectorResult(
            success=success,
            data=rates,
            records_count=len(rates),
            error="; ".join(errors) if errors else None,
        )

    async def health_check(self) -> bool:
        try:
            client = await self._http()
            resp = await client.get("https://api.stlouisfed.org/", timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False

    # ── FED ───────────────────────────────────────────────────────────────────

    async def _fetch_fed(self) -> Optional[dict]:
        """Federal Funds Rate via FRED (series FEDFUNDS)."""
        cache_key = "cbr:FED"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        if not settings.FRED_KEY:
            logger.debug("FRED_KEY not set — skipping FED rate")
            return None

        client = await self._http()
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=FEDFUNDS&api_key={settings.FRED_KEY}"
            "&file_type=json&sort_order=desc&limit=1"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations", [])
        if not obs:
            return None

        rate_val = obs[0].get("value", ".")
        if rate_val == ".":
            return None

        result = {
            "bank": "FED",
            "currency": "USD",
            "rate": float(rate_val),
            "effective_date": obs[0].get("date"),
            "bias": None,
            "source": "FRED/FEDFUNDS",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── ECB ───────────────────────────────────────────────────────────────────

    async def _fetch_ecb(self) -> Optional[dict]:
        """ECB Main Refinancing Rate via ECB Data Warehouse REST API."""
        cache_key = "cbr:ECB"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        # Key interest rates: MRO (Main refinancing operations, fixed rate)
        url = (
            "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.MRR_FR.LEV"
            "?format=jsondata&lastNObservations=1"
        )
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()

        # Navigate ECB SDMX-JSON structure
        try:
            series = data["dataSets"][0]["series"]
            key = list(series.keys())[0]
            observations = series[key]["observations"]
            # Get the last observation value
            last_obs_key = max(observations.keys(), key=int)
            rate_val = observations[last_obs_key][0]

            # Get the date from the time dimension
            time_dim = data["structure"]["dimensions"]["observation"][0]["values"]
            date_str = time_dim[int(last_obs_key)]["id"]  # e.g. "2024-09"
            # Approximate to first of month
            effective_date = date_str + "-01" if len(date_str) == 7 else date_str
        except (KeyError, IndexError, ValueError) as exc:
            logger.debug("ECB rate parse error: %s", exc)
            return None

        result = {
            "bank": "ECB",
            "currency": "EUR",
            "rate": float(rate_val),
            "effective_date": effective_date,
            "bias": None,
            "source": "ECB/MRR_FR",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── BOJ ───────────────────────────────────────────────────────────────────

    async def _fetch_boj(self) -> Optional[dict]:
        """Bank of Japan Uncollateralized Overnight Call Rate via BOJ API."""
        cache_key = "cbr:BOJ"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        # BOJ time-series: IR01 series (Policy rate)
        url = (
            "https://www.stat-search.boj.or.jp/ssi/mtshtml/ir01_m_1_en.csv"
        )
        resp = await client.get(url)
        resp.raise_for_status()

        # Parse CSV: skip header rows, last data row is most recent
        lines = [l.strip() for l in resp.text.splitlines() if l.strip()]
        # Find data rows (start with a year like "20" + digits)
        data_rows = [l for l in lines if l[:2].isdigit() and len(l) > 5]
        if not data_rows:
            return None

        last = data_rows[-1].split(",")
        if len(last) < 2:
            return None

        try:
            rate_val = float(last[1].strip().replace('"', ''))
            date_str = last[0].strip().replace('"', '')  # YYYY/MM or similar
        except (ValueError, IndexError):
            return None

        result = {
            "bank": "BOJ",
            "currency": "JPY",
            "rate": rate_val,
            "effective_date": date_str,
            "bias": None,
            "source": "BOJ/IR01",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── BOE ───────────────────────────────────────────────────────────────────

    async def _fetch_boe(self) -> Optional[dict]:
        """Bank of England Bank Rate via BOE Statistical Interactive Dataset API."""
        cache_key = "cbr:BOE"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        url = (
            "https://www.bankofengland.co.uk/boeapps/database/fromshowcolumns.asp"
            "?Travel=NIxSTxSUx&FromSeries=1&ToSeries=50&DAT=RNG"
            "&FD=1&FM=Jan&FY=2024&TD=31&TM=Dec&TY=9999"
            "&VFD=Y&html.x=66&html.y=26&C=BYR&Filter=N"
        )
        # BOE returns HTML — use a simpler JSON endpoint
        url_json = (
            "https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp"
            "?csv.x=yes&Datefrom=01/Jan/2024&Dateto=now&SeriesCodes=IUMABEDR&CSVF=TT&UsingCodes=Y"
        )
        resp = await client.get(url_json)
        resp.raise_for_status()

        lines = [l for l in resp.text.splitlines() if l.strip() and not l.startswith("Date")]
        if not lines:
            return None

        last = lines[-1].split(",")
        if len(last) < 2:
            return None

        try:
            date_str = last[0].strip().strip('"')
            rate_val = float(last[1].strip().strip('"'))
        except (ValueError, IndexError):
            return None

        result = {
            "bank": "BOE",
            "currency": "GBP",
            "rate": rate_val,
            "effective_date": date_str,
            "bias": None,
            "source": "BOE/IUMABEDR",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── RBA ───────────────────────────────────────────────────────────────────

    async def _fetch_rba(self) -> Optional[dict]:
        """Reserve Bank of Australia Cash Rate via RBA public CSV."""
        cache_key = "cbr:RBA"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        url = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"
        resp = await client.get(url)
        resp.raise_for_status()

        # F1 table: Cash Rate Target is typically the first series
        lines = resp.text.splitlines()
        # Find data rows (lines starting with a date like "Jan")
        data_rows = [l for l in lines if l and (l[0].isalpha() or l[:4].isdigit())]
        # Look for lines containing rate data (two-column format: date, rate)
        rate_rows = []
        for line in data_rows:
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    float(parts[1].strip())
                    rate_rows.append(parts)
                except (ValueError, IndexError):
                    continue

        if not rate_rows:
            return None

        last = rate_rows[-1]
        try:
            rate_val = float(last[1].strip())
            date_str = last[0].strip()
        except (ValueError, IndexError):
            return None

        result = {
            "bank": "RBA",
            "currency": "AUD",
            "rate": rate_val,
            "effective_date": date_str,
            "bias": None,
            "source": "RBA/F1",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── BOC ───────────────────────────────────────────────────────────────────

    async def _fetch_boc(self) -> Optional[dict]:
        """Bank of Canada Overnight Rate via Valet API."""
        cache_key = "cbr:BOC"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        url = (
            "https://www.bankofcanada.ca/valet/observations/V39079/json"
            "?recent=1"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        try:
            obs = data["observations"]
            if not obs:
                return None
            last = obs[-1]
            rate_val = float(last["V39079"]["v"])
            date_str = last["d"]
        except (KeyError, ValueError, IndexError):
            return None

        result = {
            "bank": "BOC",
            "currency": "CAD",
            "rate": rate_val,
            "effective_date": date_str,
            "bias": None,
            "source": "BOC/Valet/V39079",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── SNB ───────────────────────────────────────────────────────────────────

    async def _fetch_snb(self) -> Optional[dict]:
        """Swiss National Bank Policy Rate via SNB Data Portal."""
        cache_key = "cbr:SNB"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        url = (
            "https://data.snb.ch/api/cube/snboffzisa/data/json"
            "?lastNObs=1&lang=en"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        try:
            series = data["data"]["dataSets"][0]["series"]
            key = list(series.keys())[0]
            obs = series[key]["observations"]
            last_key = max(obs.keys(), key=int)
            rate_val = float(obs[last_key][0])
            time_vals = data["data"]["structure"]["dimensions"]["observation"][0]["values"]
            date_str = time_vals[int(last_key)]["name"]
        except (KeyError, ValueError, IndexError):
            return None

        result = {
            "bank": "SNB",
            "currency": "CHF",
            "rate": rate_val,
            "effective_date": date_str,
            "bias": None,
            "source": "SNB/SNBOFFZISA",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── RBNZ ─────────────────────────────────────────────────────────────────

    async def _fetch_rbnz(self) -> Optional[dict]:
        """Reserve Bank of New Zealand Official Cash Rate via RBNZ API."""
        cache_key = "cbr:RBNZ"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        client = await self._http()
        url = (
            "https://www.rbnz.govt.nz/api/BoP/Statistics/Key_Graph_Data"
            "?$format=json"
        )
        # Fallback to a simpler endpoint
        url_series = (
            "https://www.rbnz.govt.nz/-/media/ReserveBank/Files/Statistics/tables/"
            "b2/hb2-monthly.csv"
        )
        try:
            resp = await client.get(url_series, timeout=10.0)
            resp.raise_for_status()
            lines = resp.text.splitlines()
            # Find OCR rows (last numeric row)
            data_rows = []
            for line in lines:
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        float(parts[-1].strip())
                        data_rows.append(parts)
                    except ValueError:
                        continue
            if not data_rows:
                return None
            last = data_rows[-1]
            rate_val = float(last[-1].strip())
            date_str = last[0].strip()
        except Exception:
            return None

        result = {
            "bank": "RBNZ",
            "currency": "NZD",
            "rate": rate_val,
            "effective_date": date_str,
            "bias": None,
            "source": "RBNZ/HB2",
        }
        await cache.set(cache_key, result, ttl=_CACHE_TTL)
        return result

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _persist(self, rates: list[dict]) -> None:
        """Upsert rate records into central_bank_rates table."""
        from sqlalchemy import select  # noqa: PLC0415
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        async with async_session_factory() as session:
            async with session.begin():
                for r in rates:
                    try:
                        effective_date = _parse_date(r["effective_date"])
                        if effective_date is None:
                            continue

                        stmt = (
                            insert(CentralBankRate)
                            .values(
                                bank=r["bank"],
                                currency=r["currency"],
                                rate=Decimal(str(r["rate"])),
                                effective_date=effective_date,
                                bias=r.get("bias"),
                                source=r.get("source"),
                            )
                            .on_conflict_do_update(
                                constraint="uix_central_bank_rates",
                                set_={
                                    "rate": Decimal(str(r["rate"])),
                                    "bias": r.get("bias"),
                                    "source": r.get("source"),
                                },
                            )
                        )
                        await session.execute(stmt)
                    except Exception as exc:
                        logger.error(
                            "CentralBankCollector: persist failed for %s: %s",
                            r.get("bank"),
                            exc,
                        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(date_str: Optional[str]) -> Optional[datetime.datetime]:
    """Parse various date string formats into UTC-aware datetime."""
    if not date_str:
        return None
    formats = [
        "%Y-%m-%d",
        "%Y-%m",
        "%d/%m/%Y",
        "%b %Y",
        "%Y/%m",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            d = datetime.datetime.strptime(date_str.strip(), fmt)
            return d.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    # Try ISO parse as fallback
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
    except (ValueError, IndexError):
        logger.debug("Cannot parse date string: %r", date_str)
        return None
