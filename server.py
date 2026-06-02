#!/usr/bin/env python3
"""FlyClaw Web Server - Graphical interface for flight info queries & config.

Usage:
    python server.py                 # Start on http://0.0.0.0:8080
    python server.py --port 9090     # Custom port
    python server.py --host 127.0.0.1 --port 8080  # Local only
"""

import argparse
import logging
import os
import time
import yaml

from datetime import date as date_type
from flask import Flask, render_template, request, jsonify, send_from_directory

import config as cfg
from __init__ import __version__
from airport_manager import airport_manager
from route_cache import route_cache
from sources.fr24 import FR24Source
from sources.google_flights import GoogleFlightsSource
from sources.airplaneslive import AirplanesLiveSource

logger = logging.getLogger("flyclaw.server")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(PROJECT_DIR, "templates")
STATIC_DIR = os.path.join(PROJECT_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)


def _query_fr24_flight(flight_number: str, timeout: int) -> list[dict]:
    src = FR24Source(timeout=timeout)
    return src.query_by_flight(flight_number)


def _query_gf_flight(flight_number: str, timeout: int, serpapi_key: str) -> list[dict]:
    src = GoogleFlightsSource(timeout=timeout, serpapi_key=serpapi_key)
    return src.query_by_flight(flight_number)


def _query_al_flight(flight_number: str, timeout: int) -> list[dict]:
    src = AirplanesLiveSource(timeout=timeout)
    return src.query_by_flight(flight_number)


def _query_gf_route(
    origin, dest, date_str, timeout, serpapi_key,
    return_date=None, adults=1, children=0, infants=0,
    cabin="economy", stops=0, sort=None, limit=None,
    retry=3, retry_delay=0.5, retry_backoff=2.0,
) -> list[dict]:
    src = GoogleFlightsSource(timeout=timeout, serpapi_key=serpapi_key,
                              retry=retry, retry_delay=retry_delay,
                              retry_backoff=retry_backoff)
    return src.query_by_route(
        origin, dest, date_str,
        return_date=return_date, adults=adults, children=children,
        infants=infants, cabin=cabin, stops=stops,
        sort=sort, limit=limit,
    )


def _query_sk_route(origin, dest, date_str, timeout,
                    stops=0, limit=None, retry=3, cabin="economy",
                    mcp_enabled=False, mcp_url="",
                    return_date=None,
                    retry_delay=0.5, retry_backoff=2.0) -> list[dict]:
    from sources.skiplagged import SkiplaggedSource
    src = SkiplaggedSource(timeout=timeout, retry=retry,
                           mcp_enabled=mcp_enabled, mcp_url=mcp_url,
                           retry_delay=retry_delay,
                           retry_backoff=retry_backoff)
    return src.query_by_route(origin, dest, date_str, stops=stops, limit=limit,
                              cabin=cabin, return_date=return_date)


def _query_fliggy_route(origin_name, dest_name, date_str, timeout,
                        cabin="economy", stops=0, limit=None,
                        return_date=None, api_key="", sign_secret="") -> list[dict]:
    from sources.fliggy_mcp import FliggyMCPSource
    src = FliggyMCPSource(timeout=timeout, api_key=api_key or None,
                          sign_secret=sign_secret or None)
    return src.query_by_route(origin_name, dest_name, date_str,
                              cabin=cabin, stops=stops, limit=limit,
                              return_date=return_date, timeout=timeout)


def _normalize_flight_number(fn: str) -> str:
    if not fn:
        return fn
    fn = fn.lstrip("_")
    i = len(fn)
    while i > 0 and fn[i - 1].isdigit():
        i -= 1
    if i == 0 or i == len(fn):
        return fn
    prefix = fn[:i]
    numeric = fn[i:]
    stripped = numeric.lstrip("0") or "0"
    return prefix + stripped


def _get_source_priority(record: dict) -> int:
    source = record.get("source", "")
    conf = cfg.get_config()
    sources_cfg = conf.get("sources", {})
    for name, scfg in sources_cfg.items():
        if name == source:
            return scfg.get("priority", 999)
    return 999


