import numpy as np
import pandas as pd
from shapely.geometry import box

from totens.portfolio.spatial import cluster_summary, fit_clusters, inside_boundary, metric_grid, nearest_totems


def test_boundary_includes_border_excludes_outside_and_missing():
    assert inside_boundary([0, 1, 2, np.nan], [0, 1, 0, 0], box(-1, -1, 1, 1)).tolist() == [True, True, False, False]


def test_coincident_point_weights_preserve_density_without_expansion():
    points = pd.DataFrame({"latitude": [-10., -11.], "longitude": [-63., -64.], "weight": [50, 1]})
    labels = fit_clusters(points, eps=150, min_samples=50)
    clusters, summary = cluster_summary(points, labels)
    assert len(clusters) == 1 and clusters.n.iloc[0] == 50
    assert summary["noise_records"] == 1 and summary["records"] == 51


def test_no_clusters_and_empty_input_have_valid_denominators():
    points = pd.DataFrame({"latitude": [-10.], "longitude": [-63.], "weight": [1]})
    clusters, summary = cluster_summary(points, fit_clusters(points, min_samples=50))
    assert clusters.empty and summary["noise_fraction"] == 1
    assert len(fit_clusters(points.iloc[:0])) == 0


def test_metric_grid_preserves_weights_and_zero_distance_is_inside_all_radii():
    points = pd.DataFrame({"latitude": [-10., -10.01], "longitude": [-63., -63.01], "weight": [5, 7]})
    assert metric_grid(points).n.sum() == 12
    totems = pd.DataFrame({"id_unidade": ["SYNTHETIC"], "latitude": [-10.], "longitude": [-63.]})
    distances, nearest = nearest_totems([-10.], [-63.], totems)
    assert distances[0] == 0 and nearest[0] == "SYNTHETIC"
