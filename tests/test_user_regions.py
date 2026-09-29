"""User Code markers that cannot be preserved must stop generation."""

import importlib
import unittest

from libxr import generator_code_stm32 as generator


class UserRegionMarkers(unittest.TestCase):
    def setUp(self):
        importlib.reload(generator)
        self.project = {
            "Mcu": {"Type": "STM32F407IGH6", "Family": "STM32F4"},
            "GPIO": {},
            "Peripherals": {},
        }
        generator.initialize_registry(False)
        self.base = generator.generate_full_code(self.project, False, "")

    def assertRefused(self, existing, message):
        with self.assertRaisesRegex(ValueError, message) as error:
            generator.generate_full_code(self.project, False, existing)
        self.assertIn("nothing was written", str(error.exception))

    def test_canonical_markers_are_accepted(self):
        existing = self.base.replace(
            "/* User Code Begin 2 */", "/* User Code Begin 2 */\n  Keep();"
        )
        self.assertIn("Keep();", generator.generate_full_code(self.project, False, existing))

    def test_empty_existing_file_is_new(self):
        self.assertEqual(generator.generate_full_code(self.project, False, "\n"), self.base)

    def test_unpaired_begin_is_refused(self):
        existing = self.base.replace("  /* User Code End 2 */\n", "  Keep();\n", 1)
        self.assertRefused(existing, "opens before User Code End 2")

    def test_unpaired_end_is_refused(self):
        existing = self.base.replace("/* User Code Begin 1 */\n", "", 1)
        self.assertRefused(existing, "User Code End 1 \\*/ has no matching Begin")

    def test_renamed_region_is_refused(self):
        existing = self.base.replace("User Code Begin 2", "User Code Begin 7").replace(
            "User Code End 2", "User Code End 7"
        )
        self.assertRefused(existing, "does not emit")

    def test_duplicated_region_is_refused(self):
        existing = self.base.replace(
            "/* User Code End 1 */",
            "/* User Code End 1 */\n/* User Code Begin 1 */\nKeep();\n/* User Code End 1 */",
            1,
        )
        self.assertRefused(existing, "duplicated")

    def test_missing_region_is_refused(self):
        existing = self.base.replace("  /* User Code Begin 2 */\n  /* User Code End 2 */\n", "", 1)
        self.assertRefused(existing, "End 2 markers are missing")

    def test_malformed_marker_is_refused(self):
        existing = self.base.replace("/* User Code Begin 1 */", "// User Code Begin 1", 1)
        self.assertRefused(existing, "malformed User Code marker")

    def test_region_inside_disabled_block_is_refused(self):
        existing = self.base.replace(
            "/* User Code Begin 1 */\n/* User Code End 1 */",
            "#if 0\n/* User Code Begin 1 */\nKeep();\n/* User Code End 1 */\n#endif",
            1,
        )
        self.assertRefused(existing, "inside a preprocessor conditional")

    def test_conditional_split_across_marker_is_refused(self):
        existing = self.base.replace(
            "/* User Code Begin 1 */\n/* User Code End 1 */",
            "#if 0\n/* User Code Begin 1 */\n#endif\nKeep();\n/* User Code End 1 */",
            1,
        )
        self.assertRefused(existing, "inside a preprocessor conditional")

    def test_conditional_inside_region_body_is_preserved(self):
        body = "#if 0\n  Disabled();\n#endif\n  Kept();"
        existing = self.base.replace(
            "/* User Code Begin 2 */", "/* User Code Begin 2 */\n" + body, 1
        )
        self.assertIn(body, generator.generate_full_code(self.project, False, existing))

    def test_prose_mentioning_markers_is_not_a_marker(self):
        existing = self.base.replace(
            "/* User Code Begin 2 */",
            "/* User Code Begin 2 */\n  // keep code between the User Code Begin/End lines",
            1,
        )
        generator.generate_full_code(self.project, False, existing)


if __name__ == "__main__":
    unittest.main()
