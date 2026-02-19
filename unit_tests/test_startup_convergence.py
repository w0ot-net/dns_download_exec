from __future__ import absolute_import

import unittest

import dnsdle as startup_module


_PATCHABLE = (
    "parse_cli_args",
    "build_config",
    "configure_active_logger",
    "compute_max_ciphertext_slice_bytes",
    "build_publish_items",
    "apply_mapping",
    "build_runtime_state",
    "generate_client_artifacts",
    "build_publish_items_from_sources",
)


def _noop_generate_client_artifacts(runtime_state):
    return {"artifacts": (), "managed_dir": "", "artifact_count": 0, "target_os": ()}


def _noop_build_publish_items_from_sources(
    sources, compression_level, max_ciphertext_slice_bytes,
    seen_plaintext_sha256=None, seen_file_ids=None,
):
    return []


class _FakeConfig(object):
    compression_level = 9


class StartupConvergenceTests(unittest.TestCase):
    def setUp(self):
        self._originals = {}
        for name in _PATCHABLE:
            self._originals[name] = getattr(startup_module, name)

    def tearDown(self):
        for name in _PATCHABLE:
            setattr(startup_module, name, self._originals[name])

    def _install(
        self,
        parse_stub,
        build_config_stub,
        configure_logger_stub,
        budget_stub,
        publish_stub,
        mapping_stub,
        runtime_stub,
        generate_stub=None,
        source_publish_stub=None,
    ):
        startup_module.parse_cli_args = parse_stub
        startup_module.build_config = build_config_stub
        startup_module.configure_active_logger = configure_logger_stub
        startup_module.compute_max_ciphertext_slice_bytes = budget_stub
        startup_module.build_publish_items = publish_stub
        startup_module.apply_mapping = mapping_stub
        startup_module.build_runtime_state = runtime_stub
        startup_module.generate_client_artifacts = (
            generate_stub or _noop_generate_client_artifacts
        )
        startup_module.build_publish_items_from_sources = (
            source_publish_stub or _noop_build_publish_items_from_sources
        )

    def test_converges_over_multiple_promotions_for_collision_heavy_manifest(self):
        call_log = []
        fake_config = _FakeConfig()

        budget_by_query_len = {1: 120, 3: 80, 5: 48}
        mapping_len_by_budget = {
            120: (3, 2, 3),
            80: (5, 4, 5),
            48: (5, 5, 4),
        }

        def parse_stub(argv):
            call_log.append(("parse", tuple(argv or ())))
            return "parsed-args"

        def build_config_stub(parsed_args):
            self.assertEqual("parsed-args", parsed_args)
            call_log.append(("config", parsed_args))
            return fake_config

        def configure_logger_stub(config):
            self.assertIs(fake_config, config)
            call_log.append(("configure_logger", "ok"))

        def budget_stub(config, query_token_len=1):
            self.assertIs(fake_config, config)
            call_log.append(("budget", query_token_len))
            return budget_by_query_len[query_token_len], {"query_token_len": query_token_len}

        def publish_stub(config, max_ciphertext_slice_bytes):
            self.assertIs(fake_config, config)
            call_log.append(("publish", max_ciphertext_slice_bytes))
            return [
                {
                    "budget": max_ciphertext_slice_bytes,
                    "file_id": "pub0",
                    "plaintext_sha256": "psha0",
                },
            ]

        def mapping_stub(publish_items, config):
            self.assertIs(fake_config, config)
            token_lens = mapping_len_by_budget[publish_items[0]["budget"]]
            call_log.append(("map", token_lens))
            return [
                {
                    "slice_token_len": value,
                    "file_id": "m%d" % idx,
                    "file_tag": "t%d" % idx,
                    "slice_tokens": tuple("s%d_%d" % (idx, j) for j in range(value)),
                }
                for idx, value in enumerate(token_lens)
            ]

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

        self._install(
            parse_stub,
            build_config_stub,
            configure_logger_stub,
            budget_stub,
            publish_stub,
            mapping_stub,
            runtime_stub,
        )
        runtime_state, generation_result = startup_module.build_startup_state(["--dummy"])

        budget_queries = [entry[1] for entry in call_log if entry[0] == "budget"]
        publish_budgets = [entry[1] for entry in call_log if entry[0] == "publish"]
        map_lens = [entry[1] for entry in call_log if entry[0] == "map"]

        self.assertEqual([1, 3, 5], budget_queries)
        self.assertEqual([120, 80, 48], publish_budgets)
        self.assertEqual([(3, 2, 3), (5, 4, 5), (5, 5, 4), (5, 5, 4)], map_lens)
        self.assertEqual(48, runtime_state["max_ciphertext_slice_bytes"])
        self.assertEqual(5, runtime_state["query_token_len"])
        self.assertEqual((5, 5, 4), runtime_state["realized_token_lens"])
        self.assertEqual(
            runtime_state["query_token_len"],
            max(runtime_state["realized_token_lens"]),
        )

    def test_stops_when_realized_max_token_len_falls_below_current_query_len(self):
        fake_config = _FakeConfig()
        budget_calls = []

        budget_by_query_len = {1: 100, 4: 64}
        mapping_len_by_budget = {
            100: (4, 4, 3),
            64: (2, 2, 1),
        }

        def parse_stub(_argv):
            return "parsed-args"

        def build_config_stub(parsed_args):
            self.assertEqual("parsed-args", parsed_args)
            return fake_config

        def configure_logger_stub(config):
            self.assertIs(fake_config, config)

        def budget_stub(config, query_token_len=1):
            self.assertIs(fake_config, config)
            budget_calls.append(query_token_len)
            return budget_by_query_len[query_token_len], {"query_token_len": query_token_len}

        def publish_stub(_config, max_ciphertext_slice_bytes):
            return [
                {
                    "budget": max_ciphertext_slice_bytes,
                    "file_id": "pub0",
                    "plaintext_sha256": "psha0",
                },
            ]

        def mapping_stub(publish_items, _config):
            token_lens = mapping_len_by_budget[publish_items[0]["budget"]]
            return [
                {
                    "slice_token_len": value,
                    "file_id": "m%d" % idx,
                    "file_tag": "t%d" % idx,
                    "slice_tokens": tuple("s%d_%d" % (idx, j) for j in range(value)),
                }
                for idx, value in enumerate(token_lens)
            ]

        def runtime_stub(config, mapped_publish_items, max_ciphertext_slice_bytes, budget_info):
            self.assertIs(fake_config, config)
            return (
                max_ciphertext_slice_bytes,
                budget_info["query_token_len"],
                tuple(item["slice_token_len"] for item in mapped_publish_items),
            )

        self._install(
            parse_stub,
            build_config_stub,
            configure_logger_stub,
            budget_stub,
            publish_stub,
            mapping_stub,
            runtime_stub,
        )
        runtime_state, _generation_result = startup_module.build_startup_state(["--dummy"])

        self.assertEqual([1, 4], budget_calls)
        self.assertEqual((64, 4, (2, 2, 1)), runtime_state)

    def test_runtime_state_is_built_from_final_iteration_only(self):
        fake_config = _FakeConfig()
        captured = {}

        def parse_stub(_argv):
            return "parsed-args"

        def build_config_stub(parsed_args):
            self.assertEqual("parsed-args", parsed_args)
            return fake_config

        def configure_logger_stub(config):
            self.assertIs(fake_config, config)

        def budget_stub(_config, query_token_len=1):
            if query_token_len == 1:
                return 72, {"query_token_len": 1}
            return 40, {"query_token_len": 3}

        def publish_stub(_config, max_ciphertext_slice_bytes):
            return [
                {
                    "budget": max_ciphertext_slice_bytes,
                    "file_id": "pub0",
                    "plaintext_sha256": "psha0",
                },
            ]

        def mapping_stub(publish_items, _config):
            if publish_items[0]["budget"] == 72:
                return [
                    {"slice_token_len": 3, "file_id": "m0", "file_tag": "t0", "slice_tokens": ("a", "b", "c")},
                    {"slice_token_len": 2, "file_id": "m1", "file_tag": "t1", "slice_tokens": ("x", "y")},
                ]
            return [
                {"slice_token_len": 3, "file_id": "m0", "file_tag": "t0", "slice_tokens": ("a", "b", "c")},
                {"slice_token_len": 3, "file_id": "m1", "file_tag": "t1", "slice_tokens": ("x", "y", "z")},
            ]

        def runtime_stub(config, mapped_publish_items, max_ciphertext_slice_bytes, budget_info):
            captured["config"] = config
            captured["max_ciphertext_slice_bytes"] = max_ciphertext_slice_bytes
            captured["query_token_len"] = budget_info["query_token_len"]
            captured["lens"] = tuple(item["slice_token_len"] for item in mapped_publish_items)
            return "ok"

        self._install(
            parse_stub,
            build_config_stub,
            configure_logger_stub,
            budget_stub,
            publish_stub,
            mapping_stub,
            runtime_stub,
        )
        result, _generation_result = startup_module.build_startup_state(["--dummy"])

        self.assertEqual("ok", result)
        self.assertIs(fake_config, captured["config"])
        self.assertEqual(40, captured["max_ciphertext_slice_bytes"])
        self.assertEqual(3, captured["query_token_len"])
        self.assertEqual((3, 3), captured["lens"])


if __name__ == "__main__":
    unittest.main()
