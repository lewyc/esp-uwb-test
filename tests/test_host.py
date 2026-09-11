import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from tools.uwb_suite.analysis import solve_position, summarize_measurements
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


if __name__ == "__main__":
    unittest.main()
