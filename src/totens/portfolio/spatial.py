"""Geometric coverage, metric grids and weighted, bounded DBSCAN experiments."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from scipy.sparse import vstack
from sklearn.cluster import DBSCAN
from sklearn.neighbors import BallTree, NearestNeighbors, sort_graph_by_row_values

EARTH_RADIUS = 6_371_000.
GRID_CRS = "+proj=laea +lat_0=-10.5 +lon_0=-63.5 +datum=WGS84 +units=m +no_defs"


def load_geography(folder: Path) -> tuple[object, gpd.GeoDataFrame]:
    states = gpd.read_parquet(folder / "br_uf_2024__br_uf_2024.parquet").to_crs(4326)
    ro = states[states.sigla_uf.eq("RO")]
    if len(ro) != 1:
        raise ValueError("invalid_ro_boundary")
    municipalities = gpd.read_parquet(folder / "ro_municipios_2024__ro_municipios_2024.parquet").to_crs(4326)
    return shapely.make_valid(ro.geometry.union_all()), municipalities


def inside_boundary(latitude, longitude, boundary) -> np.ndarray:
    latitude, longitude = np.asarray(latitude, dtype=float), np.asarray(longitude, dtype=float)
    valid = np.isfinite(latitude) & np.isfinite(longitude) & (abs(latitude) <= 90) & (abs(longitude) <= 180)
    result = np.zeros(len(latitude), dtype=bool)
    for start in range(0, len(latitude), 100_000):
        positions = np.arange(start, min(start + 100_000, len(latitude)))
        positions = positions[valid[positions]]
        result[positions] = shapely.covers(boundary, shapely.points(longitude[positions], latitude[positions]))
    return result


def nearest_totems(latitude, longitude, totems: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    points = totems[["latitude", "longitude"]].to_numpy(float)
    if not len(points) or not np.isfinite(points).all():
        raise ValueError("invalid_totem_points")
    tree = BallTree(np.radians(points), metric="haversine")
    distances, indices = tree.query(np.radians(np.column_stack([latitude, longitude])), k=1)
    return distances[:, 0] * EARTH_RADIUS, totems.id_unidade.to_numpy()[indices[:, 0]]


def metric_grid(frame: pd.DataFrame, size: int = 1000) -> pd.DataFrame:
    transform = Transformer.from_crs(4326, GRID_CRS, always_xy=True)
    x, y = transform.transform(frame.longitude.to_numpy(), frame.latitude.to_numpy())
    work = pd.DataFrame({"grid_x": np.floor(np.asarray(x) / size).astype(int),
                         "grid_y": np.floor(np.asarray(y) / size).astype(int),
                         "weight": frame["weight"].to_numpy() if "weight" in frame else np.ones(len(frame))})
    result = work.groupby(["grid_x", "grid_y"], as_index=False).weight.sum().rename(columns={"weight": "n"})
    inverse = Transformer.from_crs(GRID_CRS, 4326, always_xy=True)
    lon, lat = inverse.transform(((result.grid_x + .5) * size).to_numpy(), ((result.grid_y + .5) * size).to_numpy())
    result["longitude"], result["latitude"] = lon, lat
    return result.sort_values(["n", "grid_x", "grid_y"], ascending=[False, True, True]).reset_index(drop=True)


def weighted_points(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.groupby(["latitude", "longitude"], as_index=False).size().rename(columns={"size": "weight"}).sort_values(["latitude", "longitude"])


def neighbor_graph(points: pd.DataFrame, max_eps: float = 300, max_edges: int = 12_000_000):
    radians = np.radians(points[["latitude", "longitude"]].to_numpy(float))
    neighbors = NearestNeighbors(radius=max_eps / EARTH_RADIUS, metric="haversine", algorithm="ball_tree", n_jobs=1).fit(radians)
    chunks, edges = [], 0
    for start in range(0, len(points), 2000):
        chunk = neighbors.radius_neighbors_graph(radians[start:start + 2000], mode="distance")
        edges += chunk.nnz
        if edges > max_edges:
            raise ValueError("clustering_neighbor_budget_exceeded")
        chunks.append(chunk)
    graph = vstack(chunks, format="csr")
    return sort_graph_by_row_values(graph, copy=False, warn_when_not_sorted=False)


def fit_clusters(points: pd.DataFrame, eps: float = 150, min_samples: int = 50, graph=None) -> np.ndarray:
    if points.empty:
        return np.array([], dtype=int)
    if graph is None:
        graph = neighbor_graph(points, eps)
    model = DBSCAN(eps=eps / EARTH_RADIUS, min_samples=min_samples, metric="precomputed", n_jobs=1)
    return model.fit_predict(graph, sample_weight=points.weight.to_numpy(float))


def cluster_summary(points: pd.DataFrame, labels: np.ndarray) -> tuple[pd.DataFrame, dict]:
    work = points.assign(cluster=labels)
    result = []
    for label, group in work[work.cluster.ge(0)].groupby("cluster"):
        weights = group.weight.to_numpy()
        result.append({"cluster": int(label), "n": int(weights.sum()), "unique_points": len(group),
                       "latitude": float(np.average(group.latitude, weights=weights)),
                       "longitude": float(np.average(group.longitude, weights=weights))})
    summary = pd.DataFrame(result, columns=["cluster", "n", "unique_points", "latitude", "longitude"])
    if not summary.empty:
        summary = summary.sort_values(["n", "cluster"], ascending=[False, True]).reset_index(drop=True)
    total = int(points.weight.sum())
    noise = int(work.loc[work.cluster.eq(-1), "weight"].sum())
    return summary, {"clusters": len(summary), "records": total, "unique_points": len(points),
                     "noise_records": noise, "noise_fraction": noise / total if total else None,
                     "largest_cluster_records": int(summary.n.max()) if len(summary) else 0}


def footprint_cells(points: pd.DataFrame, labels: np.ndarray, size: int = 1000) -> set[tuple[int, int]]:
    grid = metric_grid(points.loc[labels >= 0], size)
    return set(zip(grid.grid_x, grid.grid_y))


def analyze_spatial(cohort: pd.DataFrame, daily: pd.DataFrame, boundary, totems: pd.DataFrame, progress=None, *, eps=150, min_samples=50, grid_size=1000,
                    eps_values=(100,150,300), min_values=(25,50,100), radii=(150,300,500)) -> tuple[pd.DataFrame, dict, dict[str, pd.DataFrame]]:
    cohort = cohort.copy()
    cohort["inside_ro"] = inside_boundary(cohort.latitude, cohort.longitude, boundary)
    cohort["spatial_eligible"] = cohort.high_risk & cohort.coordinates_valid & cohort.inside_ro
    spatial = cohort[cohort.spatial_eligible].copy()
    if spatial.empty:
        raise ValueError("no_spatial_high_risk_records")
    distances, nearest = nearest_totems(spatial.latitude, spatial.longitude, totems)
    cohort["distance_totem_m"] = np.nan
    cohort.loc[spatial.index, "distance_totem_m"] = distances
    spatial["distance_totem_m"], spatial["nearest_totem"] = distances, nearest
    last = daily.loc[daily.complete_month, "date"].max().to_period("M")
    first = last - 11
    months = pd.period_range(first, last, freq="M")
    complete = set(daily.loc[daily.complete_month, "date"].dt.to_period("M"))
    if not set(months).issubset(complete):
        raise ValueError("insufficient_complete_spatial_months")
    window = spatial[spatial.local_date.between(first.start_time, last.end_time)].copy()
    summary = {
        "high_risk_protocols": int(cohort.high_risk.sum()),
        "high_risk_coordinate_invalid_or_conflicting": int((cohort.high_risk & ~cohort.coordinates_valid).sum()),
        "high_risk_outside_ro": int((cohort.high_risk & cohort.coordinates_valid & ~cohort.inside_ro).sum()),
        "spatial_eligible": len(spatial), "totem_points": len(totems),
        "window_start": str(first.start_time.date()), "window_end": str(last.end_time.date()),
        "window_records": len(window), "grid_size_m": grid_size, "grid_crs": GRID_CRS,
        "coverage_scope": "geometric proximity to the available register; operation dates and causal effects unknown",
        "coverage": {str(radius): {"inside": int((distances <= radius).sum()), "denominator": len(spatial),
                                    "fraction": float((distances <= radius).mean())} for radius in radii},
        "window_coverage": {str(radius): {"inside": int(window.distance_totem_m.le(radius).sum()), "denominator": len(window),
                                           "fraction": float(window.distance_totem_m.le(radius).mean()) if len(window) else None}
                            for radius in radii},
    }
    assert summary["high_risk_coordinate_invalid_or_conflicting"] + summary["high_risk_outside_ro"] + len(spatial) == summary["high_risk_protocols"]
    points = weighted_points(window)
    grid = metric_grid(window, grid_size)
    graph = neighbor_graph(points, max(eps_values)) if len(points) else None
    rows, default_clusters, default_labels = [], pd.DataFrame(), np.array([], dtype=int)
    for distance in eps_values:
        for minimum in min_values:
            labels = fit_clusters(points, distance, minimum, graph)
            clusters, stats = cluster_summary(points, labels)
            rows.append({"eps_m": distance, "min_samples": minimum, **stats})
            if (distance, minimum) == (eps, min_samples):
                default_clusters, default_labels = clusters, labels
            if progress:
                progress({"event": "clustering_configuration_complete", "eps_m": distance, "min_samples": minimum, "clusters": stats["clusters"]})
    halves, footprints = [], []
    split = (first + 6).start_time
    for name, half in (("first_semester", window[window.local_date.lt(split)]), ("second_semester", window[window.local_date.ge(split)])):
        half_points = weighted_points(half)
        labels = fit_clusters(half_points, eps, min_samples)
        _, stats = cluster_summary(half_points, labels)
        halves.append({"period": name, **stats})
        footprints.append(footprint_cells(half_points, labels, grid_size))
    union = footprints[0] | footprints[1]
    summary["semester_clustered_grid_jaccard"] = len(footprints[0] & footprints[1]) / len(union) if union else None
    summary["stability_definition"] = "Jaccard of occupied 1-km cells with clustered points in each semester; not label equality"
    summary["default_clustering"] = next(row for row in rows if (row["eps_m"], row["min_samples"]) == (eps, min_samples))
    summary["sensitivity"] = rows
    summary["semesters"] = halves
    summary["grid_top10_share"] = float(grid.head(10).n.sum() / len(window)) if len(window) else None
    clustered_points = points.assign(cluster=default_labels)
    return cohort, summary, {"grid": grid, "cluster_sensitivity": pd.DataFrame(rows),
                            "clusters": default_clusters, "clustered_points": clustered_points,
                            "semester_comparison": pd.DataFrame(halves), "spatial_records": spatial}
