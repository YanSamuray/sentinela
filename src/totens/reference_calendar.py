"""Scoped civil calendars with evidenced validity and explicit unknown coverage."""
from __future__ import annotations

import pandas as pd


def expand_rules(rules, years, sources):
    """Expand fixed civil-date rules only within evidenced validity intervals."""
    rows = []
    for rule in rules:
        source = sources.get(rule["source_id"])
        if not source or source["status"] != "archived":
            raise ValueError("calendar_rule_requires_archived_source")
        start, end = pd.Timestamp(rule["valid_from"]), pd.Timestamp(rule["valid_to"])
        if end < start or rule["event_kind"] not in ("FERIADO", "FACULTATIVO", "SUSPENSAO_EXPEDIENTE"):
            raise ValueError("invalid_calendar_rule")
        if not rule.get("locator") or not rule.get("audience") or not rule.get("publication_date"):
            raise ValueError("calendar_rule_evidence_missing")
        for year in sorted(set(years)):
            date = pd.Timestamp(year=year, month=rule["month"], day=rule["day"])
            if start <= date <= end:
                rows.append({"data": date, "descricao": rule["title"], "tipo": rule.get("scope", "MUNICIPAL"),
                    "municipio_nome": rule.get("municipality_name", "TODOS"), "codigo_municipio_ibge": rule.get("municipality_code"),
                    "source": source["url"], "source_version": source["sha256"], "verified": True,
                    "status": "official_document_scoped", "vigencia_inicio": start, "vigencia_fim": end,
                    "event_kind": rule["event_kind"], "audience": rule["audience"],
                    "source_id": rule["source_id"], "rule_id": rule["rule_id"], "locator": rule["locator"],
                    "publication_date": pd.Timestamp(rule["publication_date"]),
                    "verification_basis": "archived_official_document", "original_date": rule.get("original_date")})
    return pd.DataFrame(rows)


def local_dates(values):
    index = pd.DatetimeIndex(values)
    if index.tz is not None:
        index = index.tz_convert("America/Porto_Velho").tz_localize(None)
    return index.normalize()


def municipal_daily(dates, municipalities, calendar, coverage, *, audience="EXECUTIVO_MUNICIPAL"):
    """One row per local date/city; unknown absence is nullable, not a false holiday."""
    dates = local_dates(dates).unique().sort_values()
    codes = list(dict.fromkeys(str(code) for code in municipalities))
    keys = pd.MultiIndex.from_product([dates, codes], names=["data", "codigo_municipio_ibge"])
    result = keys.to_frame(index=False)
    result["year"] = result.data.dt.year
    cv = coverage.loc[coverage.audience.eq(audience), ["codigo_municipio_ibge", "year", "coverage_status"]].copy()
    cv["codigo_municipio_ibge"] = cv.codigo_municipio_ibge.astype(str)
    if cv.duplicated(["codigo_municipio_ibge", "year"]).any():
        raise ValueError("duplicate_calendar_coverage")
    result = result.merge(cv, on=["codigo_municipio_ibge", "year"], how="left", validate="many_to_one")
    result["coverage_status"] = result.coverage_status.fillna("not_researched")
    events = calendar[calendar.tipo.eq("MUNICIPAL") & calendar.verified & calendar.audience.eq(audience)].copy()
    events["data"] = local_dates(events.data)
    for kind, column in (("FERIADO", "holiday_municipal"), ("FACULTATIVO", "optional_municipal"), ("SUSPENSAO_EXPEDIENTE", "closure_municipal")):
        observed = set(zip(events.loc[events.event_kind.eq(kind), "data"], events.loc[events.event_kind.eq(kind), "codigo_municipio_ibge"].astype(str)))
        result[column] = pd.array([True if (d, c) in observed else False if s == "confirmed" else pd.NA
                                  for d, c, s in zip(result.data, result.codigo_municipio_ibge, result.coverage_status)], dtype="boolean")
    result["audience"] = audience
    return result.drop(columns="year")


def review_legacy(legacy, official):
    rows = []
    keys = ["data", "codigo_municipio_ibge", "descricao"]
    duplicate = legacy.duplicated(keys, keep=False)
    for position, (_, row) in enumerate(legacy.iterrows()):
        match = official[official.tipo.eq("MUNICIPAL") & official.data.eq(row.data) & official.codigo_municipio_ibge.eq(row.codigo_municipio_ibge)]
        transfer = official[official.tipo.eq("MUNICIPAL") & official.codigo_municipio_ibge.eq(row.codigo_municipio_ibge)
                            & official.original_date.eq(row.data.strftime("%Y-%m-%d"))] if "original_date" in official else official.iloc[:0]
        decision = "duplicate_legacy_entry" if duplicate.iloc[position] else "same_date_official_observance_scope_only" if len(match) else "date_transfer_requires_scope_correction" if len(transfer) else "not_proven"
        rows.append({"legacy_row": position, "data": row.data, "codigo_municipio_ibge": row.codigo_municipio_ibge,
            "municipio_nome": row.municipio_nome, "descricao": row.descricao, "decision": decision,
            "evidence_rule_ids": "|".join(match.rule_id.tolist() + transfer.rule_id.tolist()),
            "note": "Date agreement does not verify the original label or general public applicability."})
    return pd.DataFrame(rows)
