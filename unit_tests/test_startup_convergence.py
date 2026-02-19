from __future__ import absolute_import

import unittest

import dnsdle as startup_module


_PATCHABLE = (
    "parse_cli_config",
    "compute_max_ciphertext_slice_bytes",
    "build_publish_items",
    "apply_mapping",
    "build_runtime_state",
)


class StartupConvergenceTests(unittest.TestCase):
    def setUp(self):
        self._originals = {}
        for name in _PATCHABLE:
            self._originals[name] = getattr(startup_module, name)

    def tearDown(self):
        for name in _PATCHABLE:
            setattr(startup_module, name, self._originals[name])

    def _install(self, parse_stub, budget_stub, publish_stub, mapping_stub, runtime_stub):
        startup_module.parse_cli_config = parse_stub
        startup_module.compute_max_ciphertext_slice_bytes = budget_stub
        startup_module.build_publish_items = publish_stub
        startup_module.apply_mapping = mapping_stub
        startup_module.build_runtime_state = runtime_stub

    def test_converges_over_multiple_promotions_for_collision_heavy_manifest(self):
        call_log = []
        fake_config = object()

        budget_by_query_len = {1: 120, 3: 80, 5: 48}
        mapping_len_by_budget = {
            120: (3, 2, 3),
            80: (5, 4, 5),
            48: (5, 5, 4),
        }

        def parse_stub(argv):
            call_log.append(("parse", tuple(argv or ())))
            return fake_config

        def budget_stub(config, query_token_len=1):
            self.assertIs(fake_config, config)
            call_log.append(("budget", query_token_len))
            return budget_by_query_len[query_token_len], {"query_token_len": query_token_len}

        def publish_stub(config, max_ciphertext_slice_bytes):
            self.assertIs(fake_config, config)
            call_log.append(("publish", max_ciphertext_slice_bytes))
            return [{"budget": max_ciphertext_slice_bytes}]

        def mapping_stub(publish_items, config):
            self.assertIs(fake_config, config)
            token_lens = mapping_len_by_budget[publish_items[0]["budget"]]
            call_log.append(("map", token_lens))
            return [{"slice_token_len": value} for value in token_lens]

        def runtime_stub(config, mapped_publish_items, max_ciphertext_slice_bytes, budget_info):
            self.assertIs(fake_config, config)
            call_log.append(
                (
                    "runtime",
                    max_ciphertext_slice_bytes,
                    budget_info["query_token_len"],
                    tuple(item["slice_token_len"] for item in mapped_publish_items),
                )
            )
            return {
                "max_ciphertext_slice_bytes": max_ciphertext_slice_bytes,
                "query_token_len": budget_info["query_token_len"],
                "realized_token_lens": tuple(
                    item["slice_token_len"] for item in mapped_publish_items
                ),
            }

        self._install(parse_stub, budget_stub, publish_stub, mapping_stub, runtime_stub)
        runtime_state = startup_module.build_startup_state(["--dummy"])

        budget_queries = [entry[1] for entry in call_log if entry[0] == "budget"]
        publish_budgets = [entry[1] for entry in call_log if entry[0] == "publish"]
        map_lens = [entry[1] for entry in call_log if entry[0] == "map"]

        self.assertEqual([1, 3, 5], budget_queries)
        self.assertEqual([120, 80, 48], publish_budgets)
        self.assertEqual([(3, 2, 3), (5, 4, 5), (5, 5, 4)], map_lens)
        self.assertEqual(48, runtime_state["max_ciphertext_slice_bytes"])
        self.assertEqual(5, runtime_state["query_token_len"])
        self.assertEqual((5, 5, 4), runtime_state["realized_token_lens"])
        self.assertEqual(
            runtime_state["query_token_len"],
            max(runtime_state["realized_token_lens"]),
        )

    def test_stops_when_realized_max_token_len_falls_below_current_query_len(self):
        fake_config = object()
        budget_calls = []

        budget_by_query_len = {1: 100, 4: 64}
        mapping_len_by_budget = {
            100: (4, 4, 3),
            64: (2, 2, 1),
        }

        def parse_stub(_argv):
            return fake_config

        def budget_stub(config, query_token_len=1):
            self.assertIs(fake_config, config)
            budget_calls.append(query_token_len)
            return budget_by_query_len[query_token_len], {"query_token_len": query_token_len}

        def publish_stub(_config, max_ciphertext_slice_bytes):
            return [{"budget": max_ciphertext_slice_bytes}]

        def mapping_stub(publish_items, _config):
            token_lens = mapping_len_by_budget[publish_items[0]["budget"]]
            return [{"slice_token_len": value} for value in token_lens]

        def runtime_stub(config, mapped_publish_items, max_ciphertext_slice_bytes, budget_info):
            self.assertIs(fake_config, config)
            return (
                max_ciphertext_slice_bytes,
                budget_info["query_token_len"],
                tuple(item["slice_token_len"] for item in mapped_publish_items),
            )

        self._install(parse_stub, budget_stub, publish_stub, mapping_stub, runtime_stub)
        runtime_state = startup_module.build_startup_state(["--dummy"])

        self.assertEqual([1, 4], budget_calls)
        self.assertEqual((64, 4, (2, 2, 1)), runtime_state)

    def test_runtime_state_is_built_from_final_iteration_only(self):
        fake_config = object()
        captured = {}

        def parse_stub(_argv):
            return fake_config

        def budget_stub(_config, query_token_len=1):
            if query_token_len == 1:
                return 72, {"query_token_len": 1}
            return 40, {"query_token_len": 3}

        def publish_stub(_config, max_ciphertext_slice_bytes):
            return [{"budget": max_ciphertext_slice_bytes}]

        def mapping_stub(publish_items, _config):
            if publish_items[0]["budget"] == 72:
                return [{"slice_token_len": 3}, {"slice_token_len": 2}]
            return [{"slice_token_len": 3}, {"slice_token_len": 3}]

        def runtime_stub(config, mapped_publish_items, max_ciphertext_slice_bytes, budget_info):
            captured["config"] = config
            captured["max_ciphertext_slice_bytes"] = max_ciphertext_slice_bytes
            captured["query_token_len"] = budget_info["query_token_len"]
            captured["lens"] = tuple(item["slice_token_len"] for item in mapped_publish_items)
            return "ok"

        self._install(parse_stub, budget_stub, publish_stub, mapping_stub, runtime_stub)
        result = startup_module.build_startup_state(["--dummy"])

        self.assertEqual("ok", result)
        self.assertIs(fake_config, captured["config"])
        self.assertEqual(40, captured["max_ciphertext_slice_bytes"])
        self.assertEqual(3, captured["query_token_len"])
        self.assertEqual((3, 3), captured["lens"])


if __name__ == "__main__":
    unittest.main()
