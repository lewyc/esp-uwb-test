import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from tools.uwb_suite.analysis import (
    evaluate_filters,
    fit_calibration,
    solve_position,
    summarize_measurements,
    timestamp_delta_40,
)
from tools.uwb_suite.logging import RunLogger
from tools.uwb_suite.nodes import simulated_nodes
from tools.uwb_suite.runner import SuiteRunner


class AnalysisTests(unittest.TestCase):
    def test_statistics_include_failed_attempts(self):
        rows = [
            {"status": "ok", "range_m": 1.1, "true_distance_m": 1.0, "host_time_s": 1.0, "node": 1, "peer": 2},
            {"status": "timeout", "host_time_s": 1.1, "node": 1, "peer": 2},
            {"status": "ok", "range_m": 0.9, "true_distance_m": 1.0, "host_time_s": 1.3, "node": 1, "peer": 2},
        ]
        result = summarize_measurements(rows)
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["successes"], 2)
        self.assertAlmostEqual(result["completion_ratio"], 2 / 3)
        self.assertAlmostEqual(result["bias_m"], 0.0)
        self.assertAlmostEqual(result["mae_m"], 0.1)
        self.assertAlmostEqual(result["rmse_m"], 0.1)
        self.assertAlmostEqual(result["longest_dropout_s"], 0.3)

    def test_position_with_known_height_and_slant_ranges(self):
        anchors = [
            {"node": 2, "x": 0, "y": 0, "z": 0},
            {"node": 3, "x": 4, "y": 0, "z": 0},
            {"node": 4, "x": 4, "y": 4, "z": 0},
            {"node": 5, "x": 0, "y": 4, "z": 0},
        ]
        truth = (1.2, 1.4, 1.0)
        ranges = {a["node"]: math.dist(truth, (a["x"], a["y"], a["z"])) for a in anchors}
        result = solve_position(anchors, ranges, truth[2])
        self.assertAlmostEqual(result["x_m"], truth[0], places=5)
        self.assertAlmostEqual(result["y_m"], truth[1], places=5)
        self.assertLess(result["range_residual_rmse_m"], 1e-5)

    def test_bad_anchor_geometry_is_rejected(self):
        anchors = [{"node": 2, "x": 0, "y": 0}, {"node": 3, "x": 1, "y": 0}, {"node": 4, "x": 2, "y": 0}]
        with self.assertRaises(ValueError):
            solve_position(anchors, {2: 1, 3: 1, 4: 1}, 0)

    def test_timestamp_wraparound(self):
        self.assertEqual(timestamp_delta_40(25, (1 << 40) - 10), 35)

    def test_calibration_uses_independent_validation(self):
        stations = [
            {"station_id": 1, "true_distance_m": 1.0, "fit_role": "fit"},
            {"station_id": 2, "true_distance_m": 3.0, "fit_role": "fit"},
            {"station_id": 3, "true_distance_m": 2.0, "fit_role": "validation"},
        ]
        rows = []
        for station in stations:
            for _ in range(10):
                truth = station["true_distance_m"]
                rows.append({"station_id": station["station_id"], "status": "ok",
                             "raw_range_m": (truth - 0.2) / 0.9,
                             "true_distance_m": truth, "fit_role": station["fit_role"]})
        result = fit_calibration(rows, stations)
        self.assertEqual(result["evaluation_kind"], "independent_validation")
        affine = next(model for model in result["models"] if model["name"] == "scale_plus_offset")
        self.assertAlmostEqual(affine["scale"], 0.9, places=6)
        self.assertAlmostEqual(affine["offset_m"], 0.2, places=6)
        self.assertAlmostEqual(affine["validation"]["rmse_m"], 0.0, places=6)
        rows.append({"station_id": 3, "status": "timeout", "true_distance_m": 2.0,
                     "fit_role": "validation"})
        with_failure = fit_calibration(rows, stations)
        validation = next(model for model in with_failure["models"]
                          if model["name"] == "scale_plus_offset")["validation"]
        self.assertEqual(validation["attempts"], 11)
        self.assertAlmostEqual(validation["completion_ratio"], 10 / 11)

    def test_degenerate_calibration_rejects_affine_model(self):
        rows = [{"station_id": 1, "status": "ok", "raw_range_m": 1.2,
                 "true_distance_m": 1.0, "fit_role": "fit"} for _ in range(25)]
        result = fit_calibration(rows)
        self.assertNotIn("scale_plus_offset", [model["name"] for model in result["models"]])
        self.assertTrue(any("distinct" in warning for warning in result["warnings"]))

    def test_filters_handle_outlier_and_missing_diagnostics(self):
        rows = []
        for index, value in enumerate([1.0, 1.01, 0.99, 4.0, 1.0, 1.02, 0.98]):
            rows.append({"station_id": 1, "status": "ok", "raw_range_m": value,
                         "true_distance_m": 1.0,
                         "rx_power_dbm": -70 if index != 0 else None,
                         "first_path_power_dbm": -72 if index != 0 else None})
        result = evaluate_filters(rows)
        self.assertGreaterEqual(result["hampel"]["rejected"], 1)
        self.assertEqual(result["quality_weighting"]["missing"], 1)
        self.assertFalse(result["quality_weighting"]["hard_cutoff_used"])


