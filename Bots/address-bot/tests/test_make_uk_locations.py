import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_uk_locations.py"
SPEC = importlib.util.spec_from_file_location("make_uk_locations", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class MakeUkLocationsTests(unittest.TestCase):
    def test_mayfair_matches_expected_four_part_shape(self):
        rows = [
            {
                "NAME1": "Mayfair",
                "TYPE": "populatedPlace",
                "LOCAL_TYPE": "Suburban Area",
                "DISTRICT_BOROUGH": "City of Westminster",
                "COUNTY_UNITARY": "Greater London",
                "REGION": "London",
                "COUNTRY": "England",
            }
        ]

        locations = module.build_locations(rows)

        self.assertEqual(
            [item.text for item in locations],
            [
                "Mayfair, City of Westminster, Greater London, "
                "United Kingdom"
            ],
        )

    def test_scottish_unitary_authority_uses_scotland_as_area(self):
        rows = [
            {
                "NAME1": "Leith",
                "TYPE": "populatedPlace",
                "LOCAL_TYPE": "Suburban Area",
                "DISTRICT_BOROUGH": "",
                "COUNTY_UNITARY": "City of Edinburgh",
                "REGION": "Scotland",
                "COUNTRY": "Scotland",
            }
        ]

        self.assertEqual(
            module.build_locations(rows)[0].text,
            "Leith, City of Edinburgh, Scotland, United Kingdom",
        )

    def test_non_place_records_and_parent_repetitions_are_excluded(self):
        rows = [
            {
                "NAME1": "Mayfair",
                "TYPE": "transportNetwork",
                "LOCAL_TYPE": "Named Road",
                "DISTRICT_BOROUGH": "Bolton",
                "COUNTY_UNITARY": "",
                "REGION": "North West",
                "COUNTRY": "England",
            },
            {
                "NAME1": "Reading",
                "TYPE": "populatedPlace",
                "LOCAL_TYPE": "Town",
                "DISTRICT_BOROUGH": "",
                "COUNTY_UNITARY": "Reading",
                "REGION": "South East",
                "COUNTRY": "England",
            },
        ]

        self.assertEqual(module.build_locations(rows), [])

    def test_writer_matches_existing_locations_js_format(self):
        locations = [
            module.Location(
                "Mayfair", "City of Westminster", "Greater London"
            ),
            module.Location(
                "Soho", "City of Westminster", "Greater London"
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "united-kingdom"
            count = module.write_locations(output, locations)
            generated = (
                output
                / "greater-london"
                / "city-of-westminster"
                / "LOCATIONS.js"
            ).read_text(encoding="utf-8")

        self.assertEqual(count, 1)
        self.assertEqual(
            generated,
            "LOCATIONS = [\n"
            '  "Mayfair, City of Westminster, Greater London, United Kingdom",\n'
            '  "Soho, City of Westminster, Greater London, United Kingdom"\n'
            "]\n",
        )

    def test_writer_preserves_existing_northern_ireland_subtree(self):
        locations = [
            module.Location(
                "Mayfair", "City of Westminster", "Greater London"
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "united-kingdom"
            ni_file = (
                output
                / "northern-ireland"
                / "belfast"
                / "LOCATIONS.js"
            )
            ni_file.parent.mkdir(parents=True)
            ni_file.write_text(
                'LOCATIONS = ["Ballymurphy, Belfast, Northern Ireland, United Kingdom"]\n',
                encoding="utf-8",
            )

            module.write_locations(output, locations)

            preserved = ni_file.read_text(encoding="utf-8")

        self.assertIn("Ballymurphy", preserved)


if __name__ == "__main__":
    unittest.main()
