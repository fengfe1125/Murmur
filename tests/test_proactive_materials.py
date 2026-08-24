from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import run_unittest  # noqa: E402

from murmur.initiative import pick_intent  # noqa: E402
from murmur.proactive_materials import (  # noqa: E402
    ProactiveMaterial,
    dossier_materials,
    select_material,
)


class ProactiveMaterialTests(unittest.TestCase):
    def test_dossier_lines_get_stable_source_bound_ids(self):
        blocks = {"正在发生": "- 下周三有面试\n- 猫最近在吃药"}
        first = dossier_materials(blocks)
        second = dossier_materials(blocks)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0].category, "current_topic")
        self.assertEqual(first[0].source_ref, "下周三有面试")
        self.assertNotEqual(first[0].material_id, first[1].material_id)

    def test_unknown_or_empty_blocks_do_not_become_material(self):
        self.assertEqual(dossier_materials({}), [])
        self.assertEqual(dossier_materials({"正在发生": "（还不知道）"}), [])

    def test_material_bound_intents_are_never_chosen_without_a_source(self):
        chosen = {
            pick_intent([], material_available=False).key for _ in range(100)
        }
        self.assertNotIn("接上次", chosen)
        self.assertNotIn("想到他", chosen)

    def test_cooldown_skips_the_same_source_for_fourteen_days(self):
        now = datetime(2026, 8, 23, 10, tzinfo=UTC)
        a = ProactiveMaterial("a", "current_topic", "面试")
        b = ProactiveMaterial("b", "current_topic", "猫吃药")
        picked = select_material(
            [a, b], {"a": now + timedelta(days=10)}, now
        )
        self.assertEqual(picked, b)
        self.assertIsNone(select_material([a], {"a": now}, now - timedelta(seconds=1)))


if __name__ == "__main__":
    run_unittest()