class LoggingTests(unittest.TestCase):
    def test_partial_and_final_files_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = RunLogger(Path(directory), "range", {"stage": "range"})
            logger.event({"v": 1, "type": "measurement", "status": "ok", "range_m": 2.1,
                          "true_distance_m": 2.0, "node": 1, "peer": 2, "seq": 1}, "simulation")
            path = logger.path
            logger.finish()
            manifest = json.loads((path / "manifest.json").read_text())
            summary = json.loads((path / "summary.json").read_text())
            with (path / "measurements.csv").open(newline="") as stream:
                measurements = list(csv.DictReader(stream))
            self.assertEqual(manifest["stage"], "range")
            self.assertEqual(summary["attempts"], 1)
            self.assertEqual(measurements[0]["raw_range_m"], "2.1")
            self.assertAlmostEqual(float(measurements[0]["signed_error_m"]), 0.1)
            self.assertTrue((path / "events.jsonl").stat().st_size > 0)
            self.assertTrue((path / "report.md").exists())

    def test_sequence_gaps_and_restart_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = RunLogger(Path(directory), "hardware", {"stage": "hardware"})
            logger.event({"v": 1, "type": "ack", "node": 1, "boot": 10, "seq": 1}, "usb")
            logger.event({"v": 1, "type": "ack", "node": 1, "boot": 10, "seq": 3}, "usb")
            logger.event({"v": 1, "type": "ack", "node": 1, "boot": 11, "seq": 1}, "usb")
            path = logger.path
            summary = logger.finish()
            types = [json.loads(line)["type"] for line in (path / "events.jsonl").read_text().splitlines()]
            self.assertIn("host_sequence_gap", types)
            self.assertIn("host_restart_detected", types)
            self.assertEqual(summary["event_counts"]["host_sequence_gap"], 1)

    def test_calibration_preserves_raw_range(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = RunLogger(Path(directory), "range", {"stage": "range"})
            logger.event({"v": 1, "type": "measurement", "status": "ok", "range_m": 1.2,
                          "calibration_offset_m": -0.1, "node": 1, "peer": 2}, "usb")
            path = logger.path
            logger.finish()
            with (path / "measurements.csv").open(newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertAlmostEqual(float(row["raw_range_m"]), 1.2)
            self.assertAlmostEqual(float(row["range_m"]), 1.1)


class SimulationTests(unittest.TestCase):
    def test_complete_simulated_range_run(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = SuiteRunner(simulated_nodes(2),
                                 {"stage": "range", "attempts": 4, "rate_hz": 0, "true_distance_m": 2.0},
                                 Path(directory), compact=True)
            result = runner.run()
            self.assertEqual(result["stage_status"], "complete")
            self.assertEqual(result["attempts"], 4)
            self.assertGreater(result["successes"], 0)
            self.assertEqual(result["event_counts"].get("host_sequence_gap", 0), 0)

    def test_calibration_run_writes_models_and_node_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "stage": "calibration", "attempts": 5, "rate_hz": 0,
                "timestamp_mode": "standard_adjusted",
                "node_settings": {
                    "1": {"tx_antenna_delay": 16000, "rx_antenna_delay": 16100},
                    "2": {"tx_antenna_delay": 16200, "rx_antenna_delay": 16300},
                },
                "calibration_stations": [
                    {"station_id": 1, "true_distance_m": 1.0, "fit_role": "fit",
                     "attempts": 5, "warmup_attempts": 1},
                    {"station_id": 2, "true_distance_m": 3.0, "fit_role": "fit",
                     "attempts": 5, "warmup_attempts": 0},
                    {"station_id": 3, "true_distance_m": 2.0, "fit_role": "validation",
                     "attempts": 5, "warmup_attempts": 0},
                ],
            }
            runner = SuiteRunner(simulated_nodes(2), config, Path(directory), compact=True)
            summary = runner.run()
            path = runner.log.path
            manifest = json.loads((path / "manifest.json").read_text())
            result = json.loads((path / "calibration_result.json").read_text())
            with (path / "measurements.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(summary["stage_status"], "complete")
            self.assertEqual(manifest["timestamp_mode"], "standard_adjusted")
            self.assertEqual(manifest["antenna_delays"][0]["tx_antenna_delay"], 16000)
            self.assertEqual(result["evaluation_kind"], "independent_validation")
            self.assertEqual(len(rows), 15)
            self.assertEqual(rows[0]["raw_range_m"], rows[0]["range_m"])
            self.assertEqual(rows[0]["excluded_from_fit"], "True")
            self.assertTrue((path / "calibration_report.md").exists())

    def test_calibration_ctrl_c_finalizes_partial_run(self):
        with tempfile.TemporaryDirectory() as directory:
            nodes = simulated_nodes(2)
            original_execute = nodes[0].execute
            calls = {"ranges": 0}
            def interrupting_execute(command, timeout=5.0):
                if command.get("cmd") == "range":
                    calls["ranges"] += 1
                    if calls["ranges"] == 3:
                        raise KeyboardInterrupt
                return original_execute(command, timeout)
            nodes[0].execute = interrupting_execute
            config = {"stage": "calibration", "attempts": 5, "rate_hz": 0,
                      "calibration_stations": [
                          {"station_id": 1, "true_distance_m": 1.0, "fit_role": "fit",
                           "attempts": 5, "warmup_attempts": 0}]}
            runner = SuiteRunner(nodes, config, Path(directory), compact=True)
            summary = runner.run()
            manifest = json.loads((runner.log.path / "manifest.json").read_text())
            self.assertEqual(summary["stage_status"], "interrupted")
            self.assertTrue(manifest["calibration_partial"])
            self.assertEqual(summary["attempts"], 2)
            self.assertTrue((runner.log.path / "calibration_result.json").exists())


if __name__ == "__main__":
    unittest.main()
