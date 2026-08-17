import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "make_northern_ireland_locations.py"
)
SPEC = importlib.util.spec_from_file_location(
    "make_northern_ireland_locations",
    SCRIPT,
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class MakeNorthernIrelandLocationsTests(unittest.TestCase):
    def test_assigns_townland_to_district_and_writes_expected_shape(self):
        district = module.District(
            code="N09000003",
            name="Belfast",
            polygons=(
                {
                    "type": "Polygon",
                    "coordinates": [[
                        [-6.1, 54.5],
                        [-5.8, 54.5],
                        [-5.8, 54.7],
                        [-6.1, 54.7],
                        [-6.1, 54.5],
                    ]],
                },
            ),
            bounds=(-6.1, 54.5, -5.8, 54.7),
        )
        townland = module.Townland(1, "Ballymurphy", -5.98, 54.59)

        assigned = module.assign_townlands([townland], [district])

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "northern-ireland"
            metadata = module.write_locations(output, assigned)
            generated = (
                output / "belfast" / "LOCATIONS.js"
            ).read_text(encoding="utf-8")
            saved_metadata = json.loads(
                (output / "SOURCE.json").read_text(encoding="utf-8")
            )

        self.assertEqual(
            generated,
            "LOCATIONS = [\n"
            '  "Ballymurphy, Belfast, Northern Ireland, United Kingdom"\n'
            "]\n",
        )
        self.assertEqual(metadata["districts"], 1)
        self.assertEqual(saved_metadata["locations"], 1)

    def test_unmatched_townland_fails_closed(self):
        district = module.District(
            code="N09000003",
            name="Belfast",
            polygons=(
                {
                    "type": "Polygon",
                    "coordinates": [[
                        [-6.1, 54.5],
                        [-5.8, 54.5],
                        [-5.8, 54.7],
                        [-6.1, 54.7],
                        [-6.1, 54.5],
                    ]],
                },
            ),
            bounds=(-6.1, 54.5, -5.8, 54.7),
        )

        with self.assertRaises(RuntimeError):
            module.assign_townlands(
                [module.Townland(1, "Outside", -7.0, 55.0)],
                [district],
            )

    def test_parses_all_current_districts_from_ons_geojson(self):
        features = [
            {
                "properties": {
                    "LAD25CD": f"N090000{index:02d}",
                    "LAD25NM": f"District {index}",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [index, 0],
                        [index + 0.5, 0],
                        [index + 0.5, 0.5],
                        [index, 0.5],
                        [index, 0],
                    ]],
                },
            }
            for index in range(1, 12)
        ]

        districts = module.districts_from_geojson(
            {"type": "FeatureCollection", "features": features}
        )

        self.assertEqual(len(districts), 11)
        self.assertEqual(districts[0].code, "N09000001")
        self.assertEqual(districts[-1].code, "N09000011")


if __name__ == "__main__":
    unittest.main()
