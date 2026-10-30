from analysis.summary_figures import _parse_side_car


def test_listed_basenames_include_rows_without_fi_specs(tmp_path):
    side = tmp_path / "_fi_protocols.csv"
    side.write_text(
        "metadata_file,fi_min_pA,fi_max_pA,fi_n_steps,cell_id\n"
        "fre001/a_metadata.json,-50,50,5,\n"
        "b_metadata.json,,,,\n"
        ",-50,50,5,\n"
    )

    listed, specs, cell_ids = _parse_side_car(side)

    assert listed == {"a_metadata.json", "b_metadata.json"}
    assert set(specs) == {"a_metadata.json"}
    assert cell_ids == {}


def test_missing_side_car_whitelists_nothing(tmp_path):
    listed, specs, cell_ids = _parse_side_car(tmp_path / "missing.csv")

    assert listed == set()
    assert specs == {}
    assert cell_ids == {}