def _extract_date(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    return iso_str[:10] if len(iso_str) >= 10 else ""


def _merge_records(records: list[dict], date_aware: bool = False) -> list[dict]:
    for rec in records:
        fn = rec.get("flight_number")
        if fn:
            rec["flight_number"] = _normalize_flight_number(fn)

    sorted_records = sorted(records, key=_get_source_priority)

    grouped: dict[str, dict] = {}
    raw_groups: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []

    for rec in sorted_records:
        fn = rec.get("flight_number", "")
        if not fn:
            ungrouped.append(rec)
            continue
        if date_aware:
            date_part = _extract_date(rec.get("scheduled_departure"))
            key = f"{fn}|{date_part}"
        else:
            key = fn
        raw_groups.setdefault(key, []).append(rec)
        if key not in grouped:
            grouped[key] = rec.copy()
        else:
            existing = grouped[key]
            for k, val in rec.items():
                if k == "source":
                    sources = set(existing.get("source", "").split(","))
                    sources.add(val)
                    sources.discard("")
                    existing["source"] = ",".join(sorted(sources))
                elif not existing.get(k) and val:
                    existing[k] = val

    result = list(grouped.values()) + ungrouped
    conf = cfg.get_config()
    rate = conf.get("output", {}).get("exchange_rate_cny_usd", 7.25)

    for merged_rec in result:
        fn = merged_rec.get("flight_number", "")
        if not fn:
            continue
        if date_aware:
            dep = merged_rec.get("scheduled_departure", "")
            date_part = dep[:10] if dep and len(dep) >= 10 else ""
            group_key = f"{fn}|{date_part}"
        else:
            group_key = fn
        raw = raw_groups.get(group_key, [])
        if len(raw) <= 1:
            continue
        best_price_usd = None
        best_record = None
        for r in raw:
            p = r.get("price")
            if p is None or p == 0:
                continue
            cur = r.get("currency", "USD").upper()
            price_usd = p / rate if cur == "CNY" else p
            if best_price_usd is None or price_usd < best_price_usd:
                best_price_usd = price_usd
                best_record = r
        merged_rec.pop("price", None)
        merged_rec.pop("currency", None)
        if best_record:
            merged_rec["price"] = best_record["price"]
            merged_rec["currency"] = best_record.get("currency", "USD")

    if date_aware:
        result.sort(key=lambda r: _extract_date(r.get("scheduled_departure")), reverse=True)
    return result


def _deduplicate_codeshares(records: list[dict]) -> list[dict]:
    if not records:
        return records
    groups: dict[tuple, list[dict]] = {}
    ungrouped: list[dict] = []
    for rec in records:
        dep = rec.get("scheduled_departure", "")
        arr = rec.get("scheduled_arrival", "")
        orig = rec.get("origin_iata", "")
        dest = rec.get("destination_iata", "")
        stops = rec.get("stops")
        if dep and arr and orig and dest:
            key = (dep, arr, orig, dest, stops)
            groups.setdefault(key, []).append(rec)
        else:
            ungrouped.append(rec)
    result = []
    for group in groups.values():
        if len(group) == 1:
            result.append(group[0])
        else:
            scored = []
            for rec in group:
                score = sum(1 for v in rec.values() if v is not None and v != "")
                if rec.get("price") is not None:
                    score += 5
                scored.append((score, rec))
            scored.sort(key=lambda x: x[0], reverse=True)
            main = scored[0][1].copy()
            alt_fns = [r["flight_number"] for _, r in scored[1:]
                       if r.get("flight_number") and r["flight_number"] != main.get("flight_number")]
            if alt_fns:
                main["codeshare_variants"] = alt_fns
            for _, rec in scored[1:]:
                for k, v in rec.items():
                    if k in ("flight_number", "source", "codeshare_variants"):
                        continue
                    if not main.get(k) and v:
                        main[k] = v
            result.append(main)
    return result + ungrouped


def _extract_relay_route(results: list[dict], flight_number: str) -> dict | None:
    fn_upper = flight_number.strip().upper()
    for r in results:
        if r.get("flight_number", "").upper() == fn_upper:
            o = r.get("origin_iata", "")
            d = r.get("destination_iata", "")
            if o and d:
                return {"origin": o, "destination": d}
    for r in results:
        o = r.get("origin_iata", "")
        d = r.get("destination_iata", "")
        if o and d:
            return {"origin": o, "destination": d}
    return None


def _extract_relay_date(results: list[dict], user_date: str | None) -> str:
    today = date_type.today().isoformat()
    if user_date and user_date != "today":
        return user_date
    if user_date == "today":
        return today
    best_date = None
    for r in results:
        dep = r.get("scheduled_departure", "")
        if dep and len(dep) >= 10:
            d = dep[:10]
            if d >= today:
                if best_date is None or d < best_date:
                    best_date = d
    if best_date:
        return best_date
    return today


def _has_price(results: list[dict]) -> bool:
    return any(r.get("price") is not None for r in results)


# ---------------------------------------------------------------------------
# Routes - Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", version=__version__)


@app.route("/config")
def config_page():
    return render_template("config.html", version=__version__)


@app.route("/query")
def query_page():
    return render_template("query.html", version=__version__)


@app.route("/guide")
def guide_page():
    return render_template("guide.html", version=__version__)


# ---------------------------------------------------------------------------
# Routes - Static
# ---------------------------------------------------------------------------

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# ---------------------------------------------------------------------------
# API - Config management
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
def api_get_config():
    conf = cfg.get_config()
    return jsonify({"status": "ok", "config": conf})


@app.route("/api/config", methods=["POST"])
def api_save_config():
    try:
        data = request.get_json(force=True)
        if not data or "config" not in data:
            return jsonify({"status": "error", "message": "Missing 'config' in request body"}), 400
        new_config = data["config"]
        with open(cfg.CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(new_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return jsonify({"status": "ok", "message": "Configuration saved successfully"})
    except yaml.YAMLError as e:
        return jsonify({"status": "error", "message": f"YAML error: {e}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/config/defaults", methods=["GET"])
def api_get_defaults():
    return jsonify({"status": "ok", "defaults": cfg.DEFAULTS})


# ---------------------------------------------------------------------------
# API - Flight query by flight number
# ---------------------------------------------------------------------------

@app.route("/api/query/flight", methods=["GET"])
def api_query_flight():
    flight_number = request.args.get("flight", "").strip()
    if not flight_number:
        return jsonify({"status": "error", "message": "Missing 'flight' parameter"}), 400

    conf = cfg.get_config()
    sources_cfg = conf["sources"]
    query_cfg = conf["query"]
    base_timeout = query_cfg.get("timeout", 20)
    return_time = query_cfg.get("return_time", 12)

    tasks = []
    if sources_cfg["fr24"]["enabled"]:
        tasks.append(("FR24", lambda: _query_fr24_flight(flight_number, sources_cfg["fr24"]["timeout"])))
    if sources_cfg["google_flights"]["enabled"]:
        serpapi_key = sources_cfg["google_flights"].get("serpapi_key", "")
        tasks.append(("GoogleFlights", lambda: _query_gf_flight(flight_number, sources_cfg["google_flights"]["timeout"], serpapi_key)))
    if sources_cfg["airplanes_live"]["enabled"]:
        tasks.append(("AirplanesLive", lambda: _query_al_flight(flight_number, sources_cfg["airplanes_live"]["timeout"])))

    results = _execute_tasks(tasks, base_timeout, return_time)

    route_cache.update_from_records(results)
    route_cache.save()

    relay_enabled = query_cfg.get("route_relay", True)
    if relay_enabled and results and not _has_price(results):
        route = _extract_relay_route(results, flight_number)
        if route:
            relay_timeout = query_cfg.get("relay_timeout", 8)
            relay_date = _extract_relay_date(results, request.args.get("date"))
            relay_cfg = query_cfg.get("relay_engines", {"google_flights": True, "skiplagged": True})
            relay_tasks = []
            if relay_cfg.get("google_flights", True) and sources_cfg["google_flights"]["enabled"]:
                gf_cfg = sources_cfg["google_flights"]
                relay_tasks.append(("GoogleFlights-Relay",
                    lambda o=route["origin"], d=route["destination"]: _query_gf_route(
                        o, d, relay_date, gf_cfg["timeout"], gf_cfg.get("serpapi_key", ""),
                        retry=gf_cfg.get("retry", 2),
                        retry_delay=gf_cfg.get("retry_delay", 0.5),
                        retry_backoff=gf_cfg.get("retry_backoff", 2.0))))
            if relay_cfg.get("skiplagged", True) and sources_cfg.get("skiplagged", {}).get("enabled", False):
                sk_cfg = sources_cfg["skiplagged"]
                relay_tasks.append(("Skiplagged-Relay",
                    lambda o=route["origin"], d=route["destination"]: _query_sk_route(
                        o, d, relay_date, sk_cfg["timeout"],
                        retry=sk_cfg.get("retry", 3),
                        retry_delay=sk_cfg.get("retry_delay", 0.5),
                        retry_backoff=sk_cfg.get("retry_backoff", 2.0),
                        mcp_enabled=sk_cfg.get("mcp_enabled", False),
                        mcp_url=sk_cfg.get("mcp_url", ""))))
            if relay_tasks and relay_timeout > 2:
                relay_results = _execute_tasks(relay_tasks, relay_timeout, 1)
                results.extend(relay_results)

    merged = _merge_records(results, date_aware=True)
    merged = _deduplicate_codeshares(merged)

    filter_date = request.args.get("date", "").strip()
    if filter_date:
        if filter_date == "today":
            filter_date = date_type.today().isoformat()
        merged = [r for r in merged if _extract_date(r.get("scheduled_departure")) == filter_date]

    out_currency = request.args.get("currency", conf.get("output", {}).get("currency", "cny"))
    _apply_currency(merged, out_currency, conf)

    return jsonify({"status": "ok", "count": len(merged), "results": merged})


# ---------------------------------------------------------------------------
# API - Search by route
# ---------------------------------------------------------------------------

@app.route("/api/search/route", methods=["GET"])
def api_search_route():
    origin_input = request.args.get("from", "").strip()
    dest_input = request.args.get("to", "").strip()
    if not origin_input or not dest_input:
        return jsonify({"status": "error", "message": "Missing 'from' or 'to' parameter"}), 400

    date_str = request.args.get("date", "").strip()
    if not date_str:
        return jsonify({"status": "error", "message": "Missing 'date' parameter (YYYY-MM-DD)"}), 400

    conf = cfg.get_config()
    filter_inactive = conf["query"].get("filter_inactive_airports", True)
    origins = airport_manager.resolve_all(origin_input, filter_inactive=filter_inactive)
    dests = airport_manager.resolve_all(dest_input, filter_inactive=filter_inactive)

    if not origins:
        return jsonify({"status": "error", "message": f"Cannot resolve origin: {origin_input}"}), 400
    if not dests:
        return jsonify({"status": "error", "message": f"Cannot resolve destination: {dest_input}"}), 400

    sources_cfg = conf["sources"]
    global_timeout = conf["query"].get("timeout", 20)
    return_time = conf["query"].get("return_time", 12)

    adults = int(request.args.get("adults", 1))
    children = int(request.args.get("children", 0))
    infants = int(request.args.get("infants", 0))
    cabin = request.args.get("cabin", "economy")
    stops_str = request.args.get("stops", "0")
    stops_val = int(stops_str) if stops_str != "any" else "any"
    return_date = request.args.get("return_date", "").strip() or None
    sort = request.args.get("sort", "").strip() or None
    limit_str = request.args.get("limit", "").strip()
    limit_val = int(limit_str) if limit_str else None

    tasks = []

    if sources_cfg["fr24"]["enabled"]:
        tasks.append(("FR24", lambda: FR24Source(timeout=sources_cfg["fr24"]["timeout"]).query_by_route(origins[0], dests[0], date_str or None)))

    if sources_cfg["google_flights"]["enabled"] and date_str:
        gf_cfg = sources_cfg["google_flights"]
        gf_origin = origins if len(origins) > 1 else origins[0]
        gf_dest = dests if len(dests) > 1 else dests[0]
        tasks.append(("GoogleFlights", lambda: _query_gf_route(
            gf_origin, gf_dest, date_str, gf_cfg["timeout"], gf_cfg.get("serpapi_key", ""),
            return_date=return_date, adults=adults, children=children,
            infants=infants, cabin=cabin, stops=stops_val,
            sort=sort, limit=limit_val,
            retry=gf_cfg.get("retry", 2),
            retry_delay=gf_cfg.get("retry_delay", 0.5),
            retry_backoff=gf_cfg.get("retry_backoff", 2.0))))

    sk_cfg = sources_cfg.get("skiplagged", {})
    if sk_cfg.get("enabled", False) and date_str:
        for o in origins:
            for d in dests:
                tasks.append((f"Skiplagged-{o}→{d}", lambda o=o, d=d: _query_sk_route(
                    o, d, date_str, sk_cfg["timeout"],
                    stops=stops_val, limit=limit_val,
                    retry=sk_cfg.get("retry", 3),
                    cabin=cabin, return_date=return_date,
                    retry_delay=sk_cfg.get("retry_delay", 0.5),
                    retry_backoff=sk_cfg.get("retry_backoff", 2.0),
                    mcp_enabled=sk_cfg.get("mcp_enabled", False),
                    mcp_url=sk_cfg.get("mcp_url", ""))))

    fg_cfg = sources_cfg.get("fliggy_mcp", {})
    if fg_cfg.get("enabled", False) and date_str:
        fg_origin = _iata_to_city_cn(origins[0])
        fg_dest = _iata_to_city_cn(dests[0])
        tasks.append(("Fliggy", lambda: _query_fliggy_route(
            fg_origin, fg_dest, date_str, fg_cfg.get("timeout", 10),
            cabin=cabin, stops=stops_val, limit=limit_val,
            return_date=return_date,
            api_key=fg_cfg.get("api_key", ""),
            sign_secret=fg_cfg.get("sign_secret", ""))))

    sufficient = conf["query"].get("sufficient_source", "")
    sufficient_list = [sufficient] if sufficient else None
    results = _execute_tasks(tasks, global_timeout, return_time, sufficient_sources=sufficient_list)

    route_cache.update_from_records(results)
    route_cache.save()

    merged = _merge_records(results)
    merged = _deduplicate_codeshares(merged)

    if limit_val is not None:
        merged = merged[:limit_val]

    out_currency = request.args.get("currency", conf.get("output", {}).get("currency", "cny"))
    _apply_currency(merged, out_currency, conf)

    return jsonify({
        "status": "ok",
        "count": len(merged),
        "resolved_origin": origins,
        "resolved_destination": dests,
        "results": merged,
    })


# ---------------------------------------------------------------------------
# API - Airport search / autocomplete
# ---------------------------------------------------------------------------

@app.route("/api/airports", methods=["GET"])
def api_search_airports():
    query = request.args.get("q", "").strip()
    if not query or len(query) < 1:
        return jsonify({"status": "ok", "airports": []})
    results = airport_manager.search(query.lower(), limit=20)
    return jsonify({"status": "ok", "airports": results})


@app.route("/api/airports/resolve", methods=["GET"])
def api_resolve_airport():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Missing 'name' parameter"}), 400
    conf = cfg.get_config()
    filter_inactive = conf["query"].get("filter_inactive_airports", True)
    codes = airport_manager.resolve_all(name, filter_inactive=filter_inactive)
    infos = []
    for code in codes:
        info = airport_manager.get_info(code)
        if info:
            infos.append(info)
    return jsonify({"status": "ok", "codes": codes, "airports": infos})


# ---------------------------------------------------------------------------
# API - Server info
# ---------------------------------------------------------------------------

@app.route("/api/info", methods=["GET"])
def api_info():
    conf = cfg.get_config()
    enabled_sources = [k for k, v in conf.get("sources", {}).items() if v.get("enabled")]
    return jsonify({
        "status": "ok",
        "version": "0.4.4",
        "enabled_sources": enabled_sources,
        "airport_count": len(airport_manager._airports),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _execute_tasks(tasks: list, global_timeout: int, return_time: int | None = None,
                   sufficient_sources: list[str] | None = None) -> list[dict]:
    if not tasks:
        return []
    results = []
    start = time.monotonic()
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    executor = ThreadPoolExecutor(max_workers=len(tasks))
    future_to_name = {}
    for name, fn in tasks:
        future = executor.submit(fn)
        future_to_name[future] = name

    pending = set(future_to_name.keys())
    early_exit = False
    while pending:
        elapsed = time.monotonic() - start
        remaining = global_timeout - elapsed
        if remaining <= 0:
            logger.warning("Global timeout (%ds) reached", global_timeout)
            early_exit = True
            break

        wait_timeout = remaining
        if return_time and results:
            time_to_return = max(0, return_time - elapsed)
            wait_timeout = min(remaining, time_to_return) if time_to_return > 0 else 0

        if wait_timeout <= 0 and return_time and results:
            early_exit = True
            break

        done, pending = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
        for future in done:
            name = future_to_name[future]
            try:
                data = future.result()
                results.extend(data)
                logger.info("%s: %d results", name, len(data))
                if sufficient_sources and data and not early_exit:
                    name_lower = name.lower()
                    for ss in sufficient_sources:
                        ss_key = ss.lower().replace("_", "")
                        if ss_key in name_lower.replace("_", "") or name_lower in ss_key:
                            if any(r.get("price") is not None for r in data):
                                early_exit = True
                                break
            except Exception as e:
                logger.warning("%s query failed: %s", name, e)
        if early_exit:
            break
        if return_time and results and pending and (time.monotonic() - start) >= return_time:
            early_exit = True
            break

    if early_exit:
        executor.shutdown(wait=False, cancel_futures=True)
    else:
        executor.shutdown(wait=False)
    return results


def _iata_to_city_cn(iata: str) -> str:
    info = airport_manager.get_info(iata)
    if info:
        return info.get("city_cn") or info.get("city_en") or iata
    return iata


def _apply_currency(records: list[dict], out_currency: str, conf: dict) -> None:
    for r in records:
        if r.get("price") is not None:
            r.setdefault("currency", "USD")
    if out_currency not in ("usd", "cny"):
        return
    rate = conf.get("output", {}).get("exchange_rate_cny_usd", 7.25)
    if rate <= 0:
        return
    for r in records:
        if r.get("price") is None:
            continue
        cur = r.get("currency", "USD").upper()
        if out_currency == "usd" and cur == "CNY":
            r["price"] = round(r["price"] / rate, 2)
            r["currency"] = "USD"
        elif out_currency == "cny" and cur == "USD":
            r["price"] = round(r["price"] * rate, 2)
            r["currency"] = "CNY"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="flyclaw-server",
        description="FlyClaw Web Server - Graphical interface for flight information queries",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port to bind (default: 8080)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    print(f"\n  ✈️  FlyClaw Web Server v0.4.4")
    print(f"  ─────────────────────────────")
    print(f"  Local:   http://{args.host}:{args.port}")
    if args.host == "0.0.0.0":
        print(f"  Network: http://<your-ip>:{args.port}")
    print(f"  ─────────────────────────────")
    print(f"  Pages:")
    print(f"    /         Home & Navigation")
    print(f"    /config   Configuration Editor")
    print(f"    /query    Flight Query & Search")
    print(f"  ─────────────────────────────")
    print(f"  API Endpoints:")
    print(f"    GET  /api/info              Server info")
    print(f"    GET  /api/config            Read configuration")
    print(f"    POST /api/config            Save configuration")
    print(f"    GET  /api/query/flight?flight=CA981   Query by flight number")
    print(f"    GET  /api/search/route?from=PVG&to=JFK&date=2026-04-01  Search by route")
    print(f"    GET  /api/airports?q=shanghai         Airport search")
    print(f"    GET  /api/airports/resolve?name=上海   Resolve airport")
    print()

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
