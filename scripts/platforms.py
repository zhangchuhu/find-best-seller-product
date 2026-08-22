"""Marketplace routing, URL canonicalization, and product identity."""

from __future__ import annotations

from enum import Enum
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class Platform(str, Enum):
    MERCADO_MX = "mercado-libre-mx"
    SHEIN_US = "shein-us"


class UnsupportedPlatformError(ValueError):
    """Raised when a host or URL is not a supported marketplace target."""


_TRACKING_KEYS = {
    "ad",
    "adid",
    "ads",
    "affiliate",
    "affiliate_id",
    "aff_id",
    "campaign",
    "campaign_id",
    "campaignid",
    "dclid",
    "fbclid",
    "gbraid",
    "gclid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref",
    "referral",
    "referrer",
    "source",
    "srsltid",
    "ttclid",
    "tracking",
    "tracking_id",
    "twclid",
    "wbraid",
}
_MERCADO_ID_RE = re.compile(r"(?i)(?<![a-z0-9])MLM[-_ ]*(\d+)(?!\d)")
_SHEIN_PATH_ID_RE = re.compile(r"(?i)-p-(\d+)(?:\.html)?(?:/|$)")


def _split_host(value: str):
    if not isinstance(value, str) or not value.strip():
        raise UnsupportedPlatformError("platform value must be a non-empty string")
    text = value.strip()
    return urlsplit(text if "://" in text else f"//{text}")


def route_platform(value: str) -> Platform:
    """Route a URL or hostname using exact, normalized hostname boundaries."""
    parsed = _split_host(value)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if host == "www.mercadolibre.com.mx" or host.endswith(".mercadolibre.com.mx"):
        return Platform.MERCADO_MX
    if host == "us.shein.com":
        return Platform.SHEIN_US
    raise UnsupportedPlatformError(f"unsupported marketplace host: {host or value!r}")


def _is_tracking_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized.startswith(
            (
                "utm_", "ad_", "advertising_", "aff_", "affiliate_",
                "campaign_", "gad_", "ref_", "tracking_",
            )
        )
        or normalized.endswith("clid")
        or normalized in _TRACKING_KEYS
    )


def canonicalize_url(platform: Platform, url: str) -> str:
    """Validate and remove non-identity URL decoration from a product URL."""
    if not isinstance(platform, Platform):
        raise UnsupportedPlatformError("platform must be a Platform")
    if not isinstance(url, str) or not url.strip():
        raise UnsupportedPlatformError("URL must be a non-empty string")
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise UnsupportedPlatformError("product URLs must use https")
    if parsed.username is not None or parsed.password is not None:
        raise UnsupportedPlatformError("credentials are not allowed in product URLs")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsupportedPlatformError("invalid URL port") from exc
    if port not in (None, 443):
        raise UnsupportedPlatformError("unsupported URL port")
    if route_platform(url) is not platform:
        raise UnsupportedPlatformError("URL host does not match the requested platform")

    host = parsed.hostname.lower()
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    )
    return urlunsplit(("https", host, parsed.path or "/", urlencode(query, doseq=True), ""))


def _explicit_product_id(platform: Platform, explicit_id: str) -> str:
    if not isinstance(explicit_id, str) or not explicit_id.strip():
        raise ValueError("explicit product ID must be a non-empty string")
    text = explicit_id.strip()
    if platform is Platform.MERCADO_MX:
        match = re.fullmatch(r"(?i)MLM[-_ ]*(\d(?:[-_ ]*\d)*)", text)
        if match:
            return f"MLM{re.sub(r'[-_ ]', '', match.group(1))}"
    elif platform is Platform.SHEIN_US:
        match = re.fullmatch(r"(?i)(?:(?:goods|product|p)[-_ ]*)?(\d+)", text)
        if match:
            return match.group(1)
    raise ValueError("explicit product ID does not match the platform grammar")


def _url_product_id(platform: Platform, url: str) -> str | None:
    parsed = urlsplit(url)
    if platform is Platform.MERCADO_MX:
        match = _MERCADO_ID_RE.search(parsed.path)
        return f"MLM{match.group(1)}" if match else None
    match = _SHEIN_PATH_ID_RE.search(parsed.path)
    if match:
        return match.group(1)
    for key, value in parse_qsl(parsed.query):
        if key.lower() in {"goods_id", "product_id"} and value.isdigit():
            return value
    return None


def product_identity(platform: Platform, url: str, explicit_id: str | None = None) -> str:
    """Build a stable platform-scoped identity for a product URL."""
    canonical = canonicalize_url(platform, url)
    stable_id = _explicit_product_id(platform, explicit_id) if explicit_id is not None else _url_product_id(platform, canonical)
    return f"{platform.value}:{stable_id or canonical}"
